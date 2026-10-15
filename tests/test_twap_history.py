"""Read-only TWAP history and open-position funding contracts."""

from urllib.parse import parse_qs, urlsplit

import pytest
import responses

from sodex.client import (
    APIError,
    Client,
    Config,
    HistoryFilter,
    Position,
    TwapOrder,
    UserTrade,
)

_BASE = "https://testnet-gw.sodex.dev"


# Verify both markets decode exact TWAP fields without user/account/symbol IDs.
def test_twap_orders_history():
    payload = {
        "symbol": "BTC-USD",
        "orderID": 9007199254740993, "side": "SELL",
        "quantity": "2", "executedQty": "1.25",
        "executedValue": "125.000000000000000001", "minutes": 5,
        "randomize": True, "reduceOnly": False, "createdAt": 1000,
        "nextActiveAt": 1001,
        "active": False,
    }
    client = Client(Config(base_url=_BASE))
    for market in ("spot", "perps"):
        with responses.RequestsMock() as mock:
            mock.get(f"{_BASE}/api/v1/{market}/accounts/0xabc/twaps/history", json={"code": 0, "data": [payload]})
            read = getattr(client, f"{market}_twap_orders_history")
            rows = read("0xabc", HistoryFilter(
                account_id=9007199254740993, symbol="ignored", start_time=1000,
                end_time=2000, limit=50,
            ))
            assert parse_qs(urlsplit(mock.calls[0].request.url).query) == {
                "accountID": ["9007199254740993"], "limit": ["50"],
            }
        assert rows == [TwapOrder(
            symbol="BTC-USD",
            order_id=9007199254740993, side="SELL", quantity="2",
            executed_qty="1.25", executed_value="125.000000000000000001",
            minutes=5, randomize=True, reduce_only=False, created_at=1000,
            next_active_at=1001, active=False,
        )]
        for field in ("user_id", "account_id", "symbol_id"):
            assert not hasattr(rows[0], field)


# Verify fills reuse UserTrade, preserve exact IDs and fees, and omit unset filters.
def test_twap_fill_history():
    payload = {
        "symbol": "BTC-USD", "tradeID": 9007199254740993,
        "orderID": 9007199254740995, "clOrdID": "twap:1:1", "side": "BUY",
        "price": "100.000000000000000001", "quantity": "0.1",
        "fee": "-0.000000000000000001", "time": 1000,
        "builderFee": "0.000000000000000001", "feeCoin": "vUSDC", "isMaker": True,
    }
    client = Client(Config(base_url=_BASE))
    for market in ("spot", "perps"):
        with responses.RequestsMock() as mock:
            mock.get(f"{_BASE}/api/v1/{market}/accounts/0xabc/twaps/trades", json={"code": 0, "data": [payload]})
            rows = getattr(client, f"{market}_twap_fill_history")("0xabc")
            assert urlsplit(mock.calls[0].request.url).query == ""
        assert rows == [UserTrade(
            symbol="BTC-USD", trade_id=9007199254740993, order_id=9007199254740995,
            cl_ord_id="twap:1:1", side="BUY", price="100.000000000000000001",
            quantity="0.1", fee="-0.000000000000000001", timestamp=1000,
            builder_fee="0.000000000000000001", fee_coin="vUSDC", is_maker=True,
        )]


# Verify existing positions decode signed funding and zero defaults as strings.
def test_positions_cumulative_funding():
    payload = {"positions": [
        {"id": 9007199254740993, "cumFundingFee": "-0.000000000000000001"},
        {"id": 2, "cumFundingFee": "0"},
        {"id": 3, "cumFundingFee": "0"},
    ]}
    with responses.RequestsMock() as mock:
        mock.get(f"{_BASE}/api/v1/perps/accounts/0xabc/positions", json={"code": 0, "data": payload})
        rows = Client(Config(base_url=_BASE)).perps_positions("0xabc")
        assert urlsplit(mock.calls[0].request.url).query == ""
    assert isinstance(rows[0], Position)
    assert [row.position_id for row in rows] == [9007199254740993, 2, 3]
    assert [row.cum_funding_fee for row in rows] == ["-0.000000000000000001", "0", "0"]


# Verify all four TWAP reads return empty lists and propagate nonzero API errors.
def test_twap_reads_empty_and_error():
    client = Client(Config(base_url=_BASE))
    reads = (
        (client.spot_twap_orders_history, "spot/accounts/0xabc/twaps/history"),
        (client.perps_twap_orders_history, "perps/accounts/0xabc/twaps/history"),
        (client.spot_twap_fill_history, "spot/accounts/0xabc/twaps/trades"),
        (client.perps_twap_fill_history, "perps/accounts/0xabc/twaps/trades"),
    )
    for read, path in reads:
        with responses.RequestsMock() as mock:
            mock.get(f"{_BASE}/api/v1/{path}", json={"code": 0, "data": []})
            assert read("0xabc") == []
            mock.get(f"{_BASE}/api/v1/{path}", json={"code": 1, "error": "unavailable"})
            with pytest.raises(APIError, match="unavailable"):
                read("0xabc")


# Verify existing ordinary-history filters remain unchanged and hide internal flags.
def test_history_does_not_send_internal_options():
    client = Client(Config(base_url=_BASE))
    for market in ("spot", "perps"):
        for resource in ("orders", "trades"):
            with responses.RequestsMock() as mock:
                path = "orders/history" if resource == "orders" else "trades"
                mock.get(f"{_BASE}/api/v1/{market}/accounts/0xabc/{path}", json={"code": 0, "data": []})
                method = "orders_history" if resource == "orders" else "user_trades"
                read = getattr(client, f"{market}_{method}")
                read("0xabc", HistoryFilter(symbol="BTC-USD", start_time=100, end_time=200, limit=10))
                assert parse_qs(urlsplit(mock.calls[0].request.url).query) == {
                    "symbol": ["BTC-USD"], "startTime": ["100"], "endTime": ["200"], "limit": ["10"],
                }
