# SoDEX skill maintenance

The standalone `sodex/` folder is discoverable by `npx skills add`. Keep all
agent-facing scripts/references inside it so copying one skill is sufficient.
Runtime setup fetches the upstream SDK/examples at a reviewed commit; it does
not depend on files beside the installed skill.

The source pin is `SDK_REVISION` in `sodex/scripts/bootstrap.py`. When upgrading,
review the SDK examples and GitBook behavior together, then update the pin,
runtime paths and version references in the skill. Do not switch the bootstrap
to unreviewed `main` or assume the matching SDK version exists on PyPI.

## Validate a change

```bash
python -m pip install -e '.[dev]'
python -m pytest
npx skills add . --list
python skills/sodex/scripts/bootstrap.py --runtime-dir /tmp/sodex-skill-review
```

The last command installs from GitHub and queries public mainnet data; it does
not place orders. Repeat setup to check runtime reuse, then use its returned
Python path to run `sodex/scripts/doctor.py --network testnet`. Test an isolated
copy installation with `npx skills add /path/to/this/repo --skill sodex -a codex
--copy -y` from an empty temporary project, then run that installed copy's setup
from outside the repository. This verifies that no sibling SDK files are assumed.

## Behavioral review cases

| User request | Expected result |
| --- | --- |
| “Connect me to SoDEX in one step” | Bootstrap, real credential-free quote, returned runtime paths |
| “Show my positions” + owner address | Account reads; no secret or API-key registration required |
| “Show me a testnet order example” | Code with explicit network/parameters; no order submitted |
| “Submit this approved limit order” | Check constraints, use authorized parameters, report receipt separately from fill |
| “Attach take-profit and stop-loss to my entry” | A persisted native bracket plan; explain activation and partial-fill cancellation behavior |
| “Change this resting limit order” | Resolve the target and use replace; route a TP/SL target to modify |
| “Submit this batch” | Validate aggregate estimates and expose per-item success/failure |
| “Restart after a timed-out order” | Load the journal, reconcile saved IDs, never resubmit the same operation |
| “Change leverage / add isolated margin” | Preview current mode, symbol limits, position and margin conditions before execution |
| “Store this key for future sessions” | Hidden local import into an encrypted profile; no secrets in output/journal |
| “Deposit on testnet” | Explain missing Mirror API; no automatic mainnet fallback |
| “Register an API key for trading only” | Securely store key first; trade/cancel enabled and transfer/withdraw disabled; verify both engines |
| “My withdrawal timed out; try again” + hash | Resume status tracking; no duplicate withdrawal |
| “Resume trading after WebSocket reconnects” | Wait for acknowledgements and reconcile missed state/fills first |
| “Add a builder fee rate of 20” | Explain 2 bp; verify requested builder/cap on target engine |

The entrypoint maps all nine examples to references. Review those mappings when
adding or renaming examples. Protocol sources are linked at the point of use;
use the GitBook `llms.txt` index to detect renamed pages. The design reference is
[Longbridge Skills](https://github.com/longbridge/skills): portable installation,
natural-language invocation, progressively loaded references and a real first
query. No Longbridge runtime or account is required.

The CLI and its state/trading helpers are packaged inside the skill. Its SDK
pin includes additive symbol-filter and per-item receipt fields needed for
preflight and partial-result handling. The command reference defines the JSON
contract and scope. Signed tests use mocked HTTP with the real SDK signer;
release validation must not place live trades unless separately authorized.
