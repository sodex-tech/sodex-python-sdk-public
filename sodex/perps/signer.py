"""Signer for the Bolt perpetuals engine.

Wraps EVMSigner with the Bolt EIP-712 domain (name ``"futures"``) so that all
signatures produced here are cryptographically bound to the perps engine.
Attempting to replay a perps signature on the spot engine will fail because
the domain separator encodes the engine name.

Usage::

    private_key = bytes.fromhex("your-private-key-hex")
    s = PerpsSigner(chain_id=286623, private_key=private_key)

    req = NewOrderRequest(account_id=1001, symbol_id=101, orders=[...])
    sig = s.sign_new_order_request(req, nonce=1)
    # Attach sig to the HTTP request as the signature header.
"""

from __future__ import annotations

from sodex.common.signer import EVMSigner
from sodex.common.types import (
    PERPS_DOMAIN_NAME,
    ReplaceOrderRequest,
    ScheduleCancelRequest,
    TransferAssetRequest,
    new_eip712_domain,
)

from .types import (
    CancelOrderRequest,
    NewOrderRequest,
    UpdateLeverageRequest,
    UpdateMarginRequest,
)


class PerpsSigner:
    """Signs Bolt perpetuals-engine action requests using the EIP-712 ``"futures"`` domain.

    A single instance can be reused for the lifetime of a session. The private
    key is stored in memory; callers are responsible for its secure handling.
    """

    def __init__(self, chain_id: int, private_key: bytes) -> None:
        """
        Args:
            chain_id:    Must match the chain ID used by the exchange server.
            private_key: Raw 32-byte private key.
        """
        domain = new_eip712_domain(PERPS_DOMAIN_NAME, chain_id)
        self._signer = EVMSigner(domain)
        self._private_key = private_key

    # ── Common actions ────────────────────────────────────────────────────────
    #
    # Available on both the spot and perps engines. Signatures are not
    # interchangeable because each engine uses a different EIP-712 domain.

    def sign_transfer_asset_request(
        self, request: TransferAssetRequest, nonce: int
    ) -> bytes:
        """Sign an inter-account asset transfer request."""
        return self._signer.sign_action(request, nonce, self._private_key)

    def sign_replace_order_request(
        self, request: ReplaceOrderRequest, nonce: int
    ) -> bytes:
        """Sign a batch order-replacement request."""
        return self._signer.sign_action(request, nonce, self._private_key)

    def sign_schedule_cancel_request(
        self, request: ScheduleCancelRequest, nonce: int
    ) -> bytes:
        """Sign a scheduled mass-cancellation request."""
        return self._signer.sign_action(request, nonce, self._private_key)

    # ── Perps-only actions ────────────────────────────────────────────────────

    def sign_new_order_request(self, request: NewOrderRequest, nonce: int) -> bytes:
        """Sign a perpetuals order placement request."""
        return self._signer.sign_action(request, nonce, self._private_key)

    def sign_cancel_order_request(
        self, request: CancelOrderRequest, nonce: int
    ) -> bytes:
        """Sign a batch order-cancellation request."""
        return self._signer.sign_action(request, nonce, self._private_key)

    def sign_update_leverage_request(
        self, request: UpdateLeverageRequest, nonce: int
    ) -> bytes:
        """Sign a position-leverage update request."""
        return self._signer.sign_action(request, nonce, self._private_key)

    def sign_update_margin_request(
        self, request: UpdateMarginRequest, nonce: int
    ) -> bytes:
        """Sign a position-margin adjustment request."""
        return self._signer.sign_action(request, nonce, self._private_key)
