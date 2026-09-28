# Market data and account reads

Use the runtime paths from [setup](setup.md). Select the network explicitly.
For a quick quote, `scripts/doctor.py` prints JSON with the source, endpoint,
observation time and exact price strings without loading signing credentials.

For application code, construct a credential-free client even if the shell has
a trading key configured:

```python
import os
from sodex.client import Client, Config, HistoryFilter

network = os.environ["SODEX_NETWORK"]
if network not in ("mainnet", "testnet"):
    raise ValueError("Choose mainnet or testnet")
testnet = network == "testnet"
client = Client(Config(
    base_url=Client.TESTNET_BASE_URL if testnet else Client.DEFAULT_BASE_URL,
    chain_id=Client.TESTNET_CHAIN_ID if testnet else Client.DEFAULT_CHAIN_ID,
    timeout=10,
))
print(client.perps_tickers("BTC-USD"))
print(client.perps_order_book("BTC-USD", depth=5))
print(client.perps_klines("BTC-USD", "1h", HistoryFilter(limit=24)))
print([item.symbol for item in client.spot_symbols()])
# Set this to the requested symbol from that response, e.g. vBTC_vUSDC.
print(client.spot_tickers(os.environ["SODEX_SPOT_SYMBOL"]))
```

Use exact symbols from `perps_symbols()` / `spot_symbols()` on the selected
network: mainnet currently returns `BTC-USD` for BTC Perps and `vBTC_vUSDC` for
BTC Spot. The upstream examples' `BTC/USDC` default is not a portable wire
symbol; do not assume slash-separated Spot symbols or invent a normalization.
For Spot depth/candles, use `spot_order_book()` / `spot_klines()` with a Spot
symbol. Kline daily/weekly/monthly intervals are `1d`, `1w`, `1M` (case matters).
The current Perps API supports a smaller interval set than Spot; use the
current endpoint documentation when it differs from the pinned SDK docstring.
History timestamps are Unix milliseconds; monetary strings should stay strings
or become `Decimal` for calculations.

## Account state

The `examples/account.py` example queries Spot/Perps balances, Perps positions
and Perps open orders. With the locally configured owner address:

```bash
SODEX_NETWORK=mainnet "$SODEX_PYTHON" "$SODEX_SDK/examples/account.py"
```

For an explicitly selected subaccount, extend the credential-free client above:

```python
owner = os.environ["SODEX_ACCOUNT_ADDRESS"]
print(client.get_user_status(owner))
print(client.get_subaccounts(owner))
# Read this ID from the user's selected account; preserve it as Python int.
account_id = int(os.environ["SODEX_TARGET_ACCOUNT_ID"])
print(client.spot_balances(owner, account_id=account_id))
print(client.spot_orders(owner, account_id=account_id))
print(client.perps_balances(owner, account_id=account_id))
print(client.perps_positions(owner, account_id=account_id))
print(client.perps_orders(owner, account_id=account_id))
history = HistoryFilter(account_id=account_id, symbol="BTC-USD", limit=100)
print(client.perps_orders_history(owner, history))
print(client.perps_user_trades(owner, history))
```

Address means the account owner, not the API-key wallet. Use the same account
ID for open state, historical orders and fills. Do not infer that a missing
open order was filled: it may have been cancelled or rejected. Reconcile
history and fills; a single bounded history page is not the entire history.
An unknown user is not the same as an empty funded account.

Report network, account scope, symbol, query time and the actual returned state.
Never substitute sample data when a call fails.

Sources: [Python SDK guide](https://sodex.com/documentation/for-developers/sdks/python-sdk-guide),
[REST API](https://sodex.com/documentation/for-developers/api-reference/trading-api/rest-v1).
