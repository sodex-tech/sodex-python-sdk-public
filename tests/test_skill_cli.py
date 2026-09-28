"""Behavioral coverage of installed CLI plans, SDK writes and durable recovery."""

import json
import os
from pathlib import Path
import sys
import time

from eth_account import Account
import pytest
import requests
import responses

from sodex.client import Client
from sodex.client.types import (
    AccountAPIKey,
    AccountAPIKeys,
    Balance,
    Order,
    Position,
    Symbol,
    Ticker,
    UserSubaccounts,
    Coin,
    PlaceOrderResult,
    CancelOrderResult,
)

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/sodex/scripts"
sys.path.insert(0, str(SCRIPTS))
import sodex_cli as cli
from cli_state import InputError, State
from cli_trading import normalize, preflight

# Public, unfunded fixture key. Never use it with real assets.
KEY = "01" * 32
OWNER = Account.from_key(KEY).address
PASSWORD = "fixture password"
KEYSTORE = Account.encrypt(KEY, PASSWORD, kdf="pbkdf2", iterations=1000)


class ReadAPI:
    def __init__(self):
        self.symbol = Symbol.from_dict(
            dict(
                id=99,
                name="BTC-USD",
                status="TRADING",
                baseCoin="BTC",
                quoteCoin="USDC",
                tickSize="0.1",
                stepSize="0.001",
                pricePrecision=1,
                quantityPrecision=3,
                minQuantity="0.001",
                maxQuantity="100",
                minPrice="1",
                maxPrice="1000000",
                marketMinQuantity="0.002",
                marketMaxQuantity="10",
                minNotional="10",
                maxNotional="1000000",
                maxLeverage=50,
                initLeverage=10,
                buyLimitUpRatio="0.1",
                sellLimitDownRatio="0.1",
                marketDeviationRatio="0.05",
                takerFee="0.001",
            )
        )
        self.ticker = Ticker.from_dict(
            dict(
                symbol="BTC-USD", lastPx="10000", markPrice="10000", indexPrice="10000"
            )
        )
        self.orders = []
        self.positions = []
        self.history = []
        self.fills = []
        self.balance = "100000"
        self.state = dict(am="100000", ami="10000", S=[dict(s="BTC-USD", l=10, m=2)])
        self.keys = AccountAPIKeys([], [])

    def get_subaccounts(self, owner):
        return UserSubaccounts(1, 123, [])

    def get_api_keys(self, *args, **kwargs):
        return self.keys

    def perps_symbols(self, name):
        return [self.symbol]

    spot_symbols = perps_symbols

    def perps_tickers(self, name):
        return [self.ticker]

    spot_tickers = perps_tickers

    def perps_account_state(self, *args):
        return self.state

    spot_account_state = perps_account_state

    def perps_orders(self, *args, **kwargs):
        return self.orders

    spot_orders = perps_orders

    def perps_positions(self, *args, **kwargs):
        return self.positions

    def perps_coins(self, name):
        return [Coin(1, "USDC", 6)]

    def spot_balances(self, *args):
        return [Balance(1, "USDC", self.balance, "0"), Balance(2, "BTC", "1", "0")]

    def perps_orders_history(self, *args):
        return self.history

    spot_orders_history = perps_orders_history

    def perps_user_trades(self, *args):
        return self.fills

    spot_user_trades = perps_user_trades


@pytest.fixture
def setup(tmp_path):
    state = State(tmp_path)
    state.add_profile("demo", "testnet", OWNER, 123, "")
    profile = state.profile("demo")
    profile.update(signer=OWNER, keystore=KEYSTORE)
    return state, profile, ReadAPI()


def intent(action="orders", **kwargs):
    return dict(action=action, market="perps", symbol="BTC-USD", **kwargs)


def limit(**kwargs):
    return dict(side="buy", type="limit", quantity="0.01", price="10000", **kwargs)


def position(mode="CROSS", size="1"):
    return Position.from_dict(
        dict(
            symbol="BTC-USD",
            size=size,
            positionSide="BOTH",
            marginMode=mode,
            initialMargin="1000",
            leverage=10,
        )
    )


