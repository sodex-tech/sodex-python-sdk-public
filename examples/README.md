# SoDEX Python end-to-end examples

## Overview

These examples show the complete application-level lifecycle around the SDK,
not only individual REST calls:

| Flow | Runnable example | What it proves |
| --- | --- | --- |
| Deposit | [`funding.py`](./funding.py) | Discover custody/bridge routes, provision a custody address, and track a source-chain hash |
| Transfer | [`transfer_to_evm.py`](./transfer_to_evm.py) | Move funds across ValueChain EVM, Spot, and Perps and wait for the destination balance |
| Withdraw | [`evm_withdraw.py`](./evm_withdraw.py) | Build/sign a withdrawal permit, submit with sponsored or self-paid gas, and require a successful terminal status |
| API key | [`api_key.py`](./api_key.py) | List, register, or revoke a unified Spot/Perps signing key |
| Trade | [`trade.py`](./trade.py) | Read constraints and account state, then place a Spot or Perps order and return its order ID |
| Order/fill stream | [`account_websocket.py`](./account_websocket.py) | Correlate an order ID with asynchronous account order and fill pushes |
| Account state | [`account.py`](./account.py) | Query Spot/Perps balances, open orders, and positions |
| Public stream | [`websocket.py`](./websocket.py) | Subscribe to public trades and order-book updates with reconnect/resubscribe behavior |

All scripts use public `sodex` package APIs and preserve amounts with
`Decimal` and identifiers with Python `int`. They do not convert monetary
values or uint64 IDs through binary floating point.

## How the flows work

Deposits, internal transfers, withdrawals, and orders are asynchronous. A
successful HTTP response means the request was accepted; it does not by itself
mean the final balance movement or order fill has completed.

### Deposit

1. **Discover** — query the supported token/chain routes, external token
   address, decimals, minimum, and custody/bridge availability.
2. **Resolve a destination** — create a custody address only when none exists,
   or use the configured bridge contract for the bridge route.
3. **Submit on the source chain** — the integrating wallet sends the supported
   asset and saves the source-chain transaction hash.
4. **Wait for Gateway indexing** — query by chain plus source-chain hash until
   Gateway returns a deposit record.

### Transfer between ValueChain EVM, Spot, and Perps

1. **EVM -> Spot/Perps** — approve ERC-20 when required, call the four-argument
   `depositERC20`, and use destination `0` for Spot or `1` for Perps.
2. **Spot -> Perps** — submit `PERPS_WITHDRAW` and wait for the Perps credit.
3. **Perps -> Spot** — submit `SPOT_WITHDRAW` and wait for the Spot credit.
4. **Spot -> EVM** — submit `EVM_WITHDRAW` and wait for the ValueChain balance.

There is no direct Perps -> EVM route. Execute Perps -> Spot and Spot -> EVM as
two runs; each run waits for settlement before it exits.

### Withdraw

1. **Discover and validate** — resolve the token/chain route, selected custody
   or bridge method, minimum, and route availability.
2. **Prepare funds** — use the transfer example first if funds are in Spot or
   Perps.
3. **Authorize** — read the keyed ValueChain permit nonce, encode
   `WithdrawToken`, and sign the contract-provided digest.
4. **Submit** — choose Gateway-sponsored gas or submit `CallForPermit.execute`
   directly from the user's ValueChain wallet.
5. **Wait for completion** — query by transaction hash or withdrawal ID until
   every matching record is terminal, then fail the process unless all records
   are successful. The process can be stopped and resumed safely.

### Trade

1. **Resolve common state** — query the wallet registration, primary account
   ID, symbol constraints, balances/positions, and effective fee rate.
2. **Sign and submit** — use the master wallet or a registered API-key wallet.
3. **Correlate** — save `order_id` and `cl_ord_id`, then use the account
   WebSocket example to observe status transitions and fills.

### One-liners

- Deposit: discover route -> send externally -> wait for Gateway indexing.
- Transfer: snapshot destination -> submit one movement -> wait for balance change.
- Withdraw: move to EVM -> sign and submit -> wait for external settlement.
- Trade: inspect constraints -> place -> correlate order ID with WS updates.

## Shared setup

From this repository:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

From another project:

