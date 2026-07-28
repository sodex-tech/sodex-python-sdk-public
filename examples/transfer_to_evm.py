"""Run one explicit internal-transfer step before an external withdrawal.

Usage::

    export SODEX_PRIVATE_KEY=<wallet or registered API key, hex without 0x>
    export SODEX_TRANSFER_STEP=perps-to-spot  # or spot-to-evm
    export SODEX_COIN=vUSDC
    export SODEX_AMOUNT=10
    python examples/transfer_to_evm.py

The SDK discovers the user's primary account and coin ID, and applies the
Gateway treasury route. Wait for each transfer to settle before the next step.
"""

from __future__ import annotations

import os
from decimal import Decimal

from sodex.client import Client


def main() -> None:
    try:
        amount = Decimal(os.environ["SODEX_AMOUNT"])
    except (KeyError, ValueError) as exc:
        raise SystemExit("SODEX_AMOUNT is required and must be numeric") from exc

    step = os.environ.get("SODEX_TRANSFER_STEP")
    if step not in ("perps-to-spot", "spot-to-evm"):
        raise SystemExit("SODEX_TRANSFER_STEP must be perps-to-spot or spot-to-evm")

    client = Client.from_env()
    coin = os.environ.get("SODEX_COIN", "vUSDC")
    receipt = (
        client.transfer_perps_to_spot(coin, amount)
        if step == "perps-to-spot"
        else client.transfer_spot_to_evm(coin, amount)
    )
    print(f"accepted transfer id={receipt.id}; wait for settlement before continuing")


if __name__ == "__main__":
    main()
