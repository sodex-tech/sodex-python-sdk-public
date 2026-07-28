"""HTTP REST client for the Sodex exchange.

Mirrors ``client/client.go`` plus ``client/perps.go`` and ``client/spot.go`` from
the Go public SDK. A single ``Client`` class exposes both market-data
(unauthenticated) and trading (authenticated) methods for the Spark spot and
Bolt perps engines.

Usage::

    from sodex.client import Client, Config

    # Read-only client
    c = Client(Config(base_url=Client.TESTNET_BASE_URL))
    tickers = c.perps_tickers()

    # Authenticated client (private key required for trading methods)
    c = Client(Config(
        base_url=Client.TESTNET_BASE_URL,
        chain_id=Client.TESTNET_CHAIN_ID,
        private_key=bytes.fromhex("…"),
    ))
    sig_required_methods_now_work = c.place_perps_market_order(...)
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, List, Optional, Union
from urllib.parse import urlencode

import requests
from eth_abi import encode as abi_encode
from eth_hash.auto import keccak
from eth_keys import keys as eth_keys

from sodex.common.enums import (
    SignatureType,
    OrderModifier,
    OrderSide,
    OrderType,
    PositionSide,
    TimeInForce,
    WithdrawalType,
)
from sodex.common.types import (
    EIP712Domain,
    ExchangeAction,
    ReplaceOrderRequest,
    ScheduleCancelRequest,
    TransferAssetRequest,
    action_payload_hash,
)
from sodex.perps.signer import PerpsSigner
from sodex.perps.types import (
    CancelOrderRequest as PerpsCancelOrderRequest,
    ModifyOrderRequest,
    NewOrderRequest as PerpsNewOrderRequest,
    RawOrder,
    UpdateLeverageRequest,
    UpdateMarginRequest,
)
from sodex.spot.signer import SpotSigner
from sodex.spot.types import (
    BatchCancelOrderRequest,
    BatchNewOrderItem,
    BatchNewOrderRequest,
)

from .types import (
    AccountInfo,
    AccountAPIKeys,
    AddAPIKeyRequest,
    Balance,
    Candle,
    CancelOrderResult,
    CoinTransferConfig,
    DepositWithdrawalFilter,
    DepositWithdrawalHistory,
    EVMWithdrawRequest,
    EVMWithdrawSubmission,
    FundingPayment,
    HistoryFilter,
    LeverageResult,
    ModifyOrderResult,
    Order,
    OrderBook,
    PlaceOrderResult,
    Position,
    PublicTrade,
    RevokeAPIKeyRequest,
    Symbol,
    Ticker,
    TransferReceipt,
    UserDepositAddress,
    UserTrade,
)

# ── Network constants ────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://mainnet-gw.sodex.dev"
TESTNET_BASE_URL = "https://testnet-gw.sodex.dev"
DEFAULT_CHAIN_ID = 286623
TESTNET_CHAIN_ID = 138565
DEFAULT_VALUECHAIN_RPC_URL = "https://mainnet.valuechain.xyz/"

_PERPS_BASE = "/api/v1/perps"
_SPOT_BASE = "/api/v1/spot"
_USER_BASE = "/api/v1/user"
_DEPOSIT_ADDRESS_VERIFYING_CONTRACT = "0x0101010101010101010101010101010101010101"
_CREATE_DEPOSIT_ADDRESS_TYPE_HASH = keccak(
    b"CreateDepositAddress(uint64 nonce,uint64 deadline,string chain)"
)
_ADD_API_KEY_TYPE_HASH = keccak(
    b"AddAPIKey(uint64 accountID,string name,uint8 keyType,bytes publicKey,uint64 expiresAt,uint64 nonce)"
)
_ADD_PERMISSIONED_API_KEY_TYPE_HASH = keccak(
    b"UserSignedAddPermissionedAPIKeyAction(uint64 chainID,uint64 nonce,uint64 accountID,string name,uint8 keyType,bytes publicKey,uint64 expiresAt,uint64 permissions)"
)
_CALL_FOR_PERMIT_CONTRACT = "0x890B7D142841065E64E5f94a455876e6352A7801"
_WITHDRAW_TOKEN_TARGET = "0x441BDb33C7d6DC49f627a42c3d71671D50DC2e94"
_NONCES_KEYED_SELECTOR = keccak(b"nonces(address,uint192)")[:4]
_HASH_CALL_FOR_PERMIT_SELECTOR = keccak(
    b"hashCallForPermit(address,string,bytes,uint256,uint256)"
)[:4]


# ── Errors ────────────────────────────────────────────────────────────────────


class NotAuthenticatedError(RuntimeError):
    """Raised when a signed method is called without a configured private key."""

    def __init__(self) -> None:
        super().__init__(
            "client: not authenticated — set Config.private_key or SODEX_PRIVATE_KEY"
        )


class APIError(RuntimeError):
    """Raised when the Sodex API returns a non-zero ``code`` in its envelope."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"sodex API error (code {code}): {message}")
        self.code = code
        self.message = message


# ── Config ────────────────────────────────────────────────────────────────────