def resting(stop=False):
    return Order.from_dict(
        dict(
            orderID=2**63 + 11,
            clOrdID="original",
            symbol="BTC-USD",
            side="BUY",
            type="LIMIT",
            timeInForce="GTC",
            price="10000",
            origQty="0.1",
            executedQty="0.01",
            status="PARTIALLY_FILLED",
            stopPrice="9000" if stop else None,
        )
    )


# Preflight stays unsigned, preserves exact amounts and stores a fully reviewable SDK payload.
def test_plan_is_durable_and_exact(setup):
    state, profile, api = setup
    operation = cli.prepare(state, profile, intent(orders=[limit()]), api)
    assert operation["state"] == "prepared"
    assert operation["payload"]["orders"][0]["price"] == "10000"
    assert operation["payload"]["orders"][0]["quantity"] == "0.01"
    assert "keystore" not in operation["profile"]
    assert State(state.root).get(operation["id"])["payload"] == operation["payload"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("price", "10000.01"),
        ("quantity", "0.0001"),
        ("quantity", 0.1),
        ("quantity", "NaN"),
        ("quantity", "Infinity"),
        ("quantity", "0"),
        ("price", "12000"),
        ("quantity", "101"),
    ],
)
# Reject precision, float, nonfinite, zero, lot-size and dynamic price-band violations.
def test_invalid_orders_never_create_plan(setup, field, value):
    state, profile, api = setup
    order = limit()
    order[field] = value
    with pytest.raises(InputError):
        cli.prepare(state, profile, intent(orders=[order]), api)
    assert state.recent() == []


# Aggregate the whole Spot batch against available funds, including estimated taker fees.
def test_spot_batch_balance_and_market_lot_checks(setup):
    state, profile, api = setup
    api.balance = "150"
    data = intent(orders=[limit(), limit()])
    data["market"] = "spot"
    with pytest.raises(InputError, match="batch cost"):
        cli.prepare(state, profile, data, api)
    with pytest.raises(InputError, match="Market quantity"):
        cli.prepare(
            state,
            profile,
            intent(orders=[dict(side="buy", type="market", quantity="0.001")]),
            api,
        )


# Build protocol-native attached OCO exits with stable IDs and MARK_PRICE triggers.
def test_bracket_payload(setup):
    state, profile, api = setup
    op = cli.prepare(
        state,
        profile,
        intent("bracket", entry=limit(), take_profit="11000", stop_loss="9000"),
        api,
    )
    orders = op["payload"]["orders"]
    assert [o["modifier"] for o in orders] == [3, 4, 4]
    assert [o["reduceOnly"] for o in orders] == [False, True, True]
    assert [o["stopType"] for o in orders[1:]] == [2, 1]
    assert all(o["triggerType"] == 2 and o["side"] == 2 for o in orders[1:])
    assert all(len(o["clOrdID"]) <= 36 for o in orders)
    assert normalize(op["intent"]) == op["intent"]


@pytest.mark.parametrize(
    "size,kind,price,side",
    [("1", "stop_loss", "9000", 2), ("-1", "take_profit", "9000", 1)],
)
# A global TP/SL follows the actual long/short position and omits fixed quantity.
def test_position_tpsl(setup, size, kind, price, side):
    state, profile, api = setup
    api.positions = [position(size=size)]
    op = cli.prepare(
        state,
        profile,
        intent("tpsl", orders=[dict(stop_type=kind, stop_price=price)]),
        api,
    )
    order = op["payload"]["orders"][0]
    assert order["side"] == side and order["modifier"] == 2 and order["reduceOnly"]
    assert "quantity" not in order
    with pytest.raises(InputError, match="wrong side"):
        cli.prepare(
            state,
            profile,
            intent("tpsl", orders=[dict(stop_type=kind, stop_price="11000")]),
            api,
        )


@pytest.mark.parametrize(
    "stop,method", [(False, "replace_perps_orders"), (True, "modify_perps_order")]
)
# Ordinary amendments use replace; TP/SL amendments use modify and retain the target identity.
def test_amend_routes_by_target(setup, stop, method):
    state, profile, api = setup
    api.orders = [resting(stop)]
    op = cli.prepare(
        state,
        profile,
        intent(
            "amend",
            orders=[dict(order_id=str(api.orders[0].order_id), quantity="0.02")],
        ),
        api,
    )
    assert op["method"] == method
    assert op["preflight"]["targets"][0]["order_id"] == api.orders[0].order_id


