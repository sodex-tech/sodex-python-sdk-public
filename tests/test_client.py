"""HTTP REST client tests.

Uses the ``responses`` library to mock all HTTP calls — no real network access.
Verifies:

  1. URL construction (base URL + path + query params).
  2. Envelope unwrapping (success + APIError on non-zero ``code``).
  3. Response decoding into typed dataclasses.
  4. Signed request flow (headers, signature presence, body shape).
  5. Auth gating: trading methods raise NotAuthenticatedError without a key.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
import responses

from sodex.client import APIError, Client, Config, NotAuthenticatedError
from sodex.client.types import Candle, FundingPayment, HistoryFilter, OrderBook, PublicTrade, Symbol, Ticker, UserTrade
from sodex.common.enums import (
    MarginMode,
    OrderSide,
    PositionSide,
    TimeInForce,
    TransferAssetType,
)
from sodex.common.types import (
    ReplaceOrderRequest,
    ReplaceParams,
    ScheduleCancelRequest,
    TransferAssetRequest,
)
from sodex.perps.types import (
    ModifyOrderRequest,
    UpdateLeverageRequest,
    UpdateMarginRequest,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

# Same well-known dev key used in test_signing.py — never use with real funds.
_TEST_PRIVATE_KEY = bytes.fromhex(
    "0123456789012345678901234567890123456789012345678901234567890123"
)
_TESTNET_BASE_URL = "https://testnet-gw.sodex.dev"


def _read_only_client() -> Client:
    return Client(Config(base_url=_TESTNET_BASE_URL))


def _signing_client() -> Client:
    return Client(
        Config(
            base_url=_TESTNET_BASE_URL,
            chain_id=Client.TESTNET_CHAIN_ID,
            private_key=_TEST_PRIVATE_KEY,
            api_key_name="my-bot",
        )
    )


# ── 1. Defaults ──────────────────────────────────────────────────────────────


def test_default_config():
    """Empty Config falls back to mainnet defaults."""
    c = Client()
    assert c._cfg.base_url == Client.DEFAULT_BASE_URL
    assert c._cfg.chain_id == Client.DEFAULT_CHAIN_ID


def test_address_empty_without_key():
    """address property returns empty string when no private key configured."""
    assert _read_only_client().address == ""


def test_address_derivation():
    """address property derives the correct checksummed address from the private key."""
    addr = _signing_client().address
    assert addr.startswith("0x") and len(addr) == 42


# ── 2. Envelope unwrapping ───────────────────────────────────────────────────


@responses.activate
def test_apierror_on_non_zero_code():
    """A non-zero ``code`` in the envelope raises APIError carrying that code/message."""
    responses.add(
        responses.GET,
        f"{_TESTNET_BASE_URL}/api/v1/perps/markets/symbols",
        json={"code": 4001, "message": "rate limit exceeded"},
        status=200,
    )
    c = _read_only_client()
    with pytest.raises(APIError) as ei:
        c.perps_symbols()
    assert ei.value.code == 4001
    assert "rate limit exceeded" in str(ei.value)


# ── 3. Market data decoding ──────────────────────────────────────────────────


@responses.activate
def test_perps_tickers_decoding():
    """perps_tickers returns properly typed Ticker dataclasses with perps-only fields."""
    responses.add(
        responses.GET,
        f"{_TESTNET_BASE_URL}/api/v1/perps/markets/tickers",
        json={
            "code": 0,
            "data": [
                {
                    "symbol": "BTC-USD",
                    "lastPx": "71605",
                    "openPx": "70983",
                    "highPx": "73095",
                    "lowPx": "70499",
                    "bidPx": "71605",
                    "bidSz": "7.65",
                    "askPx": "71612",
                    "askSz": "7.67",
                    "volume": "148",
                    "quoteVolume": "10693722",
                    "change": "622",
                    "changePct": 0.876,
                    "markPrice": "71606",
                    "indexPrice": "71635",
                    "fundingRate": "0.0000125",
                    "openInterest": "35.31",
                }
            ],
        },
    )
    tickers = _read_only_client().perps_tickers()
    assert len(tickers) == 1
    t = tickers[0]
    assert isinstance(t, Ticker)
    assert t.symbol == "BTC-USD"
    assert t.last_price == "71605"
    assert t.mark_price == "71606"
    assert t.funding_rate == "0.0000125"
    assert t.price_change_percent == 0.876


# Validates that the public depth argument maps to Gateway's current `limit` query and decodes levels.
@responses.activate
def test_perps_order_book_with_depth():
    """perps_order_book sends ``?limit=N`` and decodes the [price, qty] arrays."""
    responses.add(
        responses.GET,
        f"{_TESTNET_BASE_URL}/api/v1/perps/markets/BTC-USD/orderbook",
        json={
            "code": 0,
            "data": {
                "updateID": 99,
                "bids": [["71600", "1.0"], ["71599", "2.0"]],
                "asks": [["71601", "0.5"]],
            },
        },
    )
    ob = _read_only_client().perps_order_book("BTC-USD", depth=10)
    assert isinstance(ob, OrderBook)
    assert ob.symbol == "BTC-USD"
    assert ob.update_id == 99
    assert len(ob.bids) == 2
    assert ob.bids[0].price == "71600" and ob.bids[0].quantity == "1.0"
    assert len(ob.asks) == 1

    # Verify the latest Gateway query name is used.
    sent_url = responses.calls[0].request.url
    assert "limit=10" in sent_url


@responses.activate
def test_perps_balances_unwraps_nested():
    """perps_balances unwraps the ``data.balances`` nested array."""
    responses.add(
        responses.GET,
        f"{_TESTNET_BASE_URL}/api/v1/perps/accounts/0xabc/balances",
        json={
            "code": 0,
            "data": {
                "blockTime": 1,
                "blockHeight": 2,
                "balances": [
                    {"id": 1, "coin": "USDC", "total": "1000", "locked": "0"},
                ],
            },
        },
    )
    bals = _read_only_client().perps_balances("0xabc")
    assert len(bals) == 1
    assert bals[0].coin == "USDC" and bals[0].total == "1000"


@responses.activate
def test_spot_account_info():
    """spot_account_info parses the ``aid`` field correctly."""
    responses.add(
        responses.GET,
        f"{_TESTNET_BASE_URL}/api/v1/spot/accounts/0xabc/state",
        json={"code": 0, "data": {"user": "0xabc", "aid": 5655, "uid": 42}},
    )
    info = _read_only_client().spot_account_info("0xabc")
    assert info.account_id == 5655
    assert info.user_id == 42


@responses.activate
def test_perps_symbols_decoding():
    """perps_symbols decodes the perps-specific maxLeverage field."""
    responses.add(
        responses.GET,
        f"{_TESTNET_BASE_URL}/api/v1/perps/markets/symbols",
        json={
            "code": 0,
            "data": [
                {
                    "id": 1,
                    "name": "BTC-USD",
                    "displayName": "BTC-USD",
                    "baseCoin": "BTC",
                    "quoteCoin": "USD",
                    "status": "TRADING",
                    "pricePrecision": 1,
                    "quantityPrecision": 5,
                    "minQuantity": "0.0001",
                    "maxQuantity": "1000",
                    "minPrice": "1",
                    "maxPrice": "1000000",
                    "tickSize": "0.1",
                    "stepSize": "0.0001",
                    "minNotional": "1",
                    "maxLeverage": 50,
                }
            ],
        },
    )
    syms = _read_only_client().perps_symbols()
    assert isinstance(syms[0], Symbol)
    assert syms[0].symbol_id == 1
    assert syms[0].max_leverage == 50


# ── 4. Signed requests ───────────────────────────────────────────────────────


@responses.activate
def test_update_leverage_signed_headers():
    """update_leverage sends all required signing headers and just the params object as the body."""
    captured = {}

    def callback(req):
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.body)
        return (
            200,
            {},
            json.dumps(
                {
                    "code": 0,
                    "data": {"symbol": "BTC-USD", "leverage": 10, "marginMode": "CROSS"},
                }
            ),
        )

    responses.add_callback(
        responses.POST,
        f"{_TESTNET_BASE_URL}/api/v1/perps/trade/leverage",
        callback=callback,
        content_type="application/json",
    )
    res = _signing_client().update_leverage(
        UpdateLeverageRequest(
            account_id=5655, symbol_id=1, leverage=10, margin_mode=MarginMode.CROSS
        )
    )
    assert res.leverage == 10

    h = captured["headers"]
    # Required signing headers
    assert h["X-API-Sign"].startswith("0x") and len(h["X-API-Sign"]) == 134  # 0x + 132 hex
    assert int(h["X-API-Nonce"]) > 0
    assert int(h["X-API-Chain"]) == Client.TESTNET_CHAIN_ID
    assert h["X-API-Key"] == "my-bot"

    # Body must be the params object only — not the full signing payload.
    assert captured["body"] == {
        "accountID": 5655,
        "symbolID": 1,
        "leverage": 10,
        "marginMode": int(MarginMode.CROSS),
    }
    assert "type" not in captured["body"]


# Validates that a signed perps transfer exposes the engine transfer ID to callers.
@responses.activate
def test_perps_transfer_returns_receipt():
    """perps_transfer decodes the transfer receipt returned by the engine."""
    responses.add(
        responses.POST,
        f"{_TESTNET_BASE_URL}/api/v1/perps/accounts/transfers",
        json={"code": 0, "data": {"id": 73}},
    )
    result = _signing_client().perps_transfer(
        TransferAssetRequest(
            id=1,
            from_account_id=5655,
            to_account_id=999,
            coin_id=1,
            amount=Decimal("100"),
            type=TransferAssetType.SPOT_WITHDRAW,
        )
    )
    assert result.id == 73


# Validates that a signed spot transfer exposes the engine transfer ID to callers.
@responses.activate
def test_spot_transfer_returns_receipt():
    responses.add(
        responses.POST,
        f"{_TESTNET_BASE_URL}/api/v1/spot/accounts/transfers",
        json={"code": 0, "data": {"id": 74}},
    )

    result = _signing_client().spot_transfer(
        TransferAssetRequest(
            id=2,
            from_account_id=5655,
            to_account_id=999,
            coin_id=1,
            amount=Decimal("100"),
            type=TransferAssetType.EVM_WITHDRAW,
        )
    )

    assert result.id == 74


@responses.activate
def test_place_perps_limit_order_helper():
    """The convenience helper builds a single-order NewOrderRequest under the hood."""
    captured = {}

    def callback(req):
        captured["body"] = json.loads(req.body)
        return (
            200,
            {},
            json.dumps(
                {
                    "code": 0,
                    "data": [{"orderID": 1, "clOrdID": "abc", "status": "NEW"}],
                }
            ),
        )

    responses.add_callback(
        responses.POST,
        f"{_TESTNET_BASE_URL}/api/v1/perps/trade/orders",
        callback=callback,
        content_type="application/json",
    )

    res = _signing_client().place_perps_limit_order(
        account_id=5655,
        symbol_id=1,
        cl_ord_id="abc",
        side=OrderSide.BUY,
        position_side=PositionSide.LONG,
        time_in_force=TimeInForce.GTC,
        price=Decimal("50000"),
        quantity=Decimal("0.01"),
    )
    assert res[0].order_id == 1

    body = captured["body"]
    assert body["accountID"] == 5655
    assert body["symbolID"] == 1
    assert len(body["orders"]) == 1
    o = body["orders"][0]
    assert o["clOrdID"] == "abc"
    assert o["price"] == "50000"
    assert o["quantity"] == "0.01"
    assert o["side"] == int(OrderSide.BUY)


# ── 5. Auth gating ──────────────────────────────────────────────────────────


def test_trading_method_without_key_raises():
    """Calling any signed method without a configured private key raises NotAuthenticatedError."""
    c = _read_only_client()
    with pytest.raises(NotAuthenticatedError):
        c.update_margin(UpdateMarginRequest(account_id=1, symbol_id=1, amount=Decimal("0")))


# ── 6. P0 additions: klines / public trades / history / schedule-cancel ─────


@responses.activate
def test_perps_klines_filter_and_decoding():
    """perps_klines passes interval/startTime/endTime/limit query params and decodes
    the single-letter JSON keys (t, o, h, l, c, v, q) into Candle dataclasses."""
    captured = {}

    def callback(req):
        captured["url"] = req.url
        return (200, {}, json.dumps({"code": 0, "data": [
            {"t": 1776000000000, "o": "70000", "h": "71000", "l": "69800",
             "c": "70500", "v": "12.5", "q": "880000", "n": 42},
        ]}))

    responses.add_callback(
        responses.GET,
        f"{_TESTNET_BASE_URL}/api/v1/perps/markets/BTC-USD/klines",
        callback=callback,
    )

    bars = _read_only_client().perps_klines(
        "BTC-USD", "1h",
        HistoryFilter(start_time=1_000, end_time=9_000, limit=100),
    )
    assert len(bars) == 1
    b = bars[0]
    assert isinstance(b, Candle)
    assert b.start_time == 1776000000000
    assert b.open == "70000" and b.close == "70500"
    assert b.trades == 42
    # Every filter field must appear on the wire.
    url = captured["url"]
    for param in ("interval=1h", "startTime=1000", "endTime=9000", "limit=100"):
        assert param in url, f"missing {param} in {url}"


@responses.activate
def test_perps_public_trades():
    """perps_public_trades decodes the public trade envelope (taker side in S)."""
    responses.add(
        responses.GET,
        f"{_TESTNET_BASE_URL}/api/v1/perps/markets/BTC-USD/trades",
        json={"code": 0, "data": [
            {"t": 7, "T": 1776000000000, "s": "BTC-USD", "S": "SELL",
             "p": "70000", "q": "0.01", "bi": 1, "si": 2},
        ]},
    )
    trades = _read_only_client().perps_public_trades("BTC-USD", limit=10)
    assert len(trades) == 1
    assert isinstance(trades[0], PublicTrade)
    assert trades[0].side == "SELL"
    assert trades[0].buyer == 1
    assert trades[0].seller == 2


@responses.activate
def test_perps_orders_history_filter():
    """perps_orders_history forwards symbol/startTime/endTime/limit to the wire."""
    captured = {}

    def callback(req):
        captured["url"] = req.url
        return (200, {}, json.dumps({"code": 0, "data": [
            {"orderID": 99, "clOrdID": "abc", "symbol": "BTC-USD",
             "side": "BUY", "type": "LIMIT", "timeInForce": "GTC",
             "price": "70000", "origQty": "0.01", "executedQty": "0.01",
             "executedValue": "700", "status": "FILLED"},
        ]}))

    responses.add_callback(
        responses.GET,
        f"{_TESTNET_BASE_URL}/api/v1/perps/accounts/0xabc/orders/history",
        callback=callback,
    )

    orders = _read_only_client().perps_orders_history(
        "0xabc", HistoryFilter(symbol="BTC-USD", limit=50),
    )
    assert len(orders) == 1 and orders[0].status == "FILLED"
    url = captured["url"]
    assert "symbol=BTC-USD" in url and "limit=50" in url


@responses.activate
def test_perps_user_trades_decoding():
    """perps_user_trades decodes trades into UserTrade dataclasses and passes
    supported filters (symbol, startTime, endTime, limit) on the wire."""
    captured = {}

    def callback(req):
        captured["url"] = req.url
        return (200, {}, json.dumps({"code": 0, "data": [
            {"symbol": "BTC-USD", "tradeID": 1, "orderID": 99, "clOrdID": "abc",
             "side": "BUY", "price": "70000", "quantity": "0.01",
             "fee": "0.35", "feeCoin": "USDC", "time": 1776000000000,
             "isMaker": True},
        ]}))

    responses.add_callback(
        responses.GET,
        f"{_TESTNET_BASE_URL}/api/v1/perps/accounts/0xabc/trades",
        callback=callback,
    )

    fills = _read_only_client().perps_user_trades(
        "0xabc", HistoryFilter(symbol="BTC-USD", limit=10),
    )
    assert len(fills) == 1 and isinstance(fills[0], UserTrade)
    assert fills[0].is_maker is True
    assert "symbol=BTC-USD" in captured["url"] and "limit=10" in captured["url"]


@responses.activate
def test_perps_funding_history():
    """perps_funding_history decodes FundingPayment dataclasses."""
    responses.add(
        responses.GET,
        f"{_TESTNET_BASE_URL}/api/v1/perps/accounts/0xabc/fundings",
        json={"code": 0, "data": [
            {"symbol": "BTC-USD", "positionID": 1, "positionSide": "LONG",
             "fundingFee": "0.5", "feeCoin": "USDC", "timestamp": 1776000000000},
        ]},
    )
    payments = _read_only_client().perps_funding_history("0xabc")
    assert len(payments) == 1 and isinstance(payments[0], FundingPayment)
    assert payments[0].funding_fee == "0.5"


@responses.activate
def test_schedule_perps_cancel_signed():
    """schedule_perps_cancel POSTs to /trade/orders/schedule-cancel with signing headers."""
    captured = {}

    def callback(req):
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.body)
        return (200, {}, json.dumps({"code": 0, "data": None}))

    responses.add_callback(
        responses.POST,
        f"{_TESTNET_BASE_URL}/api/v1/perps/trade/orders/schedule-cancel",
        callback=callback,
        content_type="application/json",
    )

    _signing_client().schedule_perps_cancel(
        ScheduleCancelRequest(account_id=5655, scheduled_timestamp=1_776_000_000_000),
    )
    # HTTP body is the params object only (no "type" wrapper).
    assert captured["body"] == {
        "accountID": 5655,
        "scheduledTimestamp": 1_776_000_000_000,
    }
    h = captured["headers"]
    assert h["X-API-Sign"].startswith("0x") and len(h["X-API-Sign"]) == 134
    assert int(h["X-API-Nonce"]) > 0


def test_schedule_cancel_without_key_raises():
    """schedule_perps_cancel raises NotAuthenticatedError when no key is configured."""
    with pytest.raises(NotAuthenticatedError):
        _read_only_client().schedule_perps_cancel(
            ScheduleCancelRequest(account_id=1),
        )


# ── 7. B2 additions: modify / replace for perps ─────────────────────────────


@responses.activate
def test_modify_perps_order_signed():
    """modify_perps_order POSTs to /trade/orders/modify with a signed body containing
    only the fields the caller actually set (price/quantity), not the entire struct."""
    captured = {}

    def callback(req):
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.body)
        return (200, {}, json.dumps({"code": 0, "data": {"code": 0}}))

    responses.add_callback(
        responses.POST,
        f"{_TESTNET_BASE_URL}/api/v1/perps/trade/orders/modify",
        callback=callback,
        content_type="application/json",
    )

    result = _signing_client().modify_perps_order(
        ModifyOrderRequest(
            account_id=5655,
            symbol_id=1,
            order_id=12345,
            price=Decimal("70100"),
        ),
    )
    assert result.code == 0
    # Body contains only the non-None fields — stop_price / quantity / cl_ord_id
    # were not set and must not appear.
    assert captured["body"] == {
        "accountID": 5655,
        "symbolID": 1,
        "orderID": 12345,
        "price": "70100",
    }
    h = captured["headers"]
    assert h["X-API-Sign"].startswith("0x") and len(h["X-API-Sign"]) == 134


@responses.activate
def test_replace_perps_orders_signed():
    """replace_perps_orders POSTs to /trade/orders/replace and decodes the
    returned list into PlaceOrderResult dataclasses (same shape as place)."""
    responses.add(
        responses.POST,
        f"{_TESTNET_BASE_URL}/api/v1/perps/trade/orders/replace",
        json={"code": 0, "data": [
            {"orderID": 100, "clOrdID": "replaced-1", "status": "NEW"},
        ]},
    )

    results = _signing_client().replace_perps_orders(
        ReplaceOrderRequest(
            account_id=5655,
            orders=[
                ReplaceParams(
                    symbol_id=1,
                    cl_ord_id="replaced-1",
                    orig_order_id=99,
                    price=Decimal("70100"),
                    quantity=Decimal("0.02"),
                ),
            ],
        ),
    )
    assert len(results) == 1
    assert results[0].order_id == 100 and results[0].cl_ord_id == "replaced-1"


def test_modify_perps_order_without_key_raises():
    """modify_perps_order raises NotAuthenticatedError without a private key."""
    with pytest.raises(NotAuthenticatedError):
        _read_only_client().modify_perps_order(
            ModifyOrderRequest(account_id=1, symbol_id=1, order_id=99, price=Decimal("1")),
        )


def test_replace_perps_orders_without_key_raises():
    """replace_perps_orders raises NotAuthenticatedError without a private key."""
    with pytest.raises(NotAuthenticatedError):
        _read_only_client().replace_perps_orders(
            ReplaceOrderRequest(account_id=1, orders=[]),
        )
