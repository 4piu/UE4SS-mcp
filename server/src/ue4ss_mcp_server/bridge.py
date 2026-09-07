"""Named-pipe server accepting bridge-mod connections.

Blocking win32 pipe I/O lives entirely in a dedicated background thread
so it never touches the MCP server's asyncio event loop. See
dev-notes/spec.md §8 for why a named pipe rather than TCP/LuaSocket, and
dev-notes/progress.md for the empirical test that verified stock Lua's
io.open() can act as a client against it.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

import pywintypes
import win32file
import win32pipe

PIPE_NAME = r"\\.\pipe\ue4ss-mcp"
_BUFFER_SIZE = 65536


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
    """

    def __init__(self, expected_token: str, state: BridgeState) -> None:
        self.expected_token = expected_token
        self.state = state
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            pipe = win32pipe.CreateNamedPipe(
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
            try:
                win32pipe.ConnectNamedPipe(pipe, None)
                self._serve_connection(pipe)
            except pywintypes.error:
                pass
            finally:
                try:
                    win32file.CloseHandle(pipe)
                except pywintypes.error:
                    pass
                self.state.mark_disconnected()

    def _serve_connection(self, pipe) -> None:
        buffer = b""
        while not self._stop.is_set():
            _, data = win32file.ReadFile(pipe, _BUFFER_SIZE)
            if not data:
                break
            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                self._handle_line(pipe, line)

    def _handle_line(self, pipe, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        if msg.get("type") != "handshake":
            return

        if msg.get("token") != self.expected_token:
            self._reply(pipe, {"type": "handshake_ack", "ok": False, "error": "bad token"})
            return

        self.state.mark_connected(
            ue4ss_version=msg.get("ue4ss_version", "unknown"),
            engine_version=msg.get("engine_version", "unknown"),
        )
        self._reply(pipe, {"type": "handshake_ack", "ok": True})

    @staticmethod
    def _reply(pipe, payload: dict) -> None:
        win32file.WriteFile(pipe, (json.dumps(payload) + "\n").encode("utf-8"))
