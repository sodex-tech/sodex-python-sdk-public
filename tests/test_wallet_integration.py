"""Regression coverage for current Gateway assets, subscription isolation and signed builders."""

import json
from decimal import Decimal
from unittest.mock import Mock

import pytest

from sodex.client import Client, Config, BuilderParams
from sodex.client.types import CoinTransferConfig
from sodex.common.enums import OrderModifier, OrderSide, OrderType, TimeInForce
from sodex.common.types import action_payload_hash
from sodex.perps.signer import PerpsSigner
from sodex.perps.types import RawOrder, NewOrderRequest
from sodex.spot.types import BatchNewOrderRequest
from sodex.ws import Client as WsClient, SubscribeParams


def asset():
    return {
        "assetName": "USDC",
        "valueChainMetadata": {
            "isNativeToken": False,
            "evmAddress": "0x" + "22" * 20,
            "tokenDecimals": 6,
        },
        "sodexMetadata": {"id": 0, "name": "vUSDC"},
        "chains": [
            {
                "chainName": "BASE_ETH",
                "isNativeToken": False,
                "tokenAddress": "0x" + "33" * 20,
                "custody": {
                    "allowDeposit": False,
                    "allowWithdraw": True,
                    "minDepositAmount": "5",
                    "minWithdrawAmount": "10",
                    "withdrawFee": "",
                },
                "bridge": {
                    "bridgeAddress": "0x" + "44" * 20,
                    "allowDeposit": True,
                    "allowWithdraw": False,
                    "minDepositAmount": "2",
                },
            }
        ],
    }


# Validate new Gateway fields preserve zero asset IDs, per-route limits and independent availability.
def test_current_asset_schema():
    parsed = CoinTransferConfig.from_dict(asset())
    assert (parsed.coin, parsed.decimals, parsed.asset_id, parsed.asset_name) == (
        "USDC",
        6,
        0,
        "vUSDC",
    )
    route = parsed.chains[0]
    assert not route.custody_available and route.bridge_available
    assert route.withdrawal_method("custody").min_withdraw_amount == "10"
    assert route.withdrawal_method("custody").withdraw_fee == ""
    with pytest.raises(ValueError, match="unavailable"):
        route.withdrawal_method("bridge")


# Validate malformed schemas fail rather than reporting zero precision or an enabled route.
def test_missing_schema_fields_fail():
    with pytest.raises(ValueError):
        CoinTransferConfig.from_dict({})
    data = asset()
    del data["valueChainMetadata"]["tokenDecimals"]
    with pytest.raises(KeyError):
        CoinTransferConfig.from_dict(data)
    data = asset()
    data["chains"][0]["custody"]["allowWithdraw"] = "false"
    with pytest.raises(ValueError):
        CoinTransferConfig.from_dict(data)


# Validate native metadata, absent engine registration and aliases do not require an ERC20 address.
def test_native_asset_alias():
    data = asset()
    data.update(assetName="SOSO", sodexMetadata=None)
    data["valueChainMetadata"] = {
        "isNativeToken": True,
        "tokenDecimals": 18,
        "wrappedToken": {"evmAddress": "0x" + "55" * 20},
    }
    c = Client()
    c._get = Mock(return_value=[data])
    parsed, route = c.get_transfer_route("WSOSO", "BASE_ETH")
    assert parsed.token_address == "0x" + "00" * 20
    assert parsed.asset_id is None
    assert parsed.valuechain_metadata["wrappedToken"]["evmAddress"] == "0x" + "55" * 20
    c._get.assert_called_once_with("/api/v1/asset/config", params={"name": "WSOSO"})


# Validate withdrawal checks use the chosen route's limit before performing any RPC signing.
def test_withdrawal_route_limit():
    c = Client(Config(private_key="11" * 32))
    c._get = Mock(return_value=[asset()])
    c._rpc_call = Mock()
    with pytest.raises(ValueError, match="minimum withdrawal"):
        c.prepare_evm_withdraw("USDC", "BASE_ETH", "0x" + "66" * 20, Decimal("6"))
    c._rpc_call.assert_not_called()


def socket():
    c = WsClient("wss://example.invalid")
    c._ws = Mock()
    return c


# Validate independent tickers and mixed-symbol arrays reach only matching subscriber callbacks.
def test_ws_symbol_isolation():
    c = socket()
    btc, eth = Mock(), Mock()
    c.subscribe(SubscribeParams(channel="ticker", symbol="BTC-USD"), btc)
    c.subscribe(SubscribeParams(channel="ticker", symbol="ETH-USD"), eth)
    c._dispatch(
        json.dumps(
            {
                "channel": "ticker",
                "type": "update",
                "data": [{"s": "BTC-USD"}, {"s": "ETH-USD"}],
            }
        )
    )
    assert btc.call_args.args[0].data == [{"s": "BTC-USD"}]
    assert eth.call_args.args[0].data == [{"s": "ETH-USD"}]


# Validate cancelling one ticker sends its own unsubscribe and leaves the other callback registered.
def test_ws_independent_unsubscribe():
    c = socket()
    a = c.subscribe(SubscribeParams(channel="ticker", symbol="BTC-USD"), Mock())
    b = c.subscribe(SubscribeParams(channel="ticker", symbol="ETH-USD"), Mock())
    c._ws.send.reset_mock()
    c.unsubscribe(a)
    sent = [json.loads(x.args[0]) for x in c._ws.send.call_args_list]
    assert any(
        x["op"] == "unsubscribe" and x["params"]["symbols"] == ["BTC-USD"] for x in sent
    )
    assert b in c._subs


