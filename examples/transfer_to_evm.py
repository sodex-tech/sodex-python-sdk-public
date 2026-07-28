"""Run one explicit internal-transfer step before an external withdrawal.

Usage::

    export SODEX_PRIVATE_KEY=<wallet or registered API key, hex without 0x>
    export SODEX_API_KEY_NAME=default
    export SODEX_TRANSFER_STEP=perps-to-spot  # or spot-to-evm
    export SODEX_FROM_ACCOUNT_ID=1001
    export SODEX_TO_ACCOUNT_ID=999
    export SODEX_COIN_ID=0
    export SODEX_AMOUNT=10
    python examples/transfer_to_evm.py

Account IDs are deliberately explicit: the SDK does not guess the deployment's
treasury or destination account. Wait for each transfer to settle before running
the next step.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal

from sodex.client import Client, Config
from sodex.common.enums import TransferAssetType
from sodex.common.types import TransferAssetRequest


def main() -> None:
    private_key_hex = os.environ.get("SODEX_PRIVATE_KEY")
    if not private_key_hex:
        raise SystemExit("SODEX_PRIVATE_KEY is required")
    try:
        from_account_id = int(os.environ["SODEX_FROM_ACCOUNT_ID"])
        to_account_id = int(os.environ["SODEX_TO_ACCOUNT_ID"])
        coin_id = int(os.environ["SODEX_COIN_ID"])
        amount = Decimal(os.environ["SODEX_AMOUNT"])
    except (KeyError, ValueError) as exc:
        raise SystemExit("transfer account IDs, coin ID, and amount are required") from exc

    step = os.environ.get("SODEX_TRANSFER_STEP")
    if step == "perps-to-spot":
        transfer_type = TransferAssetType.SPOT_WITHDRAW
    elif step == "spot-to-evm":
        transfer_type = TransferAssetType.EVM_WITHDRAW
    else:
        raise SystemExit("SODEX_TRANSFER_STEP must be perps-to-spot or spot-to-evm")

    client = Client(
        Config(
            private_key=bytes.fromhex(private_key_hex),
            api_key_name=os.environ.get("SODEX_API_KEY_NAME", ""),
        )
    )
    request = TransferAssetRequest(
        id=int(time.time() * 1000),
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        coin_id=coin_id,
        amount=amount,
        type=transfer_type,
    )
    receipt = (
        client.perps_transfer(request)
        if step == "perps-to-spot"
        else client.spot_transfer(request)
    )
    print(f"accepted transfer id={receipt.id}; wait for settlement before continuing")


if __name__ == "__main__":
    main()
