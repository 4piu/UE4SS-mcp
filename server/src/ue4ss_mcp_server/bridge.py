"""Named-pipe server accepting bridge-mod connections.

Blocking win32 pipe I/O lives entirely in background/helper threads so it
never touches the MCP server's asyncio event loop. See dev-notes/spec.md
§8 for why a named pipe rather than TCP/LuaSocket, and dev-notes/
progress.md for the empirical test that verified stock Lua's io.open()
can act as a client against it.

Threading note: a synchronous (non-overlapped) named pipe handle only
supports one in-flight I/O operation at a time -- issuing a blocking
ReadFile on one thread while another thread WriteFiles the same handle
deadlocks (confirmed empirically while building M4's request/response
protocol, not just docs). So after the handshake, no thread reads the
pipe unless it's about to; `send_request` owns the handle exclusively
(via `_io_lock`) for the full duration of one write-then-read exchange,
and the accept-loop thread sits idle (no pending I/O) between requests.

A timeout on that read needs to abandon it without blocking, and plain
`CloseHandle` can itself block until an in-flight synchronous read on
that handle completes (which, for an abandoned read, may be never) --
confirmed empirically too. pywin32's `win32file` only exposes `CancelIo`
(cancels I/O issued by the *calling* thread only, useless here since the
read is on a different thread), not `CancelIoEx` (cancels any thread's
I/O on a handle), so `_cancel_pending_io` below drops to `ctypes` for
that one call.
"""

from __future__ import annotations

import ctypes
import json
import threading
import time
import uuid
from dataclasses import dataclass, field

import pywintypes
import win32file
import win32pipe

PIPE_NAME = r"\\.\pipe\ue4ss-mcp"
_BUFFER_SIZE = 65536
_REQUEST_TIMEOUT_S = 10.0

_kernel32 = ctypes.windll.kernel32
_kernel32.CancelIoEx.restype = ctypes.c_int
_kernel32.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


def _cancel_pending_io(pipe) -> None:
    try:
        _kernel32.CancelIoEx(int(pipe), None)
    except (OSError, ValueError):
        pass


@dataclass
class BridgeState:
    connected: bool = False
    ue4ss_version: str | None = None
    engine_version: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def mark_connected(self, ue4ss_version: str, engine_version: str) -> None:
        with self._lock:
            self.connected = True
            self.ue4ss_version = ue4ss_version
            self.engine_version = engine_version

    def mark_disconnected(self) -> None:
        with self._lock:
            self.connected = False
            self.ue4ss_version = None
            self.engine_version = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "connected": self.connected,
                "ue4ss_version": self.ue4ss_version,
                "engine_version": self.engine_version,
            }