# Validate identical subscriptions share cancellation lifetime without prematurely removing the server stream.
def test_ws_duplicate_unsubscribe():
    c = socket()
    a = c.subscribe(SubscribeParams(channel="ticker", symbol="BTC-USD"), Mock())
    b = c.subscribe(SubscribeParams(channel="ticker", symbol="BTC-USD"), Mock())
    c._ws.send.reset_mock()
    c.unsubscribe(a)
    c._ws.send.assert_not_called()
    c.unsubscribe(b)
    assert json.loads(c._ws.send.call_args.args[0])["op"] == "unsubscribe"


# Validate readiness waits for all symbol ACKs, resets on resubscribe and propagates rejection.
def test_ws_ack_lifecycle():
    c = socket()
    sid = c.subscribe(
        SubscribeParams(channel="ticker", symbols=["BTC-USD", "ETH-USD"]), Mock()
    )
    with pytest.raises(TimeoutError):
        c.wait_ready(sid, 0)
    for sym in ["BTC-USD", "ETH-USD"]:
        c._dispatch(
            json.dumps(
                {
                    "op": "subscribe",
                    "id": sid,
                    "success": True,
                    "result": {"symbol": sym},
                }
            )
        )
        if sym == "BTC-USD":
            with pytest.raises(TimeoutError):
                c.wait_ready(sid, 0)
    c.wait_ready(sid, 0)
    c._resubscribe()
    with pytest.raises(TimeoutError):
        c.wait_ready(sid, 0)
    c._dispatch(
        json.dumps({"op": "subscribe", "id": sid, "success": False, "error": "quota"})
    )
    with pytest.raises(RuntimeError, match="quota"):
        c.wait_ready(sid, 0)


# Validate account events cannot silently mix different wallet owners on one socket.
def test_ws_rejects_multiple_account_owners():
    c = socket()
    c.subscribe(SubscribeParams(channel="accountState", user="0x1"), Mock())
    with pytest.raises(ValueError, match="separate"):
        c.subscribe(SubscribeParams(channel="accountState", user="0x2"), Mock())


# Validate builder order, per-order override and omission are present in the payload that gets hashed.
def test_builder_payloads():
    builder = BuilderParams(9, 20)
    override = BuilderParams(10, 30)
    order = RawOrder(
        "one",
        OrderModifier.NORMAL,
        OrderSide.BUY,
        OrderType.LIMIT,
        TimeInForce.GTC,
        price=Decimal("1"),
        quantity=Decimal("2"),
        builder=override,
    )
    request = NewOrderRequest(1010, 1, [order], builder=builder)
    payload = request.to_json_payload()
    assert list(payload)[-1] == "builder"
    assert list(payload["orders"][0])[-1] == "builder"
    assert payload["builder"] == {"id": 9, "fee": 20}
    assert payload["orders"][0]["builder"] == {"id": 10, "fee": 30}
    assert BatchNewOrderRequest(1010, [], builder).to_json_payload()["builder"] == {
        "id": 9,
        "fee": 20,
    }
    assert "builder" not in BatchNewOrderRequest(1010, []).to_json_payload()
    unsigned = NewOrderRequest(1010, 1, [order])
    assert action_payload_hash(request) != action_payload_hash(unsigned)
    signer = PerpsSigner(chain_id=138565, private_key=bytes.fromhex("11" * 32))
    assert signer.sign_new_order_request(request, 123) != signer.sign_new_order_request(
        unsigned, 123
    )


# Validate high-level Spot/Perps helpers forward builder attribution to signed request objects.
def test_high_level_builder_forwarding():
    c = Client()
    c.primary_account_id = Mock(return_value=1010)
    c._resolve_symbol_id = Mock(return_value=1)
    c.place_perps_order = Mock(return_value=[Mock()])
    c.place_spot_orders = Mock(return_value=[Mock()])
    builder = BuilderParams(9, 20)
    for price in (None, Decimal("100")):
        c.perps_order("BTC-USD", True, Decimal("1"), limit_price=price, builder=builder)
        assert (
            c.place_perps_order.call_args.args[0].to_json_payload()["builder"]
            == builder.to_dict()
        )
        c.spot_order(
            "vBTC_vUSDC", True, Decimal("1"), limit_price=price, builder=builder
        )
        assert (
            c.place_spot_orders.call_args.args[0].to_json_payload()["builder"]
            == builder.to_dict()
        )


# Validate candle subscriptions with the same symbol distinguish intervals for object and array frames.
def test_ws_candle_intervals():
    c = socket()
    minute, hour = Mock(), Mock()
    c.subscribe(
        SubscribeParams(channel="candle", symbol="BTC-USD", interval="1m"), minute
    )
    c.subscribe(
        SubscribeParams(channel="candle", symbol="BTC-USD", interval="1h"), hour
    )
    for data in ({"s": "BTC-USD", "i": "1m"}, [{"s": "BTC-USD", "i": "1m"}]):
        c._dispatch(json.dumps({"channel": "candle", "type": "update", "data": data}))
    assert minute.call_count == 2
    hour.assert_not_called()


# Validate close and cancellation wake ACK waiters and closed clients reject new subscriptions.
def test_ws_cancel_and_close_readiness():
    c = socket()
    sid = c.subscribe(SubscribeParams(channel="ticker", symbol="BTC-USD"), Mock())
    pending = c._subs[sid]
    c.unsubscribe(sid)
    assert pending.ready.is_set() and pending.error is not None
    sid = c.subscribe(SubscribeParams(channel="ticker", symbol="ETH-USD"), Mock())
    c.close()
    with pytest.raises(RuntimeError, match="closed"):
        c.wait_ready(sid, 0)
    with pytest.raises(RuntimeError, match="closed"):
        c.subscribe(SubscribeParams(channel="ticker"), Mock())
