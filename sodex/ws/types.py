"""WebSocket request/push payload types.

Mirrors ``ws/types.go`` of the Go public SDK. Channel constants and dataclass
fields use the same JSON keys (single-letter abbreviations like ``E``, ``s``,
``p``) as the wire protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

# ── Channel name constants ───────────────────────────────────────────────────

CHANNEL_TICKER = "ticker"
CHANNEL_ALL_TICKER = "allTicker"
CHANNEL_MINI_TICKER = "miniTicker"
CHANNEL_ALL_MINI_TICKER = "allMiniTicker"
CHANNEL_BOOK_TICKER = "bookTicker"
CHANNEL_ALL_BOOK_TICKER = "allBookTicker"
CHANNEL_TRADE = "trade"
CHANNEL_L2_BOOK = "l2Book"
CHANNEL_L4_BOOK = "l4Book"
CHANNEL_CANDLE = "candle"
CHANNEL_MARK_PRICE = "markPrice"
CHANNEL_ALL_MARK_PRICE = "allMarkPrice"
CHANNEL_COIN_PRICE = "coinPrice"
CHANNEL_ALL_COIN_PRICE = "allCoinPrice"
CHANNEL_ACCOUNT_STATE = "accountState"
CHANNEL_ACCOUNT_UPDATE = "accountUpdate"
CHANNEL_ACCOUNT_ORDER_UPDATE = "accountOrderUpdate"
CHANNEL_ACCOUNT_TRADE = "accountTrade"
CHANNEL_ACCOUNT_EVENT = "accountEvent"


# ── Subscription params ──────────────────────────────────────────────────────


@dataclass
class SubscribeParams:
    """Parameters for a subscribe / unsubscribe request.

    Only ``channel`` is required; other fields are channel-specific (e.g.
    ``symbol`` for trade/orderbook, ``user`` for account channels, ``interval``
    for candle, ``tick_size`` for L2 aggregation, and ``level`` for L4 depth).
    """

    channel: str
    symbol: Optional[str] = None
    symbols: Optional[List[str]] = None
    coins: Optional[List[str]] = None
    user: Optional[str] = None
    account_id: Optional[int] = None
    tick_size: Optional[str] = None
    level: Optional[int] = None
    interval: Optional[str] = None
    push_interval: Optional[str] = None

    def to_json(self) -> dict:
        d: dict = {"channel": self.channel}
        if self.symbol is not None:
            if self.channel in {
                CHANNEL_TICKER,
                CHANNEL_MINI_TICKER,
                CHANNEL_BOOK_TICKER,
                CHANNEL_MARK_PRICE,
            }:
                d["symbols"] = [self.symbol]
            else:
                d["symbol"] = self.symbol
        if self.symbols is not None:
            d["symbols"] = self.symbols
        if self.coins is not None:
            d["coins"] = self.coins
        if self.user is not None:
            d["user"] = self.user
        if self.account_id is not None:
            d["accountID"] = self.account_id
        if self.tick_size is not None:
            d["tickSize"] = self.tick_size
        if self.level is not None:
            d["level"] = self.level
        if self.interval is not None:
            d["interval"] = self.interval
        if self.push_interval is not None:
            d["pushInterval"] = self.push_interval
        return d


# ── Server push message ──────────────────────────────────────────────────────


@dataclass
class Push:
    """A server push message for a subscribed channel."""

    channel: str
    type: str  # "snapshot" or "update"
    data: Any  # raw decoded JSON; channel-specific shape

    @classmethod
    def from_dict(cls, d: dict) -> "Push":
        return cls(
            channel=d.get("channel", ""),
            type=d.get("type", ""),
            data=d.get("data"),
        )


# ── Channel data dataclasses ─────────────────────────────────────────────────
#
# Each field uses the single-letter JSON key documented by the exchange.
# Convenience ``from_dict`` constructors decode from raw dicts; raw access via
# ``Push.data`` is also supported for callers that want to inspect new fields.


@dataclass
class Ticker:
    event_time: int
    symbol: str
    last_price: str
    last_qty: str
    weighted_avg_price: str
    ask_price: str
    ask_qty: str
    bid_price: str
    bid_qty: str
    price_change: str
    price_change_percent: float
    open_price: str
    high_price: str
    low_price: str
    volume: str
    quote_volume: str
    open_time: int
    close_time: int

    @classmethod
    def from_dict(cls, d: dict) -> "Ticker":
        return cls(
            event_time=int(d.get("E", 0)),
            symbol=d.get("s", ""),
            last_price=d.get("c", ""),
            last_qty=d.get("Q", ""),
            weighted_avg_price=d.get("w", ""),
            ask_price=d.get("a", ""),
            ask_qty=d.get("A", ""),
            bid_price=d.get("b", ""),
            bid_qty=d.get("B", ""),
            price_change=d.get("p", ""),
            price_change_percent=float(d.get("P", 0)),
            open_price=d.get("o", ""),
            high_price=d.get("h", ""),
            low_price=d.get("l", ""),
            volume=d.get("v", ""),
            quote_volume=d.get("q", ""),
            open_time=int(d.get("O", 0)),
            close_time=int(d.get("C", 0)),
        )


@dataclass
class MiniTicker:
    event_time: int
    symbol: str
    last_price: str
    open_price: str
    high_price: str
    low_price: str
    volume: str
    quote_volume: str

    @classmethod
    def from_dict(cls, d: dict) -> "MiniTicker":
        return cls(
            event_time=int(d.get("E", 0)),
            symbol=d.get("s", ""),
            last_price=d.get("c", ""),
            open_price=d.get("o", ""),
            high_price=d.get("h", ""),
            low_price=d.get("l", ""),
            volume=d.get("v", ""),
            quote_volume=d.get("q", ""),
        )


@dataclass
class BookTicker:
    event_time: int
    symbol: str
    update_id: int
    ask_price: str
    ask_qty: str
    bid_price: str
    bid_qty: str

    @classmethod
    def from_dict(cls, d: dict) -> "BookTicker":
        return cls(
            event_time=int(d.get("E", 0)),
            symbol=d.get("s", ""),
            update_id=int(d.get("u", 0)),
            ask_price=d.get("a", ""),
            ask_qty=d.get("A", ""),
            bid_price=d.get("b", ""),
            bid_qty=d.get("B", ""),
        )


@dataclass
class Trade:
    event_time: int
    trade_time: int
    trade_id: int
    symbol: str
    side: str
    price: str
    quantity: str
    buyer_id: int
    seller_id: int

    @classmethod
    def from_dict(cls, d: dict) -> "Trade":
        return cls(
            event_time=int(d.get("E", 0)),
            trade_time=int(d.get("T", 0)),
            trade_id=int(d.get("t", 0)),
            symbol=d.get("s", ""),
            side=d.get("S", ""),
            price=d.get("p", ""),
            quantity=d.get("q", ""),
            buyer_id=int(d.get("bi", 0)),
            seller_id=int(d.get("si", 0)),
        )


@dataclass
class L2Book:
    event_time: int
    symbol: str
    update_id: int
    asks: List[List[str]] = field(default_factory=list)  # [[price, qty], …]
    bids: List[List[str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "L2Book":
        return cls(
            event_time=int(d.get("E", 0)),
            symbol=d.get("s", ""),
            update_id=int(d.get("u", 0)),
            asks=list(d.get("a", []) or []),
            bids=list(d.get("b", []) or []),
        )


@dataclass
class Candle:
    open_time: int
    close_time: int
    symbol: str
    interval: str
    open: str
    high: str
    low: str
    close: str
    volume: str
    quote_volume: str
    num_trades: int
    closed: bool

    @classmethod
    def from_dict(cls, d: dict) -> "Candle":
        return cls(
            open_time=int(d.get("t", 0)),
            close_time=int(d.get("T", 0)),
            symbol=d.get("s", ""),
            interval=d.get("i", ""),
            open=d.get("o", ""),
            high=d.get("h", ""),
            low=d.get("l", ""),
            close=d.get("c", ""),
            volume=d.get("v", ""),
            quote_volume=d.get("q", ""),
            num_trades=int(d.get("n", 0)),
            closed=bool(d.get("x", False)),
        )


@dataclass
class MarkPrice:
    event_time: int
    symbol: str
    open_interest: str
    mark_px: str
    index_px: str
    funding_rate: str
    next_funding_time: int

    @classmethod
    def from_dict(cls, d: dict) -> "MarkPrice":
        return cls(
            event_time=int(d.get("E", 0)),
            symbol=d.get("s", ""),
            open_interest=d.get("oi", ""),
            mark_px=d.get("p", ""),
            index_px=d.get("i", ""),
            funding_rate=d.get("r", ""),
            next_funding_time=int(d.get("T", 0)),
        )


@dataclass
class CoinPrice:
    """Perps collateral oracle price and margin ratio update."""

    event_time: int
    coin_id: int
    coin: str
    price: str
    margin_ratio: str

    @classmethod
    def from_dict(cls, d: dict) -> "CoinPrice":
        return cls(
            event_time=int(d.get("E", 0)),
            coin_id=int(d.get("i", 0)),
            coin=d.get("a", ""),
            price=d.get("p", ""),
            margin_ratio=d.get("mr", ""),
        )


# ── Account channel push types ───────────────────────────────────────────────


@dataclass
class AccountOrderUpdate:
    event_time: int
    trade_time: int
    symbol: str
    cl_ord_id: str
    order_id: int
    side: str
    order_type: str
    price: str
    orig_qty: str
    status: str
    filled_qty: str
    filled_value: str
    trade_id: Optional[int]
    last_qty: Optional[str]
    last_price: Optional[str]
    fee: Optional[str]
    is_maker: Optional[bool]
    exec_type: str
    reason: Optional[str]
    time_in_force: str = ""
    funds: Optional[str] = None
    margin_frozen: str = ""
    position_side: Optional[str] = None
    reduce_only: Optional[bool] = None
    stop_price: Optional[str] = None
    stop_type: Optional[str] = None
    trigger_type: Optional[str] = None
    position_id: Optional[int] = None
    primary_order_id: Optional[int] = None
    attached_order_ids: Optional[List[int]] = None

    @classmethod
    def from_dict(cls, d: dict) -> "AccountOrderUpdate":
        trade_id = d.get("t")
        position_id = d.get("pid")
        primary_order_id = d.get("poid")
        attached_order_ids = d.get("aoids")
        return cls(
            event_time=int(d.get("E", 0)),
            trade_time=int(d.get("T", 0)),
            symbol=d.get("s", ""),
            cl_ord_id=d.get("c", ""),
            order_id=int(d.get("i", 0)),
            side=d.get("S", ""),
            order_type=d.get("o", ""),
            price=d.get("p", ""),
            orig_qty=d.get("q", ""),
            status=d.get("X", ""),
            filled_qty=d.get("z", ""),
            filled_value=d.get("v", ""),
            trade_id=int(trade_id) if trade_id is not None else None,
            last_qty=d.get("l"),
            last_price=d.get("L"),
            fee=d.get("n"),
            is_maker=bool(d["m"]) if d.get("m") is not None else None,
            exec_type=d.get("x", ""),
            reason=d.get("r"),
            time_in_force=d.get("f", ""),
            funds=d.get("F"),
            margin_frozen=d.get("M", ""),
            position_side=d.get("ps"),
            reduce_only=bool(d["R"]) if d.get("R") is not None else None,
            stop_price=d.get("sp"),
            stop_type=d.get("st"),
            trigger_type=d.get("tt"),
            position_id=int(position_id) if position_id is not None else None,
            primary_order_id=(
                int(primary_order_id) if primary_order_id is not None else None
            ),
            attached_order_ids=(
                [int(x) for x in attached_order_ids]
                if attached_order_ids is not None
                else None
            ),
        )


@dataclass
class AccountTrade:
    event_time: int
    trade_time: int
    trade_id: int
    symbol: str
    order_id: int
    cl_ord_id: str
    side: str
    price: str
    quantity: str
    fee: str
    is_maker: bool
    direction: str = ""  # perps only: "LONG" / "SHORT"

    @classmethod
    def from_dict(cls, d: dict) -> "AccountTrade":
        return cls(
            event_time=int(d.get("E", 0)),
            trade_time=int(d.get("T", 0)),
            trade_id=int(d.get("t", 0)),
            symbol=d.get("s", ""),
            order_id=int(d.get("i", 0)),
            cl_ord_id=d.get("c", ""),
            side=d.get("S", ""),
            price=d.get("p", ""),
            quantity=d.get("q", ""),
            fee=d.get("f", ""),
            is_maker=bool(d.get("m", False)),
            direction=d.get("d", ""),
        )
