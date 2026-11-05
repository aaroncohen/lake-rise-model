"""Home Assistant long-term statistics, read over the websocket API.

HA keeps hourly statistics *permanently* for any sensor with a ``state_class``, while raw history
is purged after ~10 days. They are the only record of rain older than that window: the hourly
``change`` of ``sensor.gw3000b_daily_rain_piezo`` is exactly that hour's rainfall (verified
against the raw-history accumulator diff, 2026-11-04). HA exposes statistics only through the
websocket command ``recorder/statistics_during_period`` -- there is no REST endpoint -- so this is
a minimal stdlib RFC 6455 client: one authenticated request per connection, text frames, client
masking, fragmentation and ping/pong; no extensions or subprotocols are negotiated.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..hourly import hour_grid

_OP_CONT, _OP_TEXT, _OP_CLOSE, _OP_PING, _OP_PONG = 0x0, 0x1, 0x8, 0x9, 0xA


class StatisticsError(RuntimeError):
    """The websocket handshake, authentication, or the statistics command failed."""


class _WebSocket:
    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._buf = b""

    def handshake(self, host: str, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        self._sock.sendall(
            (f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        while b"\r\n\r\n" not in self._buf:
            self._fill()
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101 " not in f"{status} ":
            raise StatisticsError(f"websocket upgrade refused: {status}")

    def _fill(self) -> None:
        chunk = self._sock.recv(1 << 16)
        if not chunk:
            raise StatisticsError("connection closed by Home Assistant")
        self._buf += chunk

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            self._fill()
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        n = len(payload)
        if n < 126:
            header = struct.pack(">BB", 0x80 | opcode, 0x80 | n)
        elif n < 1 << 16:
            header = struct.pack(">BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            header = struct.pack(">BBQ", 0x80 | opcode, 0x80 | 127, n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(header + mask + masked)

    def send_json(self, obj: dict) -> None:
        self._send_frame(_OP_TEXT, json.dumps(obj).encode())

    def recv_json(self) -> dict:
        message = b""
        while True:
            b1, b2 = self._read(2)
            fin, opcode, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None     # servers must not mask; tolerate it
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == _OP_PING:
                self._send_frame(_OP_PONG, payload)
                continue
            if opcode == _OP_PONG:
                continue
            if opcode == _OP_CLOSE:
                raise StatisticsError("Home Assistant closed the websocket")
            if opcode not in (_OP_TEXT, _OP_CONT):
                raise StatisticsError(f"unexpected websocket opcode {opcode}")
            message += payload
            if fin:
                return json.loads(message)

    def close(self) -> None:
        try:
            self._send_frame(_OP_CLOSE, b"")
        except OSError:
            pass
        self._sock.close()


def _connect(base_url: str, timeout: float) -> _WebSocket:
    u = urlparse(base_url)
    secure = u.scheme == "https"
    host = u.hostname or "localhost"
    port = u.port or (443 if secure else 80)
    sock = socket.create_connection((host, port), timeout=timeout)
    if secure:
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
    ws = _WebSocket(sock)
    base_path = u.path.rstrip("/")
    ws.handshake(f"{host}:{port}", f"{base_path}/api/websocket")
    return ws


def request(ws: _WebSocket, token: str, command: dict) -> object:
    """Authenticate on an open websocket and run one command; return its ``result``."""
    if ws.recv_json().get("type") != "auth_required":
        raise StatisticsError("unexpected websocket greeting")
    ws.send_json({"type": "auth", "access_token": token})
    if ws.recv_json().get("type") != "auth_ok":
        raise StatisticsError("websocket authentication failed")
    ws.send_json({"id": 1, **command})
    while True:
        msg = ws.recv_json()
        if msg.get("id") == 1 and msg.get("type") == "result":
            if not msg.get("success"):
                raise StatisticsError(f"statistics request failed: {msg.get('error')}")
            return msg.get("result")


def statistics_during_period(base_url: str, token: str, statistic_id: str, start: datetime,
                             end: datetime, types: tuple[str, ...] = ("change",),
                             timeout: float = 60.0) -> list[dict]:
    """Hourly long-term statistics rows for one statistic over ``[start, end)``."""
    ws = _connect(base_url, timeout)
    try:
        result = request(ws, token, {
            "type": "recorder/statistics_during_period",
            "start_time": start.isoformat(), "end_time": end.isoformat(),
            "statistic_ids": [statistic_id], "period": "hour", "types": list(types),
        })
    finally:
        ws.close()
    return list((result or {}).get(statistic_id, []))


def _row_start(row: dict) -> datetime:
    s = row["start"]
    if isinstance(s, (int, float)):                       # epoch ms (HA >= 2023.3)
        return datetime.fromtimestamp(s / 1000, tz=timezone.utc)
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def hourly_change(rows: list[dict], start: datetime, end: datetime) -> list[float | None]:
    """Lay statistics rows on the clock-hour grid ``[start, end)`` as each hour's ``change``.
    An hour with no row (HA was down, or the sensor didn't exist yet) is ``None`` -- missing,
    never dry. A negative change is a reset artifact and clamps to 0."""
    by_hour: dict[datetime, float | None] = {}
    for row in rows:
        try:
            t = _row_start(row)
        except (KeyError, ValueError, TypeError):
            continue
        c = row.get("change")
        by_hour[t] = None if c is None else round(max(float(c), 0.0), 4)
    return [by_hour.get(h) for h in hour_grid(start, end)]
