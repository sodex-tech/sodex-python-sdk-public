"""Generate and register an API key, then configure a trading client.

Usage::

    export SODEX_PRIVATE_KEY=<master wallet key, hex without 0x>
    export SODEX_ACCOUNT_ID=<spot/perps account ID>
    python examples/api_key.py

The generated private key is intentionally not printed or persisted. Replace
the placeholder secret-manager call with the integration's secure storage.
"""

from __future__ import annotations

import os

from sodex.client import AddAPIKeyRequest, Client, Config, generate_api_key
from sodex.common.enums import APIKeyPermission


def save_to_secret_manager(name: str, private_key: bytes) -> None:
    """Replace with KMS/Vault/Secrets Manager integration before real use."""
    del name, private_key


def main() -> None:
    private_key_hex = os.environ.get("SODEX_PRIVATE_KEY")
    account_id_text = os.environ.get("SODEX_ACCOUNT_ID")
    if not private_key_hex or not account_id_text:
        raise SystemExit("SODEX_PRIVATE_KEY and SODEX_ACCOUNT_ID are required")

    master = Client(Config(private_key=bytes.fromhex(private_key_hex)))
    generated = generate_api_key("my-bot")
    master.add_api_key(
        master.address,
        AddAPIKeyRequest(
            account_id=int(account_id_text),
            name=generated.name,
            public_key=generated.address,
            permissions=APIKeyPermission.TRADE | APIKeyPermission.CANCEL,
        ),
    )
    save_to_secret_manager(generated.name, generated.private_key)

    trading = Client(
        Config(
            private_key=generated.private_key,
            api_key_name=generated.name,
            account_address=master.address,
        )
    )
    print(
        f"registered API key {generated.name} address={trading.address} "
        f"for master={trading.account_address}"
    )


if __name__ == "__main__":
    main()