class BridgeServer:
    """Accepts one bridge-mod connection at a time, forever.

    Reconnects are expected often (the game restarts constantly during
    modding iteration) -- after a client disconnects, a fresh pipe
    instance is created and the loop waits for the next connection.

    Liveness between requests is detected lazily: nothing polls an idle
    connection, so a game that vanishes without a call in flight isn't
    noticed until the next `send_request` fails. Acceptable for now --
    revisit only if that staleness becomes a real problem.
    """

    def __init__(self, expected_token: str, state: BridgeState) -> None:
        self.expected_token = expected_token
        self.state = state
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._pipe = None
        self._active_handle_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._read_buffer = b""
        self._torn_down: threading.Event | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Stop accepting/serving and block until the accept thread has
        actually exited (not just been signaled to).

        The accept loop spends most of its life inside a blocking
        `ConnectNamedPipe` call that `self._stop` alone can't interrupt --
        closing the handle it's blocked on from this thread forces that
        call to error out so the loop can observe `_stop` and exit.
        Joining afterwards matters just as much as the close: without it,
        a caller that immediately creates a new BridgeServer (any test
        that does, since PIPE_NAME is a fixed global name) can race a
        client connection against this instance's still-in-progress
        teardown and land on the dying pipe instead of the new one.

        A single close-then-join isn't quite enough, for two reasons
        confirmed empirically while chasing a real hang here: (1) plain
        `CloseHandle` does *not* reliably interrupt a pending
        `ConnectNamedPipe` on another thread the way it does a pending
        `ReadFile` -- that needs `CancelIoEx` first. (2) there's a narrow
        window, right as one connection's cleanup hands off to the next
        pipe instance, where `self._pipe` is briefly `None` -- a `stop()`
        landing exactly there would see nothing to cancel while the
        accept loop goes on to block on a *new* instance forever. So this
        polls cancel+close+join instead of doing it once.
        """
        self._stop.set()
        deadline = time.time() + 5.0
        while self._thread.is_alive() and time.time() < deadline:
            torn_down = self._torn_down
            if torn_down is not None:
                torn_down.set()
            with self._active_handle_lock:
                pipe = self._pipe
            if pipe is not None:
                _cancel_pending_io(pipe)
                try:
                    win32file.CloseHandle(pipe)
                except pywintypes.error:
                    pass
            self._thread.join(timeout=0.1)

    def _create_pipe_instance(self):
        """CreateNamedPipe, retrying briefly on ERROR_PIPE_BUSY (231).

        With `nMaxInstances=1`, Windows doesn't always free the previous
        instance's slot the instant its handle is closed -- there can be
        a short OS-internal delay (confirmed empirically: back-to-back
        create/close cycles hit this often enough to matter). Retrying
        beats letting `_run` die permanently on a transient race.
        """
        last_exc = None
        for _ in range(40):
            if self._stop.is_set():
                raise pywintypes.error(0, "CreateNamedPipe", "stopping")
            try:
                return win32pipe.CreateNamedPipe(
                    PIPE_NAME,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_BYTE
                    | win32pipe.PIPE_READMODE_BYTE
                    | win32pipe.PIPE_WAIT,
                    1,
                    _BUFFER_SIZE,
                    _BUFFER_SIZE,
                    0,
                    None,
                )
            except pywintypes.error as exc:
                last_exc = exc
                if exc.winerror != 231:  # ERROR_PIPE_BUSY
                    raise
                time.sleep(0.05)
        raise last_exc

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                pipe = self._create_pipe_instance()
            except pywintypes.error:
                continue
            with self._active_handle_lock:
                self._pipe = pipe
            self._read_buffer = b""
            self._torn_down = threading.Event()
            try:
                win32pipe.ConnectNamedPipe(pipe, None)
                if self._stop.is_set():
                    break
                self._do_handshake(pipe)
                self._wait_for_teardown()
            except Exception:
                pass
            finally:
                try:
                    win32file.CloseHandle(pipe)
                except pywintypes.error:
                    pass
                with self._active_handle_lock:
                    self._pipe = None
                self.state.mark_disconnected()

    def _wait_for_teardown(self) -> None:
        torn_down = self._torn_down
        while not self._stop.is_set() and not torn_down.is_set():
            torn_down.wait(timeout=0.5)

    def _do_handshake(self, pipe) -> None:
        line = self._read_line(pipe)
        if line is None:
            raise ConnectionError("client disconnected before handshake")

        msg = json.loads(line.decode("utf-8"))
        if msg.get("type") != "handshake":
            raise ConnectionError("expected handshake as first message")

        if msg.get("token") != self.expected_token:
            self._write_message(pipe, {"type": "handshake_ack", "ok": False, "error": "bad token"})
            raise ConnectionError("bad token")

        self.state.mark_connected(
            ue4ss_version=msg.get("ue4ss_version", "unknown"),
            engine_version=msg.get("engine_version", "unknown"),
        )
        self._write_message(pipe, {"type": "handshake_ack", "ok": True})

    def _read_line(self, pipe) -> bytes | None:
        while b"\n" not in self._read_buffer:
            _, data = win32file.ReadFile(pipe, _BUFFER_SIZE)
            if not data:
                return None
            self._read_buffer += data
        line, self._read_buffer = self._read_buffer.split(b"\n", 1)
        return line

    @staticmethod
    def _write_message(pipe, payload: dict) -> None:
        win32file.WriteFile(pipe, (json.dumps(payload) + "\n").encode("utf-8"))

    def _teardown_pipe(self, pipe) -> None:
        with self._active_handle_lock:
            if self._pipe is pipe:
                self._pipe = None
        try:
            win32file.CloseHandle(pipe)
        except pywintypes.error:
            pass
        self.state.mark_disconnected()
        torn_down = self._torn_down
        if torn_down is not None:
            torn_down.set()

    def _blocking_read_line(self, pipe, result_box: dict) -> None:
        try:
            result_box["line"] = self._read_line(pipe)
        except (pywintypes.error, OSError, UnicodeDecodeError) as exc:
            result_box["error"] = exc

    def send_request(self, op: str, params: dict, timeout: float = _REQUEST_TIMEOUT_S) -> dict:
        """Send a request to the connected bridge mod and block for its response.

        Safe to call whether or not a game is connected -- returns a clean
        `{"ok": False, "error": "not connected"}` rather than raising,
        matching every other bridge tool's "report not connected" contract.
        Serialized via `_io_lock`: only one request is ever in flight on
        the pipe at a time, matching the bridge mod's own one-at-a-time
        read/dispatch/write loop.
        """
        with self._io_lock:
            pipe = self._pipe
            if pipe is None or not self.state.snapshot()["connected"]:
                return {"ok": False, "error": "not connected"}

            req_id = uuid.uuid4().hex
            payload = {"type": "request", "id": req_id, "op": op, "params": params}
            try:
                win32file.WriteFile(pipe, (json.dumps(payload) + "\n").encode("utf-8"))
            except pywintypes.error as exc:
                self._teardown_pipe(pipe)
                return {"ok": False, "error": f"write failed: {exc}"}

            # win32's synchronous ReadFile has no built-in timeout, so the
            # actual wait happens on a helper thread we can give up on. If
            # it doesn't return in time we tear the connection down (which
            # closes the handle and unblocks/kills that helper) rather than
            # leaving it to race a future request on the same handle.
            result_box: dict = {}
            reader = threading.Thread(target=self._blocking_read_line, args=(pipe, result_box), daemon=True)
            reader.start()
            reader.join(timeout=timeout)

            if reader.is_alive():
                _cancel_pending_io(pipe)
                reader.join(timeout=1.0)
                self._teardown_pipe(pipe)
                return {"ok": False, "error": "timed out waiting for bridge response"}

            if "error" in result_box:
                self._teardown_pipe(pipe)
                return {"ok": False, "error": f"read failed: {result_box['error']}"}

            line = result_box.get("line")
            if line is None:
                self._teardown_pipe(pipe)
                return {"ok": False, "error": "bridge disconnected"}

            try:
                msg = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                return {"ok": False, "error": f"malformed response: {exc}"}

            if msg.get("id") != req_id:
                return {"ok": False, "error": "response id mismatch (protocol desync)"}
            return msg
