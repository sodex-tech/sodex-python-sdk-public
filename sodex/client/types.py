"""Response data types returned by the Sodex REST API.

These mirror the structs in ``client/types.go`` of the Go public SDK. Fields
are kept as plain Python attributes (not Decimal) so callers can decide how to
parse them — the exchange returns numeric values as JSON strings.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Generic, List, Optional, TypeVar

from eth_keys import keys as eth_keys

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
class Coin:
    """A spot or perpetuals engine coin."""

    coin_id: int
    coin: str
    precision: int
    margin_ratio: Optional[str] = None
    price: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Coin":
        return cls(
            coin_id=int(d.get("id", 0)),
            coin=d.get("name", ""),
            precision=int(d.get("precision", 0)),
            margin_ratio=d.get("marginRatio"),
            price=d.get("price"),
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
    collateral: Optional[str] = None
    margin_ratio: Optional[str] = None
    price: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Balance":
        return cls(
            coin_id=int(d.get("id", 0)),
            coin=d.get("coin", ""),
            total=d.get("total", ""),
            locked=d.get("locked", ""),
            collateral=d.get("collateral"),
            margin_ratio=d.get("marginRatio"),
            price=d.get("price"),
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
    funds: str = ""
    margin_frozen: str = ""
    position_side: str = ""
    reduce_only: bool = False
    stop_price: Optional[str] = None
    stop_type: Optional[str] = None
    trigger_type: Optional[str] = None
    position_id: Optional[int] = None
    primary_order_id: Optional[int] = None
    attached_order_ids: List[int] = field(default_factory=list)
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
            price=d.get("price") or "",
            orig_qty=d.get("origQty") or "",
            executed_qty=d.get("executedQty", ""),
            executed_value=d.get("executedValue", ""),
            status=d.get("status", ""),
            funds=d.get("funds") or "",
            margin_frozen=d.get("marginFrozen", ""),
            position_side=d.get("positionSide", ""),
            reduce_only=bool(d.get("reduceOnly", False)),
            stop_price=d.get("stopPrice"),
            stop_type=d.get("stopType"),
            trigger_type=d.get("triggerType"),
            position_id=(
                int(d["positionID"]) if d.get("positionID") is not None else None
            ),
            primary_order_id=(
                int(d["primaryOrderID"])
                if d.get("primaryOrderID") is not None
                else None
            ),
            attached_order_ids=[int(x) for x in d.get("attachedOrderIDs", [])],
            created_at=int(d.get("createdAt", 0)),
            updated_at=int(d.get("updatedAt", 0)),
        )


@dataclass
class Position:
    """A perpetuals position using the current Gateway V1 wire shape."""

    position_id: int
    symbol: str
    margin_mode: str
    position_side: str
    size: str
    initial_margin: str
    avg_entry_price: str
    cum_open_cost: str
    cum_trading_fee: str
    cum_closed_size: str
    avg_close_price: str
    max_size: str
    realized_pnl: str
    leverage: int
    active: bool
    is_taken_over: bool
    take_over_price: str
    created_at: int
    updated_at: int
    symbol_id: int = 0
    account_id: int = 0
    mark_price: str = ""
    liq_price: str = ""
    unrealized_pnl: str = ""
    margin: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(
            position_id=int(d.get("id", 0)),
            symbol=d.get("symbol", ""),
            margin_mode=d.get("marginMode", ""),
            position_side=d.get("positionSide", ""),
            size=d.get("size", d.get("quantity", "")),
            initial_margin=d.get("initialMargin", ""),
            avg_entry_price=d.get("avgEntryPrice", d.get("entryPrice", "")),
            cum_open_cost=d.get("cumOpenCost", ""),
            cum_trading_fee=d.get("cumTradingFee", ""),
            cum_closed_size=d.get("cumClosedSize", ""),
            avg_close_price=d.get("avgClosePrice", ""),
            max_size=d.get("maxSize", ""),
            realized_pnl=d.get("realizedPnL", ""),
            leverage=int(d.get("leverage", 0)),
            active=bool(d.get("active", False)),
            is_taken_over=bool(d.get("isTakenOver", False)),
            take_over_price=d.get("takeOverPrice", ""),
            created_at=int(d.get("createdAt", 0)),
            updated_at=int(d.get("updatedAt", 0)),
            symbol_id=int(d.get("symbolID", 0)),
            account_id=int(d.get("accountID", 0)),
            mark_price=d.get("markPrice", ""),
            liq_price=d.get("liquidationPrice", ""),
            unrealized_pnl=d.get("unrealizedPnl", ""),
            margin=d.get("margin", ""),
        )

    @property
    def quantity(self) -> str:
        """Backward-compatible alias for :attr:`size`."""
        return self.size

    @property
    def entry_price(self) -> str:
        """Backward-compatible alias for :attr:`avg_entry_price`."""
        return self.avg_entry_price


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
class ModifyOrderResult:
    """Response from the perps modify-order endpoint.

    ``code == 0`` indicates the modification was accepted. A non-zero ``code``
    means the engine rejected the modification and ``error`` explains why.
    """

    code: int
    error: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "ModifyOrderResult":
        return cls(
            code=int(d.get("code", 0)),
            error=d.get("error", "") or "",
        )


@dataclass
class Candle:
    """A single OHLCV bar returned by the klines endpoint."""

    start_time: int  # unix milliseconds
    open: str
    high: str
    low: str
    close: str
    base_volume: str  # volume in the base currency
    quote_volume: str  # volume in the quote currency
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
    side: str  # "BUY" / "SELL" — taker side
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
    builder_fee: Optional[str] = None

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
            builder_fee=d.get("builderFee"),
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

    All fields optional — ``None`` means "omit from the query". The API
    caps limit at 1000 (orders/trades) or 1500 (klines).
    """

    symbol: Optional[str] = None
    account_id: Optional[int] = None
    start_time: Optional[int] = None  # unix milliseconds
    end_time: Optional[int] = None  # unix milliseconds
    limit: Optional[int] = None


