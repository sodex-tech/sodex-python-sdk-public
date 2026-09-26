# Spot and Perps trading

Start from `examples/trade.py` for the lifecycle and from
`sodex/client/client.py` for high-level method signatures. Use the pinned
runtime from [setup](setup.md). For credentials, read
[credentials and builders](credentials-builders.md).

## Before submitting

Resolve the authorized network, owner/account, market, symbol, side, order
type, quantity, limit price when applicable, and any reduce-only, time-in-force
or builder settings. A request to show an example authorizes generating code,
not executing its order. Do not silently choose a quantity, leverage or fee.

Read registration status, account ID, symbol metadata, balances/positions and
effective fees before signing. Match quantity to `step_size`, price to
`tick_size`, and check current min/max quantity, price and notional limits.
Use decimal arithmetic; do not silently round a user's order. If the account
is not active or funds/limits are insufficient, report the unmet condition.

`examples/trade.py` prints these constraints and then immediately submits;
it is **not** a dry-run or a complete risk/constraint validator. For a preview,
use only its read calls (`get_user_status`, `primary_account_id`,
`spot_symbols`/`perps_symbols`, `get_fee_rate`, account state). Run the example
only after the concrete trade is authorized and parameters have been checked:

```bash
# Set every value to the authorized order, including the network.
export SODEX_NETWORK=testnet
export SODEX_MARKET=perps
export SODEX_SYMBOL=BTC-USD
export SODEX_ORDER_SIDE=BUY
export SODEX_ORDER_TYPE=LIMIT
# SODEX_ORDER_QUANTITY and SODEX_ORDER_PRICE must already be configured.
"$SODEX_PYTHON" "$SODEX_SDK/examples/trade.py"
```

Those market/side values are illustrative, not defaults to apply to a user's
request. `SODEX_CANCEL_AFTER_PLACE=true` submits a real order before cancelling;
it cannot guarantee no fill. Never enable it for a market order: that example
checks the cancellation restriction only after placement.
For Spot, set `SODEX_SYMBOL` to the exact symbol returned by `spot_symbols()`;
the upstream `BTC/USDC` fallback is not valid on current mainnet, which returns
`vBTC_vUSDC`. Do not change the spelling or synthesize an alias.

## Application integration

Use `spot_order()` or `perps_order()` for one order and high-level cancel
methods for one cancellation. Supply a persisted `cl_ord_id` when building a
recoverable workflow. It is a correlation ID, not a promise of idempotent retry.

```python
import os
from decimal import Decimal
from sodex.client import Client

client = Client.from_env()  # Network and locally stored trading credentials set.
receipt = client.perps_order(
    os.environ["SODEX_SYMBOL"],
    os.environ["SODEX_ORDER_SIDE"] == "BUY",
    Decimal(os.environ["SODEX_ORDER_QUANTITY"]),
    limit_price=Decimal(os.environ["SODEX_ORDER_PRICE"]),
    cl_ord_id=os.environ["SODEX_CLIENT_ORDER_ID"],
)
print(receipt.order_id, receipt.cl_ord_id, receipt.status, receipt.message)
```

This snippet assumes validated parameters and an authorized limit order.
Omit `limit_price` only for an authorized market order. Perps also supports
`reduce_only`, `position_side` and `time_in_force`; use the SDK enums and the
user's requested behavior. For a subaccount, pass its explicit `account_id`.

For an independently authorized cancellation:

```python
cancelled = client.cancel_perps_order(
    os.environ["SODEX_SYMBOL"],
    order_id=int(os.environ["SODEX_ORDER_ID"]),
)
print(cancelled.status, cancelled.message)
```

Spot equivalents are `spot_order()` and `cancel_spot_order()`. For builder
attribution, pass `builder=BuilderParams(id=builder_id, fee=fee)` only after
verifying the user's cap on the target engine; see the builder reference.

## Acceptance, fills and recovery

Persist `order_id`, `cl_ord_id`, account, market and intended parameters.
A positive order ID is acceptance, not a fill. Report the exact status;
rejected receipts must not be described as successful trades.

Subscribe and wait for account-stream acknowledgement before submitting if
the integration must observe immediate events. Use [WebSocket](websocket.md)
and reconcile against REST order/fill history on connection loss or late
subscription. A timed-out submission may already have executed: query open
orders, history and fills using saved IDs before considering another order.
Do not use a new nonce to blindly repeat a write.

Sources: [Python SDK guide](https://sodex.com/documentation/for-developers/sdks/python-sdk-guide),
[API keys and nonces](https://sodex.com/documentation/for-developers/developers/trading/api-keys-and-nonces),
[trading concepts](https://sodex.com/documentation/for-developers/developers/trading/trading-mechanics).
