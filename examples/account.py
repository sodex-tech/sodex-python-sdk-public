"""Query balances, open orders, and positions for a user.

Usage::

    # Read-only — address only, no signing required.
    export SODEX_ADDRESS=0x...
    python examples/account.py

    # Or: sign with a private key and auto-derive the address.
    export SODEX_PRIVATE_KEY=<hex, no 0x>
    python examples/account.py
"""

from __future__ import annotations

import os

from sodex.client import Client


def main() -> None:
    c = Client.from_env()
    address = os.environ.get("SODEX_ADDRESS") or c.account_address

    if not address:
        raise SystemExit("either SODEX_PRIVATE_KEY or SODEX_ADDRESS must be set")

    print(f"Querying {address}\n")

    # ── Perps ────────────────────────────────────────────────────────────────
    print("── Perps ──────────────────────────────────────────────────────")

    balances = c.perps_balances(address)
    print(f"Balances ({len(balances)}):")
    for b in balances:
        print(f"  {b.coin:<8} total={b.total:<20} locked={b.locked}")

    positions = c.perps_positions(address)
    print(f"\nOpen positions ({len(positions)}):")
    for p in positions:
        print(
            f"  {p.symbol:<12} side={p.position_side:<5} qty={p.quantity:<12} "
            f"entry={p.entry_price:<12} realizedPnL={p.realized_pnl:<12} "
            f"leverage={p.leverage}"
        )

    orders = c.perps_orders(address)
    print(f"\nOpen orders ({len(orders)}):")
    for o in orders:
        print(
            f"  [{o.order_id}] {o.symbol:<12} {o.side:<4} {o.type:<6} "
            f"qty={o.orig_qty:<10} price={o.price:<10} status={o.status}"
        )

    # ── Spot ─────────────────────────────────────────────────────────────────
    print("\n── Spot ───────────────────────────────────────────────────────")

    info = c.spot_account_info(address)
    print(f"Spot account: aid={info.account_id} uid={info.user_id}")

    spot_balances = c.spot_balances(address)
    print(f"Balances ({len(spot_balances)}):")
    for b in spot_balances:
        print(f"  {b.coin:<8} total={b.total:<20} locked={b.locked}")


if __name__ == "__main__":
    main()
