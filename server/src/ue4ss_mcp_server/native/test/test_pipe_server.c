/* Standalone test of pipe_server.c's raw Win32 logic -- no Lua involved.
 * Exercises the async/poll API: basic accept/read/write round trip via
 * polling (not blocking), cancelling a pending async accept from
 * another thread, and multiple concurrent client connections against
 * the same pipe name, all polled non-blockingly the way main.lua's
 * single-threaded tick loop will.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>

#include "../src/pipe_server.h"

static const char* PIPE_NAME = "\\\\.\\pipe\\ue4ssmcp-native-test";

static int failures = 0;

#define CHECK(cond, msg) do { \
    if (!(cond)) { \
        printf("FAIL: %s (line %d)\n", msg, __LINE__); \
        failures++; \
    } else { \
        printf("ok: %s\n", msg); \
    } \
} while (0)

static PipePollResult poll_until_done(PipePollResult (*poll_fn)(PipeInstance*, char*, size_t), PipeInstance* inst, char* errbuf, size_t errbuf_len, DWORD timeout_ms)
{
    DWORD start = GetTickCount();
    for (;;) {
        PipePollResult r = poll_fn(inst, errbuf, errbuf_len);
        if (r != PIPE_POLL_PENDING) {
            return r;
        }
        if (GetTickCount() - start > timeout_ms) {
            return PIPE_POLL_PENDING; /* timed out still pending */
        }
        Sleep(10); /* mimics main.lua's tick interval */
    }
}

static PipePollResult poll_accept_until_done(PipeInstance* inst, char* errbuf, size_t errbuf_len, DWORD timeout_ms)
{
    return poll_until_done(pipe_poll_accept, inst, errbuf, errbuf_len, timeout_ms);
}

static PipePollResult poll_read_until_done(PipeInstance* inst, char* out_buf, size_t out_buf_len, char* errbuf, size_t errbuf_len, DWORD timeout_ms)
{
    DWORD start = GetTickCount();
    for (;;) {
        PipePollResult r = pipe_poll_read_line(inst, out_buf, out_buf_len, errbuf, errbuf_len);
        if (r != PIPE_POLL_PENDING) {
            return r;
        }
        if (GetTickCount() - start > timeout_ms) {
            return PIPE_POLL_PENDING;
        }
        Sleep(10);
    }
}

/* --- Test 1: basic round trip, all polled non-blockingly --- */

