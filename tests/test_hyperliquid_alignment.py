"""Usability coverage for methods called directly by the runnable examples."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
import responses

from sodex.client import Client, Config, TransferReceipt
from sodex.common.enums import APIKeyPermission, PositionSide, TransferAssetType


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


# Validates the trade example's Spot and Perps helpers resolve IDs and return the REST order receipt unchanged.
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
    assert calls[0][1][4] == PositionSide.LONG
    assert calls[1][1][1] == 7


# Validates the API-key example generates, registers, and returns a client ready to sign for the master account.
def test_approve_agent_returns_ready_to_trade_client(monkeypatch):
    master = _client()
    registered = []
    monkeypatch.setattr(master, "primary_account_id", lambda: 1010)
    monkeypatch.setattr(
        master,
        "add_api_key",
        lambda user, request: registered.append((user, request)),
    )

    generated, trading = master.approve_agent(
        "bot", permissions=APIKeyPermission.TRADE | APIKeyPermission.CANCEL
    )

    assert generated.name == "bot"
    assert registered[0][0] == master.address
    assert registered[0][1].account_id == 1010
    assert trading.account_address == master.address
    assert trading.address == generated.address


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
