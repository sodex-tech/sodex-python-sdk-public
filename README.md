# sodex-python-sdk

Official Python SDK for the Sodex exchange. Provides:

- **EIP-712 signing** — low-level signing primitives for the Spark (spot) and Bolt (perpetuals) engines.
- **HTTP REST client** — ready-to-use client that signs and sends requests automatically.
- **WebSocket client** — auto-reconnecting subscriber for real-time market data and account updates.

Mirrors the capabilities of the [Go public SDK](https://github.com/sodex-tech/sodex-go-sdk-public).

## Requirements

- Python 3.9+

## AI agent skill — one-command install

Install the [SoDEX skill](./skills/sodex/SKILL.md) for your coding agent:

```bash
npx skills add sodex-tech/sodex-python-sdk-public --skill sodex -g -a codex -y
# For Claude Code, replace "codex" with "claude-code".
```

Then ask: **“Use $sodex to connect and verify a live BTC perpetual quote.”**

The agent creates an isolated Python environment, installs the reviewed SDK
source, and verifies a live public quote without a wallet. Python 3.9+ and Git
are required; npm is used only to install the skill. Trading credentials are
configured locally when needed, and signed actions require the user's
authorization. All nine SDK examples are covered, with GitBook-backed guidance
for account reads, trading, streams, funding, API keys and builder fees.

See the [install/runtime guide](./skills/sodex/references/setup.md) for other
agents, project installs, updates and troubleshooting. The skill follows the
portable install and on-demand reference approach demonstrated by
[Longbridge Skills](https://github.com/longbridge/skills); it uses SoDEX's SDK
directly and does not require a separate CLI or MCP server.

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

# Deposit and withdrawal status APIs can return multiple records.
deposit = client.get_deposit_status(route.chain, "0xexternal-deposit-hash")

# Transfers return acceptance. Wait and reconcile before a dependent transfer.
previous_spot = next((b.total for b in client.spot_balances(client.account_address)
                      if b.coin == "vUSDC"), None)
receipt = client.transfer_perps_to_spot("vUSDC", Decimal("10"))
client.wait_for_spot_balance_change("vUSDC", previous_spot)
# Check the expected credit for this operation before moving funds onward.

# ValueChain EVM can credit Spot (destination="spot") or Perps directly.
client.deposit_evm_to_engine("USDC", Decimal("10"), "perps")

request = client.prepare_evm_withdraw(
    coin="USDC",
    chain=route.chain,
    receiver="0xrecipient",
    amount=Decimal("10"),
    withdrawal_type="custody",  # or "bridge"
)
submission = client.submit_evm_withdraw(client.address, request)  # sponsored gas
withdrawal = client.wait_for_withdrawal(
    route.chain, tx_hash=submission.tx_hash
)
```

`custody_available` and `bridge_available` describe **deposit** availability.
Use `route.withdrawal_method("custody")` or `route.withdrawal_method("bridge")`
to validate withdrawals and obtain that route's minimum and fee. A missing or
empty fee is unknown, not zero. `route.custody` and `route.bridge` expose the
independent allow flags and optional completion estimates.

Asset discovery uses Gateway's `name` filter and nested metadata schema.
`asset.coin` is the canonical asset name; `asset.asset_name` is its engine name.
`asset.valuechain_metadata` preserves native/wrapped token metadata. Asset ID
zero is valid; an absent engine registration remains `None`. The parser also
accepts explicitly identifiable legacy fixtures, but live requests use `name`.

`prepare_evm_withdraw()` uses the documented ValueChain
`nonces(address,uint192)` and `hashCallForPermit(...)` contract ABI.

Custody-address creation uses Gateway's current public, chain-only v1 API and
is mainnet-only.

### User registration status

```python
from sodex.client import Client

client = Client.from_env()

print(client.get_user_status("0x..."))
```

`get_user_status()` returns `Active` with an exact Python `int` user ID, or
`UserNotFound`; the trade example checks it before placing an order.

### API keys

```python
from sodex.client import Client, RevokeAPIKeyRequest

master = Client.from_env()
generated, trading = master.approve_agent("my-bot")

# Query and revoke the same key on both engines through Gateway's aggregate API.
print(master.get_api_keys(name="my-bot"))
master.revoke_api_key(
    master.address,
    RevokeAPIKeyRequest(master.primary_account_id(), "my-bot"),
)

# Approve a builder's maximum fee rate on both Spot and Perps.
master.approve_builder_fee(builder_id=9, max_fee_rate=20)
```

Store `generated.private_key` in a secret manager; the SDK neither persists nor
prints it. The unified registration call makes the key available to Spot and
Perps. Omitting `permissions` enables every permission. For a restricted key,
each `APIKeyPermission` bit included in the mask disables that permission.

### WebSocket client

```python
from sodex.client import Client as RestClient
from sodex.ws import Client

rest = RestClient.from_env()
c = Client.from_base_url(rest.base_url, engine="perps")
c.connect()

subscription = c.subscribe_account(
    rest.account_address,
    symbols=["BTC-USD"],
    on_order_update=lambda order: print(order.order_id, order.status),
    on_trade=lambda fill: print(fill.order_id, fill.trade_id, fill.price),
)
subscription.wait_ready(timeout=10)  # raises on rejection or timeout
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
| [`examples/transfer_to_evm.py`](./examples/transfer_to_evm.py) | Transfer across EVM, Spot, and Perps and wait for settlement |
| [`examples/api_key.py`](./examples/api_key.py) | List/register/revoke a unified API key |
| [`examples/approve_builder_fee.py`](./examples/approve_builder_fee.py) | Approve a builder fee on Spot and Perps |
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


## Builder-enabled orders

```python
from sodex.client import BuilderParams

receipt = client.perps_order(
    "BTC-USD", True, Decimal("0.001"), limit_price=Decimal("50000"),
    builder=BuilderParams(id=9, fee=20),
)
```

Approve the builder first with the master wallet. The fee is signed as part of
the order payload. Spot supports batch attribution; Perps supports a batch
default and `RawOrder(builder=...)` overrides. Omitting a builder preserves the
original signing payload.

## Completion and recovery

- `wait_for_deposit` means indexed, not credited. Inspect status and destination funds.
- `wait_for_withdrawal` returns terminal records including failures; check every record.
- Engine transfers return acceptance; `deposit_evm_to_engine` waits for EVM execution,
  not engine settlement. Reconcile the relevant operation before dependent writes.
- Balance changes alone do not uniquely identify a transfer in an active account.
- WebSocket subscriptions expose `wait_ready`; acknowledgements do not replay missed
  trades. After disconnect, reconcile REST state and wait for subscription readiness
  again. Use one WebSocket client per account owner.
- `connect()` is nonblocking. `wait_ready` must be called outside the reader callbacks.
  Closing the client or cancelling a subscription releases pending readiness waiters.
