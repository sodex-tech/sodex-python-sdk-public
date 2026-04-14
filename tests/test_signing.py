"""Signing tests for the Sodex Python SDK.

Test organisation mirrors ``common/signer/evm_signer_test.go`` in the Go SDK.
The same 7-property structure is used:

    1. Wire format     — every signature is exactly 66 bytes with the right type prefix.
    2. Round-trip      — sign followed by recover returns the original signer address.
    3. Cross-engine    — a spot signature cannot verify correctly on the perps domain.
    4. Determinism     — identical inputs always produce the same signature (RFC 6979).
    5. Sensitivity     — changing nonce, action type, or params changes the signature.
    6. Error handling  — malformed inputs raise typed sentinel errors.
    7. Domain caching  — domain_separator() is idempotent and chain-ID-aware.
"""

from decimal import Decimal

import pytest
from eth_keys import keys as eth_keys

from sodex.common.enums import SignatureType, TransferAssetType
from sodex.common.signer import EVMSigner
from sodex.common.types import (
    InvalidSignatureLengthError,
    InvalidSignatureTypeError,
    PERPS_DOMAIN_NAME,
    SPOT_DOMAIN_NAME,
    ScheduleCancelRequest,
    TransferAssetRequest,
    new_eip712_domain,
)

# ── Test fixtures ─────────────────────────────────────────────────────────────

# Well-known deterministic test key from the standard Ethereum test vectors.
# Do NOT use with real funds.
_TEST_PRIVATE_KEY_HEX = (
    "0123456789012345678901234567890123456789012345678901234567890123"
)
TEST_PRIVATE_KEY: bytes = bytes.fromhex(_TEST_PRIVATE_KEY_HEX)
TEST_CHAIN_ID: int = 286623


def new_signer(domain_name: str) -> EVMSigner:
    return EVMSigner(new_eip712_domain(domain_name, TEST_CHAIN_ID))


def expected_address() -> str:
    """Return the checksummed Ethereum address derived from TEST_PRIVATE_KEY."""
    pk = eth_keys.PrivateKey(TEST_PRIVATE_KEY)
    return pk.public_key.to_checksum_address()


# ── 1. Wire format ────────────────────────────────────────────────────────────


def test_sign_action_wire_format():
    """sign_action must return a 66-byte signature whose first byte is SignatureType.EIP712."""
    s = new_signer(SPOT_DOMAIN_NAME)
    sig = s.sign_action(
        ScheduleCancelRequest(account_id=1001), nonce=1, private_key=TEST_PRIVATE_KEY
    )

    assert len(sig) == 66, "wire-format signature must be exactly 66 bytes"
    assert sig[0] == SignatureType.EIP712, "leading byte must be SignatureType.EIP712 (0x01)"


# ── 2. Round-trip ─────────────────────────────────────────────────────────────


def test_sign_and_recover_spot():
    """Signing and recovering under the spot domain returns the correct address."""
    s = new_signer(SPOT_DOMAIN_NAME)
    req = ScheduleCancelRequest(account_id=1001)

    sig = s.sign_action(req, nonce=1, private_key=TEST_PRIVATE_KEY)
    assert s.recover_signer(req, nonce=1, signature=sig) == expected_address()


def test_sign_and_recover_perps():
    """Signing and recovering under the perps domain returns the correct address."""
    s = new_signer(PERPS_DOMAIN_NAME)
    req = ScheduleCancelRequest(account_id=2002)

    sig = s.sign_action(req, nonce=7, private_key=TEST_PRIVATE_KEY)
    assert s.recover_signer(req, nonce=7, signature=sig) == expected_address()


@pytest.mark.parametrize(
    "req,nonce",
    [
        (ScheduleCancelRequest(account_id=1001), 42),
        (ScheduleCancelRequest(account_id=1001, scheduled_timestamp=9_999_999), 42),
    ],
)
def test_sign_and_recover_multiple_requests(req, nonce):
    """Round-trip works for every concrete shared request type."""
    s = new_signer(SPOT_DOMAIN_NAME)
    sig = s.sign_action(req, nonce=nonce, private_key=TEST_PRIVATE_KEY)
    assert s.recover_signer(req, nonce=nonce, signature=sig) == expected_address()


# ── 3. Cross-engine isolation ─────────────────────────────────────────────────


def test_cross_engine_isolation():
    """A spot signature must not recover the correct address under the perps domain.

    This is the primary defence-in-depth test against cross-engine replay attacks.
    Recovery will succeed cryptographically but yield a wrong address, so the
    exchange server will reject the request.
    """
    spot_signer = new_signer(SPOT_DOMAIN_NAME)
    perps_signer = new_signer(PERPS_DOMAIN_NAME)
    req = ScheduleCancelRequest(account_id=1001)

    spot_sig = spot_signer.sign_action(req, nonce=5, private_key=TEST_PRIVATE_KEY)
    recovered = perps_signer.recover_signer(req, nonce=5, signature=spot_sig)

    assert recovered != expected_address(), (
        "spot signature must not recover the correct address under the perps domain"
    )


# ── 4. Determinism ────────────────────────────────────────────────────────────


