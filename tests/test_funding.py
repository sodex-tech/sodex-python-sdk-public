"""Funding and external transfer API tests."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
import responses
from eth_abi import decode as abi_decode
from eth_hash.auto import keccak
from eth_keys import keys as eth_keys

from sodex.client import Client, Config
from sodex.client.types import EVMWithdrawRequest


_BASE_URL = "https://testnet-gw.sodex.dev"
_USER = "0x1111111111111111111111111111111111111111"
_PRIVATE_KEY = bytes.fromhex(
    "0123456789012345678901234567890123456789012345678901234567890123"
)


def _client() -> Client:
    return Client(Config(base_url=_BASE_URL))


def _history_data() -> dict:
    return {
        "records": [
            {
                "account": _USER,
                "amount": "1250000",
                "chain": "BASE_ETH",
                "coin": "USDC",
                "decimals": 6,
                "failCode": "",
                "failReason": "",
                "n": "8",
                "receiver": _USER,
                "reportAmount": "1.25",
                "sender": "0x2222222222222222222222222222222222222222",
                "status": "completed",
                "statusTime": 1720000001000,
                "stmp": 1720000000000,
                "token": "0x3333333333333333333333333333333333333333",
                "txHash": "0xabc",
                "originTxHash": "0xdef",
                "type": "deposit",
                "withdrawFee": "0",
                "withdrawId": 42,
            }
        ],
        "total": 1,
    }


# Validates token/chain capability decoding, including distinct custody and bridge fields.
@responses.activate
def test_get_transfer_configs_decodes_custody_and_bridge_settings():
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/asset/config?coin=USDC",
        json={
            "code": 0,
            "data": [
                {
                    "id": 7,
                    "name": "USD Coin",
                    "coin": "USDC",
                    "tokenAddress": "0xtoken",
                    "decimals": 6,
                    "chains": [
                        {
                            "chain": "BASE_ETH",
                            "coinAddress": "0xcoin",
                            "bridgeAddress": "0xbridge",
                            "custodyWithdrawFee": "0.1",
                            "bridgeWithdrawFee": "0.2",
                            "minDepositAmount": "1",
                            "minWithdrawAmount": "2",
                            "custodyDisabled": False,
                        }
                    ],
                }
            ],
        },
    )

    config = _client().get_transfer_configs("USDC")[0]

    assert config.asset_id == 7
    assert config.asset_name == "USD Coin"
    assert config.coin == "USDC"
    assert config.chains[0].bridge_address == "0xbridge"
    assert config.chains[0].custody_withdraw_fee == "0.1"
    assert config.chains[0].bridge_withdraw_fee == "0.2"
    assert config.chains[0].custody_disabled is False
    assert config.chains[0].custody_available is True
    assert config.chains[0].bridge_available is True


# Validates custody deposit-address lookup and typed status decoding.
@responses.activate
def test_get_deposit_address():
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{_USER}/deposit-address?chain=BASE_ETH",
        json={
            "code": 0,
            "data": {"chain": "BASE_ETH", "address": "0xdeposit", "status": "ready"},
        },
    )

    result = _client().get_deposit_address(_USER, "BASE_ETH")

    assert result.address == "0xdeposit"
    assert result.status == "ready"


# Validates latest Gateway v1 uses a public chain-only body for address creation.
@responses.activate
def test_create_deposit_address_uses_latest_chain_only_request():
    client = _client()

    def callback(request):
        assert json.loads(request.body) == {"chain": "TON"}
        assert "X-API-Sign" not in request.headers
        return 200, {}, json.dumps(
            {
                "code": 0,
                "data": {
                    "chain": "TON",
                    "address": "EQ-deposit",
                    "status": "Processing",
                },
            }
        )

    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/user/{_USER}/deposit-address",
        callback=callback,
        content_type="application/json",
    )

    result = client.create_deposit_address(_USER, "TON")

    assert result.address == "EQ-deposit"
    assert result.status == "Processing"


# Validates deposit hash lookup and the multi-record history response shape.
@responses.activate
def test_get_deposit_status():
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/deposit/status?chain=BASE_ETH&txHash=0xabc",
        json={"code": 0, "data": _history_data()},
    )

    result = _client().get_deposit_status("BASE_ETH", "0xabc")

    assert result.total == 1
    assert result.records[0].tx_hash == "0xabc"


# Validates withdrawal lookup by opaque withdrawal ID without inventing numeric coercion.
@responses.activate
def test_get_withdraw_status_by_id():
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/withdraw/status?chain=BASE_ETH&withdrawId=external-42",
        json={"code": 0, "data": _history_data()},
    )

    result = _client().get_withdraw_status("BASE_ETH", withdraw_id="external-42")

    assert result.records[0].status == "completed"


# Validates prepared EVM-withdrawal submission body and sponsored transaction decoding.
@responses.activate
def test_submit_evm_withdraw():
    def callback(request):
        assert json.loads(request.body) == {
            "cmdData": "0x1234",
            "nonce": "7",
            "deadline": "1800000000",
            "signature": "0xsigned",
        }
        return 200, {}, json.dumps(
            {
                "code": 0,
                "data": {
                    "txHash": "0xtx",
                    "senderAddress": "0xsender",
                    "senderNonce": 9,
                },
            }
        )

    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/user/{_USER}/evm-withdraw",
        callback=callback,
        content_type="application/json",
    )

    result = _client().submit_evm_withdraw(
        _USER,
        EVMWithdrawRequest(
            cmd_data="0x1234",
            nonce="7",
            deadline="1800000000",
            signature="0xsigned",
        ),
    )

    assert result.tx_hash == "0xtx"
    assert result.sender_nonce == 9


# Validates ABI encoding, keyed nonce lookup, contract digest lookup, and raw 27/28 signature.
@responses.activate
def test_prepare_evm_withdraw_uses_documented_contract_abi():
    rpc_url = "https://rpc.valuechain.test"
    client = Client(
        Config(
            base_url=_BASE_URL,
            chain_id=286623,
            private_key=_PRIVATE_KEY,
            valuechain_rpc_url=rpc_url,
        )
    )
    digest = bytes.fromhex("ab" * 32)
    seen = {"rpc_calls": 0}

    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/asset/config?coin=USDC",
        json={
            "code": 0,
            "data": [
                {
                    "coin": "USDC",
                    "tokenAddress": "0xtoken",
                    "decimals": 6,
                    "chains": [
                        {
                            "chain": "BASE_ETH",
                            "coinAddress": "0xcoin",
                            "bridgeAddress": "0xbridge",
                            "custodyWithdrawFee": "0.1",
                            "bridgeWithdrawFee": "0.2",
                            "minDepositAmount": "1",
                            "minWithdrawAmount": "1",
                            "custodyDisabled": False,
                        }
                    ],
                }
            ],
        },
    )

    def rpc_callback(request):
        payload = json.loads(request.body)
        call_data = bytes.fromhex(payload["params"][0]["data"][2:])
        seen["rpc_calls"] += 1
        if seen["rpc_calls"] == 1:
            assert call_data[:4] == keccak(b"nonces(address,uint192)")[:4]
            owner, key = abi_decode(["address", "uint192"], call_data[4:])
            assert owner.lower() == client.address.lower()
            assert key == 7
            result = "0x" + (99).to_bytes(32, "big").hex()
        else:
            assert call_data[:4] == keccak(
                b"hashCallForPermit(address,string,bytes,uint256,uint256)"
            )[:4]
            target, command, cmd_data, nonce, deadline = abi_decode(
                ["address", "string", "bytes", "uint256", "uint256"],
                call_data[4:],
            )
            decoded = abi_decode(
                [
                    "string",
                    "string",
                    "string",
                    "uint256",
                    "uint8",
                    "string",
                    "bool",
                ],
                cmd_data,
            )
            assert target.lower() == "0x441bdb33c7d6dc49f627a42c3d71671d50dc2e94"
            assert command == "WithdrawToken"
            assert decoded == (
                "USDC",
                "BASE_ETH",
                "0xreceiver",
                1_250_000,
                1,
                "memo-7",
                True,
            )
            assert nonce == 99
            assert deadline == 1800000000
            result = "0x" + digest.hex()
        return 200, {}, json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})

    responses.add_callback(
        responses.POST, rpc_url, callback=rpc_callback, content_type="application/json"
    )
    responses.add_callback(
        responses.POST, rpc_url, callback=rpc_callback, content_type="application/json"
    )

    request = client.prepare_evm_withdraw(
        coin="USDC",
        chain="BASE_ETH",
        receiver="0xreceiver",
        amount=Decimal("1.25"),
        withdrawal_type="bridge",
        deadline=1800000000,
        nonce_key=7,
        memo="memo-7",
    )

    signature = bytes.fromhex(request.signature[2:])
    recoverable = signature[:-1] + bytes([signature[-1] - 27])
    recovered = eth_keys.Signature(recoverable).recover_public_key_from_msg_hash(digest)
    assert request.nonce == "99"
    assert request.deadline == "1800000000"
    assert signature[-1] in (27, 28)
    assert recovered.to_checksum_address() == client.address
    assert seen["rpc_calls"] == 2


# Validates the human-unit minimum boundary before any chain signing.
@responses.activate
def test_prepare_evm_withdraw_rejects_amount_below_minimum():
    client = Client(
        Config(base_url=_BASE_URL, private_key=_PRIVATE_KEY, valuechain_rpc_url="rpc")
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/asset/config?coin=USDC",
        json={
            "code": 0,
            "data": [
                {
                    "coin": "USDC",
                    "tokenAddress": "0xtoken",
                    "decimals": 6,
                    "chains": [
                        {
                            "chain": "BASE_ETH",
                            "coinAddress": "0xcoin",
                            "bridgeAddress": "0xbridge",
                            "custodyWithdrawFee": "0.1",
                            "bridgeWithdrawFee": "0.2",
                            "minDepositAmount": "1",
                            "minWithdrawAmount": "2",
                            "custodyDisabled": False,
                        }
                    ],
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="below minimum"):
        client.prepare_evm_withdraw(
            "USDC", "BASE_ETH", "0xreceiver", Decimal("1.25")
        )
