"""Usability coverage for methods called directly by the runnable examples."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace
import threading
import time

import pytest
import responses

from examples import transfer_to_evm as transfer_example
from examples.websocket import handle_trade
from sodex.client import Client, Config, NonceManager, TransferReceipt
from sodex.common.enums import PositionSide, TransferAssetType
from sodex.ws import Push


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


# Validates examples can select a network and configure either a wallet or API-key signer from environment variables.
def test_from_env_accepts_hex_key_and_selects_network(monkeypatch):
    monkeypatch.setenv("SODEX_NETWORK", "testnet")
    monkeypatch.setenv("SODEX_PRIVATE_KEY", "0x" + _PRIVATE_KEY_HEX)
    monkeypatch.setenv(
        "SODEX_ACCOUNT_ADDRESS", "0x1111111111111111111111111111111111111111"
    )
    monkeypatch.setenv("SODEX_API_KEY_NAME", "bot")
    monkeypatch.setenv("SODEX_VALUECHAIN_RPC_URL", "https://rpc.test")

    client = Client.from_env()

    assert client.base_url == Client.TESTNET_BASE_URL
    assert client.account_address == "0x1111111111111111111111111111111111111111"
    assert client._cfg.api_key_name == "bot"
    assert client._cfg.valuechain_rpc_url == "https://rpc.test"


# Validates read-only examples can use a master address without any signing secret.
def test_from_env_accepts_read_only_account_address(monkeypatch):
    monkeypatch.delenv("SODEX_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("SODEX_NETWORK", "mainnet")
    monkeypatch.setenv(
        "SODEX_ACCOUNT_ADDRESS", "0x1111111111111111111111111111111111111111"
    )

    client = Client.from_env()

    assert client.address == ""
    assert client.account_address == "0x1111111111111111111111111111111111111111"


# Validates malformed secrets fail during construction rather than on the first signed example action.
def test_from_private_key_rejects_invalid_hex_early():
    with pytest.raises(ValueError, match="32-byte"):
        Client.from_private_key("not-hex")


# Validates the trade example's primary-account and fee-rate discovery endpoints and typed decoding.
@responses.activate
def test_trade_common_state_discovers_account_and_fee_rate():
    client = _client()
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{client.address}/subaccounts",
        json={"code": 0, "data": {"userID": 88, "primaryAccountID": 1010, "subaccounts": []}},
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{client.address}/fee-rate?market=perps&symbol=BTC-USD",
        json={
            "code": 0,
            "data": {
                "makerFeeRate": "0.0001",
                "takerFeeRate": "0.0002",
                "feeTier": 1,
                "stakingTier": 0,
                "makerRebateTier": 0,
            },
        },
    )

    assert client.primary_account_id() == 1010
    assert client.get_fee_rate("perps", symbol="BTC-USD").maker_fee_rate == "0.0001"


# Validates order helpers resolve IDs, default Perps to one-way BOTH, and return REST receipts.
def test_order_helpers_resolve_ids_and_return_order_id(monkeypatch):
    client = _client()
    receipt = SimpleNamespace(order_id=7001)
    calls = []
    monkeypatch.setattr(client, "primary_account_id", lambda: 1010)
    monkeypatch.setattr(client, "_resolve_symbol_id", lambda market, symbol: 7)
    monkeypatch.setattr(
        client,
        "place_perps_limit_order",
        lambda *args: calls.append(("perps", args)) or [receipt],
    )
    monkeypatch.setattr(
        client,
        "place_spot_market_order",
        lambda *args: calls.append(("spot", args)) or [receipt],
    )

    perps = client.perps_order("BTC-USD", True, Decimal("0.01"), limit_price=Decimal("100"))
    spot = client.spot_order("BTC/USDC", False, Decimal("0.02"))

    assert perps.order_id == 7001
    assert spot.order_id == 7001
    assert calls[0][1][1] == 7
    assert calls[0][1][4] == PositionSide.BOTH
    assert calls[1][1][1] == 7


# Validates Spot display names are resolved locally instead of sent as invalid server filters.
@responses.activate
def test_spot_symbols_resolves_display_name_locally():
    endpoint = f"{_BASE_URL}/api/v1/spot/markets/symbols"
    responses.add(
        responses.GET,
        endpoint,
        json={
            "code": 0,
            "data": [
                {
                    "id": 7,
                    "name": "vBTC_vUSDC",
                    "displayName": "BTC/USDC",
                }
            ],
        },
    )

    symbols = _client().spot_symbols("BTC/USDC")

    assert [item.symbol_id for item in symbols] == [7]
    assert responses.calls[0].request.url == endpoint


# Validates the API-key example registers full access by omitting the disabled-permission mask.
def test_approve_agent_returns_ready_to_trade_client(monkeypatch):
    master = _client()
    registered = []
    monkeypatch.setattr(master, "primary_account_id", lambda: 1010)
    monkeypatch.setattr(
        master,
        "add_api_key",
        lambda user, request: registered.append((user, request)),
    )

    generated, trading = master.approve_agent("bot")

    assert generated.name == "bot"
    assert registered[0][0] == master.address
    assert registered[0][1].account_id == 1010
    assert registered[0][1].permissions is None
    assert trading.account_address == master.address
    assert trading.address == generated.address


# Validates the public-trade example consumes every item in Gateway's batched payload.
def test_websocket_trade_example_handles_batched_push(capsys):
    trade = {
        "E": 1766848149693,
        "T": 1766847863273,
        "t": 6275,
        "s": "BTC-USD",
        "S": "BUY",
        "p": "3511.6",
        "q": "0.0268",
    }

    handle_trade(Push(channel="trade", type="update", data=[trade, trade]))

    assert capsys.readouterr().out.count("[trade]") == 2


# Validates transfer examples hide protocol account/coin IDs while preserving direction and returned transfer IDs.
def test_primary_transfer_helpers_map_directions_and_receipts(monkeypatch):
    client = _client()
    requests = []
    monkeypatch.setattr(client, "primary_account_id", lambda: 1010)
    monkeypatch.setattr(client, "_resolve_coin_id", lambda market, coin: 7)
    monkeypatch.setattr(
        client,
        "perps_transfer",
        lambda request: requests.append(request) or TransferReceipt(1),
    )
    monkeypatch.setattr(
        client,
        "spot_transfer",
        lambda request: requests.append(request) or TransferReceipt(2),
    )

    assert client.transfer_perps_to_spot("vUSDC", Decimal("3"), transfer_id=11).id == 1
    assert client.transfer_spot_to_perps("vUSDC", Decimal("4"), transfer_id=12).id == 2
    assert client.transfer_spot_to_evm("vUSDC", Decimal("5"), transfer_id=13).id == 2
    assert [request.type for request in requests] == [
        TransferAssetType.SPOT_WITHDRAW,
        TransferAssetType.PERPS_WITHDRAW,
        TransferAssetType.EVM_WITHDRAW,
    ]


# Validates separate clients using the same signer/network cannot allocate duplicate millisecond nonces.
def test_nonce_manager_is_shared_across_client_instances():
    manager = NonceManager(clock=lambda: 1_000)
    first = Client(
        Config(
            base_url=_BASE_URL,
            chain_id=Client.TESTNET_CHAIN_ID,
            private_key=_PRIVATE_KEY_HEX,
            nonce_manager=manager,
        )
    )
    second = Client(
        Config(
            base_url=_BASE_URL,
            chain_id=Client.TESTNET_CHAIN_ID,
            private_key=_PRIVATE_KEY_HEX,
            nonce_manager=manager,
        )
    )

    assert first._nonce() == 1_000
    assert second._nonce() == 1_001


# Validates one signer key serializes the complete task so later nonces cannot overtake earlier HTTP writes.
def test_nonce_manager_serializes_signed_request_lifecycle():
    manager = NonceManager(clock=lambda: 2_000)
    state_lock = threading.Lock()
    active = 0
    max_active = 0

    def task(nonce):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with state_lock:
            active -= 1
        return nonce

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: manager.run("signer", task), range(2)))

    assert sorted(results) == [2_000, 2_001]
    assert max_active == 1


# Validates Spot and Perps workflow waits poll until the selected coin balance actually changes.
@pytest.mark.parametrize(
    ("wait_method", "balance_method"),
    [
        ("wait_for_spot_balance_change", "spot_balances"),
        ("wait_for_perps_balance_change", "perps_balances"),
    ],
)
def test_engine_balance_waits_observe_real_balance_change(
    monkeypatch, wait_method, balance_method
):
    client = _client()
    responses = [
        [SimpleNamespace(coin="vUSDC", total="10")],
        [SimpleNamespace(coin="vUSDC", total="11")],
    ]
    monkeypatch.setattr(client, balance_method, lambda *args: responses.pop(0))

    result = getattr(client, wait_method)(
        "vUSDC", "10", timeout=1, interval=0
    )

    assert result[0].total == "11"


# Validates the runnable transfer example dispatches and waits for all five documented directions.
@pytest.mark.parametrize(
    ("step", "expected"),
    [
        ("evm-to-spot", ["deposit:spot", "wait:spot"]),
        ("evm-to-perps", ["deposit:perps", "wait:perps"]),
        ("spot-to-perps", ["transfer:spot-perps", "wait:perps"]),
        ("perps-to-spot", ["transfer:perps-spot", "wait:spot"]),
        ("spot-to-evm", ["transfer:spot-evm", "wait:evm"]),
    ],
)
def test_transfer_example_covers_every_direction(monkeypatch, step, expected):
    calls = []

    class FakeClient:
        address = "0x1111111111111111111111111111111111111111"
        account_address = address

        def get_transfer_configs(self, coin):
            return [
                SimpleNamespace(
                    coin=coin,
                    asset_name="vUSDC",
                    token_address="0x2222222222222222222222222222222222222222",
                )
            ]

        def spot_balances(self, user):
            return [SimpleNamespace(coin="vUSDC", total="10")]

        def perps_balances(self, user):
            return [SimpleNamespace(coin="vUSDC", total="10")]

        def deposit_evm_to_engine(self, coin, amount, destination, **kwargs):
            calls.append(f"deposit:{destination}")
            return SimpleNamespace(deposit_tx_hash="0xdeposit")

        def wait_for_spot_balance_change(self, *args, **kwargs):
            calls.append("wait:spot")
            return [SimpleNamespace(coin="vUSDC", total="11")]

        def wait_for_perps_balance_change(self, *args, **kwargs):
            calls.append("wait:perps")
            return [SimpleNamespace(coin="vUSDC", total="11")]

        def transfer_spot_to_perps(self, *args):
            calls.append("transfer:spot-perps")
            return TransferReceipt(1)

        def transfer_perps_to_spot(self, *args):
            calls.append("transfer:perps-spot")
            return TransferReceipt(2)

        def get_valuechain_balance(self, *args):
            return 10

        def transfer_spot_to_evm(self, *args):
            calls.append("transfer:spot-evm")
            return TransferReceipt(3)

        def wait_for_evm_balance_increase(self, *args, **kwargs):
            calls.append("wait:evm")
            return 11

    fake = FakeClient()
    monkeypatch.setenv("SODEX_AMOUNT", "1")
    monkeypatch.setenv("SODEX_TRANSFER_STEP", step)
    monkeypatch.setattr(
        transfer_example.Client, "from_env", classmethod(lambda cls: fake)
    )

    transfer_example.main()

    assert calls == expected
