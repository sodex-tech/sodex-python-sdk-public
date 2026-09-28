# Deposits, transfers and withdrawals

These recipes derive from `examples/funding.py`, `examples/transfer_to_evm.py`
and `examples/evm_withdraw.py`. Use the pinned runtime from [setup](setup.md).
Mirror APIs are mainnet-only. Stop and explain that boundary for a testnet
funding request; do not switch to mainnet on the user's behalf.

## Discover first (no writes)

```python
from sodex.client import Client, Config

client = Client(Config(base_url=Client.DEFAULT_BASE_URL, chain_id=Client.DEFAULT_CHAIN_ID))
asset, chain = client.get_transfer_route("USDC", "BASE_ETH")
print(asset.coin, asset.asset_name, asset.asset_id, asset.token_address, asset.decimals)
for name, method in (("custody", chain.custody), ("bridge", chain.bridge)):
    if method is not None:
        print(name, method.allow_deposit, method.allow_withdraw,
              method.min_deposit_amount, method.min_withdraw_amount, method.withdraw_fee)
```

Use the user's asset/network and live config, not these illustrative values.
`asset.coin` is the canonical external asset; `asset.asset_name` is the engine
coin. Engine ID `0` is valid; null engine metadata means an asset cannot enter
the trading engines. Use configured ValueChain token address/decimals, keeping
native SOSO distinct from WSOSO.

Deposit availability and withdrawal availability are independent.
`chain.custody_available` / `bridge_available` describe deposits. For a
withdrawal use `chain.withdrawal_method("custody")` or `"bridge"`, which
validates that route's withdrawal availability. Apply its own minimum/fee.
An empty fee is unknown; only an explicit `"0"` is a zero fee.

## Deposit

1. Resolve asset, exact chain name, route, minimum and destination from current
   config. For custody, amount arriving after source fees must meet the minimum.
2. After authorization to provision a custody address, run `funding.py` with
   owner, coin, chain, and `SODEX_DEPOSIT_ROUTE=custody`. The example can create
   an address; it is not a pure discovery command. It waits through Processing
   and rejects Suspicious status. Require Enabled before sending.
