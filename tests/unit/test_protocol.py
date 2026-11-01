import socket
import struct
import threading

import pytest

from claudebridge.bridge import protocol


def pair():
    return socket.socketpair()


def test_framing_roundtrip():
    a, b = pair()
    protocol.send_msg(a, {"id": 1, "op": "ping", "text": "äöü ∑"})
    assert protocol.recv_msg(b) == {"id": 1, "op": "ping", "text": "äöü ∑"}


def test_clean_eof_returns_none():
    a, b = pair()
    a.close()
    assert protocol.recv_msg(b) is None


def test_oversized_frame_rejected():
    a, b = pair()
    a.sendall(struct.pack(">I", protocol.MAX_FRAME + 1))
    with pytest.raises(protocol.ProtocolError):
        protocol.recv_msg(b)


def _handshake(server_token, client_token):
    a, b = pair()
    result = {}

    def serve():
        result["server"] = protocol.server_handshake(a, server_token, "0.1.0", 42)

    t = threading.Thread(target=serve)
    t.start()
    try:
        result["hello"] = protocol.client_handshake(b, client_token)
    except protocol.ProtocolError as e:
        result["error"] = e
    t.join(5)
    return result


def test_handshake_success_is_mutual():
    token = protocol.new_token()
    r = _handshake(token, token)
    assert r["server"] is True
    assert r["hello"]["addon_version"] == "0.1.0"
    assert r["hello"]["pid"] == 42


def test_handshake_wrong_token_rejected():
    r = _handshake(protocol.new_token(), protocol.new_token())
    assert r["server"] is False
    assert r["error"].code == protocol.AUTH_FAILED


def test_server_must_prove_token():
    """A fake addon that doesn't know the token can't impersonate FreeCAD."""
    token = protocol.new_token()
    a, b = pair()

    def fake_addon():
        protocol.send_msg(a, {"type": "hello", "proto": protocol.PROTOCOL_VERSION, "nonce": "00", "pid": 1})
        protocol.recv_msg(a)
        protocol.send_msg(a, {"type": "welcome", "ok": True, "mac": "bogus"})

    t = threading.Thread(target=fake_addon)
    t.start()
    with pytest.raises(protocol.ProtocolError) as e:
        protocol.client_handshake(b, token)
    t.join(5)
    assert e.value.code == protocol.AUTH_FAILED


def test_major_version_mismatch_client_side():
    a, b = pair()
    protocol.send_msg(a, {"type": "hello", "proto": "2.0.0", "nonce": "00", "pid": 1})
    with pytest.raises(protocol.ProtocolError) as e:
        protocol.client_handshake(b, protocol.new_token())
    assert e.value.code == protocol.VERSION_MISMATCH


def test_major_version_mismatch_server_side():
    a, b = pair()
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("ok", protocol.server_handshake(a, b"x" * 32, "0.1.0", 1)))
    t.start()
    protocol.recv_msg(b)  # hello
    protocol.send_msg(b, {"type": "auth", "proto": "2.0.0", "nonce": "00", "mac": ""})
    welcome = protocol.recv_msg(b)
    t.join(5)
    assert result["ok"] is False
    assert welcome["error"]["code"] == protocol.VERSION_MISMATCH


def test_minor_versions_compatible():
    assert protocol.compatible("1.0.0", "1.4.2")
    assert not protocol.compatible("1.0.0", "2.0.0")


def test_mac_is_role_bound():
    token = protocol.new_token()
    assert protocol.mac(token, "client", "n") != protocol.mac(token, "server", "n")


def test_handshake_rejects_large_frames_before_auth():
    import socket
    import struct

    import pytest

    from freecad_bridge import protocol

    a, b = socket.socketpair()
    with a, b:
        a.sendall(struct.pack(">I", protocol.MAX_HANDSHAKE_FRAME + 1))
        with pytest.raises(protocol.ProtocolError):
            protocol.recv_msg(b, protocol.MAX_HANDSHAKE_FRAME)
