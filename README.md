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

### REST client

```python
from decimal import Decimal
from sodex.client import Client, Config
from sodex.common.enums import OrderSide, PositionSide, TimeInForce

# Read-only (no signing)
c = Client(Config(base_url=Client.TESTNET_BASE_URL))
print(c.perps_tickers()[0])

# Authenticated trading
c = Client(Config(
    base_url=Client.TESTNET_BASE_URL,
    chain_id=Client.TESTNET_CHAIN_ID,
    private_key=bytes.fromhex("your-private-key-hex"),
))
res = c.place_perps_limit_order(
    account_id=1001,
    symbol_id=1,
    cl_ord_id="my-order-001",
    side=OrderSide.BUY,
    position_side=PositionSide.LONG,
    time_in_force=TimeInForce.GTC,
    price=Decimal("50000"),
    quantity=Decimal("0.01"),
)
print(res[0].order_id)
```

### Funding flows

```python
from decimal import Decimal
from sodex.client import Client, Config

client = Client(Config(private_key=bytes.fromhex("master-wallet-key")))

# Discover token/chain routes. Custody and bridge availability are distinct.
asset = client.get_transfer_configs("USDC")[0]
route = next(x for x in asset.chains if x.chain == "BASE_ETH")
print(route.custody_available, route.bridge_available)

# Custody deposit address.
address = client.get_deposit_address(client.address, route.chain)
if not address.address:
    address = client.create_deposit_address(client.address, route.chain)

# Deposit and withdrawal status APIs can return multiple records.
deposit = client.get_deposit_status(route.chain, "0xexternal-deposit-hash")

# Funds must already be in the ValueChain EVM account before this step.
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

### API keys

```python
from sodex.client import AddAPIKeyRequest, Client, Config, generate_api_key
from sodex.common.enums import APIKeyPermission

master = Client(Config(private_key=bytes.fromhex("master-wallet-key")))
generated = generate_api_key("my-bot")
master.add_api_key(
    master.address,
    AddAPIKeyRequest(
        account_id=1001,
        name=generated.name,
        public_key=generated.address,
        permissions=APIKeyPermission.TRADE | APIKeyPermission.CANCEL,
    ),
)

trading = Client(Config(
    private_key=generated.private_key,
    api_key_name=generated.name,
    account_address=master.address,
))
```

Store `generated.private_key` in a secret manager; the SDK neither persists nor
prints it. Aggregate API-key operations update/query both Spot and Perps.

### WebSocket client

```python
from sodex.ws import Client, SubscribeParams, CHANNEL_TICKER

c = Client.from_base_url("https://testnet-gw.sodex.dev", engine="perps")
c.connect()

c.subscribe(
    SubscribeParams(channel=CHANNEL_TICKER, symbol="BTC-USD"),
    lambda push: print(push.channel, push.data),
)
```

### Examples

Runnable end-to-end examples live in [`examples/`](./examples):

| File | Shows |
|---|---|
| [`examples/trade.py`](./examples/trade.py) | Place + cancel a perps limit order |
| [`examples/account.py`](./examples/account.py) | Query balances, orders, positions (spot + perps) |
| [`examples/websocket.py`](./examples/websocket.py) | Subscribe to trades + order book |
| [`examples/funding.py`](./examples/funding.py) | Discover custody/bridge routes and query deposit/withdrawal status |
| [`examples/evm_withdraw.py`](./examples/evm_withdraw.py) | Prepare, submit, and track an EVM withdrawal |
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