3. Verify the assigned address against `getDepositWalletList` on the documented
   custody contract, following [Generate Custody Address](https://sodex.com/documentation/for-developers/developers/mirror-protocol/generate-address).
   The SDK example does not perform this verification. Preserve case and chain;
   do not reuse an address across routes or invent a missing memo/tag.
4. The source-chain wallet sends the authorized asset/amount. For a bridge
   route, the example only discovers metadata/contract; it does not construct
   the external-chain call. Read that chain's published bridge ABI before
   generating code; do not guess it or send tokens directly to the contract.
5. Save the source-chain hash and track it with the same chain:

```bash
# Owner, coin and chain are configured locally for the selected route.
SODEX_NETWORK=mainnet SODEX_DEPOSIT_ROUTE=custody \
  "$SODEX_PYTHON" "$SODEX_SDK/examples/funding.py"
# After the source wallet sends, set SODEX_DEPOSIT_TX_HASH to its hash.
SODEX_NETWORK=mainnet SODEX_WAIT_SECONDS=120 \
  "$SODEX_PYTHON" "$SODEX_SDK/examples/funding.py"
```

With a hash configured the script tracks only. `wait_for_deposit()` returns
when indexed; inspect every record's status/failure and the destination
balance. Custody deposits credit **Spot directly**; do not call
`deposit_evm_to_engine()` for that same deposit. Bridge destination depends on
the authorized call; `toClob=true` also already credits Spot. An empty result or
timeout is not success and is not a reason to send funds again.

## Move existing funds between EVM, Spot and Perps

Choose exactly one direction per authorized step. `SODEX_COIN` is the external
asset name; the example resolves its engine mapping from asset config.

| `SODEX_TRANSFER_STEP` | Signer | Wait for |
| --- | --- | --- |
| `evm-to-spot` | Master wallet, with native SOSO for gas | Successful EVM transaction, then Spot balance |
| `evm-to-perps` | Master wallet, with native SOSO for gas | Successful EVM transaction, then Perps balance |
| `spot-to-perps` | Master or permitted registered key | Perps credit |
| `perps-to-spot` | Master or permitted registered key | Spot credit |
| `spot-to-evm` | Master or permitted registered key | Owner's ValueChain balance increase |

```bash
# Network, signer, SODEX_COIN, SODEX_AMOUNT and SODEX_TRANSFER_STEP are
# explicitly configured for one authorized movement.
"$SODEX_PYTHON" "$SODEX_SDK/examples/transfer_to_evm.py"
```

There is no direct Perps → EVM route. Use Perps → Spot → EVM sequentially,
confirming each credit before the next step. `deposit_evm_to_engine()` handles
ERC-20 approval (or native value) and EVM execution; engine settlement is a
separate wait. Its four-argument deposit uses destination 0 for Spot, 1 for
Perps. New accounts have activation requirements/fees; consult
[ValueChain transfers](https://sodex.com/documentation/for-developers/developers/mirror-protocol/lifecycle/valuechain-transfers)
before calculating the spendable destination amount.

The example's balance-change wait is an observation, not proof that this exact
transfer settled when other activity shares the account. In production,
persist receipts/hashes immediately on submission (before waiting), reconcile
the corresponding state, and validate the expected credit. The example prints
its transfer receipt only after waiting; do not blindly rerun it after timeout.
Copy/adapt it in the user's project if durable recovery is needed; keep the
cached reviewed SDK unchanged.

## Withdraw to an external chain

Resolve network, owner, asset, chain, route, amount, receiver and fee before
authorization. Funds must already be in the master wallet's **ValueChain EVM**
balance. Preserve case and destination tags, e.g. `address:00123`; do not parse
the tag as an integer or put it in the SDK's separate route `memo` argument.

With master credentials locally configured and the transfer authorized:

```bash
# Set SODEX_COIN, SODEX_CHAIN, SODEX_WITHDRAW_RECEIVER,
# SODEX_WITHDRAW_AMOUNT to the reviewed request.
SODEX_NETWORK=mainnet SODEX_WITHDRAW_ROUTE=custody \
  SODEX_WITHDRAW_GAS_MODE=sponsored SODEX_WAIT_SECONDS=120 \
  "$SODEX_PYTHON" "$SODEX_SDK/examples/evm_withdraw.py"
```

Choose `bridge` only if that route allows withdrawals. `sponsored` submits the
permit through Gateway; `self-paid` submits on ValueChain using the master
wallet's gas. `prepare_evm_withdraw()` reads the contract nonce/digest and signs
it; never replace this with personal signing or a Trading API signature prefix.
Do not print or persist the signed permit in chat/logs.

Persist the returned **ValueChain request hash** before waiting. Resume by
setting `SODEX_WITHDRAW_TX_HASH` or, once available, `SODEX_WITHDRAW_ID`:

```bash
SODEX_NETWORK=mainnet SODEX_WAIT_SECONDS=120 \
  "$SODEX_PYTHON" "$SODEX_SDK/examples/evm_withdraw.py"
```

When either reference is present, this command only tracks; it does not submit
another withdrawal. Prefer the withdrawal ID once known. The status hash is
not the final external-chain hash. Check all matching terminal records:
`wait_for_withdrawal()` also returns failures, while the example's
`require_success()` rejects them. Timeouts print pending and can exit zero, so
an example's process exit code alone is not proof of settlement. Verify final
status, destination and amount before reporting completion. If submission
itself timed out, reconcile history before attempting another signed permit.

Sources: [custody deposits](https://sodex.com/documentation/for-developers/developers/mirror-protocol/lifecycle/custody-deposits),
[bridge deposits](https://sodex.com/documentation/for-developers/developers/mirror-protocol/lifecycle/deposits),
[withdrawals](https://sodex.com/documentation/for-developers/developers/mirror-protocol/lifecycle/withdrawals),
[history and status](https://sodex.com/documentation/for-developers/api-reference/mirror-api/history-and-status).