# Leverage preserves an occupied symbol's mode; isolated margin enforces precision and reserve limits.
def test_leverage_and_margin_boundaries(setup):
    state, profile, api = setup
    api.positions = [position()]
    with pytest.raises(InputError, match="margin mode"):
        cli.prepare(
            state, profile, intent("leverage", leverage=5, margin_mode="isolated"), api
        )
    assert (
        cli.prepare(
            state, profile, intent("leverage", leverage=5, margin_mode="cross"), api
        )["payload"]["leverage"]
        == 5
    )
    api.positions = [position("ISOLATED")]
    assert (
        cli.prepare(state, profile, intent("margin", amount="-10"), api)["payload"][
            "amount"
        ]
        == "-10"
    )
    for value in ("0.0000001", "-1000", "10001"):
        with pytest.raises(InputError):
            cli.prepare(state, profile, intent("margin", amount=value), api)


@pytest.mark.parametrize(
    "mask,action,allowed",
    [
        (1, "orders", False),
        (1, "cancel", True),
        (2, "orders", True),
        (3, "cancel", False),
    ],
)
# Check registered signer, expiry and disable-mask permissions, including cancel-only keys.
def test_key_permissions(setup, mask, action, allowed):
    state, profile, api = setup
    profile["api_key_name"] = "bot"
    api.keys.perps = [
        AccountAPIKey("bot", "EIP712", OWNER, int(time.time() * 1000) + 10000, mask)
    ]
    api.orders = [resting()]
    data = intent(
        action,
        orders=(
            [limit()] if action == "orders" else [dict(order_id=api.orders[0].order_id)]
        ),
    )
    if allowed:
        assert cli.prepare(state, profile, data, api)["state"] == "prepared"
    else:
        with pytest.raises(InputError, match="permit"):
            cli.prepare(state, profile, data, api)


@responses.activate
# A signed SDK batch preserves item failures and uint64 IDs; accepted HTTP is not all-order success.
def test_execute_partial_and_no_duplicate(setup):
    state, profile, api = setup
    op = cli.prepare(state, profile, intent(orders=[limit(), limit()]), api)
    items = op["payload"]["orders"]
    responses.post(
        Client.TESTNET_BASE_URL + "/api/v1/perps/trade/orders",
        json={
            "code": 0,
            "data": [
                dict(code=0, orderID=2**63 + 1, clOrdID=items[0]["clOrdID"]),
                dict(
                    code=4001, error="insufficient margin", clOrdID=items[1]["clOrdID"]
                ),
            ],
        },
    )

    def factory(context, **kwargs):
        return cli.client(context, **kwargs) if kwargs else api

    done = cli.execute(state, op["id"], profile, PASSWORD, factory)
    assert done["state"] == "partial"
    receipt = done["events"][-1]["data"]["receipt"]
    assert receipt[0]["order_id"] == 2**63 + 1 and receipt[1]["code"] == 4001
    request = responses.calls[0].request
    assert json.loads(request.body) == op["payload"]
    assert request.headers["X-API-Chain"] == str(Client.TESTNET_CHAIN_ID)
    assert request.headers["X-API-Sign"].startswith("0x01")
    with pytest.raises(InputError, match="already been attempted"):
        cli.execute(State(state.root), op["id"], profile, PASSWORD, factory)
    assert len(responses.calls) == 1


@responses.activate
# A lost response leaves an ambiguous durable intent; restart reconciliation reads the original ID only.
def test_timeout_restart_reconcile(setup):
    state, profile, api = setup
    op = cli.prepare(state, profile, intent(orders=[limit()]), api)
    responses.post(
        Client.TESTNET_BASE_URL + "/api/v1/perps/trade/orders",
        body=requests.Timeout("secret proxy password"),
    )
    done = cli.execute(
        state,
        op["id"],
        profile,
        PASSWORD,
        lambda context, **kwargs: cli.client(context, **kwargs) if kwargs else api,
    )
    assert done["state"] == "unknown"
    api.history = [
        Order.from_dict(
            dict(
                orderID=45,
                clOrdID=op["payload"]["orders"][0]["clOrdID"],
                status="FILLED",
            )
        )
    ]
    resumed = cli.reconcile(State(state.root), op["id"], api)
    observation = resumed["events"][-1]["data"]
    assert observation["orders"][0]["status"] == "FILLED"
    assert observation["missing_client_order_ids"] == []
    assert "secret proxy password" not in json.dumps(resumed)
    assert len(responses.calls) == 1


