"""List, register, or revoke a unified Spot/Perps API key.

Usage::

    export SODEX_PRIVATE_KEY=<master wallet key>
    export SODEX_API_KEY_ACTION=register  # list | register | revoke
    export SODEX_TARGET_API_KEY_NAME=my-bot
    python examples/api_key.py

The generated private key is intentionally not printed or persisted. Replace
the placeholder secret-manager call before using registration in production.
"""

from __future__ import annotations

import os

from sodex.client import Client, RevokeAPIKeyRequest


def save_to_secret_manager(name: str, private_key: bytes) -> None:
    """Replace with KMS/Vault/Secrets Manager integration before real use."""
    del name, private_key


def main() -> None:
    action = os.environ.get("SODEX_API_KEY_ACTION", "register").lower()
    if action not in ("list", "register", "revoke"):
        raise SystemExit("SODEX_API_KEY_ACTION must be list, register, or revoke")
    name = os.environ.get("SODEX_TARGET_API_KEY_NAME", "my-bot")
    master = Client.from_env()

    if action == "list":
        keys = master.get_api_keys(name=os.environ.get("SODEX_TARGET_API_KEY_NAME"))
        print("Spot API keys:", keys.spot)
        print("Perps API keys:", keys.perps)
        return

    if not master.address or master.address.lower() != master.account_address.lower():
        raise SystemExit("register/revoke requires the master wallet private key")
    account_id = master.primary_account_id()
    if action == "revoke":
        master.revoke_api_key(
            master.address, RevokeAPIKeyRequest(account_id=account_id, name=name)
        )
        print(f"revoked API key {name} from Spot and Perps")
        return

    generated, trading = master.approve_agent(name, account_id=account_id)
    save_to_secret_manager(generated.name, generated.private_key)
    print(
        f"registered API key {generated.name} address={trading.address} "
        f"for master={trading.account_address}"
    )


if __name__ == "__main__":
    main()
