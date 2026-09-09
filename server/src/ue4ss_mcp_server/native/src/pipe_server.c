#include "pipe_server.h"

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define BUFFER_SIZE 65536
#define CREATE_RETRY_ATTEMPTS 40
#define CREATE_RETRY_SLEEP_MS 50
#define ERR_BUF_LEN 256

typedef enum
{
    OP_NONE = 0,
    OP_ACCEPT,
    OP_READ
} PendingOp;

struct PipeInstance
{
    HANDLE handle;
    CRITICAL_SECTION lock; /* guards every field below across threads */

    HANDLE op_thread;   /* background thread for the in-flight op, or NULL */
    PendingOp op_kind;
    int op_done;
    int op_ok;
    char op_err[ERR_BUF_LEN];
    char* op_line_result; /* heap copy of a completed read's line; owned until polled */

    int cancelled;
    int connected;

    char* read_buf; /* growable byte buffer for partial-line assembly */
    size_t read_buf_len;
    size_t read_buf_cap;
};

static void set_err(char* errbuf, size_t errbuf_len, const char* fmt, unsigned long code)
{
    if (!errbuf || errbuf_len == 0) {
        return;
    }
    _snprintf_s(errbuf, errbuf_len, _TRUNCATE, fmt, code);
}

PipeInstance* pipe_create_instance(const char* name, char* errbuf, size_t errbuf_len)
{
    PipeInstance* inst = (PipeInstance*)calloc(1, sizeof(PipeInstance));
    if (!inst) {
        set_err(errbuf, errbuf_len, "out of memory", 0);
        return NULL;
    }
    InitializeCriticalSection(&inst->lock);

    HANDLE h = INVALID_HANDLE_VALUE;
    for (int attempt = 0; attempt < CREATE_RETRY_ATTEMPTS; attempt++) {
        h = CreateNamedPipeA(
            name,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            UE4SSMCP_MAX_INSTANCES,
            BUFFER_SIZE,
            BUFFER_SIZE,
            0,
            NULL);
        if (h != INVALID_HANDLE_VALUE) {
            break;
        }
        DWORD err = GetLastError();
        if (err != ERROR_PIPE_BUSY) {
            set_err(errbuf, errbuf_len, "CreateNamedPipe failed (%lu)", err);
            DeleteCriticalSection(&inst->lock);
            free(inst);
            return NULL;
        }
        Sleep(CREATE_RETRY_SLEEP_MS);
    }
    if (h == INVALID_HANDLE_VALUE) {
        set_err(errbuf, errbuf_len, "CreateNamedPipe: too many instances busy (%lu)", ERROR_PIPE_BUSY);
        DeleteCriticalSection(&inst->lock);
        free(inst);
        return NULL;
    }

    inst->handle = h;
    return inst;
}

/* --- internal blocking helpers, run only on a background thread --- */

static int blocking_accept(PipeInstance* inst, char* errbuf, size_t errbuf_len)
{
    EnterCriticalSection(&inst->lock);
    if (inst->cancelled) {
        LeaveCriticalSection(&inst->lock);
        set_err(errbuf, errbuf_len, "cancelled before accept (%lu)", 0);
        return 0;
    }
    HANDLE h = inst->handle;
    LeaveCriticalSection(&inst->lock);

    BOOL ok = ConnectNamedPipe(h, NULL);
    if (!ok) {
        DWORD err = GetLastError();
        if (err == ERROR_PIPE_CONNECTED) {
            /* Client connected in the gap between CreateNamedPipe and
             * ConnectNamedPipe -- this is success, not an error. */
            ok = TRUE;
        } else if (err == ERROR_OPERATION_ABORTED) {
            set_err(errbuf, errbuf_len, "accept cancelled (%lu)", err);
            return 0;
        } else {
            set_err(errbuf, errbuf_len, "ConnectNamedPipe failed (%lu)", err);
            return 0;
        }
    }
    return 1;
}

static int try_extract_line(PipeInstance* inst, char** out_line)
{
    for (size_t i = 0; i < inst->read_buf_len; i++) {
        if (inst->read_buf[i] == '\n') {
            size_t line_len = i;
            if (line_len > 0 && inst->read_buf[line_len - 1] == '\r') {
                line_len--;
            }
            char* line = (char*)malloc(line_len + 1);
            if (!line) {
                return 0;
            }
            memcpy(line, inst->read_buf, line_len);
            line[line_len] = '\0';

            size_t remaining = inst->read_buf_len - (i + 1);
            memmove(inst->read_buf, inst->read_buf + i + 1, remaining);
            inst->read_buf_len = remaining;

            *out_line = line;
            return 1;
        }
    }
    return 0;
}

