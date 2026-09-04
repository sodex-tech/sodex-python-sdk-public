"""Approve a builder's maximum fee rate on both Spot and Perps.

Usage::

    export SODEX_PRIVATE_KEY=<master wallet key>
    export SODEX_BUILDER_ID=<builder account ID>
    export SODEX_BUILDER_FEE_RATE=<maximum fee rate>
    python examples/approve_builder_fee.py
"""

from __future__ import annotations

import os

from sodex.client import Client


def required_int(name: str) -> int:
    value = os.environ.get(name)
    if value is None:
        raise SystemExit(f"{name} is required")
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc


def main() -> None:
    master = Client.from_env()
    if not master.address or master.address.lower() != master.account_address.lower():
        raise SystemExit("builder approval requires the master wallet private key")

    account_id = master.primary_account_id()
    builder_id = required_int("SODEX_BUILDER_ID")
    max_fee_rate = required_int("SODEX_BUILDER_FEE_RATE")
    master.approve_builder_fee(
        builder_id,
        max_fee_rate,
        account_id=account_id,
    )
    print(
        f"approved builder {builder_id} with max fee rate {max_fee_rate} "
        f"for account {account_id} on Spot and Perps"
    )


if __name__ == "__main__":
    main()