# Atomic claims and persisted nonces survive a new process; an interrupted submitting state cannot replay.
def test_claim_nonce_and_process_lock(setup):
    state, profile, api = setup
    op = cli.prepare(state, profile, intent(orders=[limit()]), api)
    second = State(state.root)
    state.transition(op["id"], "prepared", "submitting", {})
    with pytest.raises(InputError):
        second.transition(op["id"], "prepared", "submitting", {})
    first_nonce = state.next_nonce("signer")
    assert second.next_nonce("signer") > first_nonce
    with state.signing_lock(profile):
        with pytest.raises(InputError, match="running command"):
            with second.signing_lock(profile):
                pass
    with pytest.raises(InputError, match="already been attempted"):
        cli.execute(second, op["id"], profile, PASSWORD)


# Keystores are encrypted, files are private, exports omit secrets, and wrong passwords cannot submit.
def test_profile_encryption_and_redaction(tmp_path, monkeypatch, capsys):
    state = State(tmp_path)
    # Fast standard PBKDF2 parameters are confined to this fixture; production uses library defaults.
    monkeypatch.setattr(Account, "encrypt", lambda key, password: KEYSTORE)
    public = state.add_profile("test", "testnet", OWNER, None, "", KEY, PASSWORD)
    stored = state.profile("test")
    assert Account.decrypt(stored["keystore"], PASSWORD).hex() == KEY
    assert "keystore" not in public and "private_key" not in public
    assert os.stat(state.profile_path("test")).st_mode & 0o777 == 0o600
    assert os.stat(tmp_path).st_mode & 0o777 == 0o700
    assert KEY not in state.profile_path("test").read_text()
    assert (
        cli.main(["--state-dir", str(tmp_path), "--profile", "test", "profile", "show"])
        == 0
    )
    assert "crypto" not in capsys.readouterr().out
    with pytest.raises(InputError):
        state.add_profile("test", "testnet", OWNER, None, "")


