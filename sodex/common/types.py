"""Core EIP-712 primitives and shared request types for the Sodex exchange.

Signing pipeline (identical two-layer approach as the Go SDK):

    ActionPayload{type, params}
      └─▶ JSON-encode ──▶ keccak256 ──▶ payloadHash

    ExchangeAction{payloadHash, nonce}
      └─▶ EIP-712 StructHash
            └─▶ keccak256(0x19 0x01 | domainSeparator | structHash) ──▶ digest

    ECDSA-sign(digest, private_key)
      └─▶ [SignatureType byte | 65-byte ECDSA sig]  (66 bytes total)

Trading-action wire format
--------------------------
Engine action signatures returned by this module are exactly 66 bytes:
    byte[0]    – SignatureType (0x01 for an engine-specific EIP-712 domain)
    byte[1:66] – 65-byte ECDSA signature  (r ‖ s ‖ v, v ∈ {0, 1})

Universal-domain API-key actions use the same layout with type byte 0x02.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable

from eth_hash.auto import keccak

from .enums import TransferAssetType

# ── Sentinel errors ───────────────────────────────────────────────────────────


class InvalidSignatureLengthError(ValueError):
    """Raised when a wire-format signature is not exactly 66 bytes."""


class InvalidSignatureTypeError(ValueError):
    """Raised when the leading byte of a signature is not a recognised SignatureType."""


# ── EIP-712 domain name constants ─────────────────────────────────────────────

SPOT_DOMAIN_NAME = "spot"  # Spark spot engine
PERPS_DOMAIN_NAME = "futures"  # Bolt perpetuals engine


# ── Protocol for signable requests ───────────────────────────────────────────


@runtime_checkable
class ActionPayloadParams(Protocol):
    """Interface that every signable request must implement.

    action_name() returns the discriminator string embedded in the JSON payload
    (e.g. "scheduleCancel"). to_json_payload() returns a dict whose field order
    must match the corresponding Go struct definition, because the JSON bytes
    are keccak256-hashed and field order is significant.
    """

    def action_name(self) -> str: ...
    def to_json_payload(self) -> dict: ...


# ── EIP-712 domain ────────────────────────────────────────────────────────────

_DOMAIN_TYPE_HASH: bytes = keccak(
    b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)


class EIP712Domain:
    """EIP-712 domain that binds signatures to a specific application and chain.

    The domain separator is derived from the domain fields and mixed into every
    signature. Changing any field produces a completely different set of valid
    signatures, preventing cross-engine and cross-chain replay attacks.

    EIP-712 type string:
        EIP712Domain(string name, string version, uint256 chainId, address verifyingContract)
    """

    def __init__(
        self,
        name: str,
        chain_id: int,
        version: str = "1",
        verifying_contract: str = "0x0000000000000000000000000000000000000000",
    ) -> None:
        self.name = name
        self.chain_id = chain_id
        self.version = version
        contract = bytes.fromhex(verifying_contract.removeprefix("0x"))
        if len(contract) != 20:
            raise ValueError("verifying_contract must be a 20-byte EVM address")
        self.verifying_contract = verifying_contract
        self._verifying_contract_bytes = contract
        self._separator: Optional[bytes] = None  # lazily computed cache

    def domain_separator(self) -> bytes:
        """Compute (and cache) the EIP-712 domain separator.

        keccak256(
            typeHash
            || keccak256(name)
            || keccak256(version)
            || uint256(chainId)           -- 32 bytes big-endian
            || address(verifyingContract) -- 20 bytes left-padded to 32 bytes
        )
        """
        if self._separator is not None:
            return self._separator
        self._separator = keccak(
            _DOMAIN_TYPE_HASH
            + keccak(self.name.encode())
            + keccak(self.version.encode())
            + self.chain_id.to_bytes(32, "big")
            + b"\x00" * 12
            + self._verifying_contract_bytes
        )
        return self._separator


def new_eip712_domain(name: str, chain_id: int) -> EIP712Domain:
    """Construct an EIP712Domain with version "1" and zero verifying contract."""
    return EIP712Domain(name=name, chain_id=chain_id)


def default_spark_domain() -> EIP712Domain:
    """Return the production EIP-712 domain for the Spark spot engine (chain 286623)."""
    return new_eip712_domain(SPOT_DOMAIN_NAME, 286623)


def default_bolt_domain() -> EIP712Domain:
    """Return the production EIP-712 domain for the Bolt perps engine (chain 286623)."""
    return new_eip712_domain(PERPS_DOMAIN_NAME, 286623)


# ── ExchangeAction ────────────────────────────────────────────────────────────

_EXCHANGE_ACTION_TYPE_HASH: bytes = keccak(
    b"ExchangeAction(bytes32 payloadHash,uint64 nonce)"
)


class ExchangeAction:
    """EIP-712 typed message that the signer ultimately signs.

    Rather than defining a unique EIP-712 type for every action, a single generic
    type is used whose payload field is the keccak256 hash of the action-specific
    data. This keeps the type schema simple and independent of the action type.

    EIP-712 type string:
        ExchangeAction(bytes32 payloadHash, uint64 nonce)
    """

    def __init__(self, payload_hash: bytes, nonce: int) -> None:
        if len(payload_hash) != 32:
            raise ValueError(f"payload_hash must be 32 bytes, got {len(payload_hash)}")
        self.payload_hash = payload_hash
        self.nonce = nonce

    def struct_hash(self) -> bytes:
        """keccak256(typeHash ‖ payloadHash ‖ nonce-as-uint256)

        The nonce is ABI-encoded as a big-endian uint256 (32 bytes, right-aligned).
        """
        nonce_bytes = self.nonce.to_bytes(32, "big")
        return keccak(_EXCHANGE_ACTION_TYPE_HASH + self.payload_hash + nonce_bytes)

    def hash(self, domain: EIP712Domain) -> bytes:
        """keccak256(0x19 ‖ 0x01 ‖ domainSeparator ‖ structHash)

        The 0x19 0x01 prefix follows EIP-191 and EIP-712, distinguishing this
        digest from a plain transaction hash.
        """
        return keccak(b"\x19\x01" + domain.domain_separator() + self.struct_hash())


# ── ActionPayload hashing ─────────────────────────────────────────────────────


class _StrictDecimalEncoder(json.JSONEncoder):
    """JSON encoder that refuses to serialise raw ``Decimal`` values.

    Decimals MUST be pre-converted to strings inside each request type's
    ``to_json_payload()`` (mirroring shopspring/decimal's default MarshalJSON,
    which outputs a *quoted* string like ``"1234.56"``).

    If a new request type forgets this conversion, the default JSON encoder
    would silently use ``float(obj)`` and emit an unquoted number — producing
    a different keccak256 hash than the server computes and rejecting every
    signature. This encoder raises loudly instead, so the mistake surfaces
    immediately during development.
    """

    def default(self, obj: object) -> object:
        if isinstance(obj, Decimal):
            raise TypeError(
                "Decimal must be pre-converted to str() in to_json_payload() "
                "before hashing — the server expects quoted JSON strings for "
                f"decimal fields (got raw Decimal {obj!r})"
            )
        return super().default(obj)


def action_payload_hash(request: ActionPayloadParams) -> bytes:
    """Compute the keccak256 hash of ActionPayload{type, params} encoded as JSON.

    This is the first of the two hashing layers in the signing pipeline. The
    resulting 32-byte hash becomes the ``payloadHash`` field of ExchangeAction.

    JSON field order follows the Go struct definition order; changing the order
    would produce a different hash and an invalid signature.
    """
    payload = {
        "type": request.action_name(),
        "params": request.to_json_payload(),
    }
    encoded = json.dumps(
        payload,
        cls=_StrictDecimalEncoder,
        separators=(
            ",",
            ":",
        ),  # compact encoding, no spaces — matches Go's json.Marshal
    ).encode()
    return keccak(encoded)


# ── Shared request types ──────────────────────────────────────────────────────


class BuilderParams:
    """Builder account and fee attached to a newly placed order."""

    def __init__(self, builder_id: int, fee_rate: int) -> None:
        self.builder_id = builder_id
        self.fee_rate = fee_rate

    def to_dict(self) -> dict:
        return {"id": self.builder_id, "fee": self.fee_rate}


class ScheduleCancelRequest:
    """Schedule a mass cancellation of all open orders after a given timestamp.

    Available on both the spot and perps engines.
    """

    def __init__(
        self,
        account_id: int,
        scheduled_timestamp: Optional[int] = None,
    ) -> None:
        self.account_id = account_id
        self.scheduled_timestamp = scheduled_timestamp

    def action_name(self) -> str:
        return "scheduleCancel"

    def to_json_payload(self) -> dict:
        # Field order matches the Go struct definition (accountID, scheduledTimestamp).
        d: dict = {"accountID": self.account_id}
        if self.scheduled_timestamp is not None:
            d["scheduledTimestamp"] = self.scheduled_timestamp
        return d


class NewTwapOrderRequest:
    """TWAP placement request shared by spot and perpetuals engines."""

    def __init__(
        self,
        account_id: int,
        symbol_id: int,
        side: int,
        quantity: Decimal,
        minutes: int,
        randomize: bool,
        reduce_only: Optional[bool] = None,
    ) -> None:
        self.account_id = account_id
        self.symbol_id = symbol_id
        self.side = side
        self.quantity = quantity
        self.minutes = minutes
        self.randomize = randomize
        self.reduce_only = reduce_only

    def action_name(self) -> str:
        return "newTwapOrder"

    def to_json_payload(self) -> dict:
        payload = {
            "accountID": self.account_id,
            "symbolID": self.symbol_id,
            "side": int(self.side),
            "quantity": str(self.quantity),
            "minutes": self.minutes,
            "randomize": self.randomize,
        }
        if self.reduce_only is not None:
            payload["reduceOnly"] = self.reduce_only
        return payload


class CancelTwapOrderRequest:
    """TWAP cancellation request shared by spot and perpetuals engines."""

    def __init__(self, account_id: int, symbol_id: int, order_id: int) -> None:
        self.account_id = account_id
        self.symbol_id = symbol_id
        self.order_id = order_id

    def action_name(self) -> str:
        return "cancelTwapOrder"

    def to_json_payload(self) -> dict:
        return {
            "accountID": self.account_id,
            "symbolID": self.symbol_id,
            "orderID": self.order_id,
        }


class ApproveBuilderFeeRequest:
    """Universal-domain request approving a builder's maximum fee rate."""

    def __init__(self, account_id: int, builder_id: int, max_fee_rate: int) -> None:
        self.account_id = account_id
        self.builder_id = builder_id
        self.max_fee_rate = max_fee_rate

    def action_name(self) -> str:
        return "approveBuilderFee"

    def to_json_payload(self) -> dict:
        return {
            "accountID": self.account_id,
            "builderID": self.builder_id,
            "maxFeeRate": self.max_fee_rate,
        }


