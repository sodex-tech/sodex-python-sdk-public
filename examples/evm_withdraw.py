"""Prepare, submit, and track a custody or bridge withdrawal to completion.

Lifecycle: validate route -> sign ValueChain permit -> submit -> wait for an
external terminal status. Submission is not final settlement; save the hash so
tracking can be resumed without submitting a second withdrawal.

Usage::

    export SODEX_PRIVATE_KEY=<master wallet key, hex without 0x>
    export SODEX_WITHDRAW_RECEIVER=0x...
    export SODEX_WITHDRAW_AMOUNT=10
    export SODEX_WITHDRAW_ROUTE=custody  # or bridge
    export SODEX_WITHDRAW_GAS_MODE=sponsored  # or self-paid
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
from decimal import Decimal

from sodex.client import Client, WaitTimeoutError


def print_history(history) -> None:
    """Print exact Gateway status fields without converting amounts to float."""
    print(f"withdrawal matches={history.total}")
    for record in history.records:
        print(
            f"status={record.status} amount={record.amount} txHash={record.tx_hash} "
            f"withdrawId={record.withdraw_id} failReason={record.fail_reason or '-'}"
        )


def require_success(client: Client, history) -> None:
    """Raise when any terminal withdrawal record represents a failure."""
    failed = [
        record
        for record in history.records
        if not client.is_successful_transfer_status(record.status)
    ]
    if failed:
        statuses = ", ".join(record.status for record in failed)
        raise RuntimeError(f"withdrawal failed with terminal status: {statuses}")


def main() -> None:
    client = Client.from_env()
    chain = os.environ.get("SODEX_CHAIN", "BASE_ETH")
    timeout = float(os.environ.get("SODEX_WAIT_SECONDS", "120"))
    interval = float(os.environ.get("SODEX_POLL_SECONDS", "5"))
    existing_hash = os.environ.get("SODEX_WITHDRAW_TX_HASH")
    existing_id = os.environ.get("SODEX_WITHDRAW_ID")
    if existing_hash or existing_id:
        try:
            history = client.wait_for_withdrawal(
                chain,
                tx_hash=existing_hash,
                withdraw_id=existing_id,
                timeout=timeout,
                interval=interval,
            )
        except WaitTimeoutError:
            print(
                "withdrawal is still pending; re-run with "
                f"SODEX_WITHDRAW_{'ID' if existing_id else 'TX_HASH'}="
                f"{existing_id or existing_hash}"
            )
            return
        print_history(history)
        require_success(client, history)
        return

    receiver = os.environ.get("SODEX_WITHDRAW_RECEIVER")
    amount = os.environ.get("SODEX_WITHDRAW_AMOUNT")
    if not receiver or not amount:
        raise SystemExit(
            "SODEX_WITHDRAW_RECEIVER and SODEX_WITHDRAW_AMOUNT are required"
        )
    if not client.address or client.address.lower() != client.account_address.lower():
        raise SystemExit("withdrawal submission requires the master wallet private key")

    request = client.prepare_evm_withdraw(
        coin=os.environ.get("SODEX_COIN", "USDC"),
        chain=chain,
        receiver=receiver,
        amount=Decimal(amount),
        withdrawal_type=os.environ.get("SODEX_WITHDRAW_ROUTE", "custody"),
    )
    gas_mode = os.environ.get("SODEX_WITHDRAW_GAS_MODE", "sponsored").lower()
    if gas_mode == "sponsored":
        submission = client.submit_evm_withdraw(client.address, request)
        tx_hash = submission.tx_hash
        print(
            f"submitted sponsored ValueChain tx={tx_hash} "
            f"senderNonce={submission.sender_nonce}"
        )
    elif gas_mode == "self-paid":
        tx_hash = client.submit_self_paid_evm_withdraw(
            request, timeout=timeout, interval=interval
        )
        print(f"submitted self-paid ValueChain tx={tx_hash}")
    else:
        raise SystemExit("SODEX_WITHDRAW_GAS_MODE must be sponsored or self-paid")
    try:
        history = client.wait_for_withdrawal(
            chain,
            tx_hash=tx_hash,
            withdraw_id=None,
            timeout=timeout,
            interval=interval,
        )
    except WaitTimeoutError:
        print(
            "withdrawal is still pending; re-run with "
            f"SODEX_WITHDRAW_TX_HASH={tx_hash}"
        )
        return
    print_history(history)
    require_success(client, history)


if __name__ == "__main__":
    main()
