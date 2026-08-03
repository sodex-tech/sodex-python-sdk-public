"""Aggregate API key lifecycle tests."""

from __future__ import annotations

import json

import responses

from sodex.client import AddAPIKeyRequest, Client, Config, generate_api_key
from sodex.common.enums import APIKeyPermission


_BASE_URL = "https://testnet-gw.sodex.dev"
_MASTER_PRIVATE_KEY = bytes.fromhex(
    "0123456789012345678901234567890123456789012345678901234567890123"
)
_PUBLIC_KEY = "0x3d4595c8742d0a58173a9963c05755b59a8f8256"
_NONCE = 1760373925000


def _master_client() -> Client:
    client = Client(
        Config(base_url=_BASE_URL, chain_id=286623, private_key=_MASTER_PRIVATE_KEY)
    )
    client._nonce = lambda: _NONCE
    return client


# Validates local CSPRNG key creation and the EVM address/private-key relationship.
def test_generate_api_key_returns_local_key_material():
    generated = generate_api_key("my-bot")
    client = Client(Config(private_key=generated.private_key))

    assert generated.name == "my-bot"
    assert len(generated.private_key) == 32
    assert generated.address == client.address


# Validates ordinary AddAPIKey signing against the TypeScript SDK golden vector and headers.
@responses.activate
def test_add_api_key_matches_cross_sdk_signature_vector():
    client = _master_client()
    expected_signature = (
        "0x027b8db803a8ede1748929c6686fa691a561b3e0f8a338503d8d13ccbff7d745"
        "bf51a30ca6f39a4cfd4cc762a1431fe1b5a1a40ba8869808e779ffdc28bcdd0c5201"
    )

    def callback(request):
        assert request.headers["X-API-Sign"] == expected_signature
        assert request.headers["X-API-Nonce"] == str(_NONCE)
        assert request.headers["X-API-Chain"] == "286623"
        assert json.loads(request.body) == {
            "accountID": 1010,
            "name": "api-key-01",
            "type": 1,
            "publicKey": _PUBLIC_KEY,
            "expiresAt": 0,
        }
        return 200, {}, json.dumps({"code": 0, "data": None})

    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/user/{client.address}/api-keys",
        callback=callback,
        content_type="application/json",
    )

    client.add_api_key(
        client.address,
        AddAPIKeyRequest(
            account_id=1010,
            name="api-key-01",
            public_key=_PUBLIC_KEY,
        ),
    )


# Validates disabled-permission masks use the distinct action, field ordering, and digest.
@responses.activate
def test_add_permissioned_api_key_uses_permissioned_action():
    client = _master_client()
    expected_signature = (
        "0x02ce43d9c70b104dab304eea275f977d3adfac035c6705374db783bdbc84a7f2"
        "9902b099ba25c508f3da08e0f606efac67a30310c1bf617232d093161a93f2f9a000"
    )

    def callback(request):
        assert request.headers["X-API-Sign"] == expected_signature
        assert json.loads(request.body)["permissions"] == 3
        return 200, {}, json.dumps({"code": 0, "data": None})

    responses.add_callback(
        responses.POST,
        f"{_BASE_URL}/api/v1/user/{client.address}/api-keys",
        callback=callback,
        content_type="application/json",
    )

    client.add_api_key(
        client.address,
        AddAPIKeyRequest(
            account_id=1010,
            name="api-key-01",
            public_key=_PUBLIC_KEY,
            permissions=APIKeyPermission.TRADE | APIKeyPermission.CANCEL,
        ),
    )


# Validates every documented disabled-permission bit matches the Gateway mask value.
def test_api_key_permission_masks_match_gateway():
    assert APIKeyPermission.TRADE == 1
    assert APIKeyPermission.CANCEL == 2
    assert APIKeyPermission.WITHDRAW == 4
    assert APIKeyPermission.TRANSFER == 8


# Validates account_address distinguishes an API-key signer from its master account.
def test_account_address_falls_back_or_uses_configured_master():
    master = "0x1111111111111111111111111111111111111111"
    api_client = Client(Config(private_key=_MASTER_PRIVATE_KEY, account_address=master))
    wallet_client = Client(Config(private_key=_MASTER_PRIVATE_KEY))

    assert api_client.account_address == master
    assert wallet_client.account_address == wallet_client.address
