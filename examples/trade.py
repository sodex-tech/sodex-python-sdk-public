"""Inspect common state, place a Spot/Perps order, and print its order ID.

Lifecycle: query registration/account/symbol/fee state -> sign and submit ->
save order_id and cl_ord_id -> observe order/fill details over account WS.

The script defaults to testnet for safety. REST acceptance is not proof of a
fill; pass the returned order ID to ``examples/account_websocket.py``.

Usage::

    export SODEX_NETWORK=testnet
    export SODEX_PRIVATE_KEY=0x...
    export SODEX_MARKET=perps
    export SODEX_SYMBOL=BTC-USD
    export SODEX_ORDER_SIDE=BUY
    export SODEX_ORDER_TYPE=LIMIT
    export SODEX_ORDER_PRICE=1000
    export SODEX_ORDER_QUANTITY=0.001
    python examples/trade.py
"""

from __future__ import annotations

import os
from decimal import Decimal

from sodex.client import Client


def main() -> None:
    network = os.environ.get("SODEX_NETWORK", "testnet").lower()
    if network not in ("mainnet", "testnet"):
        raise SystemExit("SODEX_NETWORK must be mainnet or testnet")
    market = os.environ.get("SODEX_MARKET", "perps").lower()
    if market not in ("spot", "perps"):
        raise SystemExit("SODEX_MARKET must be spot or perps")
    side = os.environ.get("SODEX_ORDER_SIDE", "BUY").upper()
    if side not in ("BUY", "SELL"):
        raise SystemExit("SODEX_ORDER_SIDE must be BUY or SELL")
    order_type = os.environ.get("SODEX_ORDER_TYPE", "LIMIT").upper()
    if order_type not in ("LIMIT", "MARKET"):
        raise SystemExit("SODEX_ORDER_TYPE must be LIMIT or MARKET")
    quantity_text = os.environ.get("SODEX_ORDER_QUANTITY")
    if not quantity_text:
        raise SystemExit("SODEX_ORDER_QUANTITY is required")
    price_text = os.environ.get("SODEX_ORDER_PRICE")
    if order_type == "LIMIT" and not price_text:
        raise SystemExit("SODEX_ORDER_PRICE is required for a limit order")

    client = Client.from_env(testnet=network == "testnet")
    if not client.account_address:
        raise SystemExit("SODEX_PRIVATE_KEY is required for trading")
    symbol = os.environ.get(
        "SODEX_SYMBOL", "BTC/USDC" if market == "spot" else "BTC-USD"
    )
    # Step 1: resolve common market/account constraints before any signed write.
    symbols = (
        client.spot_symbols(symbol)
        if market == "spot"
        else client.perps_symbols(symbol)
    )
    if not symbols:
        raise RuntimeError(f"unknown {market} symbol: {symbol}")
    metadata = symbols[0]
    status = client.get_user_status()
    account_id = client.primary_account_id()
    fee_rate = client.get_fee_rate(market, symbol=symbol)
    account_state = (
        client.spot_account_state(account_id=account_id)
        if market == "spot"
        else client.perps_account_state(account_id=account_id)
    )
    print(
        "common state:",
        {
            "network": network,
            "market": market,
            "user": client.account_address,
            "user_status": status.status,
            "user_id": status.user_id,
            "account_id": account_id,
            "symbol": metadata.symbol,
            "tick_size": metadata.tick_size,
            "step_size": metadata.step_size,
            "min_quantity": metadata.min_quantity,
            "max_quantity": metadata.max_quantity,
            "min_price": metadata.min_price,
            "max_price": metadata.max_price,
            "min_notional": metadata.min_notional,
            "maker_fee": fee_rate.maker_fee_rate,
            "taker_fee": fee_rate.taker_fee_rate,
            "account_state": account_state,
        },
    )

    quantity = Decimal(quantity_text)
    limit_price = (
        Decimal(price_text) if order_type == "LIMIT" and price_text else None
    )
    # Step 2: sign and submit with the master wallet or configured API key.
    order = (
        client.spot_order(symbol, side == "BUY", quantity, limit_price=limit_price)
        if market == "spot"
        else client.perps_order(
            symbol, side == "BUY", quantity, limit_price=limit_price
        )
    )
    if order.order_id <= 0:
        raise RuntimeError(order.message or f"order was not accepted: {order.status}")
    print(
        f"accepted: orderID={order.order_id} clOrdID={order.cl_ord_id} "
        f"status={order.status}"
    )
    print(
        "REST acceptance is not a fill; run account_websocket.py with "
        f"SODEX_MARKET={market} SODEX_SYMBOL={symbol} SODEX_ORDER_ID={order.order_id}"
    )

    if os.environ.get("SODEX_CANCEL_AFTER_PLACE", "false").lower() == "true":
        if order_type != "LIMIT":
            raise RuntimeError("a market order cannot be cancelled after placement")
        cancelled = (
            client.cancel_spot_order(symbol, order_id=order.order_id)
            if market == "spot"
            else client.cancel_perps_order(symbol, order_id=order.order_id)
        )
        print(
            f"cancel accepted: orderID={order.order_id} "
            f"clOrdID={cancelled.cl_ord_id} status={cancelled.status}"
        )


if __name__ == "__main__":
    main()
