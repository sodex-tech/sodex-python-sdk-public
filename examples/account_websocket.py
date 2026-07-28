"""Correlate REST order IDs with account order/trade WebSocket pushes.

Usage::

    export SODEX_ADDRESS=0x...
    export SODEX_ORDER_ID=12345  # optional: only print this order's events
    python examples/account_websocket.py
"""

from __future__ import annotations

import os
import signal
import threading

from sodex.client import Client as RestClient
from sodex.ws import (
    CHANNEL_ACCOUNT_ORDER_UPDATE,
    CHANNEL_ACCOUNT_STATE,
    CHANNEL_ACCOUNT_TRADE,
    AccountOrderUpdate,
    AccountTrade,
    Client,
    Push,
    SubscribeParams,
)


def main() -> None:
    user = os.environ.get("SODEX_ADDRESS")
    if not user:
        raise SystemExit("SODEX_ADDRESS is required")
    expected_order_id = int(os.environ.get("SODEX_ORDER_ID", "0"))
    symbols = [os.environ.get("SODEX_SYMBOL", "BTC-USD")]

    def selected(order_id: int) -> bool:
        return expected_order_id == 0 or order_id == expected_order_id

    def on_snapshot(push: Push) -> None:
        print(f"account snapshot type={push.type}")

    def on_order(push: Push) -> None:
        for raw in push.data if isinstance(push.data, list) else [push.data]:
            order = AccountOrderUpdate.from_dict(raw)
            if selected(order.order_id):
                print(
                    f"order orderID={order.order_id} clOrdID={order.cl_ord_id} "
                    f"status={order.status} filled={order.filled_qty}"
                )

    def on_trade(push: Push) -> None:
        for raw in push.data if isinstance(push.data, list) else [push.data]:
            trade = AccountTrade.from_dict(raw)
            if selected(trade.order_id):
                print(
                    f"fill orderID={trade.order_id} tradeID={trade.trade_id} "
                    f"price={trade.price} quantity={trade.quantity} fee={trade.fee}"
                )

    ws = Client.from_base_url(RestClient.DEFAULT_BASE_URL, engine="perps")
    ws.subscribe(SubscribeParams(CHANNEL_ACCOUNT_STATE, user=user), on_snapshot)
    ws.subscribe(
        SubscribeParams(CHANNEL_ACCOUNT_ORDER_UPDATE, symbols=symbols, user=user),
        on_order,
    )
    ws.subscribe(
        SubscribeParams(CHANNEL_ACCOUNT_TRADE, symbols=symbols, user=user), on_trade
    )
    ws.connect()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    stop.wait()
    ws.close()


if __name__ == "__main__":
    main()
