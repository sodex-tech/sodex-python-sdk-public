# Public and account WebSocket streams

The SDK provides background reading, ping/reconnect and resubscription. A
socket connecting is not the same as an acknowledged subscription, and
resubscription does not replay missed fills.

## Public trade and order-book stream

`examples/websocket.py` subscribes to BTC-USD trades and five-level Perps depth.
It hardcodes **testnet**, regardless of `SODEX_NETWORK`. Use it only for that
network; for mainnet or other symbols, adapt a copy in the user's project:

```python
import os
import threading
from sodex.client import Client as RestClient
from sodex.ws import Client as WsClient, SubscribeParams, CHANNEL_TRADE, CHANNEL_L2_BOOK

rest = RestClient.from_env()  # Set SODEX_NETWORK explicitly; no key is needed.
market = os.environ["SODEX_MARKET"]
symbol = os.environ["SODEX_SYMBOL"]
ws = WsClient.from_base_url(rest.base_url, engine=market)
ws.on_error(lambda error: print(type(error).__name__))
ws.subscribe(SubscribeParams(channel=CHANNEL_TRADE, symbol=symbol), print)
ws.subscribe(SubscribeParams(channel=CHANNEL_L2_BOOK, symbol=symbol, level=5), print)
try:
    ws.connect()
    threading.Event().wait(30)  # Bounded observation, not proof of readiness.
finally:
    ws.close()
```

Subscribe before `connect()`. Report success only after actual data arrives;
the example's immediate “connected” log merely means it started background
work. Keep the engine and symbol format aligned. For a long-running service,
use a shutdown event/signal and close the socket in `finally`.

## Account order updates and fills

`examples/account_websocket.py` uses `SODEX_NETWORK`, `SODEX_ACCOUNT_ADDRESS`,
`SODEX_MARKET`, `SODEX_SYMBOL` and optional integer `SODEX_ORDER_ID` filtering.
These streams do not require API-key authentication. Use the owner address,
not the signing-key wallet.
Always supply the exact discovered `SODEX_SYMBOL` for Spot; the example's
`BTC/USDC` fallback differs from mainnet's current `vBTC_vUSDC` symbol.

```bash
"$SODEX_PYTHON" "$SODEX_SDK/examples/account_websocket.py"
```

For integration into a trading process:

```python
import os
from sodex.client import Client as RestClient
from sodex.ws import Client as WsClient

rest = RestClient.from_env()
ws = WsClient.from_base_url(rest.base_url, engine=os.environ["SODEX_MARKET"])
subscription = ws.subscribe_account(
    os.environ["SODEX_ACCOUNT_ADDRESS"],
    symbols=[os.environ["SODEX_SYMBOL"]],
    on_order_update=lambda order: print(order.order_id, order.status),
    on_trade=lambda fill: print(fill.order_id, fill.trade_id, fill.price),
)
try:
    ws.connect()
    subscription.wait_ready(timeout=10)
    # Subscription acknowledged. Reconcile REST state before dependent actions.
    input("Observing account; press Enter to close.\n")
finally:
    subscription.close()
    ws.close()
```

`wait_ready()` raises on rejection/timeout. Call it from the main/control thread,
never from a reader callback. Use one WebSocket client per account owner.
After reconnect, re-establish readiness, pause dependent trading until state
is reconciled, and fetch REST open orders, positions/balances and fill history
covering the gap. Deduplicate fills by their identifiers and account/market;
matching `order_id` links events to the REST receipt. Readiness confirms
acknowledgements, not a completed account snapshot or backfill.

Sources: [WebSocket Streams](https://sodex.com/documentation/for-developers/api-reference/trading-api/websocket-v1),
[Python SDK guide](https://sodex.com/documentation/for-developers/sdks/python-sdk-guide),
[rate limits](https://sodex.com/documentation/for-developers/developers/trading/api-rate-limits).
