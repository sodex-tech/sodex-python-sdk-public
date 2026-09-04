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
import os
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from urllib.parse import urlencode

import requests
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_hash.auto import keccak
from eth_keys import keys as eth_keys
from eth_utils import to_checksum_address

from sodex.common.enums import (
    SignatureType,
    OrderModifier,
    OrderSide,
    OrderType,
    PositionSide,
    TimeInForce,
    TransferAssetType,
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
    CancelOrder,
    CancelOrderRequest as PerpsCancelOrderRequest,
    ModifyOrderRequest,
    NewOrderRequest as PerpsNewOrderRequest,
    RawOrder,
    UpdateLeverageRequest,
    UpdateMarginRequest,
)
from sodex.spot.signer import SpotSigner
from sodex.spot.types import (
    BatchCancelOrderItem,
    BatchCancelOrderRequest,
    BatchNewOrderItem,
    BatchNewOrderRequest,
)

from .types import (
    AccountInfo,
    AccountAPIKeys,
    AddAPIKeyRequest,
    ApproveBuilderFeeRequest,
    Balance,
    Candle,
    CancelOrderResult,
    ChainTransferConfig,
    Coin,
    CoinTransferConfig,
    DepositWithdrawalHistory,
    EVMDepositSubmission,
    EVMWithdrawRequest,
    EVMWithdrawSubmission,
    FeeRate,
    FundingPayment,
    GeneratedAPIKey,
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
    UserStatus,
    UserSubaccounts,
    UserTrade,
)

# ── Network constants ────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://mainnet-gw.sodex.dev"
TESTNET_BASE_URL = "https://testnet-gw.sodex.dev"
DEFAULT_CHAIN_ID = 286623
TESTNET_CHAIN_ID = 138565
DEFAULT_VALUECHAIN_RPC_URL = "https://mainnet.valuechain.xyz/"
TREASURY_ACCOUNT_ID = 999

_PERPS_BASE = "/api/v1/perps"
_SPOT_BASE = "/api/v1/spot"
_USER_BASE = "/api/v1/user"
_ADD_API_KEY_TYPE_HASH = keccak(
    b"AddAPIKey(uint64 accountID,string name,uint8 keyType,bytes publicKey,uint64 expiresAt,uint64 nonce)"
)
_ADD_PERMISSIONED_API_KEY_TYPE_HASH = keccak(
    b"UserSignedAddPermissionedAPIKeyAction(uint64 chainID,uint64 nonce,uint64 accountID,string name,uint8 keyType,bytes publicKey,uint64 expiresAt,uint64 permissions)"
)
_APPROVE_BUILDER_FEE_TYPE_HASH = keccak(
    b"ApproveBuilderFeeAction(uint64 chainID,uint64 nonce,uint64 accountID,uint64 builderID,uint64 maxFeeRate)"
)
_CALL_FOR_PERMIT_CONTRACT = "0x890B7D142841065E64E5f94a455876e6352A7801"
_WITHDRAW_TOKEN_TARGET = "0x441BDb33C7d6DC49f627a42c3d71671D50DC2e94"
_CLOB_GATEWAY_CONTRACT = "0x0101010101010101010101010101010101010101"
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_NONCES_KEYED_SELECTOR = keccak(b"nonces(address,uint192)")[:4]
_HASH_CALL_FOR_PERMIT_SELECTOR = keccak(
    b"hashCallForPermit(address,string,bytes,uint256,uint256)"
)[:4]
_EXECUTE_PERMIT_SELECTOR = keccak(
    b"execute(address,string,bytes,uint256,uint256,bytes)"
)[:4]
_ERC20_APPROVE_SELECTOR = keccak(b"approve(address,uint256)")[:4]
_ERC20_BALANCE_OF_SELECTOR = keccak(b"balanceOf(address)")[:4]
_DEPOSIT_ERC20_SELECTOR = keccak(
    b"depositERC20(address,uint256,address,uint256)"
)[:4]

_TERMINAL_TRANSFER_STATUSES = {
    "success",
    "succeeded",
    "failed",
    "rejected",
    "cancelled",
    "canceled",
}
_SUCCESSFUL_TRANSFER_STATUSES = {"success", "succeeded"}


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


class WaitTimeoutError(TimeoutError):
    """Raised when an SDK workflow wait exceeds its configured timeout."""

    def __init__(self, operation: str, timeout: float) -> None:
        super().__init__(f"{operation} timed out after {timeout:g} seconds")
        self.operation = operation
        self.timeout = timeout


class NonceManager:
    """Share strictly increasing nonces and serialize writes per signer/network."""

    def __init__(self, clock: Optional[Callable[[], int]] = None) -> None:
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._manager_lock = threading.Lock()
        self._locks: Dict[str, threading.RLock] = {}
        self._last_by_key: Dict[str, int] = {}

    def _lock_for(self, key: str) -> threading.RLock:
        with self._manager_lock:
            return self._locks.setdefault(key, threading.RLock())

    def _next_locked(self, key: str) -> int:
        now = self._clock()
        last = self._last_by_key.get(key, 0)
        nonce = now if now > last else last + 1
        self._last_by_key[key] = nonce
        return nonce

    def next(self, key: str) -> int:
        """Return the next strictly increasing nonce for ``key``."""
        with self._lock_for(key):
            return self._next_locked(key)

    def run(self, key: str, task: Callable[[int], Any]) -> Any:
        """Serialize nonce allocation, signing, and HTTP submission for ``key``."""
        with self._lock_for(key):
            return task(self._next_locked(key))


GLOBAL_NONCE_MANAGER = NonceManager()


# ── Config ────────────────────────────────────────────────────────────────────


@dataclass
class Config:
    """Configuration for a :class:`Client` instance."""

    #: API root (no trailing slash). Defaults to mainnet if empty.
    base_url: str = ""
    #: EVM chain ID used for EIP-712 domain separation. Defaults to mainnet (286623) if 0.
    chain_id: int = 0
    #: Raw 32-byte private key or 0x-prefixed hex string. Leave None for read-only access.
    private_key: Optional[Union[bytes, str]] = None
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
    #: Shared nonce manager. Defaults to the process-wide manager.
    nonce_manager: Optional[NonceManager] = None


# ── Client ────────────────────────────────────────────────────────────────────