# Syntax errors remain machine-readable, and large JSON identifiers are emitted without precision loss.
def test_json_output_contract(tmp_path, capsys):
    assert cli.main(["--state-dir", str(tmp_path), "read", "unknown"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert not output["ok"] and output["schema_version"] == 1
    assert cli.output_safe({"id": 2**64 - 1, "count": 1}) == {
        "id": str(2**64 - 1),
        "count": 1,
    }


@pytest.mark.parametrize(
    "market,action,http_method,endpoint",
    [
        ("spot", "orders", "POST", "orders/batch"),
        ("perps", "bracket", "POST", "orders"),
        ("perps", "tpsl", "POST", "orders"),
        ("spot", "amend", "POST", "orders/replace"),
        ("perps", "amend", "POST", "orders/replace"),
        ("perps", "modify", "POST", "orders/modify"),
        ("spot", "cancel", "DELETE", "orders/batch"),
        ("perps", "cancel", "DELETE", "orders"),
        ("perps", "leverage", "POST", "leverage"),
        ("perps", "margin", "POST", "margin"),
    ],
)
@responses.activate
# Every write command uses the real SDK signer and its documented endpoint and exact canonical body.
def test_write_routing_and_receipts(setup, market, action, http_method, endpoint):
    state, profile, api = setup
    api.orders = [resting(action == "modify")]
    api.positions = [position("ISOLATED")]
    api.state["S"][0]["m"] = 1
    args = {
        "orders": dict(orders=[limit()]),
        "bracket": dict(entry=limit(), take_profit="11000", stop_loss="9000"),
        "tpsl": dict(orders=[dict(stop_type="stop_loss", stop_price="9000")]),
        "amend": dict(
            orders=[dict(original_client_order_id="original", price="10001")]
        ),
        "modify": dict(
            orders=[dict(original_client_order_id="original", stop_price="9001")]
        ),
        "cancel": dict(orders=[dict(original_client_order_id="original")]),
        "leverage": dict(leverage=5, margin_mode="isolated"),
        "margin": dict(amount="1.25"),
    }[action]
    data = intent("amend" if action == "modify" else action, **args)
    data["market"] = market
    op = cli.prepare(state, profile, data, api)
    items = op["payload"].get("orders", op["payload"].get("cancels"))
    result = (
        [
            dict(
                code=0,
                clOrdID=i.get("clOrdID", "original"),
                orderID=i.get("orderID", index + 1),
            )
            for index, i in enumerate(items)
        ]
        if items
        else None
    )
    responses.add(
        http_method,
        Client.TESTNET_BASE_URL + "/api/v1/" + market + "/trade/" + endpoint,
        json=dict(code=0, data=result),
    )
    finished = cli.execute(
        state,
        op["id"],
        profile,
        PASSWORD,
        lambda context, **kwargs: cli.client(context, **kwargs) if kwargs else api,
    )
    assert finished["state"] == "accepted"
    assert len(responses.calls) == 1
    assert json.loads(responses.calls[0].request.body) == op["payload"]


# Empty, truncated, duplicate and unrecognized batch receipts never become all-success results.
def test_ambiguous_batch_receipts():
    payload = dict(orders=[dict(clOrdID="a"), dict(clOrdID="b")])
    success = PlaceOrderResult.from_dict(dict(code=0, clOrdID="a", orderID=1))
    failure = PlaceOrderResult.from_dict(dict(code=12, error="bad payload"))
    assert cli.receipt_state([], payload) == "unknown"
    assert cli.receipt_state([success], payload) == "unknown"
    assert cli.receipt_state([success, success], payload) == "unknown"
    assert cli.receipt_state([failure], payload) == "rejected"
    assert (
        cli.receipt_state(
            [PlaceOrderResult.from_dict(dict(code=0, clOrdID="a"))],
            dict(orders=[dict(clOrdID="a")]),
        )
        == "unknown"
    )


# Correlate failed Perps cancellations by the saved original client ID when no order ID is returned.
def test_cancel_partial_receipt():
    result = [
        CancelOrderResult.from_dict(dict(code=0, clOrdID="a", orderID=1)),
        CancelOrderResult.from_dict(dict(code=12, clOrdID="b", error="already filled")),
    ]
    payload = dict(cancels=[dict(orderID=1), dict(orderID=2)])
    targets = [dict(cl_ord_id="a", order_id=1), dict(cl_ord_id="b", order_id=2)]
    assert cli.receipt_state(result, payload, targets) == "partial"


# Fresh preflight detects market changes without sending or consuming the prepared operation.
def test_execution_rechecks_before_signing(setup):
    state, profile, api = setup
    op = cli.prepare(state, profile, intent(orders=[limit()]), api)
    api.symbol.status = "HALT"
    with pytest.raises(InputError, match="not trading"):
        cli.execute(state, op["id"], profile, PASSWORD, lambda context, **kwargs: api)
    assert state.get(op["id"])["state"] == "prepared"
    with pytest.raises(InputError, match="unlock"):
        cli.execute(
            state, op["id"], profile, "wrong password", lambda context, **kwargs: api
        )


@responses.activate
@pytest.mark.parametrize("network", ["mainnet", "testnet"])
# Mainnet and testnet public queries remain unsigned even when secret SDK environment variables exist.
def test_read_cli_ignores_ambient_secrets(tmp_path, monkeypatch, capsys, network):
    monkeypatch.setenv("SODEX_PRIVATE_KEY", "never-load-this-secret")
    monkeypatch.setenv(
        "SODEX_NETWORK", "mainnet" if network == "testnet" else "testnet"
    )
    responses.get(
        "https://" + network + "-gw.sodex.dev/api/v1/perps/markets/tickers",
        json=dict(code=0, data=[dict(symbol="BTC-USD", lastPx="10000")]),
    )
    assert (
        cli.main(
            [
                "--state-dir",
                str(tmp_path),
                "read",
                "quote",
                "--network",
                network,
                "--symbol",
                "BTC-USD",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["result"]["network"] == network
    assert not any(h.startswith("X-API-") for h in responses.calls[0].request.headers)
