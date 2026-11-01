"""Client against a fake addon that speaks the real protocol."""

import json
import os
import socket
import threading
import time

import pytest

from freecad_bridge import __version__, client, platforms, protocol


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    d = tmp_path / "b"
    monkeypatch.setenv("FREECAD_BRIDGE_DIR", str(d))
    (d / "instances").mkdir(parents=True)
    platforms.write_token(protocol.new_token())
    return d


def fake_addon(bridge_dir, pid, handler, addon_version=__version__, started=None):
    path = f"/tmp/fcb-test-{pid}-{os.urandom(3).hex()}.sock"  # tmp_path is too long for AF_UNIX on macOS
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(4)
    info = {"pid": pid, "transport": "unix", "address": path, "started": started or time.time()}
    (bridge_dir / "instances" / f"{pid}.json").write_text(json.dumps(info))

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                token = platforms.read_token()
                if not protocol.server_handshake(conn, token, addon_version, pid):
                    continue
                msg = protocol.recv_msg(conn)
                if msg:
                    protocol.send_msg(conn, {"id": msg["id"], **handler(msg)})

    threading.Thread(target=loop, daemon=True).start()
    return srv


def test_no_instance(bridge):
    with pytest.raises(client.BridgeError) as e:
        client.call("ping")
    assert e.value.code == client.NOT_RUNNING


def test_dead_instance_file_is_cleaned(bridge):
    (bridge / "instances" / "999999.json").write_text(json.dumps(
        {"pid": 999999, "transport": "unix", "address": "/nope", "started": 1}))
    assert client.list_instances() == []
    assert not (bridge / "instances" / "999999.json").exists()


def test_call_roundtrip_and_newest_instance(bridge):
    me = os.getpid()
    fake_addon(bridge, me, lambda m: {"ok": True, "result": {"op": m["op"], "args": m["args"]}})
    res = client.call("get_tree", {"depth": 1})
    assert res.result == {"op": "get_tree", "args": {"depth": 1}}
    assert res.instance.pid == me
    assert res.warnings == []


def test_error_response_raised(bridge):
    fake_addon(bridge, os.getpid(), lambda m: {"ok": False, "error": protocol.error(
        protocol.PY_EXCEPTION, "ZeroDivisionError", "Traceback…")})
    with pytest.raises(client.BridgeError) as e:
        client.call("run_python", {"code": "1/0"})
    assert e.value.code == protocol.PY_EXCEPTION
    assert e.value.traceback == "Traceback…"


def test_version_warning(bridge):
    fake_addon(bridge, os.getpid(), lambda m: {"ok": True, "result": {}}, addon_version="0.0.9")
    assert "0.0.9" in client.call("ping").warnings[0]


def test_wrong_token_fails(bridge):
    fake_addon(bridge, os.getpid(), lambda m: {"ok": True, "result": {}})
    original = platforms.read_token()
    try:
        # client reads a different token than the addon
        real_read = platforms.read_token
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            return protocol.new_token() if calls["n"] == 1 else real_read()

        platforms.read_token = flaky
        with pytest.raises(client.BridgeError) as e:
            client.call("ping")
        assert e.value.code == protocol.AUTH_FAILED
    finally:
        platforms.read_token = real_read
        assert platforms.read_token() == original
