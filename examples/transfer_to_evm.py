"""Run and settle one EVM, Spot, or Perps balance transfer.

Usage::

    export SODEX_PRIVATE_KEY=<master wallet or registered API key>
    export SODEX_TRANSFER_STEP=evm-to-perps
    export SODEX_COIN=USDC
    export SODEX_AMOUNT=10
    python examples/transfer_to_evm.py

Supported steps are evm-to-spot, evm-to-perps, spot-to-perps,
perps-to-spot, and spot-to-evm. EVM-originating steps require the master wallet;
engine-only steps may use a registered API key.
"""

from __future__ import annotations

import os
from decimal import Decimal

from sodex.client import Client

TRANSFER_STEPS = {
    "evm-to-spot",
    "evm-to-perps",
    "spot-to-perps",
    "perps-to-spot",
    "spot-to-evm",
}


def balance_total(balances, coin: str):
    """Return the exact string balance for one engine coin, if present."""
    balance = next(
        (item for item in balances if item.coin.lower() == coin.lower()), None
    )
    return balance.total if balance is not None else None


def main() -> None:
    try:
        amount = Decimal(os.environ["SODEX_AMOUNT"])
    except (KeyError, ValueError) as exc:
        raise SystemExit("SODEX_AMOUNT is required and must be numeric") from exc

    step = os.environ.get("SODEX_TRANSFER_STEP", "spot-to-perps")
    if step not in TRANSFER_STEPS:
        raise SystemExit(
            "SODEX_TRANSFER_STEP must be one of " + ", ".join(sorted(TRANSFER_STEPS))
        )

    client = Client.from_env()
    if not client.address:
        raise SystemExit("SODEX_PRIVATE_KEY is required")
    external_coin = os.environ.get("SODEX_COIN", "USDC")
    assets = client.get_transfer_configs(external_coin)
    asset = next(
        (item for item in assets if item.coin.lower() == external_coin.lower()), None
    )
    if asset is None:
        raise RuntimeError(f"unsupported transfer coin: {external_coin}")
    engine_coin = asset.asset_name
    if not engine_coin:
        raise RuntimeError(
            f"transfer config does not publish the engine coin for {asset.coin}"
        )
    timeout = float(os.environ.get("SODEX_WAIT_SECONDS", "120"))
    interval = float(os.environ.get("SODEX_POLL_SECONDS", "3"))
    user = client.account_address

    if step in ("evm-to-spot", "evm-to-perps"):
        if client.address.lower() != user.lower():
            raise RuntimeError("EVM-originating transfers require the master wallet key")
        destination = "spot" if step == "evm-to-spot" else "perps"
        current = (
            client.spot_balances(user)
            if destination == "spot"
            else client.perps_balances(user)
        )
        previous = balance_total(current, engine_coin)
        submission = client.deposit_evm_to_engine(
            external_coin,
            amount,
            destination,
            recipient=user,
            timeout=timeout,
            interval=interval,
        )
        balances = (
            client.wait_for_spot_balance_change(
                engine_coin,
                previous,
                timeout=timeout,
                interval=interval,
            )
            if destination == "spot"
            else client.wait_for_perps_balance_change(
                engine_coin,
                previous,
                timeout=timeout,
                interval=interval,
            )
        )
        print(
            f"EVM -> {destination} settled: depositTx={submission.deposit_tx_hash} "
            f"balance={balance_total(balances, engine_coin)}"
        )
        return

    if step == "spot-to-perps":
        previous = balance_total(client.perps_balances(user), engine_coin)
        receipt = client.transfer_spot_to_perps(engine_coin, amount)
        balances = client.wait_for_perps_balance_change(
            engine_coin, previous, timeout=timeout, interval=interval
        )
    elif step == "perps-to-spot":
        previous = balance_total(client.spot_balances(user), engine_coin)
        receipt = client.transfer_perps_to_spot(engine_coin, amount)
        balances = client.wait_for_spot_balance_change(
            engine_coin, previous, timeout=timeout, interval=interval
        )
    else:
        previous_evm = client.get_valuechain_balance(asset.token_address, user)
        receipt = client.transfer_spot_to_evm(engine_coin, amount)
        current_evm = client.wait_for_evm_balance_increase(
            asset.token_address,
            previous_evm,
            user_address=user,
            timeout=timeout,
            interval=interval,
        )
        print(f"Spot -> EVM settled: transferID={receipt.id} rawBalance={current_evm}")
        return

    print(
        f"{step} settled: transferID={receipt.id} "
        f"balance={balance_total(balances, engine_coin)}"
    )


if __name__ == "__main__":
    main()
