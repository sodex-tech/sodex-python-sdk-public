"""Inspect funding routes and run deposit/withdrawal status lookups.

Usage::

    export SODEX_ACCOUNT_ADDRESS=0x...       # or provide SODEX_PRIVATE_KEY
    export SODEX_COIN=USDC
    export SODEX_CHAIN=BASE_ETH
    export SODEX_DEPOSIT_TX_HASH=0x...       # optional
    export SODEX_WITHDRAW_TX_HASH=0x...      # optional
    python examples/funding.py

The example only creates a custody deposit address when the query returns an
empty address. A bridge route is reported separately from custody; submitting
an external-chain bridge transaction remains the calling wallet's job.
"""

from __future__ import annotations

import os

from sodex.client import Client


def main() -> None:
    coin = os.environ.get("SODEX_COIN", "USDC")
    chain = os.environ.get("SODEX_CHAIN", "BASE_ETH")
    client = Client.from_env()
    if not client.account_address:
        raise SystemExit(
            "SODEX_ACCOUNT_ADDRESS or SODEX_PRIVATE_KEY is required for this user flow"
        )

    asset, route = client.get_transfer_route(coin, chain)

    print(
        f"{asset.coin}/{route.chain}: custody={route.custody_available} "
        f"bridge={route.bridge_available} minDeposit={route.min_deposit_amount}"
    )
    if route.custody_available:
        address = client.ensure_deposit_address(route.chain)
        print(
            f"custody deposit address={address.address or '<provisioning>'} "
            f"status={address.status}"
        )
    if route.bridge_available:
        print(
            f"bridge contract={route.bridge_address}; construct/sign the source-chain "
            "bridge transaction with the chain wallet"
        )

    deposit_hash = os.environ.get("SODEX_DEPOSIT_TX_HASH")
    if deposit_hash:
        history = client.get_deposit_status(route.chain, deposit_hash)
        print(f"deposit matches={history.total}")
        for record in history.records:
            print(record.status, record.amount, record.tx_hash)

    withdraw_hash = os.environ.get("SODEX_WITHDRAW_TX_HASH")
    if withdraw_hash:
        history = client.get_withdraw_status(route.chain, tx_hash=withdraw_hash)
        print(f"withdrawal matches={history.total}")
        for record in history.records:
            print(record.status, record.amount, record.tx_hash, record.fail_reason)


if __name__ == "__main__":
    main()
