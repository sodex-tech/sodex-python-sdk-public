# JSON commands and durable trading workflows

Use `python` and `cli` returned by bootstrap. The CLI supports macOS and Linux
(POSIX file locks); Windows users can use WSL. It uses the official SDK for
signing and request serialization. No separate CLI package is required.

```bash
export SODEX_PYTHON='/absolute/path/from/bootstrap/python'
export SODEX_CLI='/absolute/path/from/bootstrap/cli'
"$SODEX_PYTHON" "$SODEX_CLI" --help
"$SODEX_PYTHON" "$SODEX_CLI" read quote --network testnet --symbol BTC-USD
```

Global options (`--state-dir`, `--profile`) go before the command. Reads need
an explicit network or a profile. They never load ambient signing credentials.
Normal commands emit one JSON object to stdout, including errors; `--help`
prints human-readable usage. Output has `schema_version: 1`, `ok`, and either
`result` or `error`. Exit codes: `0` success, `1` remote/operational failure or
non-accepted execution, `2` invalid input/preflight. Amounts are decimal strings.
Integers above JavaScript's safe range (`2^53-1`) are emitted as decimal strings;
do not convert them through floating point. `ok` on a query or plan means that
command completed, not that any trade executed.

## Local profiles

Create a profile using the user's authorized network and owner address:

```bash
"$SODEX_PYTHON" "$SODEX_CLI" profile init demo --network testnet \
  --owner 0xOWNER --api-key-name registered-bot
"$SODEX_PYTHON" "$SODEX_CLI" --profile demo profile show
```

Run initialization in the user's terminal: private key and encryption password
are hidden prompts. Do not ask the user to send either value to the assistant.
For a secret manager, pass `--key-env SODEX_PRIVATE_KEY` and
`--password-env SODEX_SKILL_PASSWORD`; these arguments are **variable names**.
The user must populate the variables locally, without literal keys in shell
history. Execution only needs the keystore password, optionally supplied with
`--password-env SODEX_SKILL_PASSWORD`.

Profiles use `eth-account`'s standard encrypted keystore, not plaintext keys.
Default state: `~/.local/share/sodex-skill/`; directories are mode `0700`,
profiles/database/lock files `0600`. `profile show` omits the keystore. Keep the
state directory outside source control and backups that expose private files.
It contains sensitive account activity even though the journal has no secrets.
Losing the password prevents unlocking the key; back up through the user's
existing secure process. Never print environment dumps.

Use `--account-id 123` for a specific account; otherwise the primary account is
resolved during planning and frozen in that operation. `--watch-only` creates
a keyless profile for reads and previews. To switch from watching to signing,
create a new named signing profile and a new plan. Existing profiles are not
overwritten. Local import does not register a key or authorize trades. Register
a dedicated key through the [credential workflow](credentials-builders.md)
first; preflight verifies its address, expiry and operation permission on the
selected engine. Master keys require the owner to match the signer and no
API-key name.

## Read commands

```bash
"$SODEX_PYTHON" "$SODEX_CLI" read symbols --network mainnet --market spot
"$SODEX_PYTHON" "$SODEX_CLI" read book --network testnet --symbol BTC-USD --limit 20
"$SODEX_PYTHON" "$SODEX_CLI" read candles --network testnet --symbol BTC-USD --interval 1h --limit 24
"$SODEX_PYTHON" "$SODEX_CLI" --profile demo read account
"$SODEX_PYTHON" "$SODEX_CLI" --profile demo read positions --symbol BTC-USD
"$SODEX_PYTHON" "$SODEX_CLI" --profile demo read orders --market perps
"$SODEX_PYTHON" "$SODEX_CLI" --profile demo read history --symbol BTC-USD --limit 100
"$SODEX_PYTHON" "$SODEX_CLI" --profile demo read fills --symbol BTC-USD --limit 100
```

`balances` is also available. Account queries without a profile accept
`--network`, `--owner`, and optional `--account-id`. History/fills/candles accept
`--start-time` and `--end-time` in milliseconds. The default market is `perps`;
positions require Perps. Results are one requested page, not complete lifetime
history. Discover exact Spot names (for example `vBTC_vUSDC` on mainnet).

