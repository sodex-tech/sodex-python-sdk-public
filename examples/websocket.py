"""Subscribe to the trade and L2 order book channels for BTC-USD on perps.

Usage::

    python examples/websocket.py

Runs until Ctrl-C. The SDK auto-reconnects and re-sends subscriptions on drop.
"""

from __future__ import annotations

import logging
import signal
import threading

from sodex.client import Client
from sodex.ws import (
    CHANNEL_L2_BOOK,
    CHANNEL_TRADE,
    Client as WsClient,
    L2Book,
    Push,
    SubscribeParams,
    Trade,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")


def handle_trade(push: Push) -> None:
    t = Trade.from_dict(push.data)
    print(f"[trade]   {t.symbol} {t.side} @ {t.price} qty={t.quantity}")


def handle_l2_book(push: Push) -> None:
    book = L2Book.from_dict(push.data)
    best_bid = f"{book.bids[0][0]} × {book.bids[0][1]}" if book.bids else "-"
    best_ask = f"{book.asks[0][0]} × {book.asks[0][1]}" if book.asks else "-"
    print(f"[{push.type}] {book.symbol}  bid {best_bid}  ask {best_ask}")


def main() -> None:
    w = WsClient.from_base_url(Client.TESTNET_BASE_URL, engine="perps")
    w.on_error(lambda err: logging.warning(f"ws error: {err}"))

    # Subscribe BEFORE connect — the SDK queues subscriptions and sends them as
    # soon as the socket opens, then auto-re-subscribes on reconnect.
    w.subscribe(
        SubscribeParams(channel=CHANNEL_TRADE, symbol="BTC-USD"),
        handle_trade,
    )
    w.subscribe(
        SubscribeParams(channel=CHANNEL_L2_BOOK, symbol="BTC-USD"),
        handle_l2_book,
    )

    w.connect()  # non-blocking; read loop runs on a background thread
    logging.info("connected (Ctrl-C to quit)")

    # Block the main thread until a signal arrives.
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    stop.wait()

    logging.info("shutting down…")
    w.close()


if __name__ == "__main__":
    main()
