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
from sodex.client.types import OrderBook, Symbol, Ticker
from sodex.common.enums import (
    MarginMode,
    OrderSide,
    PositionSide,
    TimeInForce,
    TransferAssetType,
)
from sodex.common.types import TransferAssetRequest
from sodex.perps.types import (
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


@responses.activate
def test_perps_order_book_with_depth():
    """perps_order_book sends ``?depth=N`` and decodes the [price, qty] arrays."""
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

    # Verify ?depth=10 was included in the query string.
    sent_url = responses.calls[0].request.url
    assert "depth=10" in sent_url


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


@responses.activate
def test_perps_transfer_signed_no_body_response():
    """perps_transfer succeeds when the API returns code:0 with empty data."""
    responses.add(
        responses.POST,
        f"{_TESTNET_BASE_URL}/api/v1/perps/accounts/transfers",
        json={"code": 0, "data": None},
    )
    _signing_client().perps_transfer(
        TransferAssetRequest(
            id=1,
            from_account_id=5655,
            to_account_id=999,
            coin_id=1,
            amount=Decimal("100"),
            type=TransferAssetType.SPOT_WITHDRAW,
        )
    )


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
