# sodex-python-sdk

Official Python SDK for the Sodex exchange. Provides:

- **EIP-712 signing** — low-level signing primitives for the Spark (spot) and Bolt (perpetuals) engines.
- **HTTP REST client** — ready-to-use client that signs and sends requests automatically.
- **WebSocket client** — auto-reconnecting subscriber for real-time market data and account updates.

Mirrors the capabilities of the [Go public SDK](https://github.com/sodex-tech/sodex-go-sdk-public).

## Requirements

- Python 3.9+

## Installation

```bash
pip install sodex-python-sdk
```

## Usage

### Zero-boilerplate setup

```bash
export SODEX_NETWORK=testnet              # mainnet is the default
export SODEX_PRIVATE_KEY=0x...            # omit for read-only calls
export SODEX_ACCOUNT_ADDRESS=0x...        # required with an API key/read-only client
export SODEX_API_KEY_NAME=my-bot          # only when the key is a registered API key
```

```python
from decimal import Decimal
from sodex.client import Client

client = Client.from_env()

# Market data needs no key.
print(client.perps_tickers("BTC-USD")[0])

# Trading resolves the primary account and symbol ID, signs, submits, and
# returns one typed receipt containing the Gateway order ID.
receipt = client.perps_order(
    "BTC-USD", True, Decimal("0.01"), limit_price=Decimal("50000")
)
print(receipt.order_id)

# A market close discovers the active position and sends the opposite
# reduce-only order.
closed = client.market_close("BTC-USD")
```

`Client.from_private_key("0x...", testnet=True)` is available when environment
variables are not appropriate. The existing low-level methods remain available
for callers that need explicit account IDs, symbol IDs, or order batches.

### Funding flows

```python
from decimal import Decimal
from sodex.client import Client

client = Client.from_env()

# Discover token/chain routes. Custody and bridge availability are distinct.
asset, route = client.get_transfer_route("USDC", "BASE_ETH")
print(route.custody_available, route.bridge_available)

# Query the custody address and create it only when Gateway returns an empty one.
address = client.ensure_deposit_address(route.chain)

# The latest Gateway also supports provisioning every custody chain at once.
addresses = client.create_deposit_addresses(client.account_address)

# Deposit and withdrawal status APIs can return multiple records.
deposit = client.get_deposit_status(route.chain, "0xexternal-deposit-hash")

# Funds in Perps/Spot must move to ValueChain EVM first.
client.transfer_perps_to_spot("vUSDC", Decimal("10"))
client.transfer_spot_to_evm("vUSDC", Decimal("10"))

request = client.prepare_evm_withdraw(
    coin="USDC",
    chain=route.chain,
    receiver="0xrecipient",
    amount=Decimal("10"),
    withdrawal_type="custody",  # or "bridge"
)
submission = client.submit_evm_withdraw(client.address, request)
withdrawal = client.get_withdraw_status(route.chain, tx_hash=submission.tx_hash)
```

`custody_available` follows `custodyDisabled == false`; `bridge_available`
follows a non-empty `bridgeAddress`. The SDK exposes the bridge contract address
but does not guess an external-chain deposit call that is absent from the
published ABI. `prepare_evm_withdraw()` uses the documented ValueChain
`nonces(address,uint192)` and `hashCallForPermit(...)` contract ABI.

Single and batch custody-address creation use Gateway's current public,
chain-only v1 API and are mainnet-only. Partner integrations can use
`create_partner_deposit_address(..., partner_api_key=...)` and
`create_partner_deposit_addresses(..., partner_api_key=...)` for the v2
partner-quota routes.

### Gateway metadata and RWA calendars

```python
from sodex.client import Client

client = Client.from_env()

print(client.get_server_time())
print(client.get_system_status())
print(client.get_user_status("0x..."))

announcements = client.get_announcements(page=1, size=20, lang="en")
hours = client.get_trading_hours("US", client.get_server_time())
next_day = client.get_next_trading_day("CXMT", client.get_server_time())
```

`get_user_status()` returns `Active` with an exact Python `int` user ID, or
`UserNotFound`. RWA markets currently accepted by Gateway include `US`, `CN`,
`HK`, `JP`, `KR`, `CME_EQUITY`, `CME_BASE_METALS`, and `CME_ENERGY`.

### API keys

```python
from sodex.client import Client
from sodex.common.enums import APIKeyPermission

master = Client.from_env()
generated, trading = master.approve_agent(
    "my-bot",
    permissions=APIKeyPermission.TRADE | APIKeyPermission.CANCEL,
)
```

Store `generated.private_key` in a secret manager; the SDK neither persists nor
prints it. Aggregate API-key operations update/query both Spot and Perps.

### WebSocket client

```python
from sodex.client import Client as RestClient
from sodex.ws import Client

rest = RestClient.from_env()
c = Client.from_base_url(rest.base_url, engine="perps")
c.connect()

c.subscribe_account(
    rest.account_address,
    symbols=["BTC-USD"],
    on_order_update=lambda order: print(order.order_id, order.status),
    on_trade=lambda fill: print(fill.order_id, fill.trade_id, fill.price),
)
```

### Examples

Runnable end-to-end examples and their lifecycle guide live in
[`examples/`](./examples/README.md):

| File | Shows |
|---|---|
| [`examples/trade.py`](./examples/trade.py) | Inspect common state and place a Spot or Perps order |
| [`examples/account.py`](./examples/account.py) | Query balances, orders, positions (spot + perps) |
| [`examples/websocket.py`](./examples/websocket.py) | Subscribe to trades + order book |
| [`examples/funding.py`](./examples/funding.py) | Discover custody/bridge routes, provision an address, and track a deposit |
| [`examples/evm_withdraw.py`](./examples/evm_withdraw.py) | Prepare, submit, resume, and track an EVM withdrawal |
| [`examples/transfer_to_evm.py`](./examples/transfer_to_evm.py) | Run one explicit Perps → Spot or Spot → EVM transfer step |
| [`examples/api_key.py`](./examples/api_key.py) | Generate/register an API key and configure a trading client |
| [`examples/account_websocket.py`](./examples/account_websocket.py) | Correlate REST order IDs with order updates and fills |

### Low-level signing only

```python
from sodex.perps.signer import PerpsSigner
from sodex.perps.types import UpdateLeverageRequest
from sodex.common.enums import MarginMode

s = PerpsSigner(chain_id=286623, private_key=bytes.fromhex("..."))
sig = s.sign_update_leverage_request(
    UpdateLeverageRequest(account_id=5655, symbol_id=1, leverage=5, margin_mode=MarginMode.CROSS),
    nonce=1,
)
```
