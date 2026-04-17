"""Place and then cancel a single perps limit order on testnet.

Usage::

    export SODEX_PRIVATE_KEY=<hex, no 0x>
    export SODEX_ACCOUNT_ID=<your account ID>
    python examples/trade.py

The order is placed far below market (GTC @ $1000) so it rests safely in the
book; the example then cancels it. No fill should occur.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal

from sodex.client import Client, Config
from sodex.common.enums import OrderSide, PositionSide, TimeInForce
from sodex.perps.types import CancelOrder, CancelOrderRequest


def main() -> None:
    pk_hex = os.environ.get("SODEX_PRIVATE_KEY")
    if not pk_hex:
        raise SystemExit("SODEX_PRIVATE_KEY is required (hex, no 0x prefix)")
    try:
        account_id = int(os.environ["SODEX_ACCOUNT_ID"])
    except (KeyError, ValueError) as e:
        raise SystemExit("SODEX_ACCOUNT_ID must be a positive integer") from e

    c = Client(Config(
        base_url=Client.TESTNET_BASE_URL,
        chain_id=Client.TESTNET_CHAIN_ID,
        private_key=bytes.fromhex(pk_hex),
    ))
    print(f"Signer address: {c.address}\n")

    # ── 1. Discover the symbol ID for BTC-USD ────────────────────────────────
    symbols = c.perps_symbols()
    btc = next((s for s in symbols if s.symbol == "BTC-USD"), None)
    if btc is None:
        raise SystemExit("BTC-USD not found in perps_symbols()")
    print(f"BTC-USD symbolID={btc.symbol_id} tickSize={btc.tick_size} stepSize={btc.step_size}\n")

    # ── 2. Place a GTC limit buy far below market (won't fill) ───────────────
    cl_ord_id = f"demo-{int(time.time() * 1000)}"
    placed = c.place_perps_limit_order(
        account_id=account_id,
        symbol_id=btc.symbol_id,
        cl_ord_id=cl_ord_id,
        side=OrderSide.BUY,
        position_side=PositionSide.LONG,
        time_in_force=TimeInForce.GTC,
        price=Decimal("1000"),        # intentionally far below market
        quantity=Decimal("0.001"),
        reduce_only=False,
    )
    if not placed:
        raise SystemExit("no results returned from place")
    order = placed[0]
    print(f"Placed: orderID={order.order_id} clOrdID={order.cl_ord_id} status={order.status}\n")

    # ── 3. Cancel it ─────────────────────────────────────────────────────────
    cancelled = c.cancel_perps_orders(CancelOrderRequest(
        account_id=account_id,
        cancels=[CancelOrder(symbol_id=btc.symbol_id, order_id=order.order_id)],
    ))
    for r in cancelled:
        print(f"Cancelled: clOrdID={r.cl_ord_id} status={r.status}")


if __name__ == "__main__":
    main()
