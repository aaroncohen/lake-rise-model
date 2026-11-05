"""The stdlib websocket client for HA long-term statistics, driven against a fake server over a
real socket pair (framing, masking, fragmentation, ping/pong, auth and error paths)."""

import json
import socket
import struct
import threading
from datetime import datetime, timedelta, timezone

import pytest

from lake_rise.sources import ha_statistics as HS


def _server_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    n = len(payload)
    b1 = (0x80 if fin else 0) | opcode
    if n < 126:
        head = struct.pack(">BB", b1, n)
    elif n < 1 << 16:
        head = struct.pack(">BBH", b1, 126, n)
    else:
        head = struct.pack(">BBQ", b1, 127, n)
    return head + payload


def _read_client_frame(sock: socket.socket) -> tuple[int, bytes]:
    def read(n):
        buf = b""
        while len(buf) < n:
            buf += sock.recv(n - len(buf))
        return buf
    b1, b2 = read(2)
    assert b2 & 0x80, "client frames must be masked"
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", read(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", read(8))[0]
    mask = read(4)
    data = bytes(b ^ mask[i % 4] for i, b in enumerate(read(n)))
    return b1 & 0x0F, data


def _fake_ha(sock: socket.socket, script):
    """Serve the HTTP upgrade, then run ``script(send, recv)`` on the frames."""
    req = b""
    while b"\r\n\r\n" not in req:
        req += sock.recv(4096)
    assert b"GET /api/websocket HTTP/1.1" in req
    sock.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                 b"Connection: Upgrade\r\n\r\n")

    def send(obj, *, fragmented=False):
        data = json.dumps(obj).encode()
        if fragmented:
            mid = len(data) // 2
            sock.sendall(_server_frame(0x1, data[:mid], fin=False) + _server_frame(0x0, data[mid:]))
        else:
            sock.sendall(_server_frame(0x1, data))

    def recv():
        while True:
            op, data = _read_client_frame(sock)
            if op == 0x1:
                return json.loads(data)
            if op == 0x8:
                return None

    script(sock, send, recv)


def _run(script, command):
    client, server = socket.socketpair()
    t = threading.Thread(target=_fake_ha, args=(server, script), daemon=True)
    t.start()
    ws = HS._WebSocket(client)
    ws.handshake("test:8123", "/api/websocket")
    try:
        return HS.request(ws, "tok", command)
    finally:
        ws.close()
        t.join(timeout=5)
        server.close()


def test_request_authenticates_and_returns_a_large_fragmented_result():
    rows = [{"start": 1_793_000_000_000 + i * 3_600_000, "change": 0.004} for i in range(3000)]

    def script(sock, send, recv):
        send({"type": "auth_required"})
        assert recv() == {"type": "auth", "access_token": "tok"}
        send({"type": "auth_ok"})
        cmd = recv()
        assert cmd["type"] == "recorder/statistics_during_period" and cmd["id"] == 1
        sock.sendall(_server_frame(0x9, b"hb"))                    # server ping mid-session
        op, data = _read_client_frame(sock)
        assert (op, data) == (0xA, b"hb")                          # answered with a pong
        send({"id": 1, "type": "result", "success": True, "result": {"s": rows}},
             fragmented=True)                                       # >64 KiB, two frames
        recv()                                                      # client close

    result = _run(script, {"type": "recorder/statistics_during_period"})
    assert result["s"] == rows


def test_request_raises_on_failed_auth():
    def script(sock, send, recv):
        send({"type": "auth_required"})
        recv()
        send({"type": "auth_invalid", "message": "bad token"})
        recv()

    with pytest.raises(HS.StatisticsError, match="authentication"):
        _run(script, {"type": "x"})


def test_request_raises_on_command_error():
    def script(sock, send, recv):
        send({"type": "auth_required"})
        recv()
        send({"type": "auth_ok"})
        recv()
        send({"id": 1, "type": "result", "success": False, "error": {"code": "unknown_command"}})
        recv()

    with pytest.raises(HS.StatisticsError, match="unknown_command"):
        _run(script, {"type": "x"})


def test_handshake_refusal_is_an_error():
    client, server = socket.socketpair()
    server.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
    with pytest.raises(HS.StatisticsError, match="403"):
        HS._WebSocket(client).handshake("test", "/api/websocket")
    client.close()
    server.close()


def test_hourly_change_lays_rows_on_the_grid_missing_stays_none():
    start = datetime(2026, 9, 5, 0, tzinfo=timezone.utc)
    rows = [
        {"start": start.timestamp() * 1000, "change": 0.591},                     # epoch ms
        {"start": (start + timedelta(hours=1)).isoformat(), "change": 0.318},     # ISO form
        {"start": (start + timedelta(hours=3)).timestamp() * 1000, "change": -0.004},
        {"start": (start + timedelta(hours=4)).timestamp() * 1000, "change": None},
    ]
    out = HS.hourly_change(rows, start, start + timedelta(hours=5))
    assert out == [0.591, 0.318, None, 0.0, None]   # gap hour and null change are missing
