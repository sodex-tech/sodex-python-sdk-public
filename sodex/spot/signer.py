"""Signer for the Spark spot engine.

Wraps EVMSigner with the Spark EIP-712 domain (name ``"spot"``) so that all
signatures produced here are cryptographically bound to the spot engine.
Attempting to replay a spot signature on the perps engine will fail because
the domain separator encodes the engine name.

Usage::

    private_key = bytes.fromhex("your-private-key-hex")
    s = SpotSigner(chain_id=286623, private_key=private_key)

    req = BatchNewOrderRequest(account_id=1001, orders=[...])
    sig = s.sign_batch_new_order_request(req, nonce=42)
    # Attach sig to the HTTP request as the signature header.
"""

from __future__ import annotations

from sodex.common.signer import EVMSigner
from sodex.common.types import (
    CancelTwapOrderRequest,
    NewTwapOrderRequest,
    ReplaceOrderRequest,
    ScheduleCancelRequest,
    SPOT_DOMAIN_NAME,
    TransferAssetRequest,
    new_eip712_domain,
)

from .types import BatchCancelOrderRequest, BatchNewOrderRequest


class SpotSigner:
    """Signs Spark spot-engine action requests using the EIP-712 ``"spot"`` domain.

    A single instance can be reused for the lifetime of a session. The private
    key is stored in memory; callers are responsible for its secure handling.
    """

    def __init__(self, chain_id: int, private_key: bytes) -> None:
        """
        Args:
            chain_id:    Must match the chain ID used by the exchange server.
            private_key: Raw 32-byte private key.
        """
        domain = new_eip712_domain(SPOT_DOMAIN_NAME, chain_id)
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

    def sign_new_twap_order_request(
        self, request: NewTwapOrderRequest, nonce: int
    ) -> bytes:
        """Sign a spot TWAP placement request."""
        return self._signer.sign_action(request, nonce, self._private_key)

    def sign_cancel_twap_order_request(
        self, request: CancelTwapOrderRequest, nonce: int
    ) -> bytes:
        """Sign a spot TWAP cancellation request."""
        return self._signer.sign_action(request, nonce, self._private_key)

    # ── Spot-only actions ─────────────────────────────────────────────────────

    def sign_batch_new_order_request(
        self, request: BatchNewOrderRequest, nonce: int
    ) -> bytes:
        """Sign a batch new-order placement request."""
        return self._signer.sign_action(request, nonce, self._private_key)

    def sign_batch_cancel_order_request(
        self, request: BatchCancelOrderRequest, nonce: int
    ) -> bytes:
        """Sign a batch order-cancellation request."""
        return self._signer.sign_action(request, nonce, self._private_key)