class ReplaceParams:
    """Parameters for a single order replacement within a batch replace request."""

    def __init__(
        self,
        symbol_id: int,
        cl_ord_id: str,
        orig_order_id: Optional[int] = None,
        orig_cl_ord_id: Optional[str] = None,
        price: Optional[Decimal] = None,
        quantity: Optional[Decimal] = None,
    ) -> None:
        self.symbol_id = symbol_id
        self.cl_ord_id = cl_ord_id
        self.orig_order_id = orig_order_id
        self.orig_cl_ord_id = orig_cl_ord_id
        self.price = price
        self.quantity = quantity

    def to_dict(self) -> dict:
        d: dict = {
            "symbolID": self.symbol_id,
            "clOrdID": self.cl_ord_id,
        }
        if self.orig_order_id is not None:
            d["origOrderID"] = self.orig_order_id
        if self.orig_cl_ord_id is not None:
            d["origClOrdID"] = self.orig_cl_ord_id
        if self.price is not None:
            d["price"] = str(self.price)
        if self.quantity is not None:
            d["quantity"] = str(self.quantity)
        return d


class ReplaceOrderRequest:
    """Batch order-replacement request. Available on both engines."""

    def __init__(self, account_id: int, orders: list[ReplaceParams]) -> None:
        self.account_id = account_id
        self.orders = orders

    def action_name(self) -> str:
        return "replaceOrder"

    def to_json_payload(self) -> dict:
        return {
            "accountID": self.account_id,
            "orders": [o.to_dict() for o in self.orders],
        }


class TransferAssetRequest:
    """Inter-account asset transfer request. Available on both engines."""

    def __init__(
        self,
        id: int,
        from_account_id: int,
        to_account_id: int,
        coin_id: int,
        amount: Decimal,
        type: TransferAssetType,
    ) -> None:
        self.id = id
        self.from_account_id = from_account_id
        self.to_account_id = to_account_id
        self.coin_id = coin_id
        self.amount = amount
        self.type = type

    def action_name(self) -> str:
        return "transferAsset"

    def to_json_payload(self) -> dict:
        # Field order mirrors the Go struct definition.
        return {
            "id": self.id,
            "fromAccountID": self.from_account_id,
            "toAccountID": self.to_account_id,
            "coinID": self.coin_id,
            "amount": str(self.amount),
            "type": int(self.type),
        }
