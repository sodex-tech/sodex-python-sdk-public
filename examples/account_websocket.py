"""Correlate a REST order ID with account order/trade WebSocket pushes.

Lifecycle: subscribe before connect -> receive snapshots/updates/fills -> match
the REST order ID -> close the grouped subscription and socket on shutdown.

Usage::

    export SODEX_ACCOUNT_ADDRESS=0x...
    export SODEX_MARKET=perps  # or spot
    export SODEX_ORDER_ID=12345  # optional: only print this order's events
    python examples/account_websocket.py
"""

from __future__ import annotations

import os
import signal
import threading

from sodex.client import Client as RestClient
from sodex.ws import (
    AccountOrderUpdate,
    AccountTrade,
    Client,
    Push,
)


def main() -> None:
    user = os.environ.get("SODEX_ADDRESS") or os.environ.get("SODEX_ACCOUNT_ADDRESS")
    if not user:
        raise SystemExit("SODEX_ADDRESS is required")
    expected_order_id = int(os.environ.get("SODEX_ORDER_ID", "0"))
    market = os.environ.get("SODEX_MARKET", "perps").lower()
    if market not in ("spot", "perps"):
        raise SystemExit("SODEX_MARKET must be spot or perps")
    symbols = [
        os.environ.get(
            "SODEX_SYMBOL", "BTC/USDC" if market == "spot" else "BTC-USD"
        )
    ]

    def selected(order_id: int) -> bool:
        return expected_order_id == 0 or order_id == expected_order_id

    def on_snapshot(push: Push) -> None:
        print(f"account snapshot type={push.type}")

    def on_order(order: AccountOrderUpdate) -> None:
        if selected(order.order_id):
            print(
                f"order orderID={order.order_id} clOrdID={order.cl_ord_id} "
                f"status={order.status} filled={order.filled_qty}"
            )

    def on_trade(trade: AccountTrade) -> None:
        if selected(trade.order_id):
            print(
                f"fill orderID={trade.order_id} tradeID={trade.trade_id} "
                f"price={trade.price} quantity={trade.quantity} fee={trade.fee}"
            )

    rest = RestClient.from_env()
    ws = Client.from_base_url(rest.base_url, engine=market)
    subscription = ws.subscribe_account(
        user,
        symbols=symbols,
        on_snapshot=on_snapshot,
        on_order_update=on_order,
        on_trade=on_trade,
    )
    ws.connect()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    stop.wait()
    subscription.close()
    ws.close()


if __name__ == "__main__":
    main()
