"""EVMSigner: the engine-agnostic EIP-712 signing and verification core.

All signing is delegated to the engine-specific signer wrappers in
``sodex.spot.signer`` and ``sodex.perps.signer``; this module contains
only the cryptographic core.
"""

from __future__ import annotations

from eth_keys import keys as eth_keys

from .enums import SignatureType
from .types import (
    ActionPayloadParams,
    EIP712Domain,
    ExchangeAction,
    InvalidSignatureLengthError,
    InvalidSignatureTypeError,
    action_payload_hash,
)


class EVMSigner:
    """Performs EIP-712 signing and signature verification for a single engine domain.

    One EVMSigner instance is created per engine (spot or perps). The domain bakes
    the engine name and chain ID into every signature, so a signature produced for
    the spot engine is cryptographically invalid on the perps engine and vice versa.
    """

    def __init__(self, domain: EIP712Domain) -> None:
        self._domain = domain

    def sign_action(
        self,
        request: ActionPayloadParams,
        nonce: int,
        private_key: bytes,
    ) -> bytes:
        """Sign an action request and return a 66-byte wire-format signature.

        Signing pipeline:

        1. Serialize ``request`` as ``ActionPayload{type, params}`` and hash
           the compact JSON to produce a 32-byte ``payloadHash``.
        2. Build ``ExchangeAction{payloadHash, nonce}`` and compute the EIP-712
           digest: ``keccak256(0x19 0x01 | domainSeparator | structHash)``.
        3. ECDSA-sign the digest with ``private_key`` (RFC 6979 deterministic,
           produces 65 bytes: r ‖ s ‖ v where v ∈ {0, 1}).
        4. Prepend the ``SignatureType`` byte (``0x01``) to form the 66-byte
           wire-format signature.

        Args:
            request:     Any signable request that implements ActionPayloadParams.
            nonce:       The caller's next valid nonce for this engine. The exchange
                         rejects requests with a stale or already-consumed nonce.
            private_key: Raw 32-byte private key (e.g. ``bytes.fromhex("0123...")``).

        Returns:
            66-byte wire-format signature: ``[SignatureType byte | 65-byte ECDSA sig]``.
        """
        payload_hash = action_payload_hash(request)
        digest = ExchangeAction(payload_hash, nonce).hash(self._domain)

        pk = eth_keys.PrivateKey(private_key)
        sig = pk.sign_msg_hash(digest)
        return bytes([SignatureType.EIP712]) + sig.to_bytes()

    def recover_signer(
        self,
        request: ActionPayloadParams,
        nonce: int,
        signature: bytes,
    ) -> str:
        """Recover the checksummed Ethereum address that produced a wire-format signature.

        This is the verification counterpart of :meth:`sign_action`. It reconstructs
        the EIP-712 digest and uses ECDSA public-key recovery to determine the signer.

        Args:
            request:   The same request object used when signing.
            nonce:     The same nonce used when signing.
            signature: 66-byte wire-format signature.

        Returns:
            Checksummed Ethereum address string, e.g. ``"0xAbCd...1234"``.

        Raises:
            InvalidSignatureLengthError: if ``len(signature) != 66``.
            InvalidSignatureTypeError:  if ``signature[0]`` is not ``SignatureType.EIP712``.
        """
        if len(signature) != 66:
            raise InvalidSignatureLengthError(
                f"invalid signature length: {len(signature)}, expected 66"
            )
        if signature[0] != SignatureType.EIP712:
            raise InvalidSignatureTypeError(
                f"invalid signature type: {signature[0]:#04x}, "
                f"expected {SignatureType.EIP712:#04x}"
            )

        payload_hash = action_payload_hash(request)
        digest = ExchangeAction(payload_hash, nonce).hash(self._domain)

        # signature[1:] strips the leading SignatureType byte, leaving 65-byte ECDSA sig.
        pub_key = eth_keys.Signature(signature[1:]).recover_public_key_from_msg_hash(digest)
        return pub_key.to_checksum_address()