```bash
pip install sodex-python-sdk
```

Common environment variables:

```bash
export SODEX_NETWORK=testnet              # mainnet or testnet
export SODEX_PRIVATE_KEY=0x...            # omit for read-only calls
export SODEX_ACCOUNT_ADDRESS=0x...        # master wallet for read-only/API-key use
export SODEX_API_KEY_NAME=my-bot          # only for a registered API key
```

Never commit private keys. Start with read-only discovery, use testnet where
the endpoint exists, and use small amounts before enabling real mainnet writes.
Deposit-address creation is currently mainnet-only.

## Examples

### 1. Discover and track a deposit

**User flow:** discover route -> get/create custody address or select bridge ->
send with the source-chain wallet -> track the transaction hash.

```bash
export SODEX_ACCOUNT_ADDRESS=0x...
export SODEX_COIN=USDC
export SODEX_CHAIN=BASE_ETH
export SODEX_DEPOSIT_ROUTE=custody       # custody | bridge
python examples/funding.py
```

The script treats the two routes independently:

- custody availability follows `custodyDisabled == false`;
- bridge availability requires a non-empty `bridgeAddress`;
- custody address `Processing` is polled until it becomes usable or the
  configured timeout expires;
- a `Suspicious` address is rejected.

After the integrating wallet submits the external-chain transaction:

```bash
export SODEX_DEPOSIT_TX_HASH=0x...
export SODEX_WAIT_SECONDS=120
python examples/funding.py
```

The Python SDK does not guess a bridge function or a non-EVM wallet call that
Gateway does not publish. The script supplies the exact destination and route
metadata; the project signs/broadcasts through its chain wallet, then hands the
hash back to `get_deposit_status`.

**Success means:** the source-chain receipt proves that source transaction
succeeded; a non-empty Gateway result proves the deposit was indexed. Inspect
the returned record status before using the trading balance.

### 2. Transfer between EVM, Spot, and Perps

**User flow:** choose one of the five supported directions, submit, and wait for
the destination balance to change.

```bash
export SODEX_PRIVATE_KEY=0x...
export SODEX_COIN=USDC
export SODEX_AMOUNT=10

SODEX_TRANSFER_STEP=evm-to-spot python examples/transfer_to_evm.py
SODEX_TRANSFER_STEP=evm-to-perps python examples/transfer_to_evm.py
SODEX_TRANSFER_STEP=spot-to-perps python examples/transfer_to_evm.py
SODEX_TRANSFER_STEP=perps-to-spot python examples/transfer_to_evm.py
SODEX_TRANSFER_STEP=spot-to-evm python examples/transfer_to_evm.py
```

A registered API-key wallet can sign engine transfers when
`SODEX_ACCOUNT_ADDRESS` and `SODEX_API_KEY_NAME` identify the master account and
key. EVM-originating transfers require the master wallet key. `SODEX_COIN` is
the external asset symbol; the SDK resolves its engine mapping (`vUSDC`,
`WSOSO`, and so on) from asset config.

**Success means:** the ValueChain transaction succeeded or the engine accepted
the transfer, and the SDK subsequently observed the destination balance change.

### 3. Withdraw from ValueChain EVM

**User flow:** validate route -> sign permit -> submit -> poll by hash/ID.

```bash
export SODEX_PRIVATE_KEY=0x...
export SODEX_COIN=USDC
export SODEX_CHAIN=BASE_ETH
export SODEX_WITHDRAW_RECEIVER=0x...
export SODEX_WITHDRAW_AMOUNT=10
export SODEX_WITHDRAW_ROUTE=custody       # custody | bridge
export SODEX_WITHDRAW_GAS_MODE=sponsored  # sponsored | self-paid
export SODEX_WAIT_SECONDS=120
python examples/evm_withdraw.py
```

The funds must already be in the master wallet's ValueChain EVM balance. Run
the transfer example first when they are in Spot or Perps.

Resume without submitting another withdrawal:

```bash
export SODEX_CHAIN=BASE_ETH
export SODEX_WITHDRAW_TX_HASH=0x...
python examples/evm_withdraw.py
```

`SODEX_WITHDRAW_ID` may be used instead. Timeout does not cancel the
withdrawal; the script prints the exact reference needed to resume.

