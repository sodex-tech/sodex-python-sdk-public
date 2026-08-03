"""Prepare, submit, and track a custody or bridge withdrawal to completion.

Lifecycle: validate route -> sign ValueChain permit -> submit -> wait for an
external terminal status. Submission is not final settlement; save the hash so
tracking can be resumed without submitting a second withdrawal.

Usage::

    export SODEX_PRIVATE_KEY=<master wallet key, hex without 0x>
    export SODEX_WITHDRAW_RECEIVER=0x...
    export SODEX_WITHDRAW_AMOUNT=10
    export SODEX_WITHDRAW_ROUTE=custody  # or bridge
    python examples/evm_withdraw.py

Resume only::

    export SODEX_CHAIN=BASE_ETH
    export SODEX_WITHDRAW_TX_HASH=0x...
    python examples/evm_withdraw.py

Run this only when the funds are already in the ValueChain EVM account. Perps
funds must first move Perps -> Spot, then Spot -> EVM with the transfer APIs.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Optional

from sodex.client import Client

TERMINAL_STATUSES = {
    "success",
    "succeeded",
    "failed",
    "rejected",
    "cancelled",
    "canceled",
}


def wait_for_withdrawal(
    client: Client,
    chain: str,
    *,
    tx_hash: Optional[str],
    withdraw_id: Optional[str],
    timeout: float,
    interval: float,
):
    """Poll one withdrawal reference until Gateway reports a terminal record."""
    deadline = time.monotonic() + timeout
    while True:
        history = client.get_withdraw_status(
            chain, tx_hash=tx_hash, withdraw_id=withdraw_id
        )
        if any(
            record.status.lower() in TERMINAL_STATUSES
            for record in history.records
        ):
            return history
        if time.monotonic() >= deadline:
            reference = withdraw_id or tx_hash or "<missing>"
            raise TimeoutError(f"withdrawal is not terminal yet: {reference}")
        time.sleep(interval)


def print_history(history) -> None:
    """Print exact Gateway status fields without converting amounts to float."""
    print(f"withdrawal matches={history.total}")
    for record in history.records:
        print(
            f"status={record.status} amount={record.amount} txHash={record.tx_hash} "
            f"withdrawId={record.withdraw_id} failReason={record.fail_reason or '-'}"
        )


def main() -> None:
    client = Client.from_env()
    chain = os.environ.get("SODEX_CHAIN", "BASE_ETH")
    timeout = float(os.environ.get("SODEX_WAIT_SECONDS", "120"))
    interval = float(os.environ.get("SODEX_POLL_SECONDS", "5"))
    existing_hash = os.environ.get("SODEX_WITHDRAW_TX_HASH")
    existing_id = os.environ.get("SODEX_WITHDRAW_ID")
    if existing_hash or existing_id:
        try:
            history = wait_for_withdrawal(
                client,
                chain,
                tx_hash=existing_hash,
                withdraw_id=existing_id,
                timeout=timeout,
                interval=interval,
            )
        except TimeoutError:
            print(
                "withdrawal is still pending; re-run with "
                f"SODEX_WITHDRAW_{'ID' if existing_id else 'TX_HASH'}="
                f"{existing_id or existing_hash}"
            )
            return
        print_history(history)
        return

    receiver = os.environ.get("SODEX_WITHDRAW_RECEIVER")
    amount = os.environ.get("SODEX_WITHDRAW_AMOUNT")
    if not receiver or not amount:
        raise SystemExit(
            "SODEX_WITHDRAW_RECEIVER and SODEX_WITHDRAW_AMOUNT are required"
        )

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
    try:
        history = wait_for_withdrawal(
            client,
            chain,
            tx_hash=submission.tx_hash,
            withdraw_id=None,
            timeout=timeout,
            interval=interval,
        )
    except TimeoutError:
        print(
            "withdrawal is still pending; re-run with "
            f"SODEX_WITHDRAW_TX_HASH={submission.tx_hash}"
        )
        return
    print_history(history)


if __name__ == "__main__":
    main()
