"""Place and then cancel a single perps limit order on testnet.

Usage::

    export SODEX_PRIVATE_KEY=<hex, no 0x>
    python examples/trade.py

The order is placed far below market (GTC @ $1000) so it rests safely in the
book; the example then cancels it. No fill should occur.
"""

from __future__ import annotations

import os
from decimal import Decimal

from sodex.client import Client


def main() -> None:
    c = Client.from_env(testnet=True)
    print(f"Signer address: {c.address}\n")

    # Account and symbol IDs are discovered automatically.
    order = c.perps_order(
        "BTC-USD",
        True,
        Decimal(os.environ.get("SODEX_DEMO_QUANTITY", "0.001")),
        limit_price=Decimal(os.environ.get("SODEX_DEMO_PRICE", "1000")),
    )
    print(
        f"Placed: orderID={order.order_id} clOrdID={order.cl_ord_id} status={order.status}\n"
    )

    cancelled = c.cancel_perps_order("BTC-USD", order_id=order.order_id)
    print(f"Cancelled: clOrdID={cancelled.cl_ord_id} status={cancelled.status}")


if __name__ == "__main__":
    main()
