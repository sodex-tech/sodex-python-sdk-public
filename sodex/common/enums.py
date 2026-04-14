"""Shared enum types mirroring common/enums in the Go SDK.

All integer values match the Go iota definitions so that JSON-serialised
requests are identical across both SDKs.
"""

from enum import IntEnum


class SignatureType(IntEnum):
    """Single-byte discriminator prepended to every wire-format signature."""

    UNKNOWN = 0  # Unrecognised; always rejected by the server.
    EIP712 = 1   # EIP-712 structured-data signature with engine-specific domain.


class OrderSide(IntEnum):
    UNKNOWN = 0
    BUY = 1
    SELL = 2


class OrderType(IntEnum):
    UNKNOWN = 0
    LIMIT = 1
    MARKET = 2


class TimeInForce(IntEnum):
    UNKNOWN = 0
    GTC = 1  # Good Till Cancel
    FOK = 2  # Fill or Kill
    IOC = 3  # Immediate or Cancel
    GTX = 4  # Good Till Crossing / Post-only


class OrderModifier(IntEnum):
    UNKNOWN = 0
    NORMAL = 1
    STOP = 2
    BRACKET = 3        # Primary order with attached TP/SL orders
    ATTACHED_STOP = 4  # Stop order attached to a primary order


class OrderStatus(IntEnum):
    UNKNOWN = 0
    NEW = 1
    PARTIALLY_FILLED = 2
    FILLED = 3
    CANCELED = 4
    REJECTED = 5
    EXPIRED = 6
    PENDING_NEW = 7
    PENDING_CANCEL = 8
    PENDING_MODIFY = 9
    TRIGGERED = 10
    REPLACED = 11
    PENDING_REPLACE = 12


class ExecType(IntEnum):
    UNKNOWN = 0
    NEW = 1
    PARTIALLY_FILLED = 2
    FILLED = 3
    CANCELED = 4
    REJECTED = 5
    MODIFIED = 6
    EXPIRED = 7
    REPLACED = 8


class PositionSide(IntEnum):
    UNKNOWN = 0
    BOTH = 1
    LONG = 2
    SHORT = 3


class MarginMode(IntEnum):
    UNKNOWN = 0
    ISOLATED = 1
    CROSS = 2


class StopType(IntEnum):
    UNKNOWN = 0
    STOP_LOSS = 1
    TAKE_PROFIT = 2


class TriggerType(IntEnum):
    """Price type used to trigger a stop order."""

    UNKNOWN = 0
    LAST_PRICE = 1
    MARK_PRICE = 2
    INDEX_PRICE = 3


class TransferAssetType(IntEnum):
    # Note: UNKNOWN is -1 to mirror the Go SDK; the zero value is EVM_DEPOSIT.
    UNKNOWN = -1
    EVM_DEPOSIT = 0
    PERPS_DEPOSIT = 1
    EVM_WITHDRAW = 2
    PERPS_WITHDRAW = 3
    INTERNAL = 4
    SPOT_WITHDRAW = 5
    SPOT_DEPOSIT = 6