@dataclass
class FeeRate:
    """Effective maker/taker fees and the tiers used to calculate them."""

    maker_fee_rate: str
    taker_fee_rate: str
    fee_discount: Optional[str]
    fee_tier: int
    staking_tier: int
    maker_rebate_tier: int

    @classmethod
    def from_dict(cls, d: dict) -> "FeeRate":
        return cls(
            maker_fee_rate=d.get("makerFeeRate", ""),
            taker_fee_rate=d.get("takerFeeRate", ""),
            fee_discount=d.get("feeDiscount"),
            fee_tier=int(d.get("feeTier", 0)),
            staking_tier=int(d.get("stakingTier", 0)),
            maker_rebate_tier=int(d.get("makerRebateTier", 0)),
        )


@dataclass
class Subaccount:
    """One account ID owned by a Sodex user."""

    account_id: int
    evm_address: str

    @classmethod
    def from_dict(cls, d: dict) -> "Subaccount":
        return cls(account_id=int(d.get("id", 0)), evm_address=d.get("evmAddress", ""))


@dataclass
class UserSubaccounts:
    """Primary account metadata and all subaccounts for a user."""

    user_id: int
    primary_account_id: int
    subaccounts: List[Subaccount] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "UserSubaccounts":
        return cls(
            user_id=int(d.get("userID", 0)),
            primary_account_id=int(d.get("primaryAccountID", 0)),
            subaccounts=[Subaccount.from_dict(x) for x in d.get("subaccounts", [])],
        )


