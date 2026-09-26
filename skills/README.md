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
| “帮我一键接入 SoDEX” | Bootstrap, real credential-free quote, returned runtime paths |
| “看我的持仓” + owner address | Account reads; no secret or API-key registration required |
| “给我测试网下单示例” | Code with explicit network/parameters; no order submitted |
| “执行这笔已确认的限价单” | Check constraints, use authorized parameters, report receipt separately from fill |
| “在测试网充值” | Explain missing Mirror API; no automatic mainnet fallback |
| “注册只能交易的 API key” | Securely store key first; trade/cancel enabled and transfer/withdraw disabled; verify both engines |
| “提现超时了，再试一次” + hash | Resume status tracking; no duplicate withdrawal |
| “WebSocket 重连后继续交易” | Wait for acknowledgements and reconcile missed state/fills first |
| “加上 builder 费率 20” | Explain 2 bp; verify requested builder/cap on target engine |

The entrypoint maps all nine examples to references. Review those mappings when
adding or renaming examples. Protocol sources are linked at the point of use;
use the GitBook `llms.txt` index to detect renamed pages. The design reference is
[Longbridge Skills](https://github.com/longbridge/skills): portable installation,
natural-language invocation, progressively loaded references and a real first
query. No Longbridge runtime or account is required.
