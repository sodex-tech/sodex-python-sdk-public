"""Synchronous WebSocket client with auto-reconnect and subscription routing.

Mirrors the behavior of ``ws/client.go`` from the Go public SDK:

  * Subscribes / unsubscribes by sending ``{"op":"subscribe", "id":N, "params":{…}}``.
  * Maintains a per-channel handler registry; on reconnect, all subscriptions
    are automatically re-sent.
  * Sends ``{"op":"ping"}`` every 30 seconds; if no response within 60 seconds
    the connection is considered dead and reconnected.
  * Non-blocking: ``connect()`` runs the read loop on a background thread, so
    the calling thread can keep registering subscriptions.

Usage::

    from sodex.ws import Client, SubscribeParams, CHANNEL_TICKER

    c = Client.from_base_url("https://testnet-gw.sodex.dev", engine="perps")
    c.connect()  # starts background thread

    def on_push(push):
        print(push.channel, push.data)

    sub_id = c.subscribe(
        SubscribeParams(channel=CHANNEL_TICKER, symbols=["BTC-USD"]), on_push
    )
    # …
    c.unsubscribe(sub_id)
    c.close()
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

import websocket  # from `websocket-client`

from .types import (
    CHANNEL_ACCOUNT_ORDER_UPDATE,
    CHANNEL_ACCOUNT_STATE,
    CHANNEL_ACCOUNT_TRADE,
    AccountOrderUpdate,
    AccountTrade,
    Push,
    SubscribeParams,
)

PING_INTERVAL = 30.0  # seconds between client → server pings
PONG_WAIT = 30.0  # extra timeout window for the read loop
RECONNECT_DELAY = 1.0  # seconds between failed (re)connects
WRITE_TIMEOUT = 10.0  # per-message send timeout

_log = logging.getLogger("sodex.ws")

Handler = Callable[[Push], None]
ErrorHandler = Callable[[Exception], None]


@dataclass
class _Subscription:
    """Internal record tracking a single ``subscribe`` call."""

    id: int
    params: SubscribeParams
    handler: Handler
    ready: threading.Event = field(default_factory=threading.Event)
    error: Optional[Exception] = None
    acknowledged_symbols: set = field(default_factory=set)


class AccountSubscription:
    """Grouped account subscriptions that can be closed with one call."""

    def __init__(self, client: "Client", subscription_ids: List[int]) -> None:
        self._client = client
        self._subscription_ids = subscription_ids
        self._closed = False

    def wait_ready(self, timeout: float = 10.0) -> None:
        """Wait for every account subscription ACK; repeat after reconnect."""
        deadline = time.monotonic() + timeout
        for subscription_id in self._subscription_ids:
            self._client.wait_ready(
                subscription_id, max(0.0, deadline - time.monotonic())
            )

    def close(self) -> None:
        """Unsubscribe every account channel in this group."""
        if self._closed:
            return
        self._closed = True
        for subscription_id in self._subscription_ids:
            self._client.unsubscribe(subscription_id)


class Client:
    """Synchronous WebSocket client for the Sodex real-time API.

    Thread safety: ``subscribe``, ``unsubscribe``, and ``close`` may be called
    from any thread. Push handlers run on the background read thread; keep them
    fast and non-blocking, or hand off to a worker thread / queue.
    """

    def __init__(self, ws_url: str) -> None:
        """
        Args:
            ws_url: Full WebSocket URL, e.g. ``"wss://testnet-gw.sodex.dev/ws/perps"``.
                    Use :meth:`from_base_url` to construct from an HTTP base URL.
        """
        self._url = ws_url
        self._ws: Optional[websocket.WebSocket] = None
        self._lock = threading.RLock()
        self._next_id = 0
        self._subs: Dict[int, _Subscription] = {}
        self._handlers: Dict[str, List[int]] = {}  # channel name → list of sub IDs
        self._on_error: Optional[ErrorHandler] = None
        self._stop = threading.Event()
        self._reader: Optional[threading.Thread] = None
        self._pinger: Optional[threading.Thread] = None
        self._last_pong = time.monotonic()

    # ── Construction helpers ─────────────────────────────────────────────────

    @classmethod
    def from_base_url(cls, base_url: str, engine: str) -> "Client":
        """Build a Client from an HTTP base URL and engine name (``"spot"`` or ``"perps"``)."""
        if engine not in ("spot", "perps"):
            raise ValueError("engine must be 'spot' or 'perps'")
        u = urlparse(base_url)
        scheme = "wss" if u.scheme == "https" else "ws"
        host = u.netloc or u.path  # tolerate scheme-less inputs
        return cls(f"{scheme}://{host}/ws/{engine}")

    # ── Public API ────────────────────────────────────────────────────────────

    def on_error(self, fn: ErrorHandler) -> None:
        """Register a callback for connection / read errors. Default: log to stderr."""
        with self._lock:
            self._on_error = fn

    def connect(self) -> None:
        """Establish the WebSocket connection and start the read & ping loops on background threads.

        Returns immediately. Use :meth:`close` to stop the loops and disconnect.
        """
        if self._reader is not None:
            return  # already connected/connecting

        self._reader = threading.Thread(
            target=self._connect_loop, name="sodex-ws-reader", daemon=True
        )
        self._reader.start()

    def subscribe(self, params: SubscribeParams, handler: Handler) -> int:
        """Register ``handler`` for ``params.channel`` and send the subscribe message.

        Returns a subscription ID that can be passed to :meth:`unsubscribe`. If
        the connection is currently down the subscription is recorded locally
        and re-sent on reconnect.
        """
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError("ws: client closed")
            # Account pushes do not consistently identify the owning wallet.
            if params.user and any(
                x.params.user and x.params.user.lower() != params.user.lower()
                for x in self._subs.values()
            ):
                raise ValueError("use a separate WebSocket client per account owner")
            self._next_id += 1
            sub_id = self._next_id
            sub = _Subscription(id=sub_id, params=params, handler=handler)
            self._subs[sub_id] = sub
            self._handlers.setdefault(params.channel, []).append(sub_id)

        self._send_subscribe("subscribe", sub_id, params)
        return sub_id

    def wait_ready(self, sub_id: int, timeout: float = 10.0) -> None:
        """Wait for server acknowledgement, raising on rejection, timeout or cancellation."""
        with self._lock:
            sub = self._subs.get(sub_id)
        if sub is None:
            raise ValueError(f"ws: subscription {sub_id} not found")
        if not sub.ready.wait(timeout):
            raise TimeoutError(f"ws: subscription {sub_id} acknowledgement timed out")
        if sub.error is not None:
            raise sub.error

    def subscribe_account(
        self,
        user: str,
        *,
        symbols: Optional[List[str]] = None,
        on_snapshot: Optional[Handler] = None,
        on_order_update: Optional[Callable[[AccountOrderUpdate], None]] = None,
        on_trade: Optional[Callable[[AccountTrade], None]] = None,
    ) -> AccountSubscription:
        """Subscribe to typed account snapshots, order updates, and fills."""
        if not any((on_snapshot, on_order_update, on_trade)):
            raise ValueError("at least one account callback is required")
        subscription_ids: List[int] = []
        if on_snapshot is not None:
            subscription_ids.append(
                self.subscribe(
                    SubscribeParams(channel=CHANNEL_ACCOUNT_STATE, user=user),
                    on_snapshot,
                )
            )
        if on_order_update is not None:

            def handle_orders(push: Push) -> None:
                items = push.data if isinstance(push.data, list) else [push.data]
                for item in items:
                    on_order_update(AccountOrderUpdate.from_dict(item))

            subscription_ids.append(
                self.subscribe(
                    SubscribeParams(
                        channel=CHANNEL_ACCOUNT_ORDER_UPDATE,
                        user=user,
                        symbols=symbols,
                    ),
                    handle_orders,
                )
            )
        if on_trade is not None:

            def handle_trades(push: Push) -> None:
                items = push.data if isinstance(push.data, list) else [push.data]
                for item in items:
                    on_trade(AccountTrade.from_dict(item))

            subscription_ids.append(
                self.subscribe(
                    SubscribeParams(
                        channel=CHANNEL_ACCOUNT_TRADE,
                        user=user,
                        symbols=symbols,
                    ),
                    handle_trades,
                )
            )
        return AccountSubscription(self, subscription_ids)

    def unsubscribe(self, sub_id: int) -> None:
        """Remove a subscription by ID. Sends an unsubscribe to the server when no handlers remain."""
        with self._lock:
            sub = self._subs.pop(sub_id, None)
            if sub is None:
                raise ValueError(f"ws: subscription {sub_id} not found")

            ids = self._handlers.get(sub.params.channel, [])
            if sub_id in ids:
                ids.remove(sub_id)
            should_send = not any(
                self._subs[i].params.to_json() == sub.params.to_json() for i in ids
            )
            if not ids:
                self._handlers.pop(sub.params.channel, None)
            sub.error = RuntimeError("ws: subscription cancelled")
            sub.ready.set()

        if should_send:
            self._send_subscribe("unsubscribe", sub_id, sub.params)
            # Overlapping symbol sets may share server resources. Restore remaining subscriptions.
            with self._lock:
                remaining = [
                    x
                    for x in self._subs.values()
                    if x.params.channel == sub.params.channel
                ]
            removed_symbols = set(
                sub.params.symbols or ([sub.params.symbol] if sub.params.symbol else [])
            )
            for other in remaining:
                other_symbols = set(
                    other.params.symbols
                    or ([other.params.symbol] if other.params.symbol else [])
                )
                if (
                    not removed_symbols
                    or not other_symbols
                    or removed_symbols & other_symbols
                ):
                    self._send_subscribe("subscribe", other.id, other.params)

    def close(self) -> None:
        """Stop the read/ping loops and close the WebSocket connection."""
        self._stop.set()
        with self._lock:
            for sub in self._subs.values():
                sub.error = RuntimeError("ws: client closed")
                sub.ready.set()
            ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:  # pragma: no cover — best-effort close
                pass
        current = threading.current_thread()
        for thread in (self._reader, self._pinger):
            if thread is not None and thread is not current and thread.is_alive():
                thread.join(timeout=2.0)

    # ── Internal: connection loop ────────────────────────────────────────────

    def _connect_loop(self) -> None:
        """Run forever: dial, read, reconnect on disconnect, until ``close`` is called."""
        attempt = 0
        while not self._stop.is_set():
            try:
                ws = websocket.create_connection(self._url, timeout=10.0)
            except Exception as e:
                self._emit_error(RuntimeError(f"ws: connect {self._url}: {e}"))
                self._stop.wait(
                    random.uniform(0.5, 1.0)
                    * min(30.0, RECONNECT_DELAY * 2 ** min(attempt, 5))
                )
                attempt += 1
                continue

            with self._lock:
                self._ws = ws
            self._last_pong = time.monotonic()
            connected_at = time.monotonic()

            # Re-subscribe everything from before the last disconnect.
            self._resubscribe()

            # Start a separate ping thread tied to this connection.
            pinger = threading.Thread(
                target=self._ping_loop, args=(ws,), name="sodex-ws-pinger", daemon=True
            )
            self._pinger = pinger
            pinger.start()

            # Block on the read loop until the connection drops.
            self._read_loop(ws)

            # Tear down this connection; the loop will reconnect.
            with self._lock:
                self._ws = None
                for sub in self._subs.values():
                    if not self._stop.is_set():
                        sub.ready.clear()
                        sub.acknowledged_symbols.clear()
            try:
                ws.close()
            except Exception:
                pass

            if not self._stop.is_set():
                attempt = 0 if time.monotonic() - connected_at >= 30 else attempt + 1
                self._stop.wait(
                    random.uniform(0.5, 1.0)
                    * min(30.0, RECONNECT_DELAY * 2 ** min(attempt, 5))
                )

    def _read_loop(self, ws: websocket.WebSocket) -> None:
        """Read frames until the socket errors out, dispatching pushes to handlers."""
        while not self._stop.is_set():
            try:
                ws.settimeout(PING_INTERVAL + PONG_WAIT)
                raw = ws.recv()
            except Exception as e:
                if not self._stop.is_set():
                    self._emit_error(RuntimeError(f"ws: read: {e}"))
                return
            if not raw:
                # Empty frame ⇒ peer-initiated close.
                return
            self._dispatch(raw)

    def _ping_loop(self, ws: websocket.WebSocket) -> None:
        """Send ``{"op":"ping"}`` every PING_INTERVAL seconds."""
        while not self._stop.is_set():
            if self._stop.wait(PING_INTERVAL):
                return
            with self._lock:
                if self._ws is not ws:
                    return
            if time.monotonic() - self._last_pong > PING_INTERVAL + PONG_WAIT:
                ws.close()
                return
            try:
                ws.send(json.dumps({"op": "ping"}, separators=(",", ":")))
            except Exception:
                return  # connection broken; read loop will detect & reconnect

    def _resubscribe(self) -> None:
        """Re-send subscribe messages for all currently-tracked subscriptions."""
        with self._lock:
            subs = list(self._subs.values())
        for s in subs:
            self._send_subscribe("subscribe", s.id, s.params)

    def _send_subscribe(self, op: str, sub_id: int, params: SubscribeParams) -> None:
        """Send a single subscribe / unsubscribe message; silently no-op if the socket is down."""
        with self._lock:
            ws = self._ws
        if ws is None:
            # Will be re-sent automatically on reconnect via _resubscribe.
            return
        if op == "subscribe":
            with self._lock:
                sub = self._subs.get(sub_id)
                if sub is not None:
                    sub.ready.clear()
                    sub.error = None
                    sub.acknowledged_symbols.clear()
        msg = {"op": op, "id": sub_id, "params": params.to_json()}
        try:
            ws.send(json.dumps(msg, separators=(",", ":")))
        except Exception as e:
            self._emit_error(RuntimeError(f"ws: send {op}: {e}"))

    def _dispatch(self, raw: bytes) -> None:
        """Route a single raw frame to the right handler(s)."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            msg = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return
        if not isinstance(msg, dict):
            return

        op = msg.get("op")
        if op == "pong":
            self._last_pong = time.monotonic()
            return
        if op == "error":
            code = msg.get("code", "")
            err = msg.get("error", "")
            self._emit_error(RuntimeError(f"ws: server error (code {code}): {err}"))
            return
        if op in ("subscribe", "unsubscribe"):
            error = (
                RuntimeError(f"ws: {op} failed: {msg.get('error', '')}")
                if msg.get("success") is not True
                else None
            )
            if op == "subscribe":
                with self._lock:
                    sub = self._subs.get(msg.get("id"))
                    if sub is not None:
                        result = msg.get("result") or {}
                        symbol = result.get("symbol")
                        if symbol:
                            sub.acknowledged_symbols.add(symbol)
                        expected = sub.params.to_json().get("symbols", [])
                        if (
                            error
                            or not symbol
                            or set(expected).issubset(sub.acknowledged_symbols)
                        ):
                            sub.error = error
                            sub.ready.set()
            if error:
                self._emit_error(error)
            return

        channel = msg.get("channel", "")
        if not channel:
            return

        push = Push.from_dict(msg)
        with self._lock:
            ids = list(self._handlers.get(channel, []))
            subs = [self._subs[i] for i in ids if i in self._subs]
        for sub in subs:
            try:
                symbols = sub.params.symbols or (
                    [sub.params.symbol] if sub.params.symbol else []
                )
                data = push.data
                if symbols:

                    def matches(item):
                        if not isinstance(item, dict):
                            return False
                        nested = (
                            item.get("k") if isinstance(item.get("k"), dict) else item
                        )
                        if (
                            sub.params.interval
                            and nested.get("i", nested.get("interval"))
                            != sub.params.interval
                        ):
                            return False
                        symbol = item.get("s", item.get("symbol", nested.get("s")))
                        return (
                            symbol in symbols
                            if symbol
                            else len(
                                {
                                    json.dumps(x.params.to_json(), sort_keys=True)
                                    for x in subs
                                }
                            )
                            == 1
                        )

                    if isinstance(data, list):
                        data = [item for item in data if matches(item)]
                        if not data:
                            continue
                    elif not matches(data):
                        continue
                if sub.params.interval and isinstance(data, dict):
                    candle = data.get("k", data)
                    if candle.get("i", candle.get("interval")) != sub.params.interval:
                        continue
                sub.handler(Push(channel=push.channel, type=push.type, data=data))
            except Exception as e:
                self._emit_error(
                    RuntimeError(f"ws: handler raised on channel {channel}: {e}")
                )

    def _emit_error(self, err: Exception) -> None:
        with self._lock:
            fn = self._on_error
        if fn is not None:
            try:
                fn(err)
            except Exception:  # pragma: no cover — never let user code break the loop
                _log.exception("ws: on_error callback raised")
        else:
            _log.warning(str(err))
