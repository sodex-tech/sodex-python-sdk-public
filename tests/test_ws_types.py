"""Typed WebSocket request and account payload tests using real wire shapes."""

from sodex.ws import (
    CHANNEL_TRADE,
    AccountOrderUpdate,
    AccountTrade,
    SubscribeParams,
)


_SPOT_ORDER_UPDATE = {
    "E": 1766849004730,
    "T": 1766848473207,
    "s": "vBTC_vUSDC",
    "c": "MAKER-ADJUST-0-70399739516726",
    "i": 58119,
    "S": "SELL",
    "o": "LIMIT",
    "f": "GTC",
    "p": "102650",
    "q": "0.36734",
    "F": None,
    "X": "NEW",
    "z": "0",
    "v": "0",
    "M": "0.36734",
    "t": None,
    "l": None,
    "L": None,
    "n": None,
    "m": None,
    "x": "NEW",
    "r": None,
}

_ACCOUNT_TRADE = {
    "E": 1766848149693,
    "T": 1766847863273,
    "t": 6275,
    "s": "vETH_vUSDC",
    "i": 51101,
    "c": "MAKER-ADJUST-1-51126829939055",
    "S": "BUY",
    "p": "3511.6",
    "q": "0.0268",
    "f": "0",
    "m": True,
}


# Validates a singular public-trade symbol maps to Gateway's required plural wire field.
def test_public_trade_subscription_uses_symbols_field():
    params = SubscribeParams(channel=CHANNEL_TRADE, symbol="BTC-USD")

    assert params.to_json() == {"channel": "trade", "symbols": ["BTC-USD"]}


# Validates the full documented spot order-update shape, including nullable fill fields.
def test_account_order_update_parses_real_spot_wire_shape():
    update = AccountOrderUpdate.from_dict(_SPOT_ORDER_UPDATE)

    assert update.order_id == 58119
    assert update.cl_ord_id == "MAKER-ADJUST-0-70399739516726"
    assert update.time_in_force == "GTC"
    assert update.margin_frozen == "0.36734"
    assert update.trade_id is None
    assert update.is_maker is None


# Validates perps-only order fields while preserving the common order ID correlation fields.
def test_account_order_update_parses_perps_extensions():
    raw = {
        **_SPOT_ORDER_UPDATE,
        "ps": "BOTH",
        "R": False,
        "sp": "90000",
        "st": "STOP_LOSS",
        "tt": "MARK_PRICE",
        "pid": 12,
        "poid": 11,
        "aoids": [13, 14],
    }

    update = AccountOrderUpdate.from_dict(raw)

    assert update.order_id == 58119
    assert update.position_side == "BOTH"
    assert update.reduce_only is False
    assert update.position_id == 12
    assert update.attached_order_ids == [13, 14]


# Validates account-trade parsing exposes REST-correlatable order IDs and fill details.
def test_account_trade_parses_real_wire_shape():
    trade = AccountTrade.from_dict({**_ACCOUNT_TRADE, "d": "Long"})

    assert trade.trade_id == 6275
    assert trade.order_id == 51101
    assert trade.cl_ord_id == "MAKER-ADJUST-1-51126829939055"
    assert trade.quantity == "0.0268"
    assert trade.is_maker is True
    assert trade.direction == "Long"