static int ensure_read_buf_capacity(PipeInstance* inst, size_t extra)
{
    size_t needed = inst->read_buf_len + extra;
    if (needed <= inst->read_buf_cap) {
        return 1;
    }
    size_t new_cap = inst->read_buf_cap == 0 ? BUFFER_SIZE : inst->read_buf_cap * 2;
    while (new_cap < needed) {
        new_cap *= 2;
    }
    char* new_buf = (char*)realloc(inst->read_buf, new_cap);
    if (!new_buf) {
        return 0;
    }
    inst->read_buf = new_buf;
    inst->read_buf_cap = new_cap;
    return 1;
}

/* Returns a malloc'd line via *out_line on success (caller frees). Runs
 * entirely on the background thread -- blocks freely, that's the point. */
static int blocking_read_line(PipeInstance* inst, char** out_line, char* errbuf, size_t errbuf_len)
{
    EnterCriticalSection(&inst->lock);
    int got = try_extract_line(inst, out_line);
    LeaveCriticalSection(&inst->lock);
    if (got) {
        return 1;
    }

    char chunk[BUFFER_SIZE];
    for (;;) {
        EnterCriticalSection(&inst->lock);
        if (inst->cancelled) {
            LeaveCriticalSection(&inst->lock);
            set_err(errbuf, errbuf_len, "cancelled during read (%lu)", 0);
            return 0;
        }
        HANDLE h = inst->handle;
        LeaveCriticalSection(&inst->lock);

        DWORD bytes_read = 0;
        BOOL ok = ReadFile(h, chunk, sizeof(chunk), &bytes_read, NULL);
        if (!ok || bytes_read == 0) {
            DWORD err = GetLastError();
            if (err == ERROR_OPERATION_ABORTED) {
                set_err(errbuf, errbuf_len, "read cancelled (%lu)", err);
            } else if (err == ERROR_BROKEN_PIPE) {
                set_err(errbuf, errbuf_len, "client disconnected (%lu)", err);
            } else {
                set_err(errbuf, errbuf_len, "ReadFile failed (%lu)", err);
            }
            return 0;
        }

        EnterCriticalSection(&inst->lock);
        if (!ensure_read_buf_capacity(inst, bytes_read)) {
            LeaveCriticalSection(&inst->lock);
            set_err(errbuf, errbuf_len, "out of memory assembling line (%lu)", 0);
            return 0;
        }
        memcpy(inst->read_buf + inst->read_buf_len, chunk, bytes_read);
        inst->read_buf_len += bytes_read;
        got = try_extract_line(inst, out_line);
        LeaveCriticalSection(&inst->lock);

        if (got) {
            return 1;
        }
    }
}

/* --- background thread entry points --- */

static DWORD WINAPI accept_thread_fn(LPVOID param)
{
    PipeInstance* inst = (PipeInstance*)param;
    char errbuf[ERR_BUF_LEN] = {0};
    int ok = blocking_accept(inst, errbuf, sizeof(errbuf));

    EnterCriticalSection(&inst->lock);
    if (ok) {
        inst->connected = 1;
    }
    inst->op_ok = ok;
    if (!ok) {
        memcpy(inst->op_err, errbuf, sizeof(errbuf));
    }
    inst->op_done = 1;
    LeaveCriticalSection(&inst->lock);
    return 0;
}

static DWORD WINAPI read_thread_fn(LPVOID param)
{
    PipeInstance* inst = (PipeInstance*)param;
    char errbuf[ERR_BUF_LEN] = {0};
    char* line = NULL;
    int ok = blocking_read_line(inst, &line, errbuf, sizeof(errbuf));

    EnterCriticalSection(&inst->lock);
    inst->op_ok = ok;
    if (ok) {
        inst->op_line_result = line;
    } else {
        memcpy(inst->op_err, errbuf, sizeof(errbuf));
    }
    inst->op_done = 1;
    LeaveCriticalSection(&inst->lock);
    return 0;
}

/* --- public async API --- */

static int start_async_op(PipeInstance* inst, PendingOp kind, LPTHREAD_START_ROUTINE fn, char* errbuf, size_t errbuf_len)
{
    EnterCriticalSection(&inst->lock);
    if (inst->op_kind != OP_NONE) {
        LeaveCriticalSection(&inst->lock);
        set_err(errbuf, errbuf_len, "an async op is already in flight on this instance (%lu)", 0);
        return 0;
    }
    inst->op_kind = kind;
    inst->op_done = 0;
    inst->op_ok = 0;
    inst->op_err[0] = '\0';
    LeaveCriticalSection(&inst->lock);

    HANDLE thread = CreateThread(NULL, 0, fn, inst, 0, NULL);
    if (thread == NULL) {
        EnterCriticalSection(&inst->lock);
        inst->op_kind = OP_NONE;
        LeaveCriticalSection(&inst->lock);
        set_err(errbuf, errbuf_len, "CreateThread failed (%lu)", GetLastError());
        return 0;
    }

    EnterCriticalSection(&inst->lock);
    inst->op_thread = thread;
    LeaveCriticalSection(&inst->lock);
    return 1;
}

int pipe_accept_async(PipeInstance* inst, char* errbuf, size_t errbuf_len)
{
    return start_async_op(inst, OP_ACCEPT, accept_thread_fn, errbuf, errbuf_len);
}

