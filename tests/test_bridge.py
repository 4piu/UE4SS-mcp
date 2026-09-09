"""Tests for the client-role BridgeClient (see bridge.py's module
docstring for the transport flip: the bridge mod hosts the pipe now,
this side connects out to it).

Each test gets its own uniquely-named pipe via FakeBridgeMod (a minimal
named-pipe SERVER stand-in playing "the game") rather than one hardcoded
name -- this also fixes the pre-existing test-isolation gap where every
test shared the same production pipe name and could collide with a real
running server on the same machine.
"""

import json
import threading
import time
import uuid

import pywintypes
import win32file
import win32pipe

from ue4ss_mcp_server.bridge import BridgeClient, BridgeState, probe_pipe_exists

_BUFFER_SIZE = 65536


def _unique_pipe_name() -> str:
    return r"\\.\pipe\ue4ss-mcp-test-" + uuid.uuid4().hex[:16]


class FakeBridgeMod:
    """Minimal named-pipe server standing in for the bridge mod (which
    now hosts the pipe for real) -- lets tests drive the "game side" of
    the handshake/request-response protocol explicitly.
    """

    def __init__(self, pipe_name: str):
        self.pipe_name = pipe_name
        self.pipe = win32pipe.CreateNamedPipe(
            pipe_name,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            1,
            _BUFFER_SIZE,
            _BUFFER_SIZE,
            0,
            None,
        )
        self._read_buffer = b""

    def accept(self, timeout_s: float = 5.0) -> None:
        """Blocks (off-thread, with a timeout) until a client connects."""
        result: dict = {}

        def _connect():
            try:
                win32pipe.ConnectNamedPipe(self.pipe, None)
                result["ok"] = True
            except pywintypes.error as exc:
                # A client can legitimately connect before this thread
                # even calls ConnectNamedPipe -- Windows reports that as
                # ERROR_PIPE_CONNECTED (535), which is success, not an error.
                if exc.winerror == 535:
                    result["ok"] = True
                else:
                    result["error"] = exc

        t = threading.Thread(target=_connect, daemon=True)
        t.start()
        t.join(timeout=timeout_s)
        if t.is_alive():
            raise TimeoutError("no client connected in time")
        if "error" in result:
            raise result["error"]

    def read_line(self) -> str:
        while b"\n" not in self._read_buffer:
            _, data = win32file.ReadFile(self.pipe, _BUFFER_SIZE)
            self._read_buffer += data
        line, self._read_buffer = self._read_buffer.split(b"\n", 1)
        return line.decode("utf-8")

    def write_line(self, s: str) -> None:
        win32file.WriteFile(self.pipe, (s + "\n").encode("utf-8"))

    def close(self) -> None:
        try:
            win32file.CloseHandle(self.pipe)
        except pywintypes.error:
            pass


def _accept_and_handshake(fake_mod: FakeBridgeMod, ok: bool = True, error: str | None = None) -> dict:
    """Runs the "game side" of one handshake: accept, read the
    handshake request, send back an ack. Returns the decoded handshake
    request the client sent.
    """
    fake_mod.accept()
    request = json.loads(fake_mod.read_line())
    assert request["type"] == "handshake"
    if ok:
        fake_mod.write_line(json.dumps({"type": "handshake_ack", "ok": True, "ue4ss_version": "3.0.1", "engine_version": "4.27"}))
    else:
        fake_mod.write_line(json.dumps({"type": "handshake_ack", "ok": False, "error": error or "bad token"}))
    return request


def test_bridge_state_defaults_to_disconnected():
    state = BridgeState()
    snap = state.snapshot()
    assert snap == {"connected": False, "ue4ss_version": None, "engine_version": None}


def test_bridge_state_mark_connected_and_disconnected():
    state = BridgeState()
    state.mark_connected(ue4ss_version="3.0.1", engine_version="4.27")
    assert state.snapshot() == {
        "connected": True,
        "ue4ss_version": "3.0.1",
        "engine_version": "4.27",
    }

    state.mark_disconnected()
    assert state.snapshot()["connected"] is False


def test_probe_pipe_exists_false_when_nothing_listening():
    assert probe_pipe_exists(_unique_pipe_name()) is False


def test_probe_pipe_exists_true_once_a_server_is_up():
    pipe_name = _unique_pipe_name()
    fake_mod = FakeBridgeMod(pipe_name)
    try:
        assert probe_pipe_exists(pipe_name) is True
    finally:
        fake_mod.close()


def test_connect_succeeds_and_updates_state():
    pipe_name = _unique_pipe_name()
    fake_mod = FakeBridgeMod(pipe_name)
    state = BridgeState()
    client = BridgeClient(pipe_name, expected_token="correct-token", state=state)
    try:
        t = threading.Thread(target=_accept_and_handshake, args=(fake_mod,))
        t.start()
        ok, err = client.connect(timeout=5.0)
        t.join(timeout=5.0)

        assert ok is True
        assert err is None
        assert client.is_connected() is True
        assert state.snapshot() == {
            "connected": True,
            "ue4ss_version": "3.0.1",
            "engine_version": "4.27",
        }
    finally:
        client.disconnect()
        fake_mod.close()


