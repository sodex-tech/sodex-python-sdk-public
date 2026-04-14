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
```

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