int pipe_read_line_async(PipeInstance* inst, char* errbuf, size_t errbuf_len)
{
    return start_async_op(inst, OP_READ, read_thread_fn, errbuf, errbuf_len);
}

/* Common poll logic: if the op has finished, reap its thread handle and
 * reset state to NONE so a new op can start. Returns 1 if op_done was
 * consumed (caller should look at inst->op_ok/op_err), 0 if still pending. */
static int poll_and_consume(PipeInstance* inst)
{
    EnterCriticalSection(&inst->lock);
    if (!inst->op_done) {
        LeaveCriticalSection(&inst->lock);
        return 0;
    }
    HANDLE thread = inst->op_thread;
    inst->op_thread = NULL;
    inst->op_kind = OP_NONE;
    LeaveCriticalSection(&inst->lock);

    if (thread != NULL) {
        WaitForSingleObject(thread, INFINITE); /* already done, this is instant */
        CloseHandle(thread);
    }
    return 1;
}

PipePollResult pipe_poll_accept(PipeInstance* inst, char* errbuf, size_t errbuf_len)
{
    if (!poll_and_consume(inst)) {
        return PIPE_POLL_PENDING;
    }
    EnterCriticalSection(&inst->lock);
    int ok = inst->op_ok;
    if (!ok && errbuf && errbuf_len > 0) {
        _snprintf_s(errbuf, errbuf_len, _TRUNCATE, "%s", inst->op_err);
    }
    LeaveCriticalSection(&inst->lock);
    return ok ? PIPE_POLL_READY : PIPE_POLL_ERROR;
}

PipePollResult pipe_poll_read_line(PipeInstance* inst, char* out_buf, size_t out_buf_len, char* errbuf, size_t errbuf_len)
{
    if (!poll_and_consume(inst)) {
        return PIPE_POLL_PENDING;
    }
    EnterCriticalSection(&inst->lock);
    int ok = inst->op_ok;
    if (ok) {
        if (out_buf && out_buf_len > 0) {
            _snprintf_s(out_buf, out_buf_len, _TRUNCATE, "%s", inst->op_line_result ? inst->op_line_result : "");
        }
        free(inst->op_line_result);
        inst->op_line_result = NULL;
    } else if (errbuf && errbuf_len > 0) {
        _snprintf_s(errbuf, errbuf_len, _TRUNCATE, "%s", inst->op_err);
    }
    LeaveCriticalSection(&inst->lock);
    return ok ? PIPE_POLL_READY : PIPE_POLL_ERROR;
}

int pipe_write(PipeInstance* inst, const char* data, size_t len, char* errbuf, size_t errbuf_len)
{
    EnterCriticalSection(&inst->lock);
    if (inst->cancelled) {
        LeaveCriticalSection(&inst->lock);
        set_err(errbuf, errbuf_len, "cancelled before write (%lu)", 0);
        return 0;
    }
    HANDLE h = inst->handle;
    LeaveCriticalSection(&inst->lock);

    size_t total_written = 0;
    while (total_written < len) {
        DWORD written = 0;
        BOOL ok = WriteFile(h, data + total_written, (DWORD)(len - total_written), &written, NULL);
        if (!ok) {
            DWORD err = GetLastError();
            set_err(errbuf, errbuf_len, "WriteFile failed (%lu)", err);
            return 0;
        }
        total_written += written;
    }
    return 1;
}

void pipe_cancel(PipeInstance* inst)
{
    EnterCriticalSection(&inst->lock);
    inst->cancelled = 1;
    HANDLE h = inst->handle;
    LeaveCriticalSection(&inst->lock);

    if (h != NULL && h != INVALID_HANDLE_VALUE) {
        CancelIoEx(h, NULL);
    }
}

void pipe_close(PipeInstance* inst)
{
    if (!inst) {
        return;
    }
    pipe_cancel(inst);

    EnterCriticalSection(&inst->lock);
    HANDLE op_thread = inst->op_thread;
    inst->op_thread = NULL;
    LeaveCriticalSection(&inst->lock);
    if (op_thread != NULL) {
        /* The op thread is blocked in a Win32 call we just cancelled --
         * it will unblock promptly with an error and exit; wait for it
         * so we never free `inst` out from under it. */
        WaitForSingleObject(op_thread, INFINITE);
        CloseHandle(op_thread);
    }

    EnterCriticalSection(&inst->lock);
    HANDLE h = inst->handle;
    inst->handle = INVALID_HANDLE_VALUE;
    int was_connected = inst->connected;
    LeaveCriticalSection(&inst->lock);

    if (h != NULL && h != INVALID_HANDLE_VALUE) {
        if (was_connected) {
            DisconnectNamedPipe(h);
        }
        CloseHandle(h);
    }

    DeleteCriticalSection(&inst->lock);
    free(inst->read_buf);
    free(inst->op_line_result);
    free(inst);
}
