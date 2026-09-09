"""Named-pipe client connecting out to a bridge mod's own hosted pipe.

Transport flip (see dev-notes/progress.md): the bridge mod hosts the
named pipe now (via a small companion native Lua module, since Lua's
stdlib can't create a pipe server) and this server connects out to it,
rather than the other way around. This eliminates the mod-side poll-
and-back-off reconnect loop entirely -- connecting is now a synchronous,
on-demand action (`wait_running_bridge`/`list_running_bridge` in
server.py drive it), not an always-running background accept thread.
Each known install gets its own uniquely-named pipe, so multiple games
(and multiple agents attached to the same game, since the bridge's
native pipe allows more than one simultaneous instance) no longer
collide on one global pipe name the way they did before.

Threading note, still true post-flip: a synchronous (non-overlapped)
named pipe handle only supports one in-flight I/O operation at a time --
issuing a blocking ReadFile on one thread while another thread WriteFiles
the same handle deadlocks (confirmed empirically while building M4's
request/response protocol, not just docs). `send_request` owns the
handle exclusively (via `_io_lock`) for the full duration of one
write-then-read exchange.

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

_BUFFER_SIZE = 65536
_REQUEST_TIMEOUT_S = 10.0
_CONNECT_POLL_INTERVAL_S = 0.2

_kernel32 = ctypes.windll.kernel32
_kernel32.CancelIoEx.restype = ctypes.c_int
_kernel32.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


def _cancel_pending_io(pipe) -> None:
    try:
        _kernel32.CancelIoEx(int(pipe), None)
    except (OSError, ValueError):
        pass


def probe_pipe_exists(pipe_name: str) -> bool:
    """Cheap, non-connecting check for whether a bridge's pipe currently
    has a listener (a game running with the bridge mod loaded) --
    `list_running_bridge` uses this rather than fully connecting/
    handshaking just to report status, since that would needlessly
    consume one of the bridge's limited connection slots.
    """
    try:
        win32pipe.WaitNamedPipe(pipe_name, 0)
        return True
    except pywintypes.error as exc:
        # ERROR_FILE_NOT_FOUND (2): no server present at all.
        # ERROR_SEM_TIMEOUT (121, from a 0ms wait): a server exists but
        # every instance is currently busy -- still counts as "running".
        return exc.winerror == 121


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


class BridgeClient:
    """Connects out to one bridge mod's hosted pipe and exchanges
    request/response messages with it.

    Liveness between requests is detected lazily: nothing polls an idle
    connection, so a game that vanishes without a call in flight isn't
    noticed until the next `send_request` fails. Acceptable for now --
    revisit only if that staleness becomes a real problem.
    """

    def __init__(self, pipe_name: str, expected_token: str, state: BridgeState) -> None:
        self.pipe_name = pipe_name
        self.expected_token = expected_token
        self.state = state
        self._pipe = None
        self._io_lock = threading.Lock()
        self._read_buffer = b""

    def is_connected(self) -> bool:
        return self._pipe is not None and self.state.snapshot()["connected"]

    def connect(self, timeout: float) -> tuple[bool, str | None]:
        """Try to connect and handshake within `timeout` seconds.

        Retries on the client-side CreateFile failing (no server up yet,
        or every instance currently busy) rather than the old mod-side
        poll-and-back-off -- this is now the one place a caller actually
        waits, and only when explicitly asked to (`wait_running_bridge`).
        """
        deadline = time.time() + timeout
        last_exc: pywintypes.error | None = None
        pipe = None
        while time.time() < deadline:
            try:
                pipe = win32file.CreateFile(
                    self.pipe_name,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None,
                )
                break
            except pywintypes.error as exc:
                last_exc = exc
                time.sleep(_CONNECT_POLL_INTERVAL_S)
        else:
            return False, f"could not connect within {timeout}s ({last_exc})"

        self._read_buffer = b""
        try:
            handshake = {"type": "handshake", "token": self.expected_token}
            win32file.WriteFile(pipe, (json.dumps(handshake) + "\n").encode("utf-8"))
            line = self._read_line(pipe)
            if line is None:
                raise ConnectionError("no handshake response")
            ack = json.loads(line.decode("utf-8"))
            if not ack.get("ok"):
                raise ConnectionError(ack.get("error", "handshake rejected"))
        except (pywintypes.error, ConnectionError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            try:
                win32file.CloseHandle(pipe)
            except pywintypes.error:
                pass
            return False, str(exc)

        self._pipe = pipe
        self.state.mark_connected(
            ue4ss_version=ack.get("ue4ss_version", "unknown"),
            engine_version=ack.get("engine_version", "unknown"),
        )
        return True, None

    def disconnect(self) -> None:
        pipe = self._pipe
        self._pipe = None
        if pipe is not None:
            _cancel_pending_io(pipe)
            try:
                win32file.CloseHandle(pipe)
            except pywintypes.error:
                pass
        self.state.mark_disconnected()

    def _teardown_pipe(self, pipe) -> None:
        if self._pipe is pipe:
            self._pipe = None
        try:
            win32file.CloseHandle(pipe)
        except pywintypes.error:
            pass
        self.state.mark_disconnected()

    def _read_line(self, pipe) -> bytes | None:
        while b"\n" not in self._read_buffer:
            _, data = win32file.ReadFile(pipe, _BUFFER_SIZE)
            if not data:
                return None
            self._read_buffer += data
        line, self._read_buffer = self._read_buffer.split(b"\n", 1)
        return line

    def _blocking_read_line(self, pipe, result_box: dict) -> None:
        try:
            result_box["line"] = self._read_line(pipe)
        except (pywintypes.error, OSError, UnicodeDecodeError) as exc:
            result_box["error"] = exc

    def send_request(self, op: str, params: dict, timeout: float = _REQUEST_TIMEOUT_S) -> dict:
        """Send a request to the connected bridge mod and block for its response.

        Safe to call whether or not currently connected -- returns a
        clean `{"ok": False, "error": "not connected"}` rather than
        raising, matching every other bridge tool's "report not
        connected" contract. Serialized via `_io_lock`: only one request
        is ever in flight on the pipe at a time, matching the bridge
        mod's own one-at-a-time read/dispatch/write loop for this client.
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
