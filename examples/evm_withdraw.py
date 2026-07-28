"""Prepare, submit, and begin tracking a custody or bridge withdrawal.

Usage::

    export SODEX_PRIVATE_KEY=<master wallet key, hex without 0x>
    export SODEX_WITHDRAW_RECEIVER=0x...
    export SODEX_WITHDRAW_AMOUNT=10
    export SODEX_WITHDRAW_ROUTE=custody  # or bridge
    python examples/evm_withdraw.py

Run this only when the funds are already in the ValueChain EVM account. Perps
funds must first move Perps -> Spot, then Spot -> EVM with the transfer APIs.
"""

from __future__ import annotations

import os
from decimal import Decimal

from sodex.client import Client


def main() -> None:
    receiver = os.environ.get("SODEX_WITHDRAW_RECEIVER")
    amount = os.environ.get("SODEX_WITHDRAW_AMOUNT")
    if not receiver or not amount:
        raise SystemExit(
            "SODEX_WITHDRAW_RECEIVER and SODEX_WITHDRAW_AMOUNT are required"
        )

    client = Client.from_env()
    chain = os.environ.get("SODEX_CHAIN", "BASE_ETH")
    request = client.prepare_evm_withdraw(
        coin=os.environ.get("SODEX_COIN", "USDC"),
        chain=chain,
        receiver=receiver,
        amount=Decimal(amount),
        withdrawal_type=os.environ.get("SODEX_WITHDRAW_ROUTE", "custody"),
    )
    submission = client.submit_evm_withdraw(client.address, request)
    print(
        f"submitted ValueChain tx={submission.tx_hash} "
        f"senderNonce={submission.sender_nonce}"
    )
    print(
        "submission is not final; poll get_withdraw_status() until external completion"
    )


if __name__ == "__main__":
    main()