class Client:
    """HTTP client for the Sodex REST API. Safe to use concurrently from multiple threads."""

    # Re-export constants so callers can do ``Client.TESTNET_BASE_URL`` etc.
    DEFAULT_BASE_URL = DEFAULT_BASE_URL
    TESTNET_BASE_URL = TESTNET_BASE_URL
    DEFAULT_CHAIN_ID = DEFAULT_CHAIN_ID
    TESTNET_CHAIN_ID = TESTNET_CHAIN_ID
    DEFAULT_VALUECHAIN_RPC_URL = DEFAULT_VALUECHAIN_RPC_URL
    TREASURY_ACCOUNT_ID = TREASURY_ACCOUNT_ID

    def __init__(self, cfg: Optional[Config] = None) -> None:
        self._cfg = cfg or Config()
        if not self._cfg.base_url:
            self._cfg.base_url = DEFAULT_BASE_URL
        if not self._cfg.chain_id:
            self._cfg.chain_id = DEFAULT_CHAIN_ID
        if isinstance(self._cfg.private_key, str):
            try:
                self._cfg.private_key = bytes.fromhex(
                    self._cfg.private_key.removeprefix("0x")
                )
            except ValueError as exc:
                raise ValueError("private_key must be a 32-byte hex string") from exc
        if self._cfg.private_key is not None and len(self._cfg.private_key) != 32:
            raise ValueError("private_key must be exactly 32 bytes")

        self._http = self._cfg.session or requests.Session()

        self._spot_sgn: Optional[SpotSigner] = None
        self._perps_sgn: Optional[PerpsSigner] = None
        if self._cfg.private_key is not None:
            self._spot_sgn = SpotSigner(self._cfg.chain_id, self._cfg.private_key)
            self._perps_sgn = PerpsSigner(self._cfg.chain_id, self._cfg.private_key)

        self._nonce_manager = self._cfg.nonce_manager or GLOBAL_NONCE_MANAGER
        signer = self.address or self.account_address or "anonymous"
        self._nonce_key = f"{self._cfg.chain_id}:{signer.lower()}"

    @classmethod
    def from_private_key(
        cls,
        private_key: Union[bytes, str],
        *,
        testnet: bool = False,
        account_address: Optional[str] = None,
        api_key_name: str = "",
        valuechain_rpc_url: Optional[str] = None,
        timeout: float = 30.0,
        nonce_manager: Optional[NonceManager] = None,
    ) -> "Client":
        """Create an authenticated mainnet or testnet client from bytes or hex."""
        return cls(
            Config(
                base_url=TESTNET_BASE_URL if testnet else DEFAULT_BASE_URL,
                chain_id=TESTNET_CHAIN_ID if testnet else DEFAULT_CHAIN_ID,
                private_key=private_key,
                account_address=account_address,
                api_key_name=api_key_name,
                valuechain_rpc_url=(
                    valuechain_rpc_url
                    if valuechain_rpc_url is not None
                    else ("" if testnet else DEFAULT_VALUECHAIN_RPC_URL)
                ),
                timeout=timeout,
                nonce_manager=nonce_manager,
            )
        )

    @classmethod
    def from_env(cls, *, testnet: Optional[bool] = None) -> "Client":
        """Create a client from ``SODEX_*`` environment variables.

        ``SODEX_PRIVATE_KEY`` is optional for read-only use. ``SODEX_NETWORK``
        accepts ``mainnet`` or ``testnet`` when ``testnet`` is not passed.
        """
        if testnet is None:
            network = os.environ.get("SODEX_NETWORK", "mainnet").strip().lower()
            if network not in ("mainnet", "testnet"):
                raise ValueError("SODEX_NETWORK must be 'mainnet' or 'testnet'")
            testnet = network == "testnet"
        private_key = os.environ.get("SODEX_PRIVATE_KEY")
        if private_key:
            return cls.from_private_key(
                private_key,
                testnet=testnet,
                account_address=os.environ.get("SODEX_ACCOUNT_ADDRESS"),
                api_key_name=os.environ.get("SODEX_API_KEY_NAME", ""),
                valuechain_rpc_url=os.environ.get("SODEX_VALUECHAIN_RPC_URL"),
            )
        return cls(
            Config(
                base_url=TESTNET_BASE_URL if testnet else DEFAULT_BASE_URL,
                chain_id=TESTNET_CHAIN_ID if testnet else DEFAULT_CHAIN_ID,
                account_address=os.environ.get("SODEX_ACCOUNT_ADDRESS"),
                valuechain_rpc_url=(
                    os.environ.get("SODEX_VALUECHAIN_RPC_URL", "")
                    if testnet
                    else os.environ.get(
                        "SODEX_VALUECHAIN_RPC_URL", DEFAULT_VALUECHAIN_RPC_URL
                    )
                ),
            )
        )

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

    @property
    def base_url(self) -> str:
        """Return the configured Gateway base URL."""
        return self._cfg.base_url

    def _nonce(self) -> int:
        """Return a strictly monotonic uint64 nonce close to ``time.time() * 1000``.

        The Sodex API expects the nonce to be a millisecond timestamp and accepts
        values within ``(now − 2 days, now + 1 day)``. This helper guarantees
        strict monotonicity even under concurrent calls from multiple threads.
        """
        return self._nonce_manager.next(self._nonce_key)

    def _with_nonce(self, task: Callable[[int], Any]) -> Any:
        return self._nonce_manager.run(self._nonce_key, task)

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

    def _post(
        self,
        path: str,
        body: Any = None,
        *,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        request_headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":"))
        if headers:
            request_headers.update(headers)
        resp = self._http.post(
            self._cfg.base_url + path,
            data=data,
            headers=request_headers,
            timeout=self._cfg.timeout,
        )
        return self._unwrap(resp)

    def _rpc_request(self, method: str, params: List[Any]) -> Any:
        if not self._cfg.valuechain_rpc_url:
            raise ValueError(
                "valuechain_rpc_url is required for this network; set it in Config "
                "or SODEX_VALUECHAIN_RPC_URL"
            )
        resp = self._http.post(
            self._cfg.valuechain_rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params,
            },
            timeout=self._cfg.timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"valuechain RPC error: {body['error']}")
        return body.get("result")

    def _rpc_call(self, to: str, data: bytes) -> str:
        result = self._rpc_request(
            "eth_call", [{"to": to, "data": "0x" + data.hex()}, "latest"]
        )
        return str(result)

    def _send_valuechain_transaction(
        self, to: str, data: bytes, *, value: int = 0
    ) -> str:
        if self._cfg.private_key is None:
            raise NotAuthenticatedError()

        def send(_: int) -> str:
            sender = self.address
            transaction = {
                "from": sender,
                "to": to_checksum_address(to),
                "data": "0x" + data.hex(),
                "value": hex(value),
            }
            nonce = int(
                self._rpc_request("eth_getTransactionCount", [sender, "pending"]), 16
            )
            chain_id = int(self._rpc_request("eth_chainId", []), 16)
            gas = int(self._rpc_request("eth_estimateGas", [transaction]), 16)
            gas_price = int(self._rpc_request("eth_gasPrice", []), 16)
            signed = Account.sign_transaction(
                {
                    "to": transaction["to"],
                    "data": transaction["data"],
                    "value": value,
                    "nonce": nonce,
                    "chainId": chain_id,
                    "gas": gas,
                    "gasPrice": gas_price,
                },
                self._cfg.private_key,
            )
            raw_transaction = getattr(signed, "raw_transaction", None)
            if raw_transaction is None:
                raw_transaction = signed.rawTransaction
            raw = raw_transaction.hex()
            if not raw.startswith("0x"):
                raw = "0x" + raw
            return str(self._rpc_request("eth_sendRawTransaction", [raw]))

        key = f"evm:{self._cfg.valuechain_rpc_url}:{self.address.lower()}"
        return self._nonce_manager.run(key, send)

    def _wait_for_valuechain_transaction(
        self, tx_hash: str, *, timeout: float, interval: float
    ) -> dict:
        receipt = self._poll_until(
            f"ValueChain transaction {tx_hash}",
            lambda: self._rpc_request("eth_getTransactionReceipt", [tx_hash]),
            lambda value: value is not None,
            timeout=timeout,
            interval=interval,
        )
        if int(receipt.get("status", "0x0"), 16) != 1:
            raise RuntimeError(f"ValueChain transaction reverted: {tx_hash}")
        return receipt

    @staticmethod
    def _poll_until(
        operation: str,
        load: Callable[[], Any],
        done: Callable[[Any], bool],
        *,
        timeout: float,
        interval: float,
        on_update: Optional[Callable[[Any], None]] = None,
    ) -> Any:
        deadline = time.monotonic() + timeout
        while True:
            value = load()
            if on_update is not None:
                on_update(value)
            if done(value):
                return value
            if time.monotonic() >= deadline:
                raise WaitTimeoutError(operation, timeout)
            time.sleep(interval)

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
        if filter.account_id is not None:
            params["accountID"] = filter.account_id
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
        data = (
            self._get(
                f"{base}/accounts/{address}/orders/history",
                params=self._history_params(filter),
            )
            or []
        )
        return [Order.from_dict(x) for x in data]

    def _user_trades(
        self, base: str, address: str, filter: HistoryFilter
    ) -> List[UserTrade]:
        data = (
            self._get(
                f"{base}/accounts/{address}/trades",
                params=self._history_params(filter),
            )
            or []
        )
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
    # Gateway — user status
    # ─────────────────────────────────────────────────────────────────────────

    def get_user_status(self, user_address: Optional[str] = None) -> UserStatus:
        """Return whether a wallet is registered and its uint64 user ID."""
        user = user_address or self.account_address
        if not user:
            raise ValueError("user_address is required for a read-only client")
        data = self._get(f"{_USER_BASE}/{user}/status") or {}
        return UserStatus.from_dict(data)

    # ─────────────────────────────────────────────────────────────────────────
    # Funding — external deposits and withdrawals
    # ─────────────────────────────────────────────────────────────────────────

    def get_transfer_configs(
        self, coin: Optional[str] = None
    ) -> List[CoinTransferConfig]:
        """Return supported deposit/withdrawal tokens, chains, fees, and limits."""
        data = self._get("/api/v1/asset/config", params={"coin": coin}) or []
        return [CoinTransferConfig.from_dict(x) for x in data]

    def get_transfer_route(
        self, coin: str, chain: str
    ) -> Tuple[CoinTransferConfig, ChainTransferConfig]:
        """Resolve one supported token/chain route or raise a useful error."""
        asset = next(
            (
                x
                for x in self.get_transfer_configs(coin)
                if x.coin.lower() == coin.lower()
            ),
            None,
        )
        if asset is None:
            raise ValueError(f"unsupported transfer coin: {coin}")
        route = next(
            (x for x in asset.chains if x.chain.lower() == chain.lower()), None
        )
        if route is None:
            raise ValueError(f"unsupported transfer chain for {asset.coin}: {chain}")
        return asset, route

    def get_deposit_address(self, user_address: str, chain: str) -> UserDepositAddress:
        """Return the user's custody deposit address and provisioning status."""
        data = (
            self._get(
                f"{_USER_BASE}/{user_address}/deposit-address", params={"chain": chain}
            )
            or {}
        )
        return UserDepositAddress.from_dict(data)

    def create_deposit_address(
        self,
        user_address: str,
        chain: str,
    ) -> UserDepositAddress:
        """Create a custody deposit address using Gateway's chain-only v1 request."""
        data = (
            self._post(
                f"{_USER_BASE}/{user_address}/deposit-address",
                {"chain": chain},
            )
            or {}
        )
        return UserDepositAddress.from_dict(data)

    def ensure_deposit_address(
        self, chain: str, user_address: Optional[str] = None
    ) -> UserDepositAddress:
        """Return an existing custody address, creating it when the API returns an empty one."""
        user = user_address or self.account_address
        if not user:
            raise ValueError("user_address is required for a read-only client")
        current = self.get_deposit_address(user, chain)
        if current.address or current.status:
            return current
        return self.create_deposit_address(user, chain)

    def get_deposit_status(self, chain: str, tx_hash: str) -> DepositWithdrawalHistory:
        """Look up all deposit records associated with an external transaction hash."""
        data = (
            self._get(
                f"{_USER_BASE}/deposit/status",
                params={"chain": chain, "txHash": tx_hash},
            )
            or {}
        )
        return DepositWithdrawalHistory.from_dict(data)

    def get_withdraw_status(
        self,
        chain: str,
        *,
        withdraw_id: Optional[str] = None,
        tx_hash: Optional[str] = None,
    ) -> DepositWithdrawalHistory:
        """Look up a withdrawal by its withdrawal ID or ValueChain transaction hash."""
        if not withdraw_id and not tx_hash:
            raise ValueError("withdraw_id or tx_hash is required")
        data = (
            self._get(
                f"{_USER_BASE}/withdraw/status",
                params={"chain": chain, "withdrawId": withdraw_id, "txHash": tx_hash},
            )
            or {}
        )
        return DepositWithdrawalHistory.from_dict(data)

    def wait_for_deposit_address(
        self,
        chain: str,
        *,
        user_address: Optional[str] = None,
        timeout: float = 120.0,
        interval: float = 3.0,
    ) -> UserDepositAddress:
        """Create an empty custody address and wait until provisioning completes."""
        user = user_address or self.account_address
        if not user:
            raise ValueError("user_address is required for a read-only client")
        initial = self.ensure_deposit_address(chain, user)
        address = self._poll_until(
            f"deposit address for {chain}",
            lambda: self.get_deposit_address(user, chain),
            lambda value: value.status not in ("", "Processing"),
            timeout=timeout,
            interval=interval,
        ) if initial.status in ("", "Processing") else initial
        if address.status == "Suspicious":
            raise RuntimeError("custody deposit address is Suspicious and must not be used")
        if address.status != "Enabled" or not address.address:
            raise RuntimeError(f"custody address is unavailable: status={address.status}")
        return address

    def wait_for_deposit(
        self,
        chain: str,
        tx_hash: str,
        *,
        timeout: float = 120.0,
        interval: float = 3.0,
    ) -> DepositWithdrawalHistory:
        """Wait until Gateway indexes at least one source-chain deposit record."""
        return self._poll_until(
            f"deposit {tx_hash}",
            lambda: self.get_deposit_status(chain, tx_hash),
            lambda history: history.total > 0,
            timeout=timeout,
            interval=interval,
        )

    def wait_for_withdrawal(
        self,
        chain: str,
        *,
        withdraw_id: Optional[str] = None,
        tx_hash: Optional[str] = None,
        timeout: float = 120.0,
        interval: float = 3.0,
        on_update: Optional[Callable[[DepositWithdrawalHistory], None]] = None,
    ) -> DepositWithdrawalHistory:
        """Wait until every matching withdrawal record reaches a terminal status."""
        if not withdraw_id and not tx_hash:
            raise ValueError("withdraw_id or tx_hash is required")
        return self._poll_until(
            f"withdrawal {withdraw_id or tx_hash}",
            lambda: self.get_withdraw_status(
                chain, withdraw_id=withdraw_id, tx_hash=tx_hash
            ),
            lambda history: bool(history.records)
            and all(
                self.is_terminal_transfer_status(record.status)
                for record in history.records
            ),
            timeout=timeout,
            interval=interval,
            on_update=on_update,
        )

    @staticmethod
    def is_terminal_transfer_status(status: str) -> bool:
        """Return whether an external transfer status is final."""
        return status.lower() in _TERMINAL_TRANSFER_STATUSES

    @staticmethod
    def is_successful_transfer_status(status: str) -> bool:
        """Return whether an external transfer status is a successful final state."""
        return status.lower() in _SUCCESSFUL_TRANSFER_STATUSES

    def submit_evm_withdraw(
        self, user_address: str, request: EVMWithdrawRequest
    ) -> EVMWithdrawSubmission:
        """Submit a prepared and signed ValueChain withdrawal permit."""
        data = (
            self._post(
                f"{_USER_BASE}/{user_address}/evm-withdraw", request.to_json_payload()
            )
            or {}
        )
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
                raise ValueError(
                    "withdrawal_type must be 'custody' or 'bridge'"
                ) from exc
        else:
            route = WithdrawalType(withdrawal_type)
        if route == WithdrawalType.CUSTODY and not chain_config.custody_available:
            raise ValueError(
                f"custody withdrawal is unavailable on {chain_config.chain}"
            )
        if route == WithdrawalType.BRIDGE and not chain_config.bridge_available:
            raise ValueError(
                f"bridge withdrawal is unavailable on {chain_config.chain}"
            )

        owner = self.address
        nonce_call = _NONCES_KEYED_SELECTOR + abi_encode(
            ["address", "uint192"], [owner, nonce_key]
        )
        permit_nonce = int(self._rpc_call(_CALL_FOR_PERMIT_CONTRACT, nonce_call), 16)
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

    def get_valuechain_balance(
        self, token_address: str, user_address: Optional[str] = None
    ) -> int:
        """Return one native or ERC-20 ValueChain balance in raw token units."""
        user = user_address or self.account_address
        if not user:
            raise ValueError("user_address is required for a read-only client")
        if token_address.lower() == _ZERO_ADDRESS:
            return int(self._rpc_request("eth_getBalance", [user, "latest"]), 16)
        data = _ERC20_BALANCE_OF_SELECTOR + abi_encode(["address"], [user])
        return int(self._rpc_call(token_address, data), 16)

    def wait_for_evm_balance_increase(
        self,
        token_address: str,
        previous_balance: int,
        *,
        user_address: Optional[str] = None,
        timeout: float = 120.0,
        interval: float = 3.0,
    ) -> int:
        """Wait until a native or ERC-20 ValueChain balance increases."""
        return self._poll_until(
            "ValueChain balance increase",
            lambda: self.get_valuechain_balance(token_address, user_address),
            lambda balance: balance > previous_balance,
            timeout=timeout,
            interval=interval,
        )

    def deposit_evm_to_engine(
        self,
        coin: str,
        amount: Decimal,
        destination: str,
        *,
        recipient: Optional[str] = None,
        timeout: float = 120.0,
        interval: float = 3.0,
    ) -> EVMDepositSubmission:
        """Approve and deposit a ValueChain token directly into Spot or Perps."""
        if self._cfg.private_key is None:
            raise NotAuthenticatedError()
        normalized_destination = destination.lower()
        if normalized_destination not in ("spot", "perps"):
            raise ValueError("destination must be 'spot' or 'perps'")
        asset = next(
            (
                config
                for config in self.get_transfer_configs(coin)
                if config.coin.lower() == coin.lower()
            ),
            None,
        )
        if asset is None:
            raise ValueError(f"unsupported transfer coin: {coin}")
        raw_amount = amount * (Decimal(10) ** asset.decimals)
        if raw_amount <= 0 or raw_amount != raw_amount.to_integral_value():
            raise ValueError(f"amount must be positive with at most {asset.decimals} decimals")
        raw = int(raw_amount)
        token_address = asset.token_address
        receiver = recipient or self.account_address
        approval_tx_hash = None
        if token_address.lower() != _ZERO_ADDRESS:
            approve_data = _ERC20_APPROVE_SELECTOR + abi_encode(
                ["address", "uint256"], [_CLOB_GATEWAY_CONTRACT, raw]
            )
            approval_tx_hash = self._send_valuechain_transaction(
                token_address, approve_data
            )
            self._wait_for_valuechain_transaction(
                approval_tx_hash, timeout=timeout, interval=interval
            )
        deposit_data = _DEPOSIT_ERC20_SELECTOR + abi_encode(
            ["address", "uint256", "address", "uint256"],
            [
                token_address,
                raw,
                receiver,
                0 if normalized_destination == "spot" else 1,
            ],
        )
        deposit_tx_hash = self._send_valuechain_transaction(
            _CLOB_GATEWAY_CONTRACT,
            deposit_data,
            value=raw if token_address.lower() == _ZERO_ADDRESS else 0,
        )
        self._wait_for_valuechain_transaction(
            deposit_tx_hash, timeout=timeout, interval=interval
        )
        return EVMDepositSubmission(
            deposit_tx_hash=deposit_tx_hash,
            approval_tx_hash=approval_tx_hash,
        )

    def submit_self_paid_evm_withdraw(
        self,
        request: EVMWithdrawRequest,
        *,
        timeout: float = 120.0,
        interval: float = 3.0,
    ) -> str:
        """Execute a prepared withdrawal permit from the user's ValueChain wallet."""
        execute_data = _EXECUTE_PERMIT_SELECTOR + abi_encode(
            ["address", "string", "bytes", "uint256", "uint256", "bytes"],
            [
                _WITHDRAW_TOKEN_TARGET,
                "WithdrawToken",
                bytes.fromhex(request.cmd_data.removeprefix("0x")),
                int(request.nonce),
                int(request.deadline),
                bytes.fromhex(request.signature.removeprefix("0x")),
            ],
        )
        tx_hash = self._send_valuechain_transaction(
            _CALL_FOR_PERMIT_CONTRACT, execute_data
        )
        self._wait_for_valuechain_transaction(
            tx_hash, timeout=timeout, interval=interval
        )
        return tx_hash

    # ─────────────────────────────────────────────────────────────────────────
    # API keys — aggregate spot/perps lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def get_api_keys(
        self,
        user_address: Optional[str] = None,
        *,
        account_id: Optional[int] = None,
        name: Optional[str] = None,
    ) -> AccountAPIKeys:
        """Return API keys registered for the same account on both engines."""
        user = user_address or self.account_address
        if not user:
            raise ValueError("user_address is required for a read-only client")
        data = self._get(
            f"{_USER_BASE}/{user}/api-keys",
            params={"accountID": account_id, "name": name},
        ) or {}
        return AccountAPIKeys.from_dict(data)

    def get_subaccounts(self, user_address: Optional[str] = None) -> UserSubaccounts:
        """Return the primary account ID and all subaccounts for a user."""
        user = user_address or self.account_address
        if not user:
            raise ValueError("user_address is required for a read-only client")
        data = self._get(f"{_USER_BASE}/{user}/subaccounts") or {}
        return UserSubaccounts.from_dict(data)

    def primary_account_id(self, user_address: Optional[str] = None) -> int:
        """Resolve the user's primary account ID for trading and transfers."""
        account_id = self.get_subaccounts(user_address).primary_account_id
        if account_id <= 0:
            raise RuntimeError("user has no primary Sodex account")
        return account_id

    def get_fee_rate(
        self,
        market: str,
        *,
        symbol: Optional[str] = None,
        user_address: Optional[str] = None,
    ) -> FeeRate:
        """Return the effective spot or perps fee rate for a user."""
        normalized = market.strip().lower()
        if normalized == "perp":
            normalized = "perps"
        if normalized not in ("spot", "perps"):
            raise ValueError("market must be 'spot' or 'perps'")
        user = user_address or self.account_address
        if not user:
            raise ValueError("user_address is required for a read-only client")
        data = (
            self._get(
                f"{_USER_BASE}/{user}/fee-rate",
                params={"market": normalized, "symbol": symbol},
            )
            or {}
        )
        return FeeRate.from_dict(data)

    def approve_agent(
        self,
        name: Optional[str] = None,
        *,
        account_id: Optional[int] = None,
        expires_at: int = 0,
        permissions: Optional[int] = None,
    ) -> Tuple[GeneratedAPIKey, "Client"]:
        """Generate/register an API wallet and return its key material and ready client."""
        from .types import generate_api_key

        if not self.address or self.account_address.lower() != self.address.lower():
            raise ValueError("approve_agent must be called on the master-wallet client")
        generated = generate_api_key(name or f"agent-{int(time.time() * 1000)}")
        self.add_api_key(
            self.address,
            AddAPIKeyRequest(
                account_id=account_id or self.primary_account_id(),
                name=generated.name,
                public_key=generated.address,
                expires_at=expires_at,
                permissions=permissions,
            ),
        )
        trading = Client(
            Config(
                base_url=self._cfg.base_url,
                chain_id=self._cfg.chain_id,
                private_key=generated.private_key,
                api_key_name=generated.name,
                account_address=self.address,
                valuechain_rpc_url=self._cfg.valuechain_rpc_url,
                timeout=self._cfg.timeout,
                session=self._cfg.session,
                nonce_manager=self._nonce_manager,
            )
        )
        return generated, trading

    def approve_builder_fee(
        self,
        builder_id: int,
        max_fee_rate: int,
        *,
        account_id: Optional[int] = None,
        user_address: Optional[str] = None,
    ) -> None:
        """Approve a builder's maximum fee rate on both Spot and Perps."""
        if self._cfg.private_key is None:
            raise NotAuthenticatedError()
        user = user_address or self.account_address
        if user.lower() != self.address.lower():
            raise ValueError("builder approval must be signed by the master wallet")
        request = ApproveBuilderFeeRequest(
            account_id=account_id or self.primary_account_id(user),
            builder_id=builder_id,
            max_fee_rate=max_fee_rate,
        )
        domain = EIP712Domain(name="universal", chain_id=self._cfg.chain_id)

        def submit(nonce: int) -> None:
            struct_hash = keccak(
                _APPROVE_BUILDER_FEE_TYPE_HASH
                + self._cfg.chain_id.to_bytes(32, "big")
                + nonce.to_bytes(32, "big")
                + request.account_id.to_bytes(32, "big")
                + request.builder_id.to_bytes(32, "big")
                + request.max_fee_rate.to_bytes(32, "big")
            )
            digest = keccak(b"\x19\x01" + domain.domain_separator() + struct_hash)
            signature = (
                bytes([SignatureType.EIP712_UNIVERSAL])
                + eth_keys.PrivateKey(self._cfg.private_key)
                .sign_msg_hash(digest)
                .to_bytes()
            )
            self._post_signed(
                f"{_USER_BASE}/{user}/builders",
                request.to_json_payload(),
                signature,
                nonce,
            )

        self._with_nonce(submit)

    def add_api_key(self, user_address: str, request: AddAPIKeyRequest) -> None:
        """Register an EVM API key on both engines with a master-wallet signature."""
        if self._cfg.private_key is None:
            raise NotAuthenticatedError()
        if user_address.lower() != self.address.lower():
            raise ValueError("user_address must match the configured private key")

        public_key = bytes.fromhex(request.public_key.removeprefix("0x"))
        if len(public_key) != 20:
            raise ValueError("public_key must be a 20-byte EVM address")
        domain = EIP712Domain(name="universal", chain_id=self._cfg.chain_id)
        common_fields = (
            request.account_id.to_bytes(32, "big")
            + keccak(request.name.encode())
            + (1).to_bytes(32, "big")
            + keccak(public_key)
            + request.expires_at.to_bytes(32, "big")
        )

        def submit(nonce: int) -> None:
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
            signature = (
                bytes([SignatureType.EIP712_UNIVERSAL])
                + eth_keys.PrivateKey(self._cfg.private_key)
                .sign_msg_hash(digest)
                .to_bytes()
            )
            self._post_signed(
                f"{_USER_BASE}/{user_address}/api-keys",
                request.to_json_payload(),
                signature,
                nonce,
            )

        self._with_nonce(submit)

    def revoke_api_key(
        self, user_address: str, request: RevokeAPIKeyRequest
    ) -> None:
        """Revoke an API key from both engines with a universal-domain signature."""
        if self._cfg.private_key is None:
            raise NotAuthenticatedError()
        if user_address.lower() != self.address.lower():
            raise ValueError("user_address must match the configured private key")
        domain = EIP712Domain(name="universal", chain_id=self._cfg.chain_id)

        def submit(nonce: int) -> None:
            digest = ExchangeAction(action_payload_hash(request), nonce).hash(domain)
            signature = (
                bytes([SignatureType.EIP712_UNIVERSAL])
                + eth_keys.PrivateKey(self._cfg.private_key)
                .sign_msg_hash(digest)
                .to_bytes()
            )
            self._delete_signed(
                f"{_USER_BASE}/{user_address}/api-keys",
                request.to_json_payload(),
                signature,
                nonce,
            )

        self._with_nonce(submit)

    # ─────────────────────────────────────────────────────────────────────────
    # Perps — Market data (unauthenticated)
    # ─────────────────────────────────────────────────────────────────────────

    def perps_symbols(self, symbol: Optional[str] = None) -> List[Symbol]:
        """Return all available perpetuals trading pairs."""
        data = (
            self._get(f"{_PERPS_BASE}/markets/symbols", params={"symbol": symbol}) or []
        )
        return [Symbol.from_dict(x) for x in data]

    def perps_coins(self, coin: Optional[str] = None) -> List[Coin]:
        """Return perpetuals collateral coins and their current margin metadata."""
        data = self._get(f"{_PERPS_BASE}/markets/coins", params={"coin": coin}) or []
        return [Coin.from_dict(x) for x in data]

    def perps_tickers(self, symbol: Optional[str] = None) -> List[Ticker]:
        """Return 24-hour rolling stats for all perps pairs."""
        data = (
            self._get(f"{_PERPS_BASE}/markets/tickers", params={"symbol": symbol}) or []
        )
        return [Ticker.from_dict(x) for x in data]

    def perps_order_book(self, symbol: str, depth: int = 0) -> OrderBook:
        """Return the order book snapshot for ``symbol``.

        Pass ``depth <= 0`` to use the API default.
        """
        params = {"limit": depth} if depth > 0 else None
        data = self._get(f"{_PERPS_BASE}/markets/{symbol}/orderbook", params=params)
        return OrderBook.from_dict(data or {}, symbol=symbol)

    def perps_account_state(
        self, address: Optional[str] = None, account_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Return the complete perpetuals account snapshot without dropping fields."""
        user = address or self.account_address
        if not user:
            raise ValueError("address is required for a read-only client")
        return (
            self._get(
                f"{_PERPS_BASE}/accounts/{user}/state", params={"accountID": account_id}
            )
            or {}
        )

    def perps_balances(
        self, address: str, account_id: Optional[int] = None
    ) -> List[Balance]:
        """Return asset balances for ``address`` on the perps engine."""
        data = (
            self._get(
                f"{_PERPS_BASE}/accounts/{address}/balances",
                params={"accountID": account_id},
            )
            or {}
        )
        return [Balance.from_dict(x) for x in (data.get("balances") or [])]

    def perps_orders(
        self,
        address: str,
        *,
        symbol: Optional[str] = None,
        account_id: Optional[int] = None,
    ) -> List[Order]:
        """Return open orders for ``address`` on the perps engine."""
        data = (
            self._get(
                f"{_PERPS_BASE}/accounts/{address}/orders",
                params={"symbol": symbol, "accountID": account_id},
            )
            or {}
        )
        return [Order.from_dict(x) for x in (data.get("orders") or [])]

    def perps_positions(
        self,
        address: str,
        *,
        symbol: Optional[str] = None,
        account_id: Optional[int] = None,
    ) -> List[Position]:
        """Return open positions for ``address``."""
        data = (
            self._get(
                f"{_PERPS_BASE}/accounts/{address}/positions",
                params={"symbol": symbol, "accountID": account_id},
            )
            or {}
        )
        return [Position.from_dict(x) for x in (data.get("positions") or [])]

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
        data = (
            self._get(
                f"{_PERPS_BASE}/accounts/{address}/fundings",
                params=self._history_params(f),
            )
            or []
        )
        return [FundingPayment.from_dict(x) for x in data]

    # ─────────────────────────────────────────────────────────────────────────
    # Perps — Authenticated trading
    # ─────────────────────────────────────────────────────────────────────────

    def place_perps_order(
        self, request: PerpsNewOrderRequest
    ) -> List[PlaceOrderResult]:
        """Submit a perpetuals order batch. Requires a configured private key."""
        if self._perps_sgn is None:
            raise NotAuthenticatedError()

        def submit(nonce: int) -> List[PlaceOrderResult]:
            sig = self._perps_sgn.sign_new_order_request(request, nonce)
            data = self._post_signed(
                f"{_PERPS_BASE}/trade/orders", request.to_json_payload(), sig, nonce
            ) or []
            return [PlaceOrderResult.from_dict(x) for x in data]

        return self._with_nonce(submit)

    def cancel_perps_orders(
        self, request: PerpsCancelOrderRequest
    ) -> List[CancelOrderResult]:
        """Cancel perpetuals orders."""
        if self._perps_sgn is None:
            raise NotAuthenticatedError()

        def submit(nonce: int) -> List[CancelOrderResult]:
            sig = self._perps_sgn.sign_cancel_order_request(request, nonce)
            data = self._delete_signed(
                f"{_PERPS_BASE}/trade/orders", request.to_json_payload(), sig, nonce
            ) or []
            return [CancelOrderResult.from_dict(x) for x in data]

        return self._with_nonce(submit)

    def modify_perps_order(self, request: ModifyOrderRequest) -> ModifyOrderResult:
        """Modify a single resting perps order's price, quantity, or stop price.

        Identify the target order by ``order_id`` or ``cl_ord_id`` (exactly one
        must be set on the request).
        """
        if self._perps_sgn is None:
            raise NotAuthenticatedError()

        def submit(nonce: int) -> ModifyOrderResult:
            sig = self._perps_sgn.sign_modify_order_request(request, nonce)
            data = self._post_signed(
                f"{_PERPS_BASE}/trade/orders/modify",
                request.to_json_payload(),
                sig,
                nonce,
            ) or {}
            return ModifyOrderResult.from_dict(data)

        return self._with_nonce(submit)

    def replace_perps_orders(
        self, request: ReplaceOrderRequest
    ) -> List[PlaceOrderResult]:
        """Atomically replace a batch of resting perpetuals orders."""
        if self._perps_sgn is None:
            raise NotAuthenticatedError()

        def submit(nonce: int) -> List[PlaceOrderResult]:
            sig = self._perps_sgn.sign_replace_order_request(request, nonce)
            data = self._post_signed(
                f"{_PERPS_BASE}/trade/orders/replace",
                request.to_json_payload(),
                sig,
                nonce,
            ) or []
            return [PlaceOrderResult.from_dict(x) for x in data]

        return self._with_nonce(submit)

    def update_leverage(self, request: UpdateLeverageRequest) -> LeverageResult:
        """Change leverage for a perpetuals position."""
        if self._perps_sgn is None:
            raise NotAuthenticatedError()

        def submit(nonce: int) -> LeverageResult:
            sig = self._perps_sgn.sign_update_leverage_request(request, nonce)
            data = self._post_signed(
                f"{_PERPS_BASE}/trade/leverage",
                request.to_json_payload(),
                sig,
                nonce,
            ) or {}
            return LeverageResult.from_dict(data)

        return self._with_nonce(submit)

    def update_margin(self, request: UpdateMarginRequest) -> None:
        """Adjust margin for a perpetuals position."""
        if self._perps_sgn is None:
            raise NotAuthenticatedError()

        def submit(nonce: int) -> None:
            sig = self._perps_sgn.sign_update_margin_request(request, nonce)
            self._post_signed(
                f"{_PERPS_BASE}/trade/margin",
                request.to_json_payload(),
                sig,
                nonce,
            )

        self._with_nonce(submit)

    def perps_transfer(self, request: TransferAssetRequest) -> TransferReceipt:
        """Transfer assets from a perps account and return the transfer ID."""
        if self._perps_sgn is None:
            raise NotAuthenticatedError()

        def submit(nonce: int) -> TransferReceipt:
            sig = self._perps_sgn.sign_transfer_asset_request(request, nonce)
            data = self._post_signed(
                f"{_PERPS_BASE}/accounts/transfers",
                request.to_json_payload(),
                sig,
                nonce,
            ) or {}
            return TransferReceipt.from_dict(data)

        return self._with_nonce(submit)

    def schedule_perps_cancel(self, request: ScheduleCancelRequest) -> None:
        """Arm (or clear) a dead-man's switch that auto-cancels perps orders.

        Pass a ``ScheduleCancelRequest`` with ``scheduled_timestamp`` set (unix ms)
        to arm, or ``None`` to clear an existing schedule.
        """
        if self._perps_sgn is None:
            raise NotAuthenticatedError()

        def submit(nonce: int) -> None:
            sig = self._perps_sgn.sign_schedule_cancel_request(request, nonce)
            self._post_signed(
                f"{_PERPS_BASE}/trade/orders/schedule-cancel",
                request.to_json_payload(),
                sig,
                nonce,
            )

        self._with_nonce(submit)

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

    def spot_symbols(self, symbol: Optional[str] = None) -> List[Symbol]:
        """Return spot pairs, optionally matching an internal or display name."""
        data = self._get(f"{_SPOT_BASE}/markets/symbols") or []
        symbols = [Symbol.from_dict(x) for x in data]
        if symbol is None:
            return symbols
        normalized = symbol.lower()
        return [
            item
            for item in symbols
            if item.symbol.lower() == normalized
            or item.display_name.lower() == normalized
        ]

    def spot_coins(self, coin: Optional[str] = None) -> List[Coin]:
        """Return spot coins and their engine IDs."""
        data = self._get(f"{_SPOT_BASE}/markets/coins", params={"coin": coin}) or []
        return [Coin.from_dict(x) for x in data]

    def spot_tickers(self, symbol: Optional[str] = None) -> List[Ticker]:
        """Return 24-hour rolling stats for all spot pairs."""
        data = (
            self._get(f"{_SPOT_BASE}/markets/tickers", params={"symbol": symbol}) or []
        )
        return [Ticker.from_dict(x) for x in data]

    def spot_order_book(self, symbol: str, depth: int = 0) -> OrderBook:
        """Return the order book snapshot for ``symbol``.

        ``symbol`` is the internal name (e.g. ``vBTC_vUSDC``).
        Pass ``depth <= 0`` to use the API default.
        """
        params = {"limit": depth} if depth > 0 else None
        data = self._get(f"{_SPOT_BASE}/markets/{symbol}/orderbook", params=params)
        return OrderBook.from_dict(data or {}, symbol=symbol)

    def spot_account_info(self, address: str) -> AccountInfo:
        """Return the account ID and user ID for ``address`` (the ``aid`` field)."""
        data = self._get(f"{_SPOT_BASE}/accounts/{address}/state") or {}
        return AccountInfo.from_dict(data)

    def spot_account_state(
        self, address: Optional[str] = None, account_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Return the complete spot account snapshot without dropping fields."""
        user = address or self.account_address
        if not user:
            raise ValueError("address is required for a read-only client")
        return (
            self._get(
                f"{_SPOT_BASE}/accounts/{user}/state", params={"accountID": account_id}
            )
            or {}
        )

    def spot_balances(
        self, address: str, account_id: Optional[int] = None
    ) -> List[Balance]:
        """Return asset balances for ``address`` on the spot engine."""
        data = (
            self._get(
                f"{_SPOT_BASE}/accounts/{address}/balances",
                params={"accountID": account_id},
            )
            or {}
        )
        return [Balance.from_dict(x) for x in (data.get("balances") or [])]

    def spot_orders(
        self,
        address: str,
        *,
        symbol: Optional[str] = None,
        account_id: Optional[int] = None,
    ) -> List[Order]:
        """Return open orders for ``address`` on the spot engine."""
        data = (
            self._get(
                f"{_SPOT_BASE}/accounts/{address}/orders",
                params={"symbol": symbol, "accountID": account_id},
            )
            or {}
        )
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

        def submit(nonce: int) -> List[PlaceOrderResult]:
            sig = self._spot_sgn.sign_batch_new_order_request(request, nonce)
            data = self._post_signed(
                f"{_SPOT_BASE}/trade/orders/batch",
                request.to_json_payload(),
                sig,
                nonce,
            ) or []
            return [PlaceOrderResult.from_dict(x) for x in data]

        return self._with_nonce(submit)

    def cancel_spot_orders(
        self, request: BatchCancelOrderRequest
    ) -> List[CancelOrderResult]:
        """Submit a batch of spot order cancellations."""
        if self._spot_sgn is None:
            raise NotAuthenticatedError()

        def submit(nonce: int) -> List[CancelOrderResult]:
            sig = self._spot_sgn.sign_batch_cancel_order_request(request, nonce)
            data = self._delete_signed(
                f"{_SPOT_BASE}/trade/orders/batch",
                request.to_json_payload(),
                sig,
                nonce,
            ) or []
            return [CancelOrderResult.from_dict(x) for x in data]

        return self._with_nonce(submit)

    def replace_spot_orders(
        self, request: ReplaceOrderRequest
    ) -> List[PlaceOrderResult]:
        """Replace a batch of existing spot orders."""
        if self._spot_sgn is None:
            raise NotAuthenticatedError()

        def submit(nonce: int) -> List[PlaceOrderResult]:
            sig = self._spot_sgn.sign_replace_order_request(request, nonce)
            data = self._post_signed(
                f"{_SPOT_BASE}/trade/orders/replace",
                request.to_json_payload(),
                sig,
                nonce,
            ) or []
            return [PlaceOrderResult.from_dict(x) for x in data]

        return self._with_nonce(submit)

    def spot_transfer(self, request: TransferAssetRequest) -> TransferReceipt:
        """Transfer assets from a spot account and return the transfer ID."""
        if self._spot_sgn is None:
            raise NotAuthenticatedError()

        def submit(nonce: int) -> TransferReceipt:
            sig = self._spot_sgn.sign_transfer_asset_request(request, nonce)
            data = self._post_signed(
                f"{_SPOT_BASE}/accounts/transfers",
                request.to_json_payload(),
                sig,
                nonce,
            ) or {}
            return TransferReceipt.from_dict(data)

        return self._with_nonce(submit)

    def schedule_spot_cancel(self, request: ScheduleCancelRequest) -> None:
        """Arm (or clear) a dead-man's switch that auto-cancels spot orders."""
        if self._spot_sgn is None:
            raise NotAuthenticatedError()

        def submit(nonce: int) -> None:
            sig = self._spot_sgn.sign_schedule_cancel_request(request, nonce)
            self._post_signed(
                f"{_SPOT_BASE}/trade/orders/schedule-cancel",
                request.to_json_payload(),
                sig,
                nonce,
            )

        self._with_nonce(submit)

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

    # ── Hyperliquid-style ergonomic helpers ──────────────────────────────────

    def _resolve_symbol_id(self, market: str, symbol: str) -> int:
        symbols = (
            self.spot_symbols(symbol)
            if market == "spot"
            else self.perps_symbols(symbol)
        )
        match = next(
            (
                item
                for item in symbols
                if item.symbol.lower() == symbol.lower()
                or item.display_name.lower() == symbol.lower()
            ),
            None,
        )
        if match is None:
            raise ValueError(f"unknown {market} symbol: {symbol}")
        return match.symbol_id

    def _resolve_coin_id(self, market: str, coin: str) -> int:
        coins = self.spot_coins(coin) if market == "spot" else self.perps_coins(coin)
        match = next(
            (item for item in coins if item.coin.lower() == coin.lower()), None
        )
        if match is None:
            raise ValueError(f"unknown {market} coin: {coin}")
        return match.coin_id

    def perps_order(
        self,
        symbol: str,
        is_buy: bool,
        quantity: Decimal,
        *,
        limit_price: Optional[Decimal] = None,
        account_id: Optional[int] = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
        reduce_only: bool = False,
        position_side: Optional[PositionSide] = None,
        cl_ord_id: Optional[str] = None,
    ) -> PlaceOrderResult:
        """Place one perps order by symbol, resolving account/symbol IDs automatically."""
        resolved_account = account_id or self.primary_account_id()
        resolved_symbol = self._resolve_symbol_id("perps", symbol)
        side = OrderSide.BUY if is_buy else OrderSide.SELL
        resolved_position_side = position_side or PositionSide.BOTH
        client_order_id = cl_ord_id or f"sdk-{self._nonce()}"
        if limit_price is None:
            results = self.place_perps_market_order(
                resolved_account,
                resolved_symbol,
                client_order_id,
                side,
                resolved_position_side,
                quantity,
                reduce_only,
            )
        else:
            results = self.place_perps_limit_order(
                resolved_account,
                resolved_symbol,
                client_order_id,
                side,
                resolved_position_side,
                time_in_force,
                limit_price,
                quantity,
                reduce_only,
            )
        if not results:
            raise RuntimeError("perps order endpoint returned no receipt")
        return results[0]

    def spot_order(
        self,
        symbol: str,
        is_buy: bool,
        quantity: Decimal,
        *,
        limit_price: Optional[Decimal] = None,
        account_id: Optional[int] = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
        cl_ord_id: Optional[str] = None,
    ) -> PlaceOrderResult:
        """Place one spot order by symbol, resolving account/symbol IDs automatically."""
        resolved_account = account_id or self.primary_account_id()
        resolved_symbol = self._resolve_symbol_id("spot", symbol)
        side = OrderSide.BUY if is_buy else OrderSide.SELL
        client_order_id = cl_ord_id or f"sdk-{self._nonce()}"
        if limit_price is None:
            results = self.place_spot_market_order(
                resolved_account,
                resolved_symbol,
                client_order_id,
                side,
                quantity,
            )
        else:
            results = self.place_spot_limit_order(
                resolved_account,
                resolved_symbol,
                client_order_id,
                side,
                time_in_force,
                limit_price,
                quantity,
            )
        if not results:
            raise RuntimeError("spot order endpoint returned no receipt")
        return results[0]

    def cancel_perps_order(
        self,
        symbol: str,
        *,
        order_id: Optional[int] = None,
        cl_ord_id: Optional[str] = None,
        account_id: Optional[int] = None,
    ) -> CancelOrderResult:
        """Cancel one perps order by order ID or client order ID."""
        results = self.cancel_perps_orders(
            PerpsCancelOrderRequest(
                account_id=account_id or self.primary_account_id(),
                cancels=[
                    CancelOrder(
                        symbol_id=self._resolve_symbol_id("perps", symbol),
                        order_id=order_id,
                        cl_ord_id=cl_ord_id,
                    )
                ],
            )
        )
        if not results:
            raise RuntimeError("perps cancel endpoint returned no receipt")
        return results[0]

    def cancel_spot_order(
        self,
        symbol: str,
        *,
        order_id: Optional[int] = None,
        orig_cl_ord_id: Optional[str] = None,
        cl_ord_id: Optional[str] = None,
        account_id: Optional[int] = None,
    ) -> CancelOrderResult:
        """Cancel one spot order by order ID or original client order ID."""
        cancel_id = cl_ord_id or f"sdk-cancel-{self._nonce()}"
        results = self.cancel_spot_orders(
            BatchCancelOrderRequest(
                account_id=account_id or self.primary_account_id(),
                cancels=[
                    BatchCancelOrderItem(
                        symbol_id=self._resolve_symbol_id("spot", symbol),
                        cl_ord_id=cancel_id,
                        order_id=order_id,
                        orig_cl_ord_id=orig_cl_ord_id,
                    )
                ],
            )
        )
        if not results:
            raise RuntimeError("spot cancel endpoint returned no receipt")
        return results[0]

    def wait_for_spot_balance_change(
        self,
        coin: str,
        previous_balance: Optional[str],
        *,
        user_address: Optional[str] = None,
        account_id: Optional[int] = None,
        timeout: float = 120.0,
        interval: float = 3.0,
    ) -> List[Balance]:
        """Wait until one Spot balance differs from its previous value."""
        user = user_address or self.account_address
        if not user:
            raise ValueError("user_address is required for a read-only client")
        return self._poll_until(
            f"{coin} Spot balance change",
            lambda: self.spot_balances(user, account_id),
            lambda balances: self._balance_total(balances, coin) != previous_balance,
            timeout=timeout,
            interval=interval,
        )

    def wait_for_perps_balance_change(
        self,
        coin: str,
        previous_balance: Optional[str],
        *,
        user_address: Optional[str] = None,
        account_id: Optional[int] = None,
        timeout: float = 120.0,
        interval: float = 3.0,
    ) -> List[Balance]:
        """Wait until one Perps balance differs from its previous value."""
        user = user_address or self.account_address
        if not user:
            raise ValueError("user_address is required for a read-only client")
        return self._poll_until(
            f"{coin} Perps balance change",
            lambda: self.perps_balances(user, account_id),
            lambda balances: self._balance_total(balances, coin) != previous_balance,
            timeout=timeout,
            interval=interval,
        )

    @staticmethod
    def _balance_total(balances: List[Balance], coin: str) -> Optional[str]:
        balance = next(
            (item for item in balances if item.coin.lower() == coin.lower()), None
        )
        return balance.total if balance is not None else None

    def transfer_perps_to_spot(
        self,
        coin: str,
        amount: Decimal,
        *,
        account_id: Optional[int] = None,
        transfer_id: Optional[int] = None,
    ) -> TransferReceipt:
        """Move funds from the user's perps account into the spot account."""
        source = account_id or self.primary_account_id()
        return self.perps_transfer(
            TransferAssetRequest(
                id=transfer_id or self._nonce(),
                from_account_id=source,
                to_account_id=TREASURY_ACCOUNT_ID,
                coin_id=self._resolve_coin_id("perps", coin),
                amount=amount,
                type=TransferAssetType.SPOT_WITHDRAW,
            )
        )

    def transfer_spot_to_perps(
        self,
        coin: str,
        amount: Decimal,
        *,
        account_id: Optional[int] = None,
        transfer_id: Optional[int] = None,
    ) -> TransferReceipt:
        """Move funds from the user's spot account into the perps account."""
        source = account_id or self.primary_account_id()
        return self.spot_transfer(
            TransferAssetRequest(
                id=transfer_id or self._nonce(),
                from_account_id=source,
                to_account_id=TREASURY_ACCOUNT_ID,
                coin_id=self._resolve_coin_id("spot", coin),
                amount=amount,
                type=TransferAssetType.PERPS_WITHDRAW,
            )
        )

    def transfer_spot_to_evm(
        self,
        coin: str,
        amount: Decimal,
        *,
        account_id: Optional[int] = None,
        transfer_id: Optional[int] = None,
    ) -> TransferReceipt:
        """Move funds from spot into the user's ValueChain EVM balance."""
        source = account_id or self.primary_account_id()
        return self.spot_transfer(
            TransferAssetRequest(
                id=transfer_id or self._nonce(),
                from_account_id=source,
                to_account_id=TREASURY_ACCOUNT_ID,
                coin_id=self._resolve_coin_id("spot", coin),
                amount=amount,
                type=TransferAssetType.EVM_WITHDRAW,
            )
        )