def test_sign_action_determinism():
    """Identical inputs must always produce identical signatures (RFC 6979)."""
    s = new_signer(SPOT_DOMAIN_NAME)
    req = ScheduleCancelRequest(account_id=1001)

    assert (
        s.sign_action(req, nonce=3, private_key=TEST_PRIVATE_KEY)
        == s.sign_action(req, nonce=3, private_key=TEST_PRIVATE_KEY)
    )


# ── 5. Sensitivity ────────────────────────────────────────────────────────────


def test_nonce_sensitivity():
    """Different nonces must produce different signatures."""
    s = new_signer(SPOT_DOMAIN_NAME)
    req = ScheduleCancelRequest(account_id=1001)

    assert (
        s.sign_action(req, nonce=0, private_key=TEST_PRIVATE_KEY)
        != s.sign_action(req, nonce=1, private_key=TEST_PRIVATE_KEY)
    )


def test_action_type_sensitivity():
    """Different action types must produce different signatures even at nonce 0."""
    s = new_signer(SPOT_DOMAIN_NAME)

    sig_cancel = s.sign_action(
        ScheduleCancelRequest(account_id=1001), nonce=0, private_key=TEST_PRIVATE_KEY
    )
    sig_transfer = s.sign_action(
        TransferAssetRequest(
            id=1,
            from_account_id=1001,
            to_account_id=1002,
            coin_id=1,
            amount=Decimal("0"),
            type=TransferAssetType.INTERNAL,
        ),
        nonce=0,
        private_key=TEST_PRIVATE_KEY,
    )

    assert sig_cancel != sig_transfer


def test_param_sensitivity():
    """Changing a request parameter must change the signature."""
    s = new_signer(SPOT_DOMAIN_NAME)

    assert (
        s.sign_action(
            ScheduleCancelRequest(account_id=1001), nonce=0, private_key=TEST_PRIVATE_KEY
        )
        != s.sign_action(
            ScheduleCancelRequest(account_id=9999), nonce=0, private_key=TEST_PRIVATE_KEY
        )
    )


# ── 6. Error handling ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sig,desc",
    [
        (b"", "empty"),
        (bytes(65), "65 bytes — raw ECDSA, missing type prefix"),
        (bytes(67), "67 bytes — one byte too long"),
    ],
)
def test_recover_invalid_signature_length(sig, desc):
    """recover_signer raises InvalidSignatureLengthError for non-66-byte inputs."""
    s = new_signer(SPOT_DOMAIN_NAME)
    req = ScheduleCancelRequest(account_id=1)

    with pytest.raises(InvalidSignatureLengthError):
        s.recover_signer(req, nonce=0, signature=sig)


@pytest.mark.parametrize(
    "type_byte,desc",
    [
        (0x00, "Unknown (0)"),
        (0x02, "hypothetical future type"),
        (0xFF, "0xFF"),
    ],
)
def test_recover_invalid_signature_type(type_byte, desc):
    """recover_signer raises InvalidSignatureTypeError for unrecognised type bytes."""
    s = new_signer(SPOT_DOMAIN_NAME)
    req = ScheduleCancelRequest(account_id=1)
    sig = bytes([type_byte]) + bytes(65)

    with pytest.raises(InvalidSignatureTypeError):
        s.recover_signer(req, nonce=0, signature=sig)


# ── 7. Domain separator ───────────────────────────────────────────────────────


def test_domain_separator_idempotent():
    """domain_separator() must return the same bytes on repeated calls (cached)."""
    domain = new_eip712_domain(SPOT_DOMAIN_NAME, TEST_CHAIN_ID)
    assert domain.domain_separator() == domain.domain_separator()


def test_domain_separator_engine_distinct():
    """Spot and perps domains must have different separators."""
    spot = new_eip712_domain(SPOT_DOMAIN_NAME, TEST_CHAIN_ID)
    perps = new_eip712_domain(PERPS_DOMAIN_NAME, TEST_CHAIN_ID)
    assert spot.domain_separator() != perps.domain_separator()


def test_domain_separator_chain_id_sensitivity():
    """The same domain name on different chain IDs must produce different separators."""
    d1 = new_eip712_domain(SPOT_DOMAIN_NAME, 286623)
    d2 = new_eip712_domain(SPOT_DOMAIN_NAME, 1)  # Ethereum mainnet
    assert d1.domain_separator() != d2.domain_separator()


# ── 8. Decimal safety net ─────────────────────────────────────────────────────


def test_raw_decimal_in_payload_raises():
    """If a request's to_json_payload() forgets to str() a Decimal, the encoder
    must raise a TypeError rather than silently emitting an unquoted number.

    An unquoted number in the hashed JSON would produce a different keccak256
    hash than the server's (which expects shopspring/decimal's quoted-string
    form), causing every signature to be rejected with no clear reason.
    """
    from sodex.common.types import action_payload_hash

    class _BrokenRequest:
        """A deliberately broken request that forgets to stringify its Decimal."""

        def action_name(self) -> str:
            return "broken"

        def to_json_payload(self) -> dict:
            return {"amount": Decimal("1.5")}  # should have been str(...)

    with pytest.raises(TypeError, match="Decimal must be pre-converted to str"):
        action_payload_hash(_BrokenRequest())