@dataclass
class Config:
    """Configuration for a :class:`Client` instance."""

    #: API root (no trailing slash). Defaults to mainnet if empty.
    base_url: str = ""
    #: EVM chain ID used for EIP-712 domain separation. Defaults to mainnet (286623) if 0.
    chain_id: int = 0
    #: Raw 32-byte private key. Leave None for read-only (market-data) access.
    private_key: Optional[bytes] = None
    #: API key name (X-API-Key header). Empty = master-wallet authentication.
    api_key_name: str = ""
    #: Master-wallet address when ``private_key`` belongs to an API key.
    account_address: Optional[str] = None
    #: ValueChain JSON-RPC URL used to prepare withdrawal permits.
    valuechain_rpc_url: str = DEFAULT_VALUECHAIN_RPC_URL
    #: HTTP request timeout in seconds.
    timeout: float = 30.0
    #: Optional preconfigured ``requests.Session`` (e.g. with custom retries).
    session: Optional[requests.Session] = None


# ── Client ────────────────────────────────────────────────────────────────────


class Client:
    """HTTP client for the Sodex REST API. Safe to use concurrently from multiple threads."""

    # Re-export constants so callers can do ``Client.TESTNET_BASE_URL`` etc.
    DEFAULT_BASE_URL = DEFAULT_BASE_URL
    TESTNET_BASE_URL = TESTNET_BASE_URL
    DEFAULT_CHAIN_ID = DEFAULT_CHAIN_ID
    TESTNET_CHAIN_ID = TESTNET_CHAIN_ID
    DEFAULT_VALUECHAIN_RPC_URL = DEFAULT_VALUECHAIN_RPC_URL

    def __init__(self, cfg: Optional[Config] = None) -> None:
        self._cfg = cfg or Config()
        if not self._cfg.base_url:
            self._cfg.base_url = DEFAULT_BASE_URL
        if not self._cfg.chain_id:
            self._cfg.chain_id = DEFAULT_CHAIN_ID

        self._http = self._cfg.session or requests.Session()

        self._spot_sgn: Optional[SpotSigner] = None
        self._perps_sgn: Optional[PerpsSigner] = None
        if self._cfg.private_key is not None:
            self._spot_sgn = SpotSigner(self._cfg.chain_id, self._cfg.private_key)
            self._perps_sgn = PerpsSigner(self._cfg.chain_id, self._cfg.private_key)

        # Strict-monotonic nonce counter (mirrors the atomic uint64 in Go).
        self._nonce_lock = threading.Lock()
        self._last_nonce = 0

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def address(self) -> str:
        """Return the checksummed address derived from the configured private key.

        Returns an empty string when no key is configured.
        """
        if self._cfg.private_key is None:
            return ""
        pk = eth_keys.PrivateKey(self._cfg.private_key)
        return pk.public_key.to_checksum_address()

    @property
    def account_address(self) -> str:
        """Return the configured master account, falling back to the signer address."""
        return self._cfg.account_address or self.address

    def _nonce(self) -> int:
        """Return a strictly monotonic uint64 nonce close to ``time.time() * 1000``.

        The Sodex API expects the nonce to be a millisecond timestamp and accepts
        values within ``(now − 2 days, now + 1 day)``. This helper guarantees
        strict monotonicity even under concurrent calls from multiple threads.
        """
        ts = int(time.time() * 1000)
        with self._nonce_lock:
            if ts <= self._last_nonce:
                ts = self._last_nonce + 1
            self._last_nonce = ts
            return ts

    # ── Internal HTTP helpers ─────────────────────────────────────────────────

    def _get(self, path: str, *, params: Optional[dict] = None) -> Any:
        url = self._cfg.base_url + path
        if params:
            # Drop None values and sort keys for a deterministic query string
            # (matches Go's url.Values.Encode which sorts alphabetically).
            filtered = sorted((k, v) for k, v in params.items() if v is not None)
            if filtered:
                url = f"{url}?{urlencode(filtered)}"
        resp = self._http.get(
            url, headers={"Accept": "application/json"}, timeout=self._cfg.timeout
        )
        return self._unwrap(resp)

    def _post(self, path: str, body: Any) -> Any:
        resp = self._http.post(
            self._cfg.base_url + path,
            data=json.dumps(body, separators=(",", ":")),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=self._cfg.timeout,
        )
        return self._unwrap(resp)

    def _rpc_call(self, to: str, data: bytes) -> str:
        resp = self._http.post(
            self._cfg.valuechain_rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_call",
                "params": [{"to": to, "data": "0x" + data.hex()}, "latest"],
            },
            timeout=self._cfg.timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"valuechain RPC error: {body['error']}")
        return str(body["result"])

    def _post_signed(
        self,
        path: str,
        body: Any,
        signature: bytes,
        nonce: int,
    ) -> Any:
        return self._send_signed("POST", path, body, signature, nonce)

    def _delete_signed(
        self,
        path: str,
        body: Any,
        signature: bytes,
        nonce: int,
    ) -> Any:
        return self._send_signed("DELETE", path, body, signature, nonce)

    def _send_signed(
        self,
        method: str,
        path: str,
        body: Any,
        signature: bytes,
        nonce: int,
    ) -> Any:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-Sign": "0x" + signature.hex(),
            "X-API-Nonce": str(nonce),
            "X-API-Chain": str(self._cfg.chain_id),
        }
        if self._cfg.api_key_name:
            headers["X-API-Key"] = self._cfg.api_key_name

        # The HTTP body is the params object only — NOT the full signing payload.
        # This is one of the most common SDK pitfalls.
        resp = self._http.request(
            method,
            self._cfg.base_url + path,
            data=json.dumps(body, separators=(",", ":")),
            headers=headers,
            timeout=self._cfg.timeout,
        )
        return self._unwrap(resp)

    # ── Shared history helpers ───────────────────────────────────────────────
    #
    # The klines / trades / history endpoints share a common query-param shape.
    # These helpers avoid repeating the same URL-building code in perps / spot.

    def _history_params(self, filter: HistoryFilter) -> dict:
        """Translate a HistoryFilter into the query-param names the API expects."""
        params: dict = {}
        if filter.symbol is not None:
            params["symbol"] = filter.symbol
        if filter.start_time is not None:
            params["startTime"] = filter.start_time
        if filter.end_time is not None:
            params["endTime"] = filter.end_time
        if filter.limit is not None:
            params["limit"] = filter.limit
        return params

    def _klines(
        self, base: str, symbol: str, interval: str, filter: HistoryFilter
    ) -> List[Candle]:
        if not interval:
            raise ValueError("interval is required")
        params: dict = {"interval": interval}
        if filter.start_time is not None:
            params["startTime"] = filter.start_time
        if filter.end_time is not None:
            params["endTime"] = filter.end_time
        if filter.limit is not None:
            params["limit"] = filter.limit
        data = self._get(f"{base}/markets/{symbol}/klines", params=params) or []
        return [Candle.from_dict(x) for x in data]

    def _public_trades(self, base: str, symbol: str, limit: int) -> List[PublicTrade]:
        params = {"limit": limit} if limit > 0 else None
        data = self._get(f"{base}/markets/{symbol}/trades", params=params) or []
        return [PublicTrade.from_dict(x) for x in data]

    def _orders_history(
        self, base: str, address: str, filter: HistoryFilter
    ) -> List[Order]:
        data = self._get(
            f"{base}/accounts/{address}/orders/history",
            params=self._history_params(filter),
        ) or []
        return [Order.from_dict(x) for x in data]

    def _user_trades(
        self, base: str, address: str, filter: HistoryFilter
    ) -> List[UserTrade]:
        data = self._get(
            f"{base}/accounts/{address}/trades",
            params=self._history_params(filter),
        ) or []
        return [UserTrade.from_dict(x) for x in data]

    @staticmethod
    def _unwrap(resp: requests.Response) -> Any:
        """Parse the standard ``{code, message, data}`` envelope.

        Raises :class:`APIError` for application-level errors (non-zero ``code``)
        and a generic ``RuntimeError`` for HTTP errors with no envelope.
        """
        body = resp.content
        try:
            envelope = json.loads(body)
        except (ValueError, json.JSONDecodeError):
            envelope = None

        if isinstance(envelope, dict):
            code = envelope.get("code")
            if code not in (None, 0):
                msg = (
                    envelope.get("message")
                    or envelope.get("msg")
                    or envelope.get("error")
                    or body.decode("utf-8", errors="replace")
                )
                raise APIError(int(code), str(msg))
            if 200 <= resp.status_code < 300:
                return envelope.get("data")

        if not (200 <= resp.status_code < 300):
            raise RuntimeError(
                f"client: HTTP {resp.status_code} from {resp.request.method} "
                f"{resp.request.path_url}: {body.decode('utf-8', errors='replace')}"
            )
        return envelope

    # ─────────────────────────────────────────────────────────────────────────
    # Funding — external deposits and withdrawals
    # ─────────────────────────────────────────────────────────────────────────

    def get_transfer_configs(self, coin: Optional[str] = None) -> List[CoinTransferConfig]:
        """Return supported deposit/withdrawal tokens, chains, fees, and limits."""
        data = self._get("/api/v1/asset/config", params={"coin": coin}) or []
        return [CoinTransferConfig.from_dict(x) for x in data]

    def get_deposit_address(self, user_address: str, chain: str) -> UserDepositAddress:
        """Return the user's custody deposit address and provisioning status."""
        data = self._get(
            f"{_USER_BASE}/{user_address}/deposit-address", params={"chain": chain}
        ) or {}
        return UserDepositAddress.from_dict(data)

    def create_deposit_address(
        self,
        user_address: str,
        chain: str,
        *,
        deadline: Optional[int] = None,
        nonce: Optional[int] = None,
    ) -> UserDepositAddress:
        """Create a custody deposit address using the master wallet's EIP-712 signature."""
        if self._cfg.private_key is None:
            raise NotAuthenticatedError()
        if user_address.lower() != self.address.lower():
            raise ValueError("user_address must match the configured private key")

        request_nonce = nonce if nonce is not None else self._nonce()
        request_deadline = deadline if deadline is not None else int(time.time()) + 900
        domain = EIP712Domain(
            name="universal",
            chain_id=self._cfg.chain_id,
            verifying_contract=_DEPOSIT_ADDRESS_VERIFYING_CONTRACT,
        )
        struct_hash = keccak(
            _CREATE_DEPOSIT_ADDRESS_TYPE_HASH
            + request_nonce.to_bytes(32, "big")
            + request_deadline.to_bytes(32, "big")
            + keccak(chain.encode())
        )
        digest = keccak(b"\x19\x01" + domain.domain_separator() + struct_hash)
        signature = eth_keys.PrivateKey(self._cfg.private_key).sign_msg_hash(digest)
        data = self._post(
            f"{_USER_BASE}/{user_address}/deposit-address",
            {
                "chain": chain,
                "nonce": request_nonce,
                "deadline": request_deadline,
                "signature": "0x" + signature.to_bytes().hex(),
            },
        ) or {}
        return UserDepositAddress.from_dict(data)

    def get_deposit_withdrawals(
        self,
        user_address: str,
        filter: Optional[DepositWithdrawalFilter] = None,
    ) -> DepositWithdrawalHistory:
        """Return a filtered page of the user's external transfer history."""
        params = filter.to_params() if filter is not None else None
        data = self._get(
            f"{_USER_BASE}/{user_address}/deposit-withdrawals", params=params
        ) or {}
        return DepositWithdrawalHistory.from_dict(data)

    def get_deposit_status(self, chain: str, tx_hash: str) -> DepositWithdrawalHistory:
        """Look up all deposit records associated with an external transaction hash."""
        data = self._get(
            f"{_USER_BASE}/deposit/status",
            params={"chain": chain, "txHash": tx_hash},
        ) or {}
        return DepositWithdrawalHistory.from_dict(data)

    def get_withdraw_status(
        self,
        chain: str,
        *,
        withdraw_id: Optional[str] = None,
        tx_hash: Optional[str] = None,
    ) -> DepositWithdrawalHistory:
        """Look up a withdrawal by its withdrawal ID or ValueChain transaction hash."""
        data = self._get(
            f"{_USER_BASE}/withdraw/status",
            params={"chain": chain, "withdrawId": withdraw_id, "txHash": tx_hash},
        ) or {}
        return DepositWithdrawalHistory.from_dict(data)

    def submit_evm_withdraw(
        self, user_address: str, request: EVMWithdrawRequest
    ) -> EVMWithdrawSubmission:
        """Submit a prepared and signed ValueChain withdrawal permit."""
        data = self._post(
            f"{_USER_BASE}/{user_address}/evm-withdraw", request.to_json_payload()
        ) or {}
        return EVMWithdrawSubmission.from_dict(data)

    def prepare_evm_withdraw(
        self,
        coin: str,
        chain: str,
        receiver: str,
        amount: Decimal,
        *,
        withdrawal_type: Union[str, WithdrawalType] = WithdrawalType.CUSTODY,
        deadline: Optional[int] = None,
        nonce_key: int = 0,
        memo: str = "",
        failed_back_to_clob: bool = True,
    ) -> EVMWithdrawRequest:
        """Build and sign the documented WithdrawToken permit using live chain nonce/hash calls."""
        if self._cfg.private_key is None:
            raise NotAuthenticatedError()

        configs = self.get_transfer_configs(coin)
        asset = next((x for x in configs if x.coin.lower() == coin.lower()), None)
        if asset is None:
            raise ValueError(f"unsupported withdrawal coin: {coin}")
        chain_config = next(
            (x for x in asset.chains if x.chain.lower() == chain.lower()), None
        )
        if chain_config is None:
            raise ValueError(f"unsupported withdrawal chain for {asset.coin}: {chain}")
        if chain_config.min_withdraw_amount and amount < Decimal(
            chain_config.min_withdraw_amount
        ):
            raise ValueError(
                f"amount is below minimum withdrawal amount {chain_config.min_withdraw_amount}"
            )

        raw_amount = amount * (Decimal(10) ** asset.decimals)
        if raw_amount != raw_amount.to_integral_value():
            raise ValueError(f"amount exceeds {asset.decimals} decimal places")
        if isinstance(withdrawal_type, str):
            try:
                route = WithdrawalType[withdrawal_type.upper()]
            except KeyError as exc:
                raise ValueError("withdrawal_type must be 'custody' or 'bridge'") from exc
        else:
            route = WithdrawalType(withdrawal_type)
        if route == WithdrawalType.CUSTODY and not chain_config.custody_available:
            raise ValueError(f"custody withdrawal is unavailable on {chain_config.chain}")
        if route == WithdrawalType.BRIDGE and not chain_config.bridge_available:
            raise ValueError(f"bridge withdrawal is unavailable on {chain_config.chain}")

        owner = self.address
        nonce_call = _NONCES_KEYED_SELECTOR + abi_encode(
            ["address", "uint192"], [owner, nonce_key]
        )
        permit_nonce = int(
            self._rpc_call(_CALL_FOR_PERMIT_CONTRACT, nonce_call), 16
        )
        request_deadline = deadline if deadline is not None else int(time.time()) + 900
        cmd_data = abi_encode(
            ["string", "string", "string", "uint256", "uint8", "string", "bool"],
            [
                asset.coin,
                chain_config.chain,
                receiver,
                int(raw_amount),
                int(route),
                memo,
                failed_back_to_clob,
            ],
        )
        hash_call = _HASH_CALL_FOR_PERMIT_SELECTOR + abi_encode(
            ["address", "string", "bytes", "uint256", "uint256"],
            [
                _WITHDRAW_TOKEN_TARGET,
                "WithdrawToken",
                cmd_data,
                permit_nonce,
                request_deadline,
            ],
        )
        digest_hex = self._rpc_call(_CALL_FOR_PERMIT_CONTRACT, hash_call)
        digest = bytes.fromhex(digest_hex.removeprefix("0x"))
        if len(digest) != 32:
            raise RuntimeError("valuechain RPC returned an invalid permit digest")
        signature = eth_keys.PrivateKey(self._cfg.private_key).sign_msg_hash(digest)
        signature_bytes = signature.to_bytes()[:-1] + bytes([signature.v + 27])
        return EVMWithdrawRequest(
            cmd_data="0x" + cmd_data.hex(),
            nonce=str(permit_nonce),
            deadline=str(request_deadline),
            signature="0x" + signature_bytes.hex(),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # API keys — aggregate spot/perps lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def get_api_keys(
        self, user_address: str, name: Optional[str] = None
    ) -> AccountAPIKeys:
        """Return API keys registered on spot and perps without merging engines."""
        data = self._get(
            f"{_USER_BASE}/{user_address}/api-keys", params={"name": name}
        ) or {}
        return AccountAPIKeys.from_dict(data)

    def add_api_key(self, user_address: str, request: AddAPIKeyRequest) -> None:
        """Register an EVM API key on both engines with a master-wallet signature."""
        if self._cfg.private_key is None:
            raise NotAuthenticatedError()
        if user_address.lower() != self.address.lower():
            raise ValueError("user_address must match the configured private key")

        public_key = bytes.fromhex(request.public_key.removeprefix("0x"))
        if len(public_key) != 20:
            raise ValueError("public_key must be a 20-byte EVM address")
        nonce = self._nonce()
        domain = EIP712Domain(name="universal", chain_id=self._cfg.chain_id)
        common_fields = (
            request.account_id.to_bytes(32, "big")
            + keccak(request.name.encode())
            + (1).to_bytes(32, "big")
            + keccak(public_key)
            + request.expires_at.to_bytes(32, "big")
        )
        if request.permissions is None:
            struct_hash = keccak(
                _ADD_API_KEY_TYPE_HASH + common_fields + nonce.to_bytes(32, "big")
            )
        else:
            struct_hash = keccak(
                _ADD_PERMISSIONED_API_KEY_TYPE_HASH
                + self._cfg.chain_id.to_bytes(32, "big")
                + nonce.to_bytes(32, "big")
                + common_fields
                + int(request.permissions).to_bytes(32, "big")
            )
        digest = keccak(b"\x19\x01" + domain.domain_separator() + struct_hash)
        signature = bytes([SignatureType.EIP712_UNIVERSAL]) + eth_keys.PrivateKey(
            self._cfg.private_key
        ).sign_msg_hash(digest).to_bytes()
        self._post_signed(
            f"{_USER_BASE}/{user_address}/api-keys",
            request.to_json_payload(),
            signature,
            nonce,
        )

    def revoke_api_key(self, user_address: str, account_id: int, name: str) -> None:
        """Revoke a named API key from both spot and perps engines."""
        if self._cfg.private_key is None:
            raise NotAuthenticatedError()
        if user_address.lower() != self.address.lower():
            raise ValueError("user_address must match the configured private key")

        request = RevokeAPIKeyRequest(account_id=account_id, name=name)
        nonce = self._nonce()
        domain = EIP712Domain(name="universal", chain_id=self._cfg.chain_id)
        digest = ExchangeAction(action_payload_hash(request), nonce).hash(domain)
        signature = bytes([SignatureType.EIP712_UNIVERSAL]) + eth_keys.PrivateKey(
            self._cfg.private_key
        ).sign_msg_hash(digest).to_bytes()
        self._delete_signed(
            f"{_USER_BASE}/{user_address}/api-keys",
            request.to_json_payload(),
            signature,
            nonce,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Perps — Market data (unauthenticated)
    # ─────────────────────────────────────────────────────────────────────────

    def perps_symbols(self) -> List[Symbol]:
        """Return all available perpetuals trading pairs."""
        data = self._get(f"{_PERPS_BASE}/markets/symbols") or []
        return [Symbol.from_dict(x) for x in data]

    def perps_tickers(self) -> List[Ticker]:
        """Return 24-hour rolling stats for all perps pairs."""
        data = self._get(f"{_PERPS_BASE}/markets/tickers") or []
        return [Ticker.from_dict(x) for x in data]

    def perps_order_book(self, symbol: str, depth: int = 0) -> OrderBook:
        """Return the order book snapshot for ``symbol``.

        Pass ``depth <= 0`` to use the API default.
        """
        params = {"depth": depth} if depth > 0 else None
        data = self._get(f"{_PERPS_BASE}/markets/{symbol}/orderbook", params=params)
        return OrderBook.from_dict(data or {}, symbol=symbol)

    def perps_balances(self, address: str) -> List[Balance]:
        """Return asset balances for ``address`` on the perps engine."""
        data = self._get(f"{_PERPS_BASE}/accounts/{address}/balances") or {}
        return [Balance.from_dict(x) for x in (data.get("balances") or [])]

    def perps_orders(self, address: str) -> List[Order]:
        """Return open orders for ``address`` on the perps engine."""
        data = self._get(f"{_PERPS_BASE}/accounts/{address}/orders") or {}
        return [Order.from_dict(x) for x in (data.get("orders") or [])]

    def perps_positions(self, address: str) -> List[Position]:
        """Return open positions for ``address``."""
        data = self._get(f"{_PERPS_BASE}/accounts/{address}/positions") or {}
        # The positions endpoint returns the same wrapper shape as orders.
        return [Position.from_dict(x) for x in (data.get("orders") or [])]

    def perps_klines(
        self, symbol: str, interval: str, filter: Optional[HistoryFilter] = None
    ) -> List[Candle]:
        """Return historical OHLCV candles for a perps symbol.

        ``interval`` is one of: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h,
        1D, 3D, 1W, 1M.
        Only ``filter.start_time`` / ``end_time`` / ``limit`` apply to klines
        (default limit 500, max 1500).
        """
        return self._klines(_PERPS_BASE, symbol, interval, filter or HistoryFilter())

    def perps_public_trades(self, symbol: str, limit: int = 0) -> List[PublicTrade]:
        """Return recent public market trades for a perps symbol (default 50, max 500)."""
        return self._public_trades(_PERPS_BASE, symbol, limit)

    def perps_orders_history(
        self, address: str, filter: Optional[HistoryFilter] = None
    ) -> List[Order]:
        """Return historical (non-open) orders for ``address`` on the perps engine."""
        return self._orders_history(_PERPS_BASE, address, filter or HistoryFilter())

    def perps_user_trades(
        self, address: str, filter: Optional[HistoryFilter] = None
    ) -> List[UserTrade]:
        """Return the user's fill history on the perps engine."""
        return self._user_trades(_PERPS_BASE, address, filter or HistoryFilter())

    def perps_funding_history(
        self, address: str, filter: Optional[HistoryFilter] = None
    ) -> List[FundingPayment]:
        """Return historical funding payments for the user's perps positions."""
        f = filter or HistoryFilter()
        data = self._get(
            f"{_PERPS_BASE}/accounts/{address}/fundings",
            params=self._history_params(f),
        ) or []
        return [FundingPayment.from_dict(x) for x in data]

    # ─────────────────────────────────────────────────────────────────────────
    # Perps — Authenticated trading
    # ─────────────────────────────────────────────────────────────────────────

    def place_perps_order(self, request: PerpsNewOrderRequest) -> List[PlaceOrderResult]:
        """Submit a perpetuals order batch. Requires a configured private key."""
        if self._perps_sgn is None:
            raise NotAuthenticatedError()
        nonce = self._nonce()
        sig = self._perps_sgn.sign_new_order_request(request, nonce)
        body = request.to_json_payload()
        data = self._post_signed(f"{_PERPS_BASE}/trade/orders", body, sig, nonce) or []
        return [PlaceOrderResult.from_dict(x) for x in data]

    def cancel_perps_orders(
        self, request: PerpsCancelOrderRequest
    ) -> List[CancelOrderResult]:
        """Cancel perpetuals orders."""
        if self._perps_sgn is None:
            raise NotAuthenticatedError()
        nonce = self._nonce()
        sig = self._perps_sgn.sign_cancel_order_request(request, nonce)
        body = request.to_json_payload()
        data = self._delete_signed(f"{_PERPS_BASE}/trade/orders", body, sig, nonce) or []
        return [CancelOrderResult.from_dict(x) for x in data]

    def modify_perps_order(self, request: ModifyOrderRequest) -> ModifyOrderResult:
        """Modify a single resting perps order's price, quantity, or stop price.

        Identify the target order by ``order_id`` or ``cl_ord_id`` (exactly one
        must be set on the request).
        """
        if self._perps_sgn is None:
            raise NotAuthenticatedError()
        nonce = self._nonce()
        sig = self._perps_sgn.sign_modify_order_request(request, nonce)
        body = request.to_json_payload()
        data = self._post_signed(
            f"{_PERPS_BASE}/trade/orders/modify", body, sig, nonce
        ) or {}
        return ModifyOrderResult.from_dict(data)

    def replace_perps_orders(
        self, request: ReplaceOrderRequest
    ) -> List[PlaceOrderResult]:
        """Atomically replace a batch of resting perpetuals orders."""
        if self._perps_sgn is None:
            raise NotAuthenticatedError()
        nonce = self._nonce()
        sig = self._perps_sgn.sign_replace_order_request(request, nonce)
        body = request.to_json_payload()
        data = self._post_signed(
            f"{_PERPS_BASE}/trade/orders/replace", body, sig, nonce
        ) or []
        return [PlaceOrderResult.from_dict(x) for x in data]

    def update_leverage(self, request: UpdateLeverageRequest) -> LeverageResult:
        """Change leverage for a perpetuals position."""
        if self._perps_sgn is None:
            raise NotAuthenticatedError()
        nonce = self._nonce()
        sig = self._perps_sgn.sign_update_leverage_request(request, nonce)
        body = request.to_json_payload()
        data = self._post_signed(f"{_PERPS_BASE}/trade/leverage", body, sig, nonce) or {}
        return LeverageResult.from_dict(data)

    def update_margin(self, request: UpdateMarginRequest) -> None:
        """Adjust margin for a perpetuals position."""
        if self._perps_sgn is None:
            raise NotAuthenticatedError()
        nonce = self._nonce()
        sig = self._perps_sgn.sign_update_margin_request(request, nonce)
        body = request.to_json_payload()
        self._post_signed(f"{_PERPS_BASE}/trade/margin", body, sig, nonce)

    def perps_transfer(self, request: TransferAssetRequest) -> TransferReceipt:
        """Transfer assets from a perps account and return the transfer ID."""
        if self._perps_sgn is None:
            raise NotAuthenticatedError()
        nonce = self._nonce()
        sig = self._perps_sgn.sign_transfer_asset_request(request, nonce)
        body = request.to_json_payload()
        data = self._post_signed(
            f"{_PERPS_BASE}/accounts/transfers", body, sig, nonce
        ) or {}
        return TransferReceipt.from_dict(data)

    def schedule_perps_cancel(self, request: ScheduleCancelRequest) -> None:
        """Arm (or clear) a dead-man's switch that auto-cancels perps orders.

        Pass a ``ScheduleCancelRequest`` with ``scheduled_timestamp`` set (unix ms)
        to arm, or ``None`` to clear an existing schedule.
        """
        if self._perps_sgn is None:
            raise NotAuthenticatedError()
        nonce = self._nonce()
        sig = self._perps_sgn.sign_schedule_cancel_request(request, nonce)
        body = request.to_json_payload()
        self._post_signed(
            f"{_PERPS_BASE}/trade/orders/schedule-cancel", body, sig, nonce
        )

    # ── Perps convenience helpers ────────────────────────────────────────────

    def place_perps_limit_order(
        self,
        account_id: int,
        symbol_id: int,
        cl_ord_id: str,
        side: OrderSide,
        position_side: PositionSide,
        time_in_force: TimeInForce,
        price: Decimal,
        quantity: Decimal,
        reduce_only: bool = False,
    ) -> List[PlaceOrderResult]:
        """One-call helper for a single perps limit order."""
        return self.place_perps_order(
            PerpsNewOrderRequest(
                account_id=account_id,
                symbol_id=symbol_id,
                orders=[
                    RawOrder(
                        cl_ord_id=cl_ord_id,
                        modifier=OrderModifier.NORMAL,
                        side=side,
                        type=OrderType.LIMIT,
                        time_in_force=time_in_force,
                        price=price,
                        quantity=quantity,
                        position_side=position_side,
                        reduce_only=reduce_only,
                    ),
                ],
            )
        )

    def place_perps_market_order(
        self,
        account_id: int,
        symbol_id: int,
        cl_ord_id: str,
        side: OrderSide,
        position_side: PositionSide,
        quantity: Decimal,
        reduce_only: bool = False,
    ) -> List[PlaceOrderResult]:
        """One-call helper for a single perps market order."""
        return self.place_perps_order(
            PerpsNewOrderRequest(
                account_id=account_id,
                symbol_id=symbol_id,
                orders=[
                    RawOrder(
                        cl_ord_id=cl_ord_id,
                        modifier=OrderModifier.NORMAL,
                        side=side,
                        type=OrderType.MARKET,
                        time_in_force=TimeInForce.IOC,
                        quantity=quantity,
                        position_side=position_side,
                        reduce_only=reduce_only,
                    ),
                ],
            )
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Spot — Market data (unauthenticated)
    # ─────────────────────────────────────────────────────────────────────────

    def spot_symbols(self) -> List[Symbol]:
        """Return all available spot trading pairs."""
        data = self._get(f"{_SPOT_BASE}/markets/symbols") or []
        return [Symbol.from_dict(x) for x in data]

    def spot_tickers(self) -> List[Ticker]:
        """Return 24-hour rolling stats for all spot pairs."""
        data = self._get(f"{_SPOT_BASE}/markets/tickers") or []
        return [Ticker.from_dict(x) for x in data]

    def spot_order_book(self, symbol: str, depth: int = 0) -> OrderBook:
        """Return the order book snapshot for ``symbol``.

        ``symbol`` is the internal name (e.g. ``vBTC_vUSDC``).
        Pass ``depth <= 0`` to use the API default.
        """
        params = {"depth": depth} if depth > 0 else None
        data = self._get(f"{_SPOT_BASE}/markets/{symbol}/orderbook", params=params)
        return OrderBook.from_dict(data or {}, symbol=symbol)

    def spot_account_info(self, address: str) -> AccountInfo:
        """Return the account ID and user ID for ``address`` (the ``aid`` field)."""
        data = self._get(f"{_SPOT_BASE}/accounts/{address}/state") or {}
        return AccountInfo.from_dict(data)

    def spot_balances(self, address: str) -> List[Balance]:
        """Return asset balances for ``address`` on the spot engine."""
        data = self._get(f"{_SPOT_BASE}/accounts/{address}/balances") or {}
        return [Balance.from_dict(x) for x in (data.get("balances") or [])]

    def spot_orders(self, address: str) -> List[Order]:
        """Return open orders for ``address`` on the spot engine."""
        data = self._get(f"{_SPOT_BASE}/accounts/{address}/orders") or {}
        return [Order.from_dict(x) for x in (data.get("orders") or [])]

    def spot_klines(
        self, symbol: str, interval: str, filter: Optional[HistoryFilter] = None
    ) -> List[Candle]:
        """Return historical OHLCV candles for a spot symbol.

        ``symbol`` is the internal name (e.g. ``"vBTC_vUSDC"``).
        """
        return self._klines(_SPOT_BASE, symbol, interval, filter or HistoryFilter())

    def spot_public_trades(self, symbol: str, limit: int = 0) -> List[PublicTrade]:
        """Return recent public market trades for a spot symbol."""
        return self._public_trades(_SPOT_BASE, symbol, limit)

    def spot_orders_history(
        self, address: str, filter: Optional[HistoryFilter] = None
    ) -> List[Order]:
        """Return historical (non-open) orders for ``address`` on the spot engine."""
        return self._orders_history(_SPOT_BASE, address, filter or HistoryFilter())

    def spot_user_trades(
        self, address: str, filter: Optional[HistoryFilter] = None
    ) -> List[UserTrade]:
        """Return the user's fill history on the spot engine."""
        return self._user_trades(_SPOT_BASE, address, filter or HistoryFilter())

    # ─────────────────────────────────────────────────────────────────────────
    # Spot — Authenticated trading
    # ─────────────────────────────────────────────────────────────────────────

    def place_spot_orders(
        self, request: BatchNewOrderRequest
    ) -> List[PlaceOrderResult]:
        """Submit a batch of spot orders. Requires a configured private key."""
        if self._spot_sgn is None:
            raise NotAuthenticatedError()
        nonce = self._nonce()
        sig = self._spot_sgn.sign_batch_new_order_request(request, nonce)
        body = request.to_json_payload()
        data = (
            self._post_signed(f"{_SPOT_BASE}/trade/orders/batch", body, sig, nonce)
            or []
        )
        return [PlaceOrderResult.from_dict(x) for x in data]

    def cancel_spot_orders(
        self, request: BatchCancelOrderRequest
    ) -> List[CancelOrderResult]:
        """Submit a batch of spot order cancellations."""
        if self._spot_sgn is None:
            raise NotAuthenticatedError()
        nonce = self._nonce()
        sig = self._spot_sgn.sign_batch_cancel_order_request(request, nonce)
        body = request.to_json_payload()
        data = (
            self._delete_signed(f"{_SPOT_BASE}/trade/orders/batch", body, sig, nonce)
            or []
        )
        return [CancelOrderResult.from_dict(x) for x in data]

    def replace_spot_orders(
        self, request: ReplaceOrderRequest
    ) -> List[PlaceOrderResult]:
        """Replace a batch of existing spot orders."""
        if self._spot_sgn is None:
            raise NotAuthenticatedError()
        nonce = self._nonce()
        sig = self._spot_sgn.sign_replace_order_request(request, nonce)
        body = request.to_json_payload()
        data = (
            self._post_signed(f"{_SPOT_BASE}/trade/orders/replace", body, sig, nonce)
            or []
        )
        return [PlaceOrderResult.from_dict(x) for x in data]

    def spot_transfer(self, request: TransferAssetRequest) -> TransferReceipt:
        """Transfer assets from a spot account and return the transfer ID."""
        if self._spot_sgn is None:
            raise NotAuthenticatedError()
        nonce = self._nonce()
        sig = self._spot_sgn.sign_transfer_asset_request(request, nonce)
        body = request.to_json_payload()
        data = self._post_signed(
            f"{_SPOT_BASE}/accounts/transfers", body, sig, nonce
        ) or {}
        return TransferReceipt.from_dict(data)

    def schedule_spot_cancel(self, request: ScheduleCancelRequest) -> None:
        """Arm (or clear) a dead-man's switch that auto-cancels spot orders."""
        if self._spot_sgn is None:
            raise NotAuthenticatedError()
        nonce = self._nonce()
        sig = self._spot_sgn.sign_schedule_cancel_request(request, nonce)
        body = request.to_json_payload()
        self._post_signed(
            f"{_SPOT_BASE}/trade/orders/schedule-cancel", body, sig, nonce
        )

    # ── Spot convenience helpers ─────────────────────────────────────────────

    def place_spot_limit_order(
        self,
        account_id: int,
        symbol_id: int,
        cl_ord_id: str,
        side: OrderSide,
        time_in_force: TimeInForce,
        price: Decimal,
        quantity: Decimal,
    ) -> List[PlaceOrderResult]:
        """One-call helper for a single spot limit order."""
        return self.place_spot_orders(
            BatchNewOrderRequest(
                account_id=account_id,
                orders=[
                    BatchNewOrderItem(
                        symbol_id=symbol_id,
                        cl_ord_id=cl_ord_id,
                        side=side,
                        type=OrderType.LIMIT,
                        time_in_force=time_in_force,
                        price=price,
                        quantity=quantity,
                    ),
                ],
            )
        )

    def place_spot_market_order(
        self,
        account_id: int,
        symbol_id: int,
        cl_ord_id: str,
        side: OrderSide,
        quantity: Decimal,
    ) -> List[PlaceOrderResult]:
        """One-call helper for a single spot market order."""
        return self.place_spot_orders(
            BatchNewOrderRequest(
                account_id=account_id,
                orders=[
                    BatchNewOrderItem(
                        symbol_id=symbol_id,
                        cl_ord_id=cl_ord_id,
                        side=side,
                        type=OrderType.MARKET,
                        time_in_force=TimeInForce.IOC,
                        quantity=quantity,
                    ),
                ],
            )
        )