## Plan, review, execute

Save an intent using the schemas below in `intent.json`, then:

```bash
"$SODEX_PYTHON" "$SODEX_CLI" --profile demo plan --file intent.json
"$SODEX_PYTHON" "$SODEX_CLI" ops show OPERATION_ID
# Only after the concrete operation is authorized:
"$SODEX_PYTHON" "$SODEX_CLI" --profile demo execute OPERATION_ID --confirm OPERATION_ID
"$SODEX_PYTHON" "$SODEX_CLI" ops reconcile OPERATION_ID
```

`--file -` reads JSON from stdin. Plans persist the network, owner/account,
signer identity, normalized intent, client IDs, SDK payload, and preflight
snapshot. Show the user the material trade parameters and any warnings;
existing authorization for that exact action remains valid. `--confirm` is an
explicit execution gate, not a substitute for user authorization.

Execution unlocks the key locally, locks the signer, reruns preflight, verifies
that the payload still matches, and durably marks the operation `submitting`
before sending. It records per-item receipts and classifies the outcome as
`accepted`, `partial`, `rejected`, or `unknown`. The same operation cannot be
submitted again, including after a rejection. Read current state before
preparing a genuinely new operation.

Preflight checks owner/account membership, API-key registration/expiry/disabled
permissions, exact symbol/trading status, price and quantity precision, grids,
market lots, notional limits, supported order types, price bands, duplicate
client IDs, target open orders, reduce-only direction/size, TP/SL direction,
leverage bounds, margin-mode restrictions and isolated margin precision.
It never rounds a price or amount. It aggregates Spot spending and estimates
Perps opening margin where the account has an established symbol configuration.

Preflight does not reserve funds or reproduce the risk engine. Estimates use
symbol taker fees and can conservatively reject orders that benefit from
position netting. Missing leverage/margin configuration is reported as an
unavailable estimate; configuring it first improves the preview. Replacement
reserve release, fee overrides, maintenance tiers, liquidation and maximum
withdrawable isolated margin remain engine checks. Market orders can execute
at changing prices within protocol protection; a limit/stop is not a fill
guarantee. TP/SL can also fail when triggered.

## Intent schemas

Every intent has `action`, `market`, and the exact `symbol`. Unknown fields
are rejected. Decimal values must be strings; leverage is an integer.
These are schema examples, not recommended trades. Replace amounts, prices
and targets with the user's parameters and current symbol metadata.

### Single or batch orders

```json
{"action":"orders","market":"perps","symbol":"BTC-USD","orders":[
  {"side":"buy","type":"limit","quantity":"0.01","price":"80000","time_in_force":"gtc"},
  {"side":"buy","type":"limit","quantity":"0.01","price":"79000","time_in_force":"gtx"}
]}
```

Use 1–100 items on **one symbol per plan**. Spot uses the same schema with
`market: "spot"` and a discovered Spot symbol. Supported sides: `buy`, `sell`.
Limit orders require price/quantity and support `gtc`, `ioc`, `gtx`; market
orders require quantity, use `ioc`, and optionally accept `price` for protection.
FOK and quote-funds sizing are not exposed. Perps accepts boolean `reduce_only`;
the CLI supports the documented one-way `BOTH` position mode. Optional
`client_order_id` must be unique, 1–36 alphanumeric/underscore/hyphen characters;
otherwise it is generated and persisted. A batch can partially succeed.

### Entry with attached take-profit/stop-loss

```json
{"action":"bracket","market":"perps","symbol":"BTC-USD",
 "entry":{"side":"buy","type":"limit","quantity":"0.01","price":"80000"},
 "take_profit":"88000","stop_loss":"76000"}
```

Provide at least one exit. The parent uses `BRACKET`; the opposite-side exits
use `ATTACHED_STOP`, `reduceOnly`, market IOC execution and `MARK_PRICE` triggers.
Exit quantities follow the parent. A custom parent ID must have at most 33
characters because `-tp`/`-sl` are appended. Native attached exits form OCO.
They activate after full parent fill, or partial fill followed by system
cancellation for insufficient margin. Manually canceling a partially filled
parent also cancels the attached exits: inspect the remaining position and
prepare separately authorized protection for it.

