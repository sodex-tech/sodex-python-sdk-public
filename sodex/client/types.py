"""Response data types returned by the Sodex REST API.

These mirror the structs in ``client/types.go`` of the Go public SDK. Fields
are kept as plain Python attributes (not Decimal) so callers can decide how to
parse them — the exchange returns numeric values as JSON strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, List, Optional, TypeVar

T = TypeVar("T")


@dataclass
class APIResponse(Generic[T]):
    """Standard JSON envelope returned by every Sodex REST endpoint.

    ``code == 0`` indicates success; any non-zero value is an application-level
    error (an :class:`~sodex.client.APIError` is raised in that case).
    """

    code: int
    message: str
    data: T


@dataclass
class Symbol:
    """A tradeable market — common shape for both spot and perps."""

    symbol_id: int
    symbol: str
    display_name: str
    base_asset: str
    quote_asset: str
    status: str
    price_precision: int
    quantity_precision: int
    min_quantity: str
    max_quantity: str
    min_price: str
    max_price: str
    tick_size: str
    step_size: str
    min_notional: str
    maker_fee: str = ""
    taker_fee: str = ""
    # Perps-only fields:
    max_leverage: Optional[int] = None
    contract_size: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Symbol":
        return cls(
            symbol_id=int(d.get("id", 0)),
            symbol=d.get("name", ""),
            display_name=d.get("displayName", ""),
            base_asset=d.get("baseCoin", ""),
            quote_asset=d.get("quoteCoin", ""),
            status=d.get("status", ""),
            price_precision=int(d.get("pricePrecision", 0)),
            quantity_precision=int(d.get("quantityPrecision", 0)),
            min_quantity=d.get("minQuantity", ""),
            max_quantity=d.get("maxQuantity", ""),
            min_price=d.get("minPrice", ""),
            max_price=d.get("maxPrice", ""),
            tick_size=d.get("tickSize", ""),
            step_size=d.get("stepSize", ""),
            min_notional=d.get("minNotional", ""),
            maker_fee=d.get("makerFee", ""),
            taker_fee=d.get("takerFee", ""),
            max_leverage=d.get("maxLeverage"),
            contract_size=d.get("contractSize"),
        )


@dataclass
class Ticker:
    """24-hour rolling statistics for a symbol."""

    symbol: str
    last_price: str
    open_price: str
    high_price: str
    low_price: str
    bid_price: str
    bid_size: str
    ask_price: str
    ask_size: str
    volume: str
    quote_volume: str
    price_change: str
    price_change_percent: float
    # Perps-only fields:
    mark_price: Optional[str] = None
    index_price: Optional[str] = None
    funding_rate: Optional[str] = None
    open_interest: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Ticker":
        return cls(
            symbol=d.get("symbol", ""),
            last_price=d.get("lastPx", ""),
            open_price=d.get("openPx", ""),
            high_price=d.get("highPx", ""),
            low_price=d.get("lowPx", ""),
            bid_price=d.get("bidPx", ""),
            bid_size=d.get("bidSz", ""),
            ask_price=d.get("askPx", ""),
            ask_size=d.get("askSz", ""),
            volume=d.get("volume", ""),
            quote_volume=d.get("quoteVolume", ""),
            price_change=d.get("change", ""),
            price_change_percent=float(d.get("changePct", 0)),
            mark_price=d.get("markPrice"),
            index_price=d.get("indexPrice"),
            funding_rate=d.get("fundingRate"),
            open_interest=d.get("openInterest"),
        )


@dataclass
class OrderBookLevel:
    """A single price level in an order book snapshot."""

    price: str
    quantity: str

    @classmethod
    def from_array(cls, arr: List[str]) -> "OrderBookLevel":
        # The API returns levels as [price, qty] arrays.
        return cls(price=arr[0], quantity=arr[1])


@dataclass
class OrderBook:
    """A full depth snapshot for a symbol."""

    symbol: str
    bids: List[OrderBookLevel] = field(default_factory=list)
    asks: List[OrderBookLevel] = field(default_factory=list)
    update_id: int = 0

    @classmethod
    def from_dict(cls, d: dict, symbol: str = "") -> "OrderBook":
        return cls(
            symbol=symbol,
            bids=[OrderBookLevel.from_array(x) for x in d.get("bids", [])],
            asks=[OrderBookLevel.from_array(x) for x in d.get("asks", [])],
            update_id=int(d.get("updateID", 0)),
        )


@dataclass
class AccountInfo:
    """Account ID and user ID returned by the spot ``/state`` endpoint."""

    address: str
    account_id: int
    user_id: int

    @classmethod
    def from_dict(cls, d: dict) -> "AccountInfo":
        return cls(
            address=d.get("user", ""),
            account_id=int(d.get("aid", 0)),
            user_id=int(d.get("uid", 0)),
        )


@dataclass
class Balance:
    """A single asset balance in an account."""

    coin_id: int
    coin: str
    total: str
    locked: str

    @classmethod
    def from_dict(cls, d: dict) -> "Balance":
        return cls(
            coin_id=int(d.get("id", 0)),
            coin=d.get("coin", ""),
            total=d.get("total", ""),
            locked=d.get("locked", ""),
        )


@dataclass
class Order:
    """A resting or historical order record."""

    order_id: int
    cl_ord_id: str
    symbol: str
    side: str
    type: str
    time_in_force: str
    price: str
    orig_qty: str
    executed_qty: str
    executed_value: str
    status: str
    margin_frozen: str = ""
    created_at: int = 0
    updated_at: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        return cls(
            order_id=int(d.get("orderID", 0)),
            cl_ord_id=d.get("clOrdID", ""),
            symbol=d.get("symbol", ""),
            side=d.get("side", ""),
            type=d.get("type", ""),
            time_in_force=d.get("timeInForce", ""),
            price=d.get("price", ""),
            orig_qty=d.get("origQty", ""),
            executed_qty=d.get("executedQty", ""),
            executed_value=d.get("executedValue", ""),
            status=d.get("status", ""),
            margin_frozen=d.get("marginFrozen", ""),
            created_at=int(d.get("createdAt", 0)),
            updated_at=int(d.get("updatedAt", 0)),
        )


@dataclass
class Position:
    """An open perpetuals position."""

    symbol: str
    symbol_id: int
    account_id: int
    position_side: str
    quantity: str
    entry_price: str
    mark_price: str
    liq_price: str
    unrealized_pnl: str
    leverage: int
    margin_mode: str
    margin: str

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(
            symbol=d.get("symbol", ""),
            symbol_id=int(d.get("symbolID", 0)),
            account_id=int(d.get("accountID", 0)),
            position_side=d.get("positionSide", ""),
            quantity=d.get("quantity", ""),
            entry_price=d.get("entryPrice", ""),
            mark_price=d.get("markPrice", ""),
            liq_price=d.get("liquidationPrice", ""),
            unrealized_pnl=d.get("unrealizedPnl", ""),
            leverage=int(d.get("leverage", 0)),
            margin_mode=d.get("marginMode", ""),
            margin=d.get("margin", ""),
        )


@dataclass
class PlaceOrderResult:
    """Single entry in the response from order-placement endpoints."""

    order_id: int
    cl_ord_id: str
    status: str
    message: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "PlaceOrderResult":
        return cls(
            order_id=int(d.get("orderID", 0)),
            cl_ord_id=d.get("clOrdID", ""),
            status=d.get("status", ""),
            message=d.get("message", ""),
        )


@dataclass
class CancelOrderResult:
    """Single entry in the response from cancel endpoints."""

    cl_ord_id: str
    status: str
    order_id: Optional[int] = None
    message: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "CancelOrderResult":
        oid = d.get("orderID")
        return cls(
            cl_ord_id=d.get("clOrdID", ""),
            status=d.get("status", ""),
            order_id=int(oid) if oid is not None else None,
            message=d.get("message", ""),
        )


@dataclass
class LeverageResult:
    """Response from the update-leverage endpoint."""

    symbol: str
    leverage: int
    margin_mode: str

    @classmethod
    def from_dict(cls, d: dict) -> "LeverageResult":
        return cls(
            symbol=d.get("symbol", ""),
            leverage=int(d.get("leverage", 0)),
            margin_mode=d.get("marginMode", ""),
        )


@dataclass
class Candle:
    """A single OHLCV bar returned by the klines endpoint."""

    start_time: int       # unix milliseconds
    open: str
    high: str
    low: str
    close: str
    base_volume: str      # volume in the base currency
    quote_volume: str     # volume in the quote currency
    trades: Optional[int] = None  # number of trades, when reported

    @classmethod
    def from_dict(cls, d: dict) -> "Candle":
        trades = d.get("n")
        return cls(
            start_time=int(d.get("t", 0)),
            open=d.get("o", ""),
            high=d.get("h", ""),
            low=d.get("l", ""),
            close=d.get("c", ""),
            base_volume=d.get("v", ""),
            quote_volume=d.get("q", ""),
            trades=int(trades) if trades is not None else None,
        )


@dataclass
class PublicTrade:
    """A single recent market trade (public, market-wide)."""

    trade_id: int
    trade_time: int  # unix milliseconds
    symbol: str
    side: str        # "BUY" / "SELL" — taker side
    price: str
    quantity: str
    buyer: Optional[int] = None
    seller: Optional[int] = None

    @classmethod
    def from_dict(cls, d: dict) -> "PublicTrade":
        return cls(
            trade_id=int(d.get("t", 0)),
            trade_time=int(d.get("T", 0)),
            symbol=d.get("s", ""),
            side=d.get("S", ""),
            price=d.get("p", ""),
            quantity=d.get("q", ""),
            buyer=int(d["bi"]) if d.get("bi") is not None else None,
            seller=int(d["si"]) if d.get("si") is not None else None,
        )


@dataclass
class UserTrade:
    """A single fill for an account (private per-user trade history).

    Distinct from PublicTrade which is market-wide and always anonymous.
    """

    symbol: str
    trade_id: int
    order_id: int
    cl_ord_id: str
    side: str
    price: str
    quantity: str
    fee: str
    fee_coin: str
    timestamp: int  # unix milliseconds
    is_maker: bool

    @classmethod
    def from_dict(cls, d: dict) -> "UserTrade":
        return cls(
            symbol=d.get("symbol", ""),
            trade_id=int(d.get("tradeID", 0)),
            order_id=int(d.get("orderID", 0)),
            cl_ord_id=d.get("clOrdID", ""),
            side=d.get("side", ""),
            price=d.get("price", ""),
            quantity=d.get("quantity", ""),
            fee=d.get("fee", ""),
            fee_coin=d.get("feeCoin", ""),
            timestamp=int(d.get("time", 0)),
            is_maker=bool(d.get("isMaker", False)),
        )


@dataclass
class FundingPayment:
    """A single funding payment debit / credit on a perps position."""

    symbol: str
    position_id: int
    position_side: str
    funding_fee: str  # Positive = user paid; negative = user received
    fee_coin: str
    timestamp: int

    @classmethod
    def from_dict(cls, d: dict) -> "FundingPayment":
        return cls(
            symbol=d.get("symbol", ""),
            position_id=int(d.get("positionID", 0)),
            position_side=d.get("positionSide", ""),
            funding_fee=d.get("fundingFee", ""),
            fee_coin=d.get("feeCoin", ""),
            timestamp=int(d.get("timestamp", 0)),
        )


@dataclass
class HistoryFilter:
    """Shared pagination / filter params for the history endpoints.

    All fields optional — zero / None means "omit from the query". The API
    caps limit at 1000 (orders/trades) or 1500 (klines).
    """

    symbol: Optional[str] = None
    order_id: Optional[int] = None  # trades endpoint only
    start_time: Optional[int] = None  # unix milliseconds
    end_time: Optional[int] = None    # unix milliseconds
    limit: Optional[int] = None
