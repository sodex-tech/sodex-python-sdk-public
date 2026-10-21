"""DCA transport, response, WebSocket, and Nova signature compatibility tests."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
import responses

from sodex.client import APIError, Client, Config, HistoryFilter, NonceManager, NotAuthenticatedError
from sodex.client.types import AccountDcaOrders
from sodex.common.enums import OrderSide
from sodex.common.types import (
    CancelDcaOrderRequest,
    DcaSchedule,
    NewDcaOrderRequest,
    action_payload_hash,
)
from sodex.perps.signer import PerpsSigner
from sodex.spot.signer import SpotSigner
from sodex.ws import DcaOrder as WsDcaOrder

BASE = "https://gateway.example"
USER = "0x1111111111111111111111111111111111111111"
PLAN_ID = 9007199254740993
# Public fixture key 1 and nonce 123 match Nova's generated vectors; never use live.
KEY = bytes.fromhex("00" * 31 + "01")
VECTORS = json.loads((Path(__file__).parent / "fixtures/dca_nova_vectors.json").read_text())


def new_request(payload):
    return NewDcaOrderRequest(
        account_id=payload["accountID"],
        symbol_id=payload["symbolID"],
        side=OrderSide(payload["side"]),
        amount=Decimal(payload["amount"]),
        schedule=DcaSchedule.from_dict(payload["schedule"]),
        min_price=Decimal(payload["minPrice"]) if "minPrice" in payload else None,
        max_price=Decimal(payload["maxPrice"]) if "maxPrice" in payload else None,
        randomize=payload["randomize"],
        immediate=payload["immediate"],
    )


def plan_payload(status="ACTIVE"):
    return {
        "symbol": "BTC-USDC", "orderID": PLAN_ID, "side": "BUY",
        "amount": "100", "schedule": {"kind": "daily", "hour": 0},
        "randomize": False, "immediate": False, "executedQty": "0.001",
        "executedValue": "90", "failures": 2, "createdAt": 1700000000000,
        "nextActiveAt": 1700086400000, "status": status,
    }


@pytest.mark.parametrize("vector", VECTORS)
# Validates every Nova schedule/price-bound hash and both market signing domains.
def test_nova_dca_vectors(vector):
    payload = json.loads(vector["request"])
    if vector["action"] == "cancelDcaOrder":
        request = CancelDcaOrderRequest(payload["accountID"], payload["symbolID"], payload["orderID"])
    else:
        request = new_request(payload)
    assert json.dumps(request.to_json_payload(), separators=(",", ":")) == vector["request"]
    assert action_payload_hash(request).hex() == vector["hash"]
    if "signature" in vector:
        signer_cls = SpotSigner if vector["domain"] == "spot" else PerpsSigner
        signer = signer_cls(chain_id=vector["chainID"], private_key=KEY)
        assert signer.sign_new_dca_order_request(request, 123).hex() == vector["signature"]


# Validates midnight, false flags, omitted optional fields, and exact decimal normalization.
def test_dca_canonical_decimals_and_midnight():
    request = NewDcaOrderRequest(
        account_id=42, symbol_id=7, side=OrderSide.BUY,
        amount=Decimal("9007199254740993.123456789012345678900"),
        schedule=DcaSchedule("daily", 0), min_price=Decimal("1E-18"),
        max_price=Decimal("90000.000"),
    )
    payload = request.to_json_payload()
    assert payload["amount"] == "9007199254740993.1234567890123456789"
    assert payload["minPrice"] == "0.000000000000000001"
    assert payload["maxPrice"] == "90000"
    assert payload["schedule"] == {"kind": "daily", "hour": 0}
    assert payload["randomize"] is False and payload["immediate"] is False
    assert list(payload) == ["accountID", "symbolID", "side", "amount", "schedule", "minPrice", "maxPrice", "randomize", "immediate"]


@pytest.mark.parametrize("market", ["spot", "perps"])
@responses.activate
# Validates all DCA read routes, account filters, terminal plans, and child/plan IDs.
def test_dca_queries(market):
    root = f"{BASE}/api/v1/{market}/accounts/{USER}/dcas"
    responses.get(root, json={"code": 0, "data": {"blockTime": 10, "blockHeight": 20, "dcas": [plan_payload()]}})
    responses.get(root + "/history", json={"code": 0, "data": [plan_payload("PRUNED")]})
    responses.get(root + "/trades", json={"code": 0, "data": [{
        "symbol": "BTC-USDC", "tradeID": PLAN_ID + 2, "orderID": PLAN_ID + 1,
        "clOrdID": f"dca:{PLAN_ID}", "side": "BUY", "price": "90000",
        "quantity": "0.001", "fee": "0.1", "builderFee": "0.02",
        "feeCoin": "vUSDC", "time": 1700000000000, "isMaker": False,
    }]})
    client = Client(Config(base_url=BASE))
    active = getattr(client, f"{market}_dca_orders")(USER, symbol="BTC-USDC", account_id=42)
    history = getattr(client, f"{market}_dca_orders_history")(USER, HistoryFilter(account_id=42, limit=1000, symbol="ignored", start_time=1))
    fills = getattr(client, f"{market}_dca_fill_history")(USER, HistoryFilter(account_id=42, limit=1000, symbol="ignored", start_time=1))
    assert active.block_height == 20 and active.block_time == 10
    assert active.dcas[0].order_id == PLAN_ID
    assert active.dcas[0].schedule.hour == 0
    assert active.dcas[0].min_price is None
    assert history[0].status == "PRUNED"
    assert history[0].failures == 2
    assert fills[0].order_id == PLAN_ID + 1 and fills[0].trade_id == PLAN_ID + 2
    assert fills[0].cl_ord_id == f"dca:{PLAN_ID}"
    assert fills[0].builder_fee == "0.02" and fills[0].timestamp == 1700000000000
    assert "accountID=42" in responses.calls[0].request.url
    assert "symbol=BTC-USDC" in responses.calls[0].request.url
    assert all("limit=1000" in call.request.url for call in responses.calls[1:])


@pytest.mark.parametrize("market", ["spot", "perps"])
@responses.activate
# Validates create/cancel are single results, sign their exact bodies, and use fresh nonces.
def test_dca_signed_submission(market):
    root = f"{BASE}/api/v1/{market}/trade/dcas"
    result = {"code": 0, "data": {"code": 0, "orderID": PLAN_ID}}
    responses.post(root, json=result)
    responses.delete(root, json=result)
    client = Client(Config(base_url=BASE, private_key=KEY, api_key_name="dca-test", nonce_manager=NonceManager()))
    request = NewDcaOrderRequest(42, 7, OrderSide.BUY, Decimal("100"), DcaSchedule("daily", 0))
    cancel = CancelDcaOrderRequest(42, 7, PLAN_ID)
    created = getattr(client, f"place_{market}_dca_order")(request)
    cancelled = getattr(client, f"cancel_{market}_dca_order")(cancel)
    assert created.code == cancelled.code == 0
    assert created.order_id == cancelled.order_id == PLAN_ID
    signer_cls = SpotSigner if market == "spot" else PerpsSigner
    signer = signer_cls(chain_id=286623, private_key=KEY)
    for call, params, sign in zip(responses.calls, [request, cancel], [signer.sign_new_dca_order_request, signer.sign_cancel_dca_order_request]):
        body = json.loads(call.request.body)
        assert body == params.to_json_payload()
        assert "params" not in body
        nonce = int(call.request.headers["X-API-Nonce"])
        assert call.request.headers["X-API-Sign"] == "0x" + sign(params, nonce).hex()
        assert call.request.headers["X-API-Key"] == "dca-test"
    assert int(responses.calls[1].request.headers["X-API-Nonce"]) > int(responses.calls[0].request.headers["X-API-Nonce"])


@pytest.mark.parametrize("market", ["spot", "perps"])
# Validates DCA writes require configured credentials before any network request.
def test_dca_requires_authentication(market):
    client = Client(Config(base_url=BASE))
    with pytest.raises(NotAuthenticatedError):
        getattr(client, f"place_{market}_dca_order")(NewDcaOrderRequest(42, 7, OrderSide.BUY, Decimal("100"), DcaSchedule("daily", 0)))
    with pytest.raises(NotAuthenticatedError):
        getattr(client, f"cancel_{market}_dca_order")(CancelDcaOrderRequest(42, 7, PLAN_ID))


# Validates nil active-plan collections normalize to empty without losing block metadata.
def test_dca_empty_collection():
    active = AccountDcaOrders.from_dict({"blockTime": 0, "blockHeight": 0, "dcas": None})
    assert active.dcas == [] and active.block_height == 0


@responses.activate
# Validates upstream DCA/indexer errors remain visible rather than becoming empty history.
def test_dca_query_error():
    responses.get(f"{BASE}/api/v1/perps/accounts/{USER}/dcas/history", json={"code": -1, "error": "activity indexer unavailable"})
    with pytest.raises(APIError, match="activity indexer unavailable"):
        Client(Config(base_url=BASE)).perps_dca_orders_history(USER)


@pytest.mark.parametrize("status", ["ACTIVE", "CANCELLED", "TERMINATED", "PRUNED"])
# Validates compressed DO plans preserve all statuses, exact uint64 IDs, and optional prices.
def test_websocket_dca_order(status):
    raw = {"s": "BTC-USDC", "i": PLAN_ID, "a": "100", "S": "BUY",
           "sc": {"kind": "monthly", "hour": 0, "day": 31}, "lp": "1", "hp": "90000",
           "r": False, "im": True, "z": "0.001", "v": "90", "f": 0,
           "ct": 1700000000000, "nt": 1700086400000, "st": status}
    order = WsDcaOrder.from_dict(raw)
    assert order.order_id == PLAN_ID and order.status == status
    assert order.schedule.day == 31 and order.schedule.hour == 0
    assert order.min_price == "1" and order.max_price == "90000"
    assert order.randomize is False and order.immediate is True
    assert order.executed_qty == "0.001" and order.executed_value == "90"
    del raw["lp"], raw["hp"]
    assert WsDcaOrder.from_dict(raw).min_price is None
