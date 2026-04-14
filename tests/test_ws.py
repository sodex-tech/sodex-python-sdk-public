"""WebSocket client tests using a real localhost server.

Spins up an in-process ``websockets`` server bound to ephemeral ports. This is
more faithful than mocking because it exercises the actual socket plumbing —
serialisation, framing, threading, and reconnect logic.

Verifies:

  1. URL construction from base URL + engine name.
  2. Subscribe message is sent in the documented JSON shape.
  3. Push messages are routed to the correct handler.
  4. Multiple subscriptions on the same channel all fire.
  5. Unsubscribe removes the handler and (when the last one is gone) sends an
     unsubscribe to the server.
  6. Auto-reconnect resends all active subscriptions.

Tests skip gracefully if the optional ``websockets`` server library isn't
installed (it's pulled in by responses' transitive deps in CI).
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from contextlib import closing
from typing import List

import pytest

from sodex.ws import (
    CHANNEL_TICKER,
    Client,
    Push,
    SubscribeParams,
)

# We use the `websockets` library (different from `websocket-client`) for the
# in-process server — they coexist fine.
try:
    import websockets
    from websockets.exceptions import ConnectionClosed
    from websockets.server import serve as ws_serve
except ImportError:  # pragma: no cover — covered by skip below
    websockets = None  # type: ignore


pytestmark = pytest.mark.skipif(
    websockets is None,
    reason="websockets server library not installed",
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _free_port() -> int:
    """Return an ephemeral TCP port that is currently free."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FakeServer:
    """In-process WebSocket server that echoes pings, ACKs subscribes, and pushes
    a configurable list of messages to every connected client.

    The server runs its asyncio loop on a dedicated daemon thread so tests can
    interact with it from the main thread synchronously.
    """

    def __init__(self) -> None:
        self.port = _free_port()
        self.received: List[dict] = []
        self.connections: List = []
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._server = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()
        assert self._ready.wait(5.0), "server failed to start"

    def stop(self) -> None:
        if self._server is not None:
            self._loop.call_soon_threadsafe(self._server.close)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(2.0)

    async def _handler(self, ws):
        self.connections.append(ws)
        try:
            async for raw in ws:
                msg = json.loads(raw)
                self.received.append(msg)
                op = msg.get("op")
                if op == "ping":
                    await ws.send(json.dumps({"op": "pong"}))
                elif op in ("subscribe", "unsubscribe"):
                    await ws.send(
                        json.dumps({"op": op, "id": msg.get("id"), "success": True})
                    )
        except ConnectionClosed:
            pass

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def main():
            self._server = await ws_serve(self._handler, "127.0.0.1", self.port)
            self._ready.set()
            await self._server.wait_closed()

        self._loop.create_task(main())
        self._loop.run_forever()

    def push(self, message: dict) -> None:
        """Push ``message`` to every currently-connected client (thread-safe)."""

        async def send_all():
            for ws in list(self.connections):
                try:
                    await ws.send(json.dumps(message))
                except Exception:
                    pass

        future = asyncio.run_coroutine_threadsafe(send_all(), self._loop)
        future.result(timeout=2.0)


@pytest.fixture
def server():
    s = _FakeServer()
    s.start()
    yield s
    s.stop()


def _wait_for(predicate, timeout: float = 3.0, interval: float = 0.02) -> bool:
    """Spin-wait until ``predicate()`` is truthy or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ── 1. URL construction ─────────────────────────────────────────────────────


def test_from_base_url_https_to_wss():
    """Building from an https:// base URL produces a wss:// URL with the engine path."""
    c = Client.from_base_url("https://testnet-gw.sodex.dev", engine="perps")
    assert c._url == "wss://testnet-gw.sodex.dev/ws/perps"


def test_from_base_url_http_to_ws():
    """Building from an http:// base URL produces a ws:// URL."""
    c = Client.from_base_url("http://localhost:8080", engine="spot")
    assert c._url == "ws://localhost:8080/ws/spot"


# ── 2. Subscribe message shape ──────────────────────────────────────────────


def test_subscribe_sends_correct_message(server):
    """subscribe sends the documented {op, id, params} JSON shape."""
    c = Client(server.url)
    c.connect()
    try:
        c.subscribe(
            SubscribeParams(channel=CHANNEL_TICKER, symbol="BTC-USD"),
            handler=lambda push: None,
        )
        assert _wait_for(lambda: len(server.received) >= 1)
        msg = server.received[0]
        assert msg["op"] == "subscribe"
        assert msg["id"] == 1
        assert msg["params"] == {"channel": "ticker", "symbol": "BTC-USD"}
    finally:
        c.close()


# ── 3. Push routing ─────────────────────────────────────────────────────────


