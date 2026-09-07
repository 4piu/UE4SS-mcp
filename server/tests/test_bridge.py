import json
import threading
import time

import pywintypes
import win32file

from ue4ss_mcp_server.bridge import PIPE_NAME, BridgeServer, BridgeState


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


def _connect_raw_pipe_client(timeout_s: float = 5.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            return win32file.CreateFile(
                PIPE_NAME,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
        except pywintypes.error:
            time.sleep(0.05)
    raise TimeoutError("bridge pipe never became available")


def test_bridge_server_accepts_handshake_and_updates_state():
    state = BridgeState()
    server = BridgeServer(expected_token="correct-token", state=state)
    server.start()
    try:
        client = _connect_raw_pipe_client()
        try:
            msg = json.dumps(
                {
                    "type": "handshake",
                    "token": "correct-token",
                    "ue4ss_version": "3.0.1",
                    "engine_version": "4.27",
                }
            )
            win32file.WriteFile(client, (msg + "\n").encode("utf-8"))
            _, response = win32file.ReadFile(client, 4096)
            ack = json.loads(response.decode("utf-8").strip())

            assert ack == {"type": "handshake_ack", "ok": True}
            assert state.snapshot() == {
                "connected": True,
                "ue4ss_version": "3.0.1",
                "engine_version": "4.27",
            }
        finally:
            win32file.CloseHandle(client)
    finally:
        server.stop()


def test_bridge_server_rejects_bad_token():
    state = BridgeState()
    server = BridgeServer(expected_token="correct-token", state=state)
    server.start()
    try:
        client = _connect_raw_pipe_client()
        try:
            msg = json.dumps({"type": "handshake", "token": "wrong-token"})
            win32file.WriteFile(client, (msg + "\n").encode("utf-8"))
            _, response = win32file.ReadFile(client, 4096)
            ack = json.loads(response.decode("utf-8").strip())

            assert ack == {"type": "handshake_ack", "ok": False, "error": "bad token"}
            assert state.snapshot()["connected"] is False
        finally:
            win32file.CloseHandle(client)
    finally:
        server.stop()


def _handshake(client, token: str = "correct-token") -> None:
    msg = json.dumps({"type": "handshake", "token": token, "ue4ss_version": "3.0.1", "engine_version": "4.27"})
    win32file.WriteFile(client, (msg + "\n").encode("utf-8"))
    win32file.ReadFile(client, 4096)  # discard handshake_ack


def test_send_request_reports_not_connected_when_no_client():
    state = BridgeState()
    server = BridgeServer(expected_token="correct-token", state=state)
    server.start()
    try:
        assert server.send_request("find_object", {"class": "Actor"}, timeout=1.0) == {
            "ok": False,
            "error": "not connected",
        }
    finally:
        server.stop()


def test_send_request_round_trip():
    state = BridgeState()
    server = BridgeServer(expected_token="correct-token", state=state)
    server.start()
    try:
        client = _connect_raw_pipe_client()
        try:
            _handshake(client)

            result_holder = {}

            def call_from_server():
                result_holder["response"] = server.send_request(
                    "find_object", {"class": "Actor"}, timeout=5.0
                )

            t = threading.Thread(target=call_from_server)
            t.start()

            # Act as the Lua bridge: read the request, reply with a canned response.
            _, data = win32file.ReadFile(client, 4096)
            request = json.loads(data.decode("utf-8").strip())
            assert request["type"] == "request"
            assert request["op"] == "find_object"
            assert request["params"] == {"class": "Actor"}

            reply = json.dumps(
                {"type": "response", "id": request["id"], "ok": True, "result": {"items": []}}
            )
            win32file.WriteFile(client, (reply + "\n").encode("utf-8"))

            t.join(timeout=5.0)
            assert result_holder["response"] == {
                "type": "response",
                "id": request["id"],
                "ok": True,
                "result": {"items": []},
            }
        finally:
            win32file.CloseHandle(client)
    finally:
        server.stop()


def test_send_request_times_out_when_bridge_never_replies():
    state = BridgeState()
    server = BridgeServer(expected_token="correct-token", state=state)
    server.start()
    try:
        client = _connect_raw_pipe_client()
        try:
            _handshake(client)
            response = server.send_request("find_object", {"class": "Actor"}, timeout=0.5)
            assert response == {"ok": False, "error": "timed out waiting for bridge response"}
        finally:
            win32file.CloseHandle(client)
    finally:
        server.stop()


def test_send_request_fails_when_client_disconnects_mid_request():
    state = BridgeState()
    server = BridgeServer(expected_token="correct-token", state=state)
    server.start()
    try:
        client = _connect_raw_pipe_client()
        _handshake(client)

        result_holder = {}

        def call_from_server():
            result_holder["response"] = server.send_request("find_object", {"class": "Actor"}, timeout=5.0)

        t = threading.Thread(target=call_from_server)
        t.start()

        win32file.ReadFile(client, 4096)  # the request line, never answered
        win32file.CloseHandle(client)  # simulate the game closing the pipe

        t.join(timeout=5.0)
        assert result_holder["response"]["ok"] is False
        assert state.snapshot()["connected"] is False
    finally:
        server.stop()


def test_send_request_works_again_after_a_reconnect():
    # Liveness between requests is detected lazily (see BridgeServer's
    # docstring) -- a passive client-side close isn't noticed until the
    # next send_request fails, and only *that* failure tears the
    # connection down and puts the accept loop back into listening mode.
    state = BridgeState()
    server = BridgeServer(expected_token="correct-token", state=state)
    server.start()
    try:
        client = _connect_raw_pipe_client()
        _handshake(client)
        win32file.CloseHandle(client)

        failed = server.send_request("find_object", {"class": "Actor"}, timeout=2.0)
        assert failed["ok"] is False

        client2 = _connect_raw_pipe_client()
        try:
            _handshake(client2)

            result_holder = {}

            def call_from_server():
                result_holder["response"] = server.send_request("find_object", {"class": "Actor"}, timeout=5.0)

            t = threading.Thread(target=call_from_server)
            t.start()

            _, data = win32file.ReadFile(client2, 4096)
            request = json.loads(data.decode("utf-8").strip())
            reply = json.dumps({"type": "response", "id": request["id"], "ok": True, "result": {}})
            win32file.WriteFile(client2, (reply + "\n").encode("utf-8"))

            t.join(timeout=5.0)
            assert result_holder["response"]["ok"] is True
        finally:
            win32file.CloseHandle(client2)
    finally:
        server.stop()
