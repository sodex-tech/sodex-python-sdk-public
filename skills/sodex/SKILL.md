---
name: sodex
description: Connect to SoDEX with a JSON CLI for market/account queries, preflighted Spot and Perps orders, TP/SL, amendments, batches, leverage and margin, encrypted local profiles, and operation recovery. Also use for SoDEX Python SDK integrations, funding, API keys, builder fees, and WebSocket workflows.
---

# SoDEX

Use the official SDK and the user's language. Resolve paths relative to this
installed skill directory; never assume the SDK repository is the current
directory. This skill includes its setup scripts and references; setup fetches
the reviewed SDK and all nine upstream examples.

## First connection

For a new integration, run with Python 3.9+ and Git:

```bash
python3 <skill-dir>/scripts/bootstrap.py --network mainnet
```

This creates an isolated runtime, installs SDK revision
`ce188ffa5971048489441de5c2f21020208f54d4` (`0.2.1` plus trading metadata fixes),
and checks a public BTC quote. It needs no wallet and ignores signing credentials during the check.
Read the returned JSON: use `python` for every command, `cli` for the bundled
command interface, and `sdk` to locate examples. Show the actual network,
symbol, price, source and observation time. A failed check is a failed connection; do not invent a quote or switch
networks silently. For an existing runtime, rerun `scripts/doctor.py` with its
Python executable. See [setup](references/setup.md) for installation and errors.

## Choose the workflow

For supported account and trading operations, use the bundled CLI instead of
writing temporary Python. Read [commands](references/commands.md) for the JSON
intent schema, profiles, preflight, execution and recovery. A `plan` is unsigned;
`execute` uses the saved operation ID, reruns checks, and submits at most once.
Do not interpret `accepted` as filled. After an ambiguous attempt, use
`ops reconcile`; never create a replacement plan simply to retry it.

Read only the reference needed for the request. Upstream example paths are
relative to the **SDK checkout returned by setup**. The bundled CLI is inside
the installed skill; use the `cli` path returned by setup.

| Request | Reference | Examples or helper |
| --- | --- | --- |
| Install, connect, diagnose | [Setup](references/setup.md) | All examples share this runtime |
| JSON commands, local profiles, TP/SL, amend, batch, leverage/margin, restart recovery | [Commands](references/commands.md) | Bundled `scripts/sodex_cli.py` |
| Price, order book, candles, balances, positions, history | [Market and account](references/market-account.md) | `examples/account.py` |
| Spot/Perps order, cancellation, builder attribution | [Trading](references/trading.md) | `examples/trade.py` |
| API-key lifecycle, builder-fee approval | [Credentials and builders](references/credentials-builders.md) | `examples/api_key.py`, `examples/approve_builder_fee.py` |
| Deposit, transfer, withdraw or resume tracking | [Funding](references/funding.md) | `examples/funding.py`, `examples/transfer_to_evm.py`, `examples/evm_withdraw.py` |
| Public stream, order updates and fills | [WebSocket](references/websocket.md) | `examples/websocket.py`, `examples/account_websocket.py` |

## Execution rules

- Start onboarding with public reads. Account reads need the owner's address
  or target account ID, not an API-key address. Ask users to configure secrets
  locally or through their secret manager; never ask them to paste keys into
  chat, code, commands recorded in history, or logs.
- Before a write, establish the user's authorized network, account, operation
  and material parameters. Present the concrete action and obtain any missing
  authorization; honor authorization already given for that exact action.
  Installing or asking for sample code does not authorize orders, approvals,
  key registration, custody-address creation, transfers or withdrawals.
- Use a dedicated registered key for routine trading. Master-only operations
  and the permission-mask convention are in the credentials reference. Do not
  run the upstream API-key registration stub unchanged.
- Set `SODEX_NETWORK` explicitly before SDK workflows: `Client.from_env()`
  otherwise defaults to mainnet. Testnet has trading endpoints, but no Mirror
  API. Never use mainnet as an automatic fallback for a testnet funding request.
- Use `Decimal("...")` for amounts and prices and Python `int` for IDs/nonces.
  Resolve symbols and route metadata from the selected network; do not hardcode
  IDs, decimals, minimums, fees or available chains from examples.
- Delegate signing and canonical serialization to SDK methods. The CLI
  persists nonces and serializes signing processes sharing one state directory.
  Other SDK applications, hosts or state directories still need separate keys
  or external coordination.
- HTTP acceptance is not completion. Save IDs/hashes, reconcile REST history
  and balances after a timeout or disconnect, and resume observation before
  considering a resubmission. Do not automatically retry signed writes.

## Sources and maintenance

SDK method signatures and executable examples are pinned to the revision above.
The references incorporate the [GitBook Python SDK guide](https://sodex.com/documentation/for-developers/sdks/python-sdk-guide)
and link the relevant protocol pages, checked on 2026-09-28. For a field or
capability not covered here, consult the [documentation index](https://sodex.com/documentation/llms.txt)
and the pinned SDK source. GitBook pages are also readable with a `.md` suffix.
Surface any newer-docs/pinned-SDK mismatch instead of inventing an API.
