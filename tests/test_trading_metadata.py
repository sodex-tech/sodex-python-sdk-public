"""Gateway metadata and per-item receipts required by order preflight."""

import pytest
import responses

from sodex.client import Client, Config
from sodex.client.types import Symbol, PlaceOrderResult, CancelOrderResult, Order, APIResponse


# Preserve market-specific filters exactly, including zero limits and leverage metadata.
def test_symbol_preserves_preflight_filters():
    symbol = Symbol.from_dict({
        "marketMinQuantity": "0.00001", "marketMaxQuantity": "100",
        "maxNotional": "0", "buyLimitUpRatio": "0.05",
        "sellLimitDownRatio": "0.06", "marketDeviationRatio": "0.1",
        "initLeverage": 10,
    })
    assert symbol.market_min_quantity == "0.00001"
    assert symbol.market_max_quantity == "100"
    assert symbol.max_notional == "0"
    assert symbol.buy_limit_up_ratio == "0.05"
    assert symbol.sell_limit_down_ratio == "0.06"
    assert symbol.market_deviation_ratio == "0.1"
    assert symbol.init_leverage == 10


# Distinguish accepted and rejected per-item receipts, including absent order IDs.
@pytest.mark.parametrize("receipt", [PlaceOrderResult, CancelOrderResult])
def test_receipt_preserves_item_errors(receipt):
    accepted = receipt.from_dict({"code": 0, "orderID": 2**64 - 1, "clOrdID": "a"})
    rejected = receipt.from_dict({"code": 4001, "error": "insufficient margin", "clOrdID": "b"})
    assert accepted.code == 0 and accepted.order_id == 2**64 - 1
    assert rejected.code == 4001 and rejected.error == "insufficient margin"
    assert rejected.order_id is None
    if receipt is CancelOrderResult:
        assert receipt.from_dict({"code": 0, "clOrdID": "cancel", "origClOrdID": "a"}).orig_cl_ord_id == "a"


# Preserve optional spot-order fields and builder settings without fabricating perps flags.
def test_spot_order_optional_fields():
    order = Order.from_dict({
        "orderID": 42, "clOrdID": "one", "symbol": "BTC-USDC",
        "executedQty": "0", "executedValue": "0", "status": "NEW",
        "builder": {"builderID": 9, "feeRate": 20},
    })
    assert order.price is None and order.orig_qty is None and order.funds is None
    assert order.reduce_only is None
    assert order.builder.builder_id == 9 and order.builder.fee_rate == 20


# Match Gateway's success and error envelope fields, including empty data.
def test_api_response_envelope_shape():
    response = APIResponse(code=0, timestamp=123, data=None)
    assert response.data is None and response.error is None
    assert APIResponse(code=-1, timestamp=124, error="failed").error == "failed"


@pytest.mark.parametrize("market,resource", [("spot", "orders"), ("perps", "orders"), ("perps", "positions")])
@responses.activate
# Forward an explicit snapshot limit without changing account/symbol filtering or authentication.
def test_account_snapshot_limit(market, resource):
    responses.get(f"https://testnet-gw.sodex.dev/api/v1/{market}/accounts/0xowner/{resource}",
        json={"code": 0, "data": {resource: []}},
        match=[responses.matchers.query_param_matcher({"accountID": "123", "symbol": "BTC-USD", "limit": "500"})])
    api = Client(Config(base_url=Client.TESTNET_BASE_URL))
    assert getattr(api, f"{market}_{resource}")("0xowner", symbol="BTC-USD", account_id=123, limit=500) == []
