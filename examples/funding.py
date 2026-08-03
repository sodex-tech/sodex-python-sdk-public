"""Discover a deposit route, resolve its destination, and track the tx hash.

Lifecycle: discover route -> get/create custody address or select bridge ->
submit with the source-chain wallet -> wait for Gateway indexing.

The source-chain transaction is intentionally outside this example: Gateway
publishes the destination and route metadata, but not one universal wallet or
bridge transaction ABI. Pass the resulting hash back as SODEX_DEPOSIT_TX_HASH.

Usage::

    export SODEX_ACCOUNT_ADDRESS=0x...       # or provide SODEX_PRIVATE_KEY
    export SODEX_COIN=USDC
    export SODEX_CHAIN=BASE_ETH
    export SODEX_DEPOSIT_ROUTE=custody     # or bridge
    export SODEX_DEPOSIT_TX_HASH=0x...       # optional
    export SODEX_WAIT_SECONDS=120            # optional polling timeout
    python examples/funding.py
"""

from __future__ import annotations

import os
import time

from sodex.client import Client


def wait_for_deposit_address(
    client: Client, chain: str, timeout: float, interval: float
):
    """Wait until custody provisioning leaves Processing, rejecting unsafe states."""
    deadline = time.monotonic() + timeout
    address = client.ensure_deposit_address(chain)
    while address.status == "Processing" and time.monotonic() < deadline:
        time.sleep(interval)
        address = client.get_deposit_address(client.account_address, chain)
    if address.status == "Suspicious":
        raise RuntimeError("custody deposit address is Suspicious and must not be used")
    if address.status != "Enabled" or not address.address:
        raise TimeoutError(
            f"custody address is not ready: status={address.status or '<empty>'}"
        )
    return address


def wait_for_deposit(
    client: Client, chain: str, tx_hash: str, timeout: float, interval: float
):
    """Wait until Gateway indexes at least one record for the source-chain hash."""
    deadline = time.monotonic() + timeout
    while True:
        history = client.get_deposit_status(chain, tx_hash)
        if history.total > 0:
            return history
        if time.monotonic() >= deadline:
            raise TimeoutError(f"deposit is not indexed yet: {tx_hash}")
        time.sleep(interval)


def main() -> None:
    coin = os.environ.get("SODEX_COIN", "USDC")
    chain = os.environ.get("SODEX_CHAIN", "BASE_ETH")
    route_type = os.environ.get("SODEX_DEPOSIT_ROUTE", "custody").lower()
    if route_type not in ("custody", "bridge"):
        raise SystemExit("SODEX_DEPOSIT_ROUTE must be custody or bridge")
    timeout = float(os.environ.get("SODEX_WAIT_SECONDS", "120"))
    interval = float(os.environ.get("SODEX_POLL_SECONDS", "5"))
    client = Client.from_env()
    asset, route = client.get_transfer_route(coin, chain)

    print(
        f"{asset.coin}/{route.chain}: custody={route.custody_available} "
        f"bridge={route.bridge_available} minDeposit={route.min_deposit_amount}"
    )
    deposit_hash = os.environ.get("SODEX_DEPOSIT_TX_HASH")
    if deposit_hash:
        try:
            history = wait_for_deposit(
                client, route.chain, deposit_hash, timeout, interval
            )
        except TimeoutError:
            print(
                "deposit is still pending or not indexed; re-run with "
                f"SODEX_DEPOSIT_TX_HASH={deposit_hash}"
            )
            return
        print(f"deposit indexed: matches={history.total}")
        for record in history.records:
            print(
                f"status={record.status} amount={record.amount} "
                f"txHash={record.tx_hash}"
            )
        return

    if route_type == "custody":
        if not route.custody_available:
            raise RuntimeError(f"custody deposit is unavailable on {route.chain}")
        if not client.account_address:
            raise SystemExit(
                "SODEX_ACCOUNT_ADDRESS or SODEX_PRIVATE_KEY is required for custody"
            )
        address = wait_for_deposit_address(client, route.chain, timeout, interval)
        print(f"custody deposit address={address.address} status={address.status}")
    else:
        if not route.bridge_available:
            raise RuntimeError(f"bridge deposit is unavailable on {route.chain}")
        print(
            f"bridge contract={route.bridge_address}; construct/sign the source-chain "
            "bridge transaction with the chain wallet"
        )


if __name__ == "__main__":
    main()