def test_connect_reports_the_bridges_rejection():
    pipe_name = _unique_pipe_name()
    fake_mod = FakeBridgeMod(pipe_name)
    state = BridgeState()
    client = BridgeClient(pipe_name, expected_token="wrong-token", state=state)
    try:
        t = threading.Thread(target=_accept_and_handshake, args=(fake_mod,), kwargs={"ok": False, "error": "bad token"})
        t.start()
        ok, err = client.connect(timeout=5.0)
        t.join(timeout=5.0)

        assert ok is False
        assert err == "bad token"
        assert client.is_connected() is False
        assert state.snapshot()["connected"] is False
    finally:
        client.disconnect()
        fake_mod.close()


def test_connect_times_out_when_nothing_is_listening():
    state = BridgeState()
    client = BridgeClient(_unique_pipe_name(), expected_token="correct-token", state=state)
    ok, err = client.connect(timeout=0.5)
    assert ok is False
    assert "could not connect" in err


def test_send_request_reports_not_connected_before_connect():
    state = BridgeState()
    client = BridgeClient(_unique_pipe_name(), expected_token="correct-token", state=state)
    assert client.send_request("find_object", {"class": "Actor"}, timeout=1.0) == {
        "ok": False,
        "error": "not connected",
    }


def _connected_client(fake_mod: FakeBridgeMod, pipe_name: str) -> tuple[BridgeClient, BridgeState]:
    state = BridgeState()
    client = BridgeClient(pipe_name, expected_token="correct-token", state=state)
    t = threading.Thread(target=_accept_and_handshake, args=(fake_mod,))
    t.start()
    ok, err = client.connect(timeout=5.0)
    t.join(timeout=5.0)
    assert ok, err
    return client, state


def test_send_request_round_trip():
    pipe_name = _unique_pipe_name()
    fake_mod = FakeBridgeMod(pipe_name)
    try:
        client, state = _connected_client(fake_mod, pipe_name)

        result_holder = {}

        def call_from_client():
            result_holder["response"] = client.send_request("find_object", {"class": "Actor"}, timeout=5.0)

        t = threading.Thread(target=call_from_client)
        t.start()

        # Act as the bridge mod: read the request, reply with a canned response.
        request = json.loads(fake_mod.read_line())
        assert request["type"] == "request"
        assert request["op"] == "find_object"
        assert request["params"] == {"class": "Actor"}

        fake_mod.write_line(json.dumps({"type": "response", "id": request["id"], "ok": True, "result": {"items": []}}))

        t.join(timeout=5.0)
        assert result_holder["response"] == {
            "type": "response",
            "id": request["id"],
            "ok": True,
            "result": {"items": []},
        }
    finally:
        client.disconnect()
        fake_mod.close()


def test_send_request_times_out_when_bridge_never_replies():
    pipe_name = _unique_pipe_name()
    fake_mod = FakeBridgeMod(pipe_name)
    try:
        client, state = _connected_client(fake_mod, pipe_name)
        response = client.send_request("find_object", {"class": "Actor"}, timeout=0.5)
        assert response == {"ok": False, "error": "timed out waiting for bridge response"}
    finally:
        client.disconnect()
        fake_mod.close()


def test_send_request_fails_when_bridge_disconnects_mid_request():
    pipe_name = _unique_pipe_name()
    fake_mod = FakeBridgeMod(pipe_name)
    client, state = _connected_client(fake_mod, pipe_name)
    try:
        result_holder = {}

        def call_from_client():
            result_holder["response"] = client.send_request("find_object", {"class": "Actor"}, timeout=5.0)

        t = threading.Thread(target=call_from_client)
        t.start()

        fake_mod.read_line()  # the request, never answered
        fake_mod.close()  # simulate the game/mod disconnecting

        t.join(timeout=5.0)
        assert result_holder["response"]["ok"] is False
        assert state.snapshot()["connected"] is False
    finally:
        client.disconnect()


def test_send_request_works_again_after_a_reconnect():
    # Liveness between requests is detected lazily (see BridgeClient's
    # docstring) -- a passive bridge-side close isn't noticed until the
    # next send_request fails; reconnecting after that means calling
    # connect() again against a fresh listener.
    pipe_name = _unique_pipe_name()
    fake_mod = FakeBridgeMod(pipe_name)
    client, state = _connected_client(fake_mod, pipe_name)
    fake_mod.close()

    failed = client.send_request("find_object", {"class": "Actor"}, timeout=2.0)
    assert failed["ok"] is False

    fake_mod2 = FakeBridgeMod(pipe_name)
    try:
        t = threading.Thread(target=_accept_and_handshake, args=(fake_mod2,))
        t.start()
        ok, err = client.connect(timeout=5.0)
        t.join(timeout=5.0)
        assert ok, err

        result_holder = {}

        def call_from_client():
            result_holder["response"] = client.send_request("find_object", {"class": "Actor"}, timeout=5.0)

        t2 = threading.Thread(target=call_from_client)
        t2.start()

        request = json.loads(fake_mod2.read_line())
        fake_mod2.write_line(json.dumps({"type": "response", "id": request["id"], "ok": True, "result": {}}))

        t2.join(timeout=5.0)
        assert result_holder["response"]["ok"] is True
    finally:
        client.disconnect()
        fake_mod2.close()
