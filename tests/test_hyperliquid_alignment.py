"""Contract and usability tests for Hyperliquid-equivalent Sodex capabilities."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
import responses

from sodex.client import Client, Config
from sodex.common.enums import (
    APIKeyPermission,
    OrderSide,
    PositionSide,
    TransferAssetType,
)
from sodex.common.types import (
    BuilderParams,
    CancelTwapOrderRequest,
    NewTwapOrderRequest,
)
from sodex.perps.types import UpdateCollateralRequest


_BASE_URL = "https://testnet-gw.sodex.dev"
_PRIVATE_KEY_HEX = "0123456789012345678901234567890123456789012345678901234567890123"


def _client() -> Client:
    return Client(
        Config(
            base_url=_BASE_URL,
            chain_id=Client.TESTNET_CHAIN_ID,
            private_key=_PRIVATE_KEY_HEX,
            valuechain_rpc_url="",
        )
    )


def _ok(data):
    return {"code": 0, "data": data}


# Validates zero-boilerplate environment setup for wallet and API-key clients on either network.
def test_from_env_accepts_hex_key_and_selects_network(monkeypatch):
    monkeypatch.setenv("SODEX_NETWORK", "testnet")
    monkeypatch.setenv("SODEX_PRIVATE_KEY", "0x" + _PRIVATE_KEY_HEX)
    monkeypatch.setenv(
        "SODEX_ACCOUNT_ADDRESS", "0x1111111111111111111111111111111111111111"
    )
    monkeypatch.setenv("SODEX_API_KEY_NAME", "bot")
    monkeypatch.setenv("SODEX_VALUECHAIN_RPC_URL", "https://rpc.test")

    client = Client.from_env()

    assert client._cfg.base_url == Client.TESTNET_BASE_URL
    assert client._cfg.chain_id == Client.TESTNET_CHAIN_ID
    assert client._cfg.api_key_name == "bot"
    assert client.account_address == "0x1111111111111111111111111111111111111111"
    assert client._cfg.valuechain_rpc_url == "https://rpc.test"


# Validates read-only users can configure one address without supplying any signing secret.
def test_from_env_accepts_read_only_account_address(monkeypatch):
    monkeypatch.delenv("SODEX_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("SODEX_NETWORK", "mainnet")
    monkeypatch.setenv(
        "SODEX_ACCOUNT_ADDRESS", "0x1111111111111111111111111111111111111111"
    )

    client = Client.from_env()

    assert client.address == ""
    assert client.account_address == "0x1111111111111111111111111111111111111111"
    assert client.base_url == Client.DEFAULT_BASE_URL


# Validates malformed secrets fail at construction with an actionable error instead of failing on first trade.
def test_from_private_key_rejects_invalid_hex_early():
    with pytest.raises(ValueError, match="32-byte"):
        Client.from_private_key("not-hex")


# Validates latest Gateway market-data coverage and the corrected V1 positions envelope.
@responses.activate
def test_market_info_and_positions_cover_hyperliquid_info_equivalents():
    client = _client()
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/spot/markets/coins?coin=vUSDC",
        json=_ok([{"id": 0, "name": "vUSDC", "precision": 6}]),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/spot/markets/miniTickers?symbol=vBTC_vUSDC",
        json=_ok(
            [
                {
                    "symbol": "vBTC_vUSDC",
                    "lastPx": "1",
                    "openPx": "1",
                    "highPx": "2",
                    "lowPx": "0.5",
                    "volume": "10",
                    "quoteVolume": "11",
                    "openTime": 1,
                    "closeTime": 2,
                }
            ]
        ),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/perps/markets/mark-prices?symbol=BTC-USD",
        json=_ok(
            [
                {
                    "symbol": "BTC-USD",
                    "openInterest": "2",
                    "markPrice": "100",
                    "indexPrice": "101",
                    "fundingRate": "0.001",
                    "nextFundingTime": 3,
                }
            ]
        ),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/perps/markets/bookTickers?symbol=BTC-USD",
        json=_ok(
            [
                {
                    "symbol": "BTC-USD",
                    "askPx": "101",
                    "askSz": "2",
                    "bidPx": "100",
                    "bidSz": "3",
                }
            ]
        ),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/perps/accounts/{client.address}/positions?accountID=1010&symbol=BTC-USD",
        json=_ok(
            {
                "positions": [
                    {
                        "id": 7,
                        "symbol": "BTC-USD",
                        "marginMode": "CROSS",
                        "positionSide": "LONG",
                        "size": "0.5",
                        "initialMargin": "10",
                        "avgEntryPrice": "90",
                        "cumOpenCost": "45",
                        "cumTradingFee": "0.1",
                        "cumClosedSize": "0",
                        "avgClosePrice": "0",
                        "maxSize": "0.5",
                        "realizedPnL": "0",
                        "leverage": 5,
                        "active": True,
                        "isTakenOver": False,
                        "takeOverPrice": "0",
                        "createdAt": 1,
                        "updatedAt": 2,
                    }
                ]
            }
        ),
    )

    assert client.spot_coins("vUSDC")[0].coin_id == 0
    assert client.spot_mini_tickers("vBTC_vUSDC")[0].volume == "10"
    assert client.perps_mark_prices("BTC-USD")[0].mark_price == "100"
    assert client.perps_book_tickers("BTC-USD")[0].bid_size == "3"
    position = client.perps_positions(
        client.address, symbol="BTC-USD", account_id=1010
    )[0]
    assert position.position_id == 7
    assert position.size == "0.5"
    assert position.quantity == "0.5"


# Validates Perps open orders retain stop, reduce-only, position, and builder fields from Gateway.
@responses.activate
def test_perps_orders_preserve_advanced_order_context():
    client = _client()
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/perps/accounts/{client.address}/orders?accountID=1010&symbol=BTC-USD",
        json=_ok(
            {
                "orders": [
                    {
                        "symbol": "BTC-USD",
                        "orderID": 9,
                        "clOrdID": "sdk-9",
                        "side": "SELL",
                        "type": "LIMIT",
                        "timeInForce": "GTC",
                        "price": "110",
                        "origQty": "0.1",
                        "status": "NEW",
                        "executedQty": "0",
                        "executedValue": "0",
                        "marginFrozen": "1",
                        "builder": {"builderID": 7, "feeRate": 5},
                        "positionSide": "LONG",
                        "reduceOnly": True,
                        "stopPrice": "105",
                        "stopType": "STOP_LOSS",
                        "triggerType": "MARK_PRICE",
                        "positionID": 6,
                        "primaryOrderID": 8,
                        "attachedOrderIDs": [10, 11],
                    }
                ]
            }
        ),
    )

    order = client.perps_orders(client.address, symbol="BTC-USD", account_id=1010)[0]

    assert order.builder.builder_id == 7
    assert order.reduce_only is True
    assert order.position_side == "LONG"
    assert order.position_id == 6
    assert order.attached_order_ids == [10, 11]


# Validates Hyperliquid-style all-mids derives exact Decimal midpoints from Gateway books.
@responses.activate
def test_all_mids_uses_spot_or_perps_best_quotes_without_float_loss():
    client = _client()
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/perps/markets/bookTickers",
        json=_ok(
            [
                {
                    "symbol": "BTC-USD",
                    "askPx": "100.2",
                    "askSz": "2",
                    "bidPx": "100.1",
                    "bidSz": "3",
                }
            ]
        ),
    )

    assert client.all_mids() == {"BTC-USD": "100.15"}


# Validates account discovery, fee/rate-limit inspection, eligibility, and builder reads in one onboarding pass.
@responses.activate
def test_account_onboarding_discovers_ids_fees_quota_and_builders():
    client = _client()
    user = client.address
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{user}/subaccounts",
        json=_ok(
            {
                "userID": 88,
                "primaryAccountID": 1010,
                "subaccounts": [{"id": 1010, "evmAddress": user}],
            }
        ),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{user}/api-key-eligibility",
        json=_ok({"eligible": True, "accountValue": "100"}),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{user}/fee-rate?market=perps",
        json=_ok(
            {
                "makerFeeRate": "0.0001",
                "takerFeeRate": "0.0004",
                "feeTier": 1,
                "stakingTier": 2,
                "makerRebateTier": 3,
            }
        ),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{user}/ratelimit",
        json=_ok(
            {
                "userID": 88,
                "cumulativeTxNum": 4,
                "cumulativeCancelNum": 2,
                "cumulativeVolume": "10",
                "transactionQuota": 100,
                "transactionQuotaUsed": 4,
                "transactionQuotaRemaining": 96,
                "transactionQuotaOverridden": False,
                "cancelQuota": 50,
                "cancelQuotaUsed": 2,
                "cancelQuotaRemaining": 48,
            }
        ),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{user}/builders",
        json=_ok({"spot": [{"userID": 88, "builderID": 9, "feeRate": 5}], "perps": []}),
    )

    assert client.primary_account_id() == 1010
    assert client.get_api_key_eligibility().eligible is True
    assert client.get_fee_rate("perp").maker_fee_rate == "0.0001"
    assert client.get_transaction_quota().transaction_quota_remaining == 96
    assert client.get_builders().spot[0].builder_id == 9


# Validates a new user can place by symbol with automatic account/symbol discovery and receive an order ID.
@responses.activate
def test_perps_order_is_plug_and_play_and_returns_order_id():
    client = _client()
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{client.address}/subaccounts",
        json=_ok({"userID": 1, "primaryAccountID": 1010, "subaccounts": []}),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/perps/markets/symbols?symbol=BTC-USD",
        json=_ok([{"id": 7, "name": "BTC-USD", "displayName": "BTC-USD"}]),
    )

    def place_callback(request):
        body = json.loads(request.body)
        assert body["accountID"] == 1010
        assert body["symbolID"] == 7
        assert body["orders"][0]["side"] == int(OrderSide.BUY)
        assert body["orders"][0]["quantity"] == "0.01"
        assert body["orders"][0]["type"] == 2
        assert body["builder"] == {"id": 9, "fee": 5}
        assert request.headers["X-API-Sign"].startswith("0x01")
        return (
            200,
            {},
            json.dumps(
                _ok(
                    [
                        {
                            "orderID": 12345,
                            "clOrdID": body["orders"][0]["clOrdID"],
                            "status": "NEW",
                        }
                    ]
                )
            ),
        )

    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/perps/trade/orders",
        callback=place_callback,
        content_type="application/json",
    )

    receipt = client.market_open(
        "BTC-USD", True, Decimal("0.01"), builder=BuilderParams(9, 5)
    )

    assert receipt.order_id == 12345
    assert receipt.cl_ord_id.startswith("sdk-")


# Validates market_close discovers an active long and submits the opposite reduce-only quantity.
@responses.activate
def test_market_close_discovers_position_and_closes_it_by_symbol():
    client = _client()
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/perps/accounts/{client.address}/positions?accountID=1010&symbol=BTC-USD",
        json=_ok(
            {
                "positions": [
                    {
                        "id": 7,
                        "symbol": "BTC-USD",
                        "marginMode": "CROSS",
                        "positionSide": "LONG",
                        "size": "0.5",
                        "initialMargin": "10",
                        "avgEntryPrice": "90",
                        "cumOpenCost": "45",
                        "cumTradingFee": "0.1",
                        "cumClosedSize": "0",
                        "avgClosePrice": "0",
                        "maxSize": "0.5",
                        "realizedPnL": "0",
                        "leverage": 5,
                        "active": True,
                        "isTakenOver": False,
                        "takeOverPrice": "0",
                        "createdAt": 1,
                        "updatedAt": 2,
                    }
                ]
            }
        ),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/perps/markets/symbols?symbol=BTC-USD",
        json=_ok([{"id": 7, "name": "BTC-USD", "displayName": "BTC-USD"}]),
    )

    def close_callback(request):
        body = json.loads(request.body)
        order = body["orders"][0]
        assert order["side"] == int(OrderSide.SELL)
        assert order["quantity"] == "0.5"
        assert order["reduceOnly"] is True
        assert order["positionSide"] == int(PositionSide.LONG)
        return (
            200,
            {},
            json.dumps(
                _ok([{"orderID": 12346, "clOrdID": order["clOrdID"], "status": "NEW"}])
            ),
        )

    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/perps/trade/orders",
        callback=close_callback,
        content_type="application/json",
    )

    receipt = client.market_close("BTC-USD", account_id=1010)

    assert receipt.order_id == 12346


# Validates Spot order/cancel usability resolves IDs, preserves builder data, and returns typed receipts.
@responses.activate
def test_spot_order_and_cancel_are_plug_and_play_and_return_order_ids():
    client = _client()
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{client.address}/subaccounts",
        json=_ok({"userID": 1, "primaryAccountID": 1010, "subaccounts": []}),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{client.address}/subaccounts",
        json=_ok({"userID": 1, "primaryAccountID": 1010, "subaccounts": []}),
    )
    for _ in range(2):
        responses.add(
            responses.GET,
            f"{_BASE_URL}/api/v1/spot/markets/symbols?symbol=vBTC_vUSDC",
            json=_ok([{"id": 8, "name": "vBTC_vUSDC", "displayName": "BTC/USDC"}]),
        )

    def place_callback(request):
        body = json.loads(request.body)
        assert body["accountID"] == 1010
        assert body["orders"][0]["symbolID"] == 8
        assert body["builder"] == {"id": 9, "fee": 5}
        return (
            200,
            {},
            json.dumps(
                _ok(
                    [
                        {
                            "orderID": 222,
                            "clOrdID": body["orders"][0]["clOrdID"],
                            "status": "NEW",
                        }
                    ]
                )
            ),
        )

    def cancel_callback(request):
        body = json.loads(request.body)
        assert body["cancels"][0]["symbolID"] == 8
        assert body["cancels"][0]["orderID"] == 222
        return (
            200,
            {},
            json.dumps(
                _ok(
                    [
                        {
                            "orderID": 222,
                            "clOrdID": body["cancels"][0]["clOrdID"],
                            "status": "CANCELED",
                        }
                    ]
                )
            ),
        )

    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/spot/trade/orders/batch",
        callback=place_callback,
        content_type="application/json",
    )
    responses.add_callback(
        responses.DELETE,
        f"{_BASE_URL}/api/v1/spot/trade/orders/batch",
        callback=cancel_callback,
        content_type="application/json",
    )

    placed = client.spot_order(
        "vBTC_vUSDC",
        True,
        Decimal("0.01"),
        limit_price=Decimal("100"),
        builder=BuilderParams(9, 5),
    )
    cancelled = client.cancel_spot_order("vBTC_vUSDC", order_id=placed.order_id)

    assert placed.order_id == 222
    assert cancelled.order_id == 222


# Validates Hyperliquid-style approve_agent generates, registers, and returns a configured trading client.
@responses.activate
def test_approve_agent_returns_ready_to_trade_client():
    client = _client()
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{client.address}/subaccounts",
        json=_ok({"userID": 1, "primaryAccountID": 1010, "subaccounts": []}),
    )

    def add_key_callback(request):
        body = json.loads(request.body)
        assert body["accountID"] == 1010
        assert body["name"] == "market-maker"
        assert body["permissions"] == 3
        assert len(body["publicKey"]) == 42
        assert request.headers["X-API-Sign"].startswith("0x02")
        return 200, {}, json.dumps(_ok(None))

    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/user/{client.address}/api-keys",
        callback=add_key_callback,
        content_type="application/json",
    )

    generated, trading = client.approve_agent(
        "market-maker",
        permissions=APIKeyPermission.TRADE | APIKeyPermission.CANCEL,
    )

    assert trading.address == generated.address
    assert trading.account_address == client.address
    assert trading._cfg.api_key_name == "market-maker"


# Validates high-level Perps→Spot and Spot→EVM transfers resolve coin IDs and use treasury routing correctly.
@responses.activate
def test_transfer_helpers_hide_protocol_ids_but_preserve_receipts():
    client = _client()
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/perps/markets/coins?coin=vUSDC",
        json=_ok([{"id": 0, "name": "vUSDC", "precision": 6}]),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/spot/markets/coins?coin=vUSDC",
        json=_ok([{"id": 0, "name": "vUSDC", "precision": 6}]),
    )

    def transfer_callback(request):
        body = json.loads(request.body)
        assert body["fromAccountID"] == 1010
        assert body["toAccountID"] == Client.TREASURY_ACCOUNT_ID
        expected = (
            int(TransferAssetType.SPOT_WITHDRAW)
            if "/perps/" in request.url
            else int(TransferAssetType.EVM_WITHDRAW)
        )
        assert body["type"] == expected
        return 200, {}, json.dumps(_ok({"id": body["id"]}))

    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/perps/accounts/transfers",
        callback=transfer_callback,
        content_type="application/json",
    )
    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/spot/accounts/transfers",
        callback=transfer_callback,
        content_type="application/json",
    )

    first = client.transfer_perps_to_spot(
        "vUSDC", Decimal("10"), account_id=1010, transfer_id=1
    )
    second = client.transfer_spot_to_evm(
        "vUSDC", Decimal("10"), account_id=1010, transfer_id=2
    )

    assert first.id == 1
    assert second.id == 2


# Validates Perps/Spot subaccount transfers accept a subaccount address and preserve direction.
@responses.activate
def test_subaccount_transfer_helpers_resolve_owned_address_and_direction():
    client = _client()
    child = "0x2222222222222222222222222222222222222222"
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{client.address}/subaccounts",
        json=_ok(
            {
                "userID": 1,
                "primaryAccountID": 1010,
                "subaccounts": [{"id": 2020, "evmAddress": child}],
            }
        ),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{client.address}/subaccounts",
        json=_ok(
            {
                "userID": 1,
                "primaryAccountID": 1010,
                "subaccounts": [{"id": 2020, "evmAddress": child}],
            }
        ),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/perps/markets/coins?coin=vUSDC",
        json=_ok([{"id": 0, "name": "vUSDC", "precision": 6}]),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/spot/markets/coins?coin=vUSDC",
        json=_ok([{"id": 0, "name": "vUSDC", "precision": 6}]),
    )

    def perps_transfer_callback(request):
        body = json.loads(request.body)
        assert body["fromAccountID"] == 2020
        assert body["toAccountID"] == 1010
        assert body["type"] == int(TransferAssetType.SUBACCOUNT_TRANSFER)
        return 200, {}, json.dumps(_ok({"id": body["id"]}))

    def spot_transfer_callback(request):
        body = json.loads(request.body)
        assert body["fromAccountID"] == 1010
        assert body["toAccountID"] == 2020
        assert body["type"] == int(TransferAssetType.SUBACCOUNT_TRANSFER)
        return 200, {}, json.dumps(_ok({"id": body["id"]}))

    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/perps/accounts/transfers",
        callback=perps_transfer_callback,
        content_type="application/json",
    )
    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/spot/accounts/transfers",
        callback=spot_transfer_callback,
        content_type="application/json",
    )

    perps_receipt = client.transfer_perps_subaccount(
        child,
        "vUSDC",
        Decimal("3"),
        is_deposit=False,
        account_id=1010,
        transfer_id=4,
    )
    spot_receipt = client.transfer_spot_subaccount(
        child,
        "vUSDC",
        Decimal("3"),
        is_deposit=True,
        account_id=1010,
        transfer_id=5,
    )

    assert perps_receipt.id == 4
    assert spot_receipt.id == 5


# Validates TWAP, builder attribution/approval, and testnet collateral signed flows match Gateway actions.
@responses.activate
def test_advanced_exchange_actions_match_gateway_capabilities():
    client = _client()

    def twap_callback(request):
        body = json.loads(request.body)
        assert body == {
            "accountID": 1010,
            "symbolID": 7,
            "side": 1,
            "quantity": "1.5",
            "minutes": 10,
            "randomize": True,
            "reduceOnly": False,
        }
        return 200, {}, json.dumps(_ok({"orderID": 700}))

    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/perps/trade/twaps",
        callback=twap_callback,
        content_type="application/json",
    )
    responses.add(
        responses.DELETE,
        f"{_BASE_URL}/api/v1/perps/trade/twaps",
        json=_ok({"orderID": 700}),
    )
    responses.add(
        responses.POST, f"{_BASE_URL}/api/v1/perps/trade/collateral", json=_ok(None)
    )

    def builder_callback(request):
        assert request.headers["X-API-Sign"].startswith("0x02")
        assert json.loads(request.body) == {
            "accountID": 1010,
            "builderID": 9,
            "maxFeeRate": 20,
        }
        return 200, {}, json.dumps(_ok(None))

    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/user/{client.address}/builders",
        callback=builder_callback,
        content_type="application/json",
    )

    placed = client.place_perps_twap(
        NewTwapOrderRequest(
            account_id=1010,
            symbol_id=7,
            side=OrderSide.BUY,
            quantity=Decimal("1.5"),
            minutes=10,
            randomize=True,
            reduce_only=False,
        )
    )
    cancelled = client.cancel_perps_twap(CancelTwapOrderRequest(1010, 7, 700))
    client.update_collateral(UpdateCollateralRequest(1010, 0, Decimal("2")))
    client.approve_builder_fee(9, 20, account_id=1010)

    assert placed.order_id == 700
    assert cancelled.order_id == 700
    request = client.place_perps_limit_order
    assert callable(request)
    assert BuilderParams(9, 5).to_dict() == {"id": 9, "fee": 5}


# Validates Spot TWAP place/query/cancel uses the same signed request contract as Gateway.
@responses.activate
def test_spot_twap_place_query_and_cancel_are_typed():
    client = _client()
    responses.add(
        responses.POST,
        f"{_BASE_URL}/api/v1/spot/trade/twaps",
        json=_ok({"orderID": 701}),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/spot/accounts/{client.address}/twaps?accountID=1010&symbol=vBTC_vUSDC",
        json=_ok(
            {
                "blockTime": 1,
                "blockHeight": 2,
                "twaps": [
                    {
                        "userID": 1,
                        "accountID": 1010,
                        "symbol": "vBTC_vUSDC",
                        "symbolID": 8,
                        "orderID": 701,
                        "quantity": "1",
                        "side": "BUY",
                        "minutes": 5,
                        "randomize": False,
                        "reduceOnly": False,
                        "executedQty": "0",
                        "executedValue": "0",
                        "createdAt": 1,
                        "nextActiveAt": 2,
                        "active": True,
                    }
                ],
            }
        ),
    )
    responses.add(
        responses.DELETE,
        f"{_BASE_URL}/api/v1/spot/trade/twaps",
        json=_ok({"orderID": 701}),
    )

    placed = client.place_spot_twap(
        NewTwapOrderRequest(1010, 8, OrderSide.BUY, Decimal("1"), 5, False, False)
    )
    queried = client.spot_twap_orders(
        client.address, symbol="vBTC_vUSDC", account_id=1010
    )
    cancelled = client.cancel_spot_twap(CancelTwapOrderRequest(1010, 8, 701))

    assert placed.order_id == 701
    assert queried.twaps[0].order_id == 701
    assert cancelled.order_id == 701


# Validates the custody flow can discover a route, create an empty address, and track the deposit transaction.
@responses.activate
def test_custody_deposit_user_flow_runs_end_to_end_through_public_api():
    client = _client()
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/asset/config?coin=USDC",
        json=_ok(
            [
                {
                    "coin": "USDC",
                    "tokenAddress": "0xtoken",
                    "decimals": 6,
                    "chains": [
                        {
                            "chain": "BASE_ETH",
                            "coinAddress": "0xcoin",
                            "bridgeAddress": "0xbridge",
                            "custodyWithdrawFee": "1",
                            "bridgeWithdrawFee": "2",
                            "minDepositAmount": "1",
                            "minWithdrawAmount": "1",
                            "custodyDisabled": False,
                        }
                    ],
                }
            ]
        ),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{client.address}/deposit-address?chain=BASE_ETH",
        json=_ok({"chain": "BASE_ETH", "address": "", "status": ""}),
    )
    responses.add(
        responses.POST,
        f"{_BASE_URL}/api/v1/user/{client.address}/deposit-address",
        json=_ok({"chain": "BASE_ETH", "address": "0xdeposit", "status": "Enabled"}),
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/deposit/status?chain=BASE_ETH&txHash=0xsource",
        json=_ok(
            {
                "records": [
                    {
                        "chain": "BASE_ETH",
                        "coin": "USDC",
                        "status": "Success",
                        "txHash": "0xsource",
                    }
                ],
                "total": 1,
            }
        ),
    )

    asset, route = client.get_transfer_route("USDC", "BASE_ETH")
    address = client.ensure_deposit_address(route.chain)
    status = client.get_deposit_status(route.chain, "0xsource")

    assert asset.coin == "USDC"
    assert route.custody_available is True and route.bridge_available is True
    assert address.address == "0xdeposit"
    assert status.records[0].status == "Success"
