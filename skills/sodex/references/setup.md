# Setup and first successful query

## Install the skill

With Node.js/npm available, install into the chosen agent:

```bash
npx skills add sodex-tech/sodex-python-sdk-public --skill sodex -g -a codex -y
# Claude Code instead:
npx skills add sodex-tech/sodex-python-sdk-public --skill sodex -g -a claude-code -y
```

Omit `-g` for a project-local install. Restart the agent session if the newly
installed skill is not discovered. For other agents, omit `-a` and choose in
the installer. Node is only needed for this installer, not the SDK runtime.

Then ask: **“Use $sodex to connect and verify a live BTC perpetual quote.”**
The agent runs setup and the public check; no wallet authorization is needed.

## Runtime

Run the bundled `scripts/bootstrap.py` from the installed skill using Python
3.9+ and Git. It clones the pinned public source into
`~/.cache/sodex-skill/ce188ffa5971/sdk`, creates a sibling `.venv`, installs
the SDK, and performs a mainnet public read. Dependencies install from the
configured Python package index; this is a pinned SDK, not a complete dependency
lock. A repeat run reuses the runtime and repeats the read.

Use `--runtime-dir /absolute/path` for a different location or `--network testnet`
for a testnet quote. `--skip-check` installs without contacting Gateway and must
not be reported as verified connectivity. The script never edits agent
configuration, imports an existing wallet, or runs signed examples.

Use the returned paths, including when the skill was installed by itself:

```bash
export SODEX_PYTHON='/absolute/path/from/bootstrap/python'
export SODEX_SDK='/absolute/path/from/bootstrap/sdk'
export SODEX_CLI='/absolute/path/from/bootstrap/cli'
"$SODEX_PYTHON" <skill-dir>/scripts/doctor.py --network mainnet --market spot --symbol vBTC_vUSDC
"$SODEX_PYTHON" "$SODEX_CLI" read quote --network testnet --symbol BTC-USD
```

On Windows, use `python` to start bootstrap and invoke the returned
`Scripts/python.exe` path with PowerShell's `&` operator. References below use
POSIX shell syntax; translate environment assignments for the user's shell.
The unified CLI uses POSIX file locks and requires macOS/Linux or WSL;
bootstrap, doctor and the SDK itself also support native Windows.
For Spot, supply the exact symbol returned by `Client.spot_symbols()` on that
network. `vBTC_vUSDC` was observed on mainnet at review time; the upstream
examples' `BTC/USDC` label is not a portable Gateway symbol.

## Credentials when the user's next task requires them

For CLI trading, use [local encrypted profiles](commands.md#local-profiles).
Profiles and the operation journal survive runtime upgrades and live outside
the cache. The environment configuration below applies to SDK examples and
application integrations.

| Task | Configuration |
| --- | --- |
| Public market data | Explicit network only; no key |
| Account reads | `SODEX_ACCOUNT_ADDRESS` = account owner's wallet |
| Routine trading with a registered key | `SODEX_PRIVATE_KEY` = locally supplied API-key secret; `SODEX_API_KEY_NAME` = registered name; `SODEX_ACCOUNT_ADDRESS` = owner |
| Master-only actions | Master key supplied locally; unset `SODEX_API_KEY_NAME`; owner must match signer |

Do not print environment dumps. The SDK does not automatically load `.env`
files. Load secrets using the user's existing local secret mechanism. For key
creation and restrictions, read [credentials](credentials-builders.md).

| Network | Gateway | Chain ID |
| --- | --- | --- |
| mainnet | `https://mainnet-gw.sodex.dev` | `286623` |
| testnet | `https://testnet-gw.sodex.dev` | `138565` |

Use the Gateway root as `Config.base_url`; SDK methods append `/api/v1/...`.
Mainnet ValueChain RPC is `https://mainnet.valuechain.xyz/`. The SDK deliberately
has no default testnet RPC. Mirror asset configuration, custody and withdrawal
APIs are mainnet-only; setting a testnet RPC does not add a testnet Mirror API.

## Diagnose without changing account state

| Symptom | Next action |
| --- | --- |
| Python/Git unavailable | Install Python 3.9+ with venv/pip and Git, then rerun |
| venv creation fails on Linux | Install the matching distribution `python3-venv` package |
| Clone/package installation interrupted | Rerun; if checkout is incomplete or modified, preserve it and use a new runtime directory |
| Import failure | Use the exact bootstrap `python` path, not the system interpreter |
| SDK methods missing | Check installed version/revision; use this pinned runtime |
| Doctor returns `status: error` | Read its error type; verify network/symbol, DNS/TLS/proxy and Gateway reachability; retry the read |
| `UserNotFound` | Follow [onboarding](https://sodex.com/documentation/user-guide/onboarding-guidance); do not retry orders |
| Signature/nonce error | Check network, key registration/name/owner, expiry, clock and competing signers |
| HTTP 429 | Bound read retries with backoff; budget all applicable rate-limit dimensions |

Source: [SDK guide](https://sodex.com/documentation/for-developers/sdks/python-sdk-guide),
[rate limits](https://sodex.com/documentation/for-developers/developers/trading/api-rate-limits).
The REST weight budget is 1200/minute per IP; order and WebSocket limits are
separate. Consult the endpoint-weight table before building polling loops.

Update the skill with `npx skills update sodex -g`. Updating skill instructions
does not upgrade the pinned runtime until a reviewed skill revision changes the
pin. Uninstall with `npx skills remove sodex -g -y`; the runtime cache is separate
and may be deleted once the user no longer needs it. Never store secrets in it.
