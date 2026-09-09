/* Core named-pipe SERVER primitives, no Lua dependency.
 *
 * The bridge mod hosts the pipe (see project plan: "Flip the bridge
 * transport"); this is the raw Win32 layer a Lua C-extension module
 * wraps.
 *
 * Non-blocking by design: UE4SS gives each mod exactly ONE dedicated
 * async thread (confirmed via UE4SS's own source -- a single
 * `m_async_thread` per mod processing every ExecuteAsync/
 * ExecuteWithDelay/LoopAsync callback strictly one at a time), so a
 * Lua-visible call that blocks (waiting for a client to connect, or for
 * the next line from one) would stall every other pending client/accept
 * for as long as it blocks -- confirmed live: this starved concurrent
 * requests during real multi-agent testing. So accept/read here are
 * *async*: starting one spawns a background thread this module manages
 * itself (independent of UE4SS's async thread entirely), and Lua polls
 * its completion (non-blocking) on a lightweight recurring tick. Only
 * `pipe_write` stays synchronous -- writing to a pipe with buffer space
 * is effectively instantaneous, unlike accept/read which wait on an
 * external actor.
 */
#ifndef UE4SSMCP_PIPE_SERVER_H
#define UE4SSMCP_PIPE_SERVER_H

#include <stddef.h>

typedef struct PipeInstance PipeInstance;

/* How many simultaneous pipe instances (= simultaneous connected
 * clients) a single named pipe name will accept. Bounded rather than
 * unlimited so one runaway client can't exhaust pipe handles. */
#define UE4SSMCP_MAX_INSTANCES 8

/* Non-blocking poll result. */
typedef enum
{
    PIPE_POLL_PENDING = 0,
    PIPE_POLL_READY = 1,
    PIPE_POLL_ERROR = -1
} PipePollResult;

/* Creates one new, not-yet-connected pipe instance for `name`
 * (already-Windows-pipe-path-formatted, e.g. "\\\\.\\pipe\\foo").
 * Synchronous but fast -- this only registers the instance with
 * Windows, it doesn't wait for a client. Returns NULL and fills errbuf
 * on failure (including "too many instances already exist"). Retries
 * briefly on a transient ERROR_PIPE_BUSY the same way bridge.py's
 * `_create_pipe_instance` does, since Windows doesn't always free a
 * just-closed instance's slot instantly. */
PipeInstance* pipe_create_instance(const char* name, char* errbuf, size_t errbuf_len);

/* Starts a background thread that waits for a client to connect.
 * Returns immediately. Poll with pipe_poll_accept. Only one async op
 * (accept or read) may be in flight on an instance at a time. */
int pipe_accept_async(PipeInstance* inst, char* errbuf, size_t errbuf_len);

/* Non-blocking: PIPE_POLL_PENDING if the accept from pipe_accept_async
 * hasn't finished yet, PIPE_POLL_READY if a client connected,
 * PIPE_POLL_ERROR (errbuf filled) otherwise. Once this returns READY or
 * ERROR the op is consumed -- calling again without starting a new one
 * returns ERROR. */
PipePollResult pipe_poll_accept(PipeInstance* inst, char* errbuf, size_t errbuf_len);

/* Starts a background thread that reads one newline-terminated line
 * (newline stripped) from a connected instance. Returns immediately.
 * Poll with pipe_poll_read_line. */
int pipe_read_line_async(PipeInstance* inst, char* errbuf, size_t errbuf_len);

/* Non-blocking: PIPE_POLL_PENDING if not finished yet, PIPE_POLL_READY
 * if a line was read (written into out_buf, NUL-terminated),
 * PIPE_POLL_ERROR (errbuf filled, e.g. on disconnect) otherwise. */
PipePollResult pipe_poll_read_line(PipeInstance* inst, char* out_buf, size_t out_buf_len, char* errbuf, size_t errbuf_len);

/* Blocks writing `len` bytes -- synchronous by design, see file header.
 * Returns 1 on success, 0 on error. */
int pipe_write(PipeInstance* inst, const char* data, size_t len, char* errbuf, size_t errbuf_len);

/* Interrupts any pending async accept/read on `inst` from another
 * thread (CancelIoEx -- plain CloseHandle does not reliably do this on
 * its own for a pending ConnectNamedPipe/ReadFile). Safe to call even
 * if nothing is pending. */
void pipe_cancel(PipeInstance* inst);

/* Cancels any pending op, waits for its background thread to actually
 * exit (avoids a use-after-free if it were still running), then
 * disconnects/closes the handle and frees `inst`. Do not use `inst`
 * afterward. */
void pipe_close(PipeInstance* inst);

#endif
