"""HTTP REST client for the Sodex exchange.

Mirrors the Go public SDK's ``client`` package: exposes a single ``Client`` class
that handles signing, nonces, and request/response envelope unwrapping for both
the Spark (spot) and Bolt (perps) engines.
"""

from .client import (
    DEFAULT_BASE_URL,
    DEFAULT_CHAIN_ID,
    TESTNET_BASE_URL,
    TESTNET_CHAIN_ID,
    APIError,
    Client,
    Config,
    NotAuthenticatedError,
)
from .types import (
    AccountInfo,
    APIResponse,
    Balance,
    Candle,
    CancelOrderResult,
    FundingPayment,
    HistoryFilter,
    LeverageResult,
    ModifyOrderResult,
    Order,
    OrderBook,
    OrderBookLevel,
    PlaceOrderResult,
    Position,
    PublicTrade,
    Symbol,
    Ticker,
    UserTrade,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_CHAIN_ID",
    "TESTNET_BASE_URL",
    "TESTNET_CHAIN_ID",
    "APIError",
    "APIResponse",
    "AccountInfo",
    "Balance",
    "Candle",
    "CancelOrderResult",
    "Client",
    "Config",
    "FundingPayment",
    "HistoryFilter",
    "LeverageResult",
    "ModifyOrderResult",
    "NotAuthenticatedError",
    "Order",
    "OrderBook",
    "OrderBookLevel",
    "PlaceOrderResult",
    "Position",
    "PublicTrade",
    "Symbol",
    "Ticker",
    "UserTrade",
]
