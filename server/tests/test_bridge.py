import json
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