static HANDLE connect_client(const char* pipe_name)
{
    for (int i = 0; i < 100; i++) {
        HANDLE h = CreateFileA(pipe_name, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
        if (h != INVALID_HANDLE_VALUE) {
            return h;
        }
        Sleep(20);
    }
    return INVALID_HANDLE_VALUE;
}

static DWORD WINAPI client_thread_roundtrip(LPVOID param)
{
    (void)param;
    HANDLE h = connect_client(PIPE_NAME);
    if (h == INVALID_HANDLE_VALUE) {
        printf("client: failed to connect\n");
        return 1;
    }
    const char* msg = "hello from client\n";
    DWORD written = 0;
    WriteFile(h, msg, (DWORD)strlen(msg), &written, NULL);

    char buf[256] = {0};
    DWORD read = 0;
    ReadFile(h, buf, sizeof(buf) - 1, &read, NULL);
    printf("client received: %.*s", (int)read, buf);

    CloseHandle(h);
    return 0;
}

static void test_basic_roundtrip_polled(void)
{
    printf("\n=== test_basic_roundtrip_polled ===\n");
    char errbuf[256];
    PipeInstance* inst = pipe_create_instance(PIPE_NAME, errbuf, sizeof(errbuf));
    CHECK(inst != NULL, "create_instance succeeded");

    HANDLE client_thread = CreateThread(NULL, 0, client_thread_roundtrip, NULL, 0, NULL);

    CHECK(pipe_accept_async(inst, errbuf, sizeof(errbuf)), "accept_async started");
    PipePollResult accept_result = poll_accept_until_done(inst, errbuf, sizeof(errbuf), 5000);
    CHECK(accept_result == PIPE_POLL_READY, "poll_accept eventually reports READY");

    CHECK(pipe_read_line_async(inst, errbuf, sizeof(errbuf)), "read_line_async started");
    char line[256];
    PipePollResult read_result = poll_read_until_done(inst, line, sizeof(line), errbuf, sizeof(errbuf), 5000);
    CHECK(read_result == PIPE_POLL_READY, "poll_read_line eventually reports READY");
    CHECK(read_result == PIPE_POLL_READY && strcmp(line, "hello from client") == 0, "line content matches");

    const char* reply = "hello from server\n";
    CHECK(pipe_write(inst, reply, strlen(reply), errbuf, sizeof(errbuf)), "write succeeded");

    WaitForSingleObject(client_thread, 2000);
    CloseHandle(client_thread);
    pipe_close(inst);
}

/* --- Test 2: cancel a pending async accept from another thread --- */

typedef struct
{
    PipeInstance* inst;
} CancelArgs;

static DWORD WINAPI canceller_thread(LPVOID param)
{
    CancelArgs* args = (CancelArgs*)param;
    Sleep(300); /* give the main thread time to be polling a pending accept */
    pipe_cancel(args->inst);
    return 0;
}

static void test_cancel_pending_accept(void)
{
    printf("\n=== test_cancel_pending_accept ===\n");
    char errbuf[256];
    PipeInstance* inst = pipe_create_instance(PIPE_NAME, errbuf, sizeof(errbuf));
    CHECK(inst != NULL, "create_instance succeeded");

    CancelArgs args = {inst};
    HANDLE canceller = CreateThread(NULL, 0, canceller_thread, &args, 0, NULL);

    CHECK(pipe_accept_async(inst, errbuf, sizeof(errbuf)), "accept_async started");
    DWORD start = GetTickCount();
    PipePollResult result = poll_accept_until_done(inst, errbuf, sizeof(errbuf), 5000);
    DWORD elapsed = GetTickCount() - start;
    CHECK(result == PIPE_POLL_ERROR, "accept was cancelled, reported as error not READY");
    CHECK(elapsed < 5000, "cancel actually interrupted the pending accept promptly");
    printf("  (errbuf: %s, elapsed: %lums)\n", errbuf, elapsed);

    WaitForSingleObject(canceller, 2000);
    CloseHandle(canceller);
    pipe_close(inst);
}

/* --- Test 3: multiple concurrent clients, all polled from one loop --- */

static DWORD WINAPI client_thread_tagged(LPVOID param)
{
    const char* tag = (const char*)param;
    HANDLE h = connect_client(PIPE_NAME);
    if (h == INVALID_HANDLE_VALUE) {
        printf("client %s: failed to connect\n", tag);
        return 1;
    }
    char msg[64];
    _snprintf_s(msg, sizeof(msg), _TRUNCATE, "hi from %s\n", tag);
    DWORD written = 0;
    WriteFile(h, msg, (DWORD)strlen(msg), &written, NULL);

    char buf[256] = {0};
    DWORD read = 0;
    ReadFile(h, buf, sizeof(buf) - 1, &read, NULL);
    printf("client %s received: %.*s", tag, (int)read, buf);
    CloseHandle(h);
    return 0;
}

static void test_multiple_concurrent_clients_single_poll_loop(void)
{
    printf("\n=== test_multiple_concurrent_clients_single_poll_loop ===\n");
    char errbuf[256];

    PipeInstance* inst_a = pipe_create_instance(PIPE_NAME, errbuf, sizeof(errbuf));
    CHECK(inst_a != NULL, "first instance created (accepting client A)");
    CHECK(pipe_accept_async(inst_a, errbuf, sizeof(errbuf)), "A: accept_async started");

    PipeInstance* inst_b = pipe_create_instance(PIPE_NAME, errbuf, sizeof(errbuf));
    CHECK(inst_b != NULL, "second instance created while first is still pending -- this is the whole point: neither accept blocks the other");
    CHECK(pipe_accept_async(inst_b, errbuf, sizeof(errbuf)), "B: accept_async started");

    HANDLE thread_a = CreateThread(NULL, 0, client_thread_tagged, (LPVOID)"A", 0, NULL);
    HANDLE thread_b = CreateThread(NULL, 0, client_thread_tagged, (LPVOID)"B", 0, NULL);

    /* Single loop polling BOTH instances, exactly like main.lua's one
     * tick will poll every connected client + the next listener. */
    int a_connected = 0, b_connected = 0;
    DWORD start = GetTickCount();
    while ((!a_connected || !b_connected) && GetTickCount() - start < 5000) {
        if (!a_connected) {
            PipePollResult r = pipe_poll_accept(inst_a, errbuf, sizeof(errbuf));
            if (r == PIPE_POLL_READY) a_connected = 1;
        }
        if (!b_connected) {
            PipePollResult r = pipe_poll_accept(inst_b, errbuf, sizeof(errbuf));
            if (r == PIPE_POLL_READY) b_connected = 1;
        }
        Sleep(10);
    }
    CHECK(a_connected, "A accepted via the shared poll loop");
    CHECK(b_connected, "B accepted via the shared poll loop, independent of A");

    CHECK(pipe_read_line_async(inst_a, errbuf, sizeof(errbuf)), "A: read_line_async started");
    CHECK(pipe_read_line_async(inst_b, errbuf, sizeof(errbuf)), "B: read_line_async started");

    char line_a[256] = {0}, line_b[256] = {0};
    int a_read = 0, b_read = 0;
    start = GetTickCount();
    while ((!a_read || !b_read) && GetTickCount() - start < 5000) {
        if (!a_read) {
            PipePollResult r = pipe_poll_read_line(inst_a, line_a, sizeof(line_a), errbuf, sizeof(errbuf));
            if (r == PIPE_POLL_READY) a_read = 1;
        }
        if (!b_read) {
            PipePollResult r = pipe_poll_read_line(inst_b, line_b, sizeof(line_b), errbuf, sizeof(errbuf));
            if (r == PIPE_POLL_READY) b_read = 1;
        }
        Sleep(10);
    }
    CHECK(a_read && strcmp(line_a, "hi from A") == 0, "A's message read correctly, not crossed with B's");
    CHECK(b_read && strcmp(line_b, "hi from B") == 0, "B's message read correctly, not crossed with A's");

    const char* reply_a = "reply to A\n";
    const char* reply_b = "reply to B\n";
    pipe_write(inst_a, reply_a, strlen(reply_a), errbuf, sizeof(errbuf));
    pipe_write(inst_b, reply_b, strlen(reply_b), errbuf, sizeof(errbuf));

    WaitForSingleObject(thread_a, 2000);
    WaitForSingleObject(thread_b, 2000);
    CloseHandle(thread_a);
    CloseHandle(thread_b);
    pipe_close(inst_a);
    pipe_close(inst_b);
}

int main(void)
{
    test_basic_roundtrip_polled();
    test_cancel_pending_accept();
    test_multiple_concurrent_clients_single_poll_loop();

    printf("\n=== %s ===\n", failures == 0 ? "ALL PASSED" : "SOME FAILED");
    return failures == 0 ? 0 : 1;
}