### Protect an existing position

```json
{"action":"tpsl","market":"perps","symbol":"BTC-USD","orders":[
  {"stop_type":"take_profit","stop_price":"88000"},
  {"stop_type":"stop_loss","stop_price":"76000","quantity":"0.01"}
]}
```

The CLI derives the closing side from the live position. Omitting quantity
means global TP/SL for the full position at trigger time. These are independent
reduce-only `STOP` orders, not a promised OCO pair. They use mark-price triggers
and market IOC execution. Trigger direction is checked against the current mark.

### Amend or cancel

```json
{"action":"amend","market":"perps","symbol":"BTC-USD","orders":[
  {"order_id":"12345678901234567890","price":"80500","quantity":"0.02"}
]}
```

Use `order_id` or `original_client_order_id`, exactly one. Amend at least one
of `price`, `quantity`, `stop_price`. Ordinary resting GTC/GTX limit orders use
the replacement endpoint with new generated client IDs; one TP/SL target uses
the modify endpoint and retains its identity. `stop_price` cannot turn an
ordinary order into a stop. Batch amendment is supported for ordinary orders;
modify TP/SL individually. Both Spot and Perps support ordinary amendments.

```json
{"action":"cancel","market":"perps","symbol":"BTC-USD","orders":[
  {"original_client_order_id":"saved-client-order-id"}
]}
```

Cancellation accepts 1–100 open targets; duplicate targets are rejected. An
order can fill between preflight and cancellation; inspect the item receipt
and reconcile. Do not assume a canceled order had no partial fills.

### Leverage, margin mode, and isolated margin

```json
{"action":"leverage","market":"perps","symbol":"BTC-USD","leverage":5,"margin_mode":"isolated"}
```

`margin_mode` is `cross` or `isolated`. The symbol's live maximum applies.
Changing mode requires no open orders or positions on that symbol. Keeping
the mode permits a leverage change subject to engine risk checks.

```json
{"action":"margin","market":"perps","symbol":"BTC-USD","amount":"10"}
```

Positive amount adds margin; negative removes it. This requires an isolated
position and settlement-coin precision. Cross margin uses account collateral;
this command is not an account transfer or a collateral conversion.

## Restart and timeout recovery

```bash
"$SODEX_PYTHON" "$SODEX_CLI" ops list
"$SODEX_PYTHON" "$SODEX_CLI" ops show OPERATION_ID
"$SODEX_PYTHON" "$SODEX_CLI" ops reconcile OPERATION_ID
```

The SQLite journal survives agent/runtime restarts and skill updates. It stores
intent before transmission, receipts when available, and subsequent read-only
observations. `submitting` after a crash and `unknown` after a timeout are
ambiguous. `reconcile` queries the saved network/owner/account, open orders,
order history, fills and account state, matching saved client/order IDs.
It does not need a private key and never sends a write or changes an ambiguous
attempt into an executable plan.

Recovery reads at most 500 open orders and 1,000 history records/fills since
one minute before the plan (or an older amendment/cancellation target's creation),
reports possible truncation, and keeps the original outcome separate
from observations. Missing records do not prove failure. For older/busy
accounts, use bounded `read history`/`read fills` windows to investigate further.
For leverage and margin adjustments there is no operation-specific receipt ID;
current state alone cannot prove that the previous write ran. Escalate unresolved
ambiguity instead of automatically adding/removing margin again.

Nonce values persist and a signer/network lock serializes CLI writers sharing
the state directory. Other state directories, hosts and SDK applications must
use separate registered signing keys or external coordination. Restoring an
old journal backup does not make an old write safe to repeat.

Sources: [REST Spot](https://sodex.com/documentation/for-developers/api-reference/trading-api/rest-v1/sodex-rest-spot-api),
[REST Perps](https://sodex.com/documentation/for-developers/api-reference/trading-api/rest-v1/sodex-rest-perps-api),
[Schema](https://sodex.com/documentation/for-developers/api-reference/trading-api/rest-v1/schema),
[TP/SL mechanics](https://sodex.com/documentation/trading-mechanics/take-profit-and-stop-loss-orders-tp-sl).