@dataclass
class ChainTransferConfig:
    """Deposit and withdrawal settings for one external chain."""

    chain: str
    coin_address: str
    bridge_address: str
    custody_withdraw_fee: str
    bridge_withdraw_fee: str
    min_deposit_amount: str
    min_withdraw_amount: str
    custody_disabled: bool

    @property
    def custody_available(self) -> bool:
        """Whether the custody route is enabled for this token/chain."""
        return not self.custody_disabled

    @property
    def bridge_available(self) -> bool:
        """Whether the asset config advertises a bridge route."""
        return bool(self.bridge_address)

    @classmethod
    def from_dict(cls, d: dict) -> "ChainTransferConfig":
        return cls(
            chain=d.get("chain", ""),
            coin_address=d.get("coinAddress", ""),
            bridge_address=d.get("bridgeAddress", ""),
            custody_withdraw_fee=d.get("custodyWithdrawFee", ""),
            bridge_withdraw_fee=d.get("bridgeWithdrawFee", ""),
            min_deposit_amount=d.get("minDepositAmount", ""),
            min_withdraw_amount=d.get("minWithdrawAmount", ""),
            custody_disabled=bool(d.get("custodyDisabled", False)),
        )


@dataclass
class CoinTransferConfig:
    """A token and the external chains on which it can be transferred."""

    coin: str
    token_address: str
    decimals: int
    chains: List[ChainTransferConfig] = field(default_factory=list)
    asset_id: Optional[int] = None
    asset_name: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "CoinTransferConfig":
        asset_id = d.get("id")
        return cls(
            coin=d.get("coin", ""),
            token_address=d.get("tokenAddress", ""),
            decimals=int(d.get("decimals", 0)),
            chains=[ChainTransferConfig.from_dict(x) for x in d.get("chains", [])],
            asset_id=int(asset_id) if asset_id is not None else None,
            asset_name=d.get("name", ""),
        )


@dataclass
class UserDepositAddress:
    """Custody deposit address assigned to a user for one chain."""

    chain: str
    address: str
    status: str

    @classmethod
    def from_dict(cls, d: dict) -> "UserDepositAddress":
        return cls(
            chain=d.get("chain", ""),
            address=d.get("address", ""),
            status=d.get("status", ""),
        )


@dataclass
class UserStatus:
    """Whether an EVM wallet is registered with Gateway."""

    status: str
    user_id: int

    @classmethod
    def from_dict(cls, d: dict) -> "UserStatus":
        return cls(status=d.get("status", ""), user_id=int(d.get("userID", 0)))


@dataclass
class DepositWithdrawalRecord:
    """One external deposit or withdrawal state transition."""

    account: str
    amount: str
    chain: str
    coin: str
    decimals: int
    fail_code: str
    fail_reason: str
    n: str
    receiver: str
    report_amount: str
    sender: str
    status: str
    status_time: int
    timestamp: int
    token: str
    tx_hash: str
    type: str
    origin_tx_hash: str = ""
    withdraw_fee: str = ""
    withdraw_id: Optional[int] = None

    @classmethod
    def from_dict(cls, d: dict) -> "DepositWithdrawalRecord":
        withdraw_id = d.get("withdrawId")
        return cls(
            account=d.get("account", ""),
            amount=d.get("amount", ""),
            chain=d.get("chain", ""),
            coin=d.get("coin", ""),
            decimals=int(d.get("decimals", 0)),
            fail_code=d.get("failCode", ""),
            fail_reason=d.get("failReason", ""),
            n=d.get("n", ""),
            receiver=d.get("receiver", ""),
            report_amount=d.get("reportAmount", ""),
            sender=d.get("sender", ""),
            status=d.get("status", ""),
            status_time=int(d.get("statusTime", 0)),
            timestamp=int(d.get("stmp", 0)),
            token=d.get("token", ""),
            tx_hash=d.get("txHash", ""),
            type=d.get("type", ""),
            origin_tx_hash=d.get("originTxHash", ""),
            withdraw_fee=d.get("withdrawFee", ""),
            withdraw_id=int(withdraw_id) if withdraw_id is not None else None,
        )


