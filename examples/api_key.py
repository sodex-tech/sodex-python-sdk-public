"""Generate and register an API key, then configure a trading client.

Usage::

    export SODEX_PRIVATE_KEY=<master wallet key, hex without 0x>
    python examples/api_key.py

The generated private key is intentionally not printed or persisted. Replace
the placeholder secret-manager call with the integration's secure storage.
"""

from __future__ import annotations

from sodex.client import Client
from sodex.common.enums import APIKeyPermission


def save_to_secret_manager(name: str, private_key: bytes) -> None:
    """Replace with KMS/Vault/Secrets Manager integration before real use."""
    del name, private_key


def main() -> None:
    master = Client.from_env()
    generated, trading = master.approve_agent(
        "my-bot",
        permissions=APIKeyPermission.TRADE | APIKeyPermission.CANCEL,
    )
    save_to_secret_manager(generated.name, generated.private_key)
    print(
        f"registered API key {generated.name} address={trading.address} "
        f"for master={trading.account_address}"
    )


if __name__ == "__main__":
    main()