**Success means:** submission returns a ValueChain hash, while final completion
requires a terminal status such as `Success`/`Succeeded`, `Failed`, `Rejected`,
or `Cancelled`.

### 4. List, register, revoke, and use an API key

**User flow:** authenticate with master wallet -> generate separate key ->
register on Spot and Perps -> save the secret securely -> configure trading.

```bash
export SODEX_PRIVATE_KEY=0x...             # master wallet for register/revoke
export SODEX_TARGET_API_KEY_NAME=my-bot
SODEX_API_KEY_ACTION=list python examples/api_key.py
SODEX_API_KEY_ACTION=register python examples/api_key.py
SODEX_API_KEY_ACTION=revoke python examples/api_key.py
```

Replace the example's `save_to_secret_manager` stub before production. The
private key is deliberately never printed or persisted by the SDK. The example
omits `permissions`, which enables every permission; each bit supplied in a
permission mask disables the corresponding permission.

For later API-key-signed calls:

```bash
export SODEX_PRIVATE_KEY=0x...            # registered API-key private key
export SODEX_ACCOUNT_ADDRESS=0x...        # master wallet
export SODEX_API_KEY_NAME=my-bot
```

**Success means:** the aggregate Gateway operation completed for both engines;
the list action can be used to verify the resulting state.

### 5. Place a Spot or Perps order

**User flow:** inspect registration/account/symbol/fee state -> sign and place
-> save order ID -> observe order and fill events.

The trade example defaults to testnet for safety:

```bash
export SODEX_NETWORK=testnet
export SODEX_PRIVATE_KEY=0x...
export SODEX_MARKET=perps                 # spot | perps
export SODEX_SYMBOL=BTC-USD               # BTC/USDC for spot
export SODEX_ORDER_SIDE=BUY               # BUY | SELL
export SODEX_ORDER_TYPE=LIMIT             # LIMIT | MARKET
export SODEX_ORDER_PRICE=1000             # required for LIMIT
export SODEX_ORDER_QUANTITY=0.001
python examples/trade.py
```

Set `SODEX_CANCEL_AFTER_PLACE=true` to cancel an accepted limit order
immediately. Before submission, the script prints `tick_size`, `step_size`,
quantity/price/notional bounds, account state, and effective fees.

To watch one returned order ID:

```bash
export SODEX_ACCOUNT_ADDRESS=0x...
export SODEX_MARKET=perps
export SODEX_SYMBOL=BTC-USD
export SODEX_ORDER_ID=12345
python examples/account_websocket.py
```

Master-wallet and registered API-key signing use the same trade code; the
shared environment variables determine which signer is loaded.

**Success means:** REST returns a non-zero `order_id`. That is acceptance, not
a fill. Account order and trade pushes provide the asynchronous details.

## Read-only state examples

```bash
SODEX_ACCOUNT_ADDRESS=0x... python examples/account.py
python examples/websocket.py
```

`account.py` prints typed Spot/Perps balances, open orders, and positions.
`websocket.py` demonstrates public subscriptions, automatic reconnect, and
automatic resubscription. Both are safe starting points for a new integration.

## Coverage and known boundaries

| Requirement | Coverage |
| --- | --- |
| Supported deposit/withdraw tokens and chains | `funding.py`, `evm_withdraw.py` |
| Custody vs bridge routes | Discovered and validated independently |
| Query/create custody address | `funding.py` |
| Deposit status by source-chain hash | `funding.py` |
| EVM -> Spot/Perps and Spot/Perps internal transfers | `transfer_to_evm.py` |
| Perps -> Spot -> EVM before withdrawal | `transfer_to_evm.py` |
| Submit and resume withdrawal tracking | `evm_withdraw.py` |
| API-key list/register/revoke lifecycle | `api_key.py` |
| Master wallet or API-key trading | `api_key.py`, `trade.py` |
| Order ID plus WS order/fill details | `trade.py`, `account_websocket.py` |

Gateway transfer config currently does not publish expected confirmation
times, an explicit `bridgeDisabled` flag, or a universal bridge transaction
ABI. The examples report the available metadata and do not invent unsupported
timing or contract behavior.