@dataclass
class DepositWithdrawalHistory:
    """One page of a user's external transfer history."""

    records: List[DepositWithdrawalRecord] = field(default_factory=list)
    total: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "DepositWithdrawalHistory":
        return cls(
            records=[
                DepositWithdrawalRecord.from_dict(x) for x in d.get("records", [])
            ],
            total=int(d.get("total", 0)),
        )


@dataclass
class EVMWithdrawRequest:
    """Gateway payload carrying a user-signed WithdrawToken permit."""

    cmd_data: str
    nonce: str
    deadline: str
    signature: str

    def to_json_payload(self) -> dict:
        return {
            "cmdData": self.cmd_data,
            "nonce": self.nonce,
            "deadline": self.deadline,
            "signature": self.signature,
        }


@dataclass
class EVMWithdrawSubmission:
    """Identifiers returned for a sponsored ValueChain withdrawal transaction."""

    tx_hash: str
    sender_address: str
    sender_nonce: int

    @classmethod
    def from_dict(cls, d: dict) -> "EVMWithdrawSubmission":
        return cls(
            tx_hash=d.get("txHash", ""),
            sender_address=d.get("senderAddress", ""),
            sender_nonce=int(d.get("senderNonce", 0)),
        )


@dataclass
class EVMDepositSubmission:
    """ValueChain transaction hashes for an EVM-to-engine deposit."""

    deposit_tx_hash: str
    approval_tx_hash: Optional[str] = None


@dataclass
class TransferReceipt:
    """Engine transfer identifier returned by spot/perps account transfers."""

    id: int

    @classmethod
    def from_dict(cls, d: dict) -> "TransferReceipt":
        return cls(id=int(d.get("id", 0)))


@dataclass
class GeneratedAPIKey:
    """Locally generated API key material; the private key is never persisted."""

    name: str
    address: str
    private_key: bytes


def generate_api_key(name: str) -> GeneratedAPIKey:
    """Generate a new secp256k1 API key using the operating system CSPRNG."""
    private_key = secrets.token_bytes(32)
    key = eth_keys.PrivateKey(private_key)
    return GeneratedAPIKey(
        name=name,
        address=key.public_key.to_checksum_address(),
        private_key=private_key,
    )


@dataclass
class AddAPIKeyRequest:
    """Request to register an EVM API key on both spot and perps engines."""

    account_id: int
    name: str
    public_key: str
    expires_at: int = 0
    permissions: Optional[int] = None

    def to_json_payload(self) -> dict:
        body = {
            "accountID": self.account_id,
            "name": self.name,
            "type": 1,
            "publicKey": self.public_key,
            "expiresAt": self.expires_at,
        }
        if self.permissions is not None:
            body["permissions"] = int(self.permissions)
        return body


@dataclass
class RevokeAPIKeyRequest:
    """Request to revoke one API key from both Spot and Perps."""

    account_id: int
    name: str

    def action_name(self) -> str:
        return "revokeAPIKey"

    def to_json_payload(self) -> dict:
        return {"accountID": self.account_id, "name": self.name}


@dataclass
class AccountAPIKey:
    """One API key registered on a trading engine."""

    name: str
    type: str
    public_key: str
    expires_at: int
    permissions: Optional[int] = None

    @classmethod
    def from_dict(cls, d: dict) -> "AccountAPIKey":
        permissions = d.get("permissions")
        return cls(
            name=d.get("name", ""),
            type=d.get("type", ""),
            public_key=d.get("publicKey", ""),
            expires_at=int(d.get("expiresAt", 0)),
            permissions=int(permissions) if permissions is not None else None,
        )


@dataclass
class AccountAPIKeys:
    """API keys registered for the same account on Spot and Perps."""

    spot: List[AccountAPIKey] = field(default_factory=list)
    perps: List[AccountAPIKey] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "AccountAPIKeys":
        return cls(
            spot=[AccountAPIKey.from_dict(x) for x in d.get("spot", [])],
            perps=[AccountAPIKey.from_dict(x) for x in d.get("perps", [])],
        )