def test_push_routed_to_handler(server):
    """A pushed message on a subscribed channel reaches the registered handler."""
    received: List[Push] = []
    c = Client(server.url)
    c.connect()
    try:
        c.subscribe(
            SubscribeParams(channel=CHANNEL_TICKER, symbol="BTC-USD"),
            handler=lambda push: received.append(push),
        )
        # Wait for subscription ACK so we know the connection is live.
        assert _wait_for(lambda: len(server.received) >= 1)

        server.push(
            {
                "channel": "ticker",
                "type": "snapshot",
                "data": {"s": "BTC-USD", "c": "70000"},
            }
        )
        assert _wait_for(lambda: len(received) >= 1)
        push = received[0]
        assert push.channel == "ticker"
        assert push.type == "snapshot"
        assert push.data == {"s": "BTC-USD", "c": "70000"}
    finally:
        c.close()


def test_multiple_handlers_same_channel_all_fire(server):
    """Two subscriptions on the same channel both receive the push."""
    received_a, received_b = [], []
    c = Client(server.url)
    c.connect()
    try:
        c.subscribe(
            SubscribeParams(channel=CHANNEL_TICKER, symbol="BTC-USD"),
            handler=lambda push: received_a.append(push),
        )
        c.subscribe(
            SubscribeParams(channel=CHANNEL_TICKER, symbol="BTC-USD"),
            handler=lambda push: received_b.append(push),
        )
        assert _wait_for(lambda: len(server.received) >= 2)

        server.push({"channel": "ticker", "type": "snapshot", "data": {}})
        assert _wait_for(lambda: received_a and received_b)
    finally:
        c.close()


# ── 4. Unsubscribe ──────────────────────────────────────────────────────────


def test_unsubscribe_removes_handler(server):
    """After unsubscribe the handler stops receiving and an unsubscribe is sent to the server."""
    received: List[Push] = []
    c = Client(server.url)
    c.connect()
    try:
        sub_id = c.subscribe(
            SubscribeParams(channel=CHANNEL_TICKER, symbol="BTC-USD"),
            handler=lambda push: received.append(push),
        )
        assert _wait_for(lambda: len(server.received) >= 1)

        c.unsubscribe(sub_id)
        # The server should have received an unsubscribe op.
        assert _wait_for(
            lambda: any(m.get("op") == "unsubscribe" for m in server.received)
        )

        # Pushes after unsubscribe must not reach the handler.
        server.push({"channel": "ticker", "type": "update", "data": {}})
        time.sleep(0.1)
        assert received == []
    finally:
        c.close()


def test_unsubscribe_keeps_other_handlers(server):
    """Unsubscribing one of two handlers on the same channel does NOT send an unsubscribe."""
    received_a, received_b = [], []
    c = Client(server.url)
    c.connect()
    try:
        a = c.subscribe(
            SubscribeParams(channel=CHANNEL_TICKER, symbol="BTC-USD"),
            handler=lambda push: received_a.append(push),
        )
        c.subscribe(
            SubscribeParams(channel=CHANNEL_TICKER, symbol="BTC-USD"),
            handler=lambda push: received_b.append(push),
        )
        assert _wait_for(lambda: len(server.received) >= 2)

        c.unsubscribe(a)
        # No unsubscribe should hit the wire because handler B is still active.
        time.sleep(0.1)
        assert all(m.get("op") != "unsubscribe" for m in server.received)

        server.push({"channel": "ticker", "type": "snapshot", "data": {}})
        assert _wait_for(lambda: len(received_b) >= 1)
        assert received_a == []  # A is gone
    finally:
        c.close()


def test_unsubscribe_unknown_id_raises():
    """Unsubscribing an ID that was never registered raises ValueError."""
    c = Client("ws://127.0.0.1:1")  # never connected
    with pytest.raises(ValueError):
        c.unsubscribe(999)


# ── 5. Reconnect & resubscribe ──────────────────────────────────────────────


def test_reconnect_resends_subscriptions(server):
    """When the underlying connection drops, the client reconnects and resubscribes."""
    c = Client(server.url)
    c.connect()
    try:
        c.subscribe(
            SubscribeParams(channel=CHANNEL_TICKER, symbol="BTC-USD"),
            handler=lambda push: None,
        )
        assert _wait_for(lambda: len(server.received) >= 1)
        initial_subs = sum(1 for m in server.received if m.get("op") == "subscribe")
        assert initial_subs == 1

        # Force-close every server-side connection to simulate a disconnect.
        # Using transport.close() drops the TCP socket without a graceful
        # WebSocket close handshake — closer to a real network failure.
        def kill_all():
            for ws in list(server.connections):
                try:
                    ws.transport.close()
                except Exception:
                    pass
            server.connections.clear()

        server._loop.call_soon_threadsafe(kill_all)

        # The client should reconnect and resend the subscribe.
        assert _wait_for(
            lambda: sum(
                1 for m in server.received if m.get("op") == "subscribe"
            ) >= initial_subs + 1,
            timeout=5.0,
        )
    finally:
        c.close()
