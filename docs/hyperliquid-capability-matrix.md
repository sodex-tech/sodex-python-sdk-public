# Sodex Python SDK × Hyperliquid Python SDK 能力对齐矩阵

> 审核日期：2026-07-31
> Hyperliquid 基线：官方 `hyperliquid-python-sdk` `0.24.0`，`2fdb18f`  
> Sodex 接口基线：`sodex-gateway/main@6258965`（Gateway `v1.6.15`）
> Sodex Python 基线：本分支 `feat/python-sdk-user-flows`

## 1. 严格对齐口径

这里的“对齐”不是方法名相似，而是用户能否完成同一种动作，并获得足够的结果继续下一步。状态只有四种：

- **已对齐**：Gateway 有等价语义，Python SDK 已提供 typed API/签名/返回值，并有离线 contract test。
- **部分对齐**：主流程可完成，但返回信息、筛选粒度或实时事件不如 Hyperliquid 完整。
- **后端阻塞**：属于通用交易所用户能力，但当前 Gateway 没有对应 endpoint、字段或必要 ABI；不能在 SDK 中伪造。
- **协议不适用**：Hyperliquid L1 的部署者、验证者、vault、staking、multisig 或 block-production 动作，在 Sodex 当前产品/协议中没有同一对象。这类不计为 Python wrapper 漏实现，但如果产品决定引入，需要单独立项。

因此，严格结论是：**当前 Gateway 能表达的 Hyperliquid 等价能力，本分支要求全部进入 Python SDK；全量 Hyperliquid 产品能力仍不能宣称 100% 对齐，后端阻塞项必须先补 Gateway。**

## 2. Info / 查询能力

| Hyperliquid `Info` 能力 | Sodex Python API | 状态 | 差异/验收说明 |
| --- | --- | --- | --- |
| `user_state` | `perps_account_state`、`perps_balances`、`perps_positions` | 部分对齐 | 当前 Gateway position 没有 mark price、unrealized PnL、liquidation price、position value、ROE 等 Hyperliquid 字段。SDK 已按真实 V1 `positions` envelope 解码，不再误读 `orders`。 |
| `spot_user_state` | `spot_account_state`、`spot_balances` | 已对齐 | 保留完整 raw snapshot，同时提供 typed balances。 |
| `open_orders` / `frontend_open_orders` | `spot_orders`、`perps_orders` | 已对齐 | 支持 account/symbol 筛选；响应含 order ID、client order ID、状态和成交量。 |
| `all_mids` | `all_mids(market=...)` | 已对齐 | 使用 Gateway best bid/ask，以 `Decimal` 计算，不经过 float。 |
| `user_fills` / `user_fills_by_time` | `spot_user_trades`、`perps_user_trades` + `HistoryFilter` | 部分对齐 | 支持 account、symbol、start/end、limit、builder fee；Gateway 不返回 closed PnL、start position、tx hash/crossed，也不支持 `aggregateByTime`。 |
| `meta` / `spot_meta` | `perps_symbols`、`perps_coins`、`spot_symbols`、`spot_coins` | 已对齐 | Sodex 返回独立 typed 列表，不复刻 Hyperliquid 的单个组合对象。 |
| `meta_and_asset_ctxs` / `spot_meta_and_asset_ctxs` | symbols/coins + tickers/book tickers/mark prices | 已对齐 | 数据可完整取得，但需要两次请求；没有 Gateway 原子组合 endpoint。 |
| `perp_dexs` | 无 | 后端阻塞 | Sodex Gateway 当前只有一个 perps engine，没有多 perpetual DEX discovery。 |
| `funding_history`（市场公共历史） | 无 | 后端阻塞 | Gateway 只有用户 funding history，没有 symbol 级公共 funding history route。 |
| `user_funding_history` | `perps_funding_history` | 已对齐 | 支持 account/symbol/time/limit。 |
| `l2_snapshot` | `spot_order_book`、`perps_order_book` | 已对齐 | 已修复实际参数为 `limit`；`depth` 仅作为兼容的 Python 参数名。 |
| `candles_snapshot` | `spot_klines`、`perps_klines` | 已对齐 | 支持 interval/time/limit。 |
| `user_fees` | `get_fee_rate` | 已对齐 | 返回 maker/taker/discount 和 tier；支持 spot/perps/symbol。 |
| `query_order_by_oid` / `query_order_by_cloid` | 无可靠等价接口 | 后端阻塞 | Gateway 可列 open/history orders，但没有按 OID/CLOID 查询单个订单、覆盖全部状态的 route。 |
| `query_sub_accounts` | `get_subaccounts`、`primary_account_id` | 已对齐 | 新用户无需手写 account ID。 |
| `historical_orders` | `spot_orders_history`、`perps_orders_history` | 已对齐 | 支持 account/symbol/time/limit。 |
| `user_non_funding_ledger_updates` | `get_deposit_withdrawals` + transfer receipt | 部分对齐 | 外部 deposit/withdraw 有历史；内部 transfer 没有统一 ledger history endpoint。 |
| `user_twap_slice_fills` | 无按 TWAP order ID 的 fill 查询 | 后端阻塞 | 用户 trades endpoint 不接受 order ID/TWAP ID 筛选。 |
| `portfolio` | 无 | 后端阻塞 | 没有统一账户时间序列/PnL portfolio endpoint。 |
| `user_rate_limit` | `get_transaction_quota` | 已对齐 | 返回交易/撤单累计值、已用和剩余额度。 |
| `extra_agents` | `get_api_keys` | 已对齐 | 聚合返回 Spot/Perps API keys，不合并丢失 engine 差异。 |
| 用户是否已注册 | `get_user_status` | 已对齐 | 对齐 Gateway `v1.6.15`，区分 `Active` / `UserNotFound` 并保留完整 uint64 user ID。 |
| referral 查询 | 无 | 后端阻塞 | Gateway 无 referral state。 |
| staking 查询 | 无 | 后端阻塞 | 费率响应有 staking tier，但 Gateway 无用户 staking summary/delegation/reward/history API。 |
| `user_role` | 无 | 后端阻塞 | Gateway 无统一 user-role endpoint。 |
| vault equities | 无 | 协议不适用 | Sodex 当前没有 Hyperliquid vault 对象。 |
| multisig / abstraction / deploy auction 状态 | 无 | 协议不适用 | 属于 Hyperliquid L1 账户抽象和部署平面。 |

## 3. Exchange / 写操作能力

| Hyperliquid `Exchange` 能力 | Sodex Python API | 状态 | 差异/验收说明 |
| --- | --- | --- | --- |
| `order` / `bulk_orders` | `perps_order` / `spot_order` + `place_perps_order` / `place_spot_orders` | 已对齐 | 高层 API 自动解析 primary account 和 symbol ID；低层 API 保留 batch；返回 typed `order_id`。 |
| builder-attributed order | `BuilderParams` + order helpers | 已对齐 | builder 进入 HTTP body 和签名 payload；不能只做本地 metadata。 |
| `market_open` / `market_close` | 同名方法 | 已对齐 | `market_close` 自动读取持仓方向/数量，发送 opposite-side reduce-only market order；支持 builder。 |
| cancel by order ID / client ID / bulk cancel | `cancel_perps_order`、`cancel_spot_order` + batch request | 已对齐 | Spot 和 Perps wire shape 分开处理。 |
| `modify_order` | `modify_perps_order` | 已对齐 | 单笔改单等价。 |
| `bulk_modify_orders_new` | 无 | 后端阻塞 | Gateway modify route/request 当前只表达单笔修改；replace batch 不是同一语义。 |
| `schedule_cancel` | `schedule_spot_cancel`、`schedule_perps_cancel` | 已对齐 | 支持设置/取消 dead-man switch。 |
| `update_leverage` | `update_leverage` | 已对齐 | typed EIP-712 request。 |
| `update_isolated_margin` | `update_margin` | 已对齐 | Sodex request 使用 account/symbol ID 和 Decimal amount。 |
| collateral update | `update_collateral` | 已对齐 | Sodex 额外能力；Gateway 标注 testnet only。 |
| TWAP place/cancel/query | `place_*_twap`、`cancel_*_twap`、`*_twap_orders` | 已对齐 | Spot/Perps 都覆盖；签名字段顺序与 Go request 一致。 |
| `usd_class_transfer` | `transfer_spot_to_perps`、`transfer_perps_to_spot` | 已对齐 | 自动解析账户/coin ID，并使用 Gateway treasury routing。 |
| `sub_account_transfer` / `sub_account_spot_transfer` | `transfer_perps_subaccount`、`transfer_spot_subaccount` | 已对齐 | 支持 child account ID 或 EVM address，使用 Gateway `SUBACCOUNT_TRANSFER=7`。 |
| `create_sub_account` | 无 | 后端阻塞 | Gateway 只有 subaccount 查询，没有创建 route。 |
| `send_asset` / `usd_transfer` / `spot_transfer` 到任意钱包 | 无 | 后端阻塞 | Gateway transfer 目标是 account ID 且受类型校验，不能等价为任意 EVM 地址转账。 |
| `withdraw_from_bridge` | `prepare_evm_withdraw` + `submit_evm_withdraw` | 已对齐 | Sodex 同时支持 custody/bridge withdrawal；自动完成 ValueChain nonce、permit hash、ABI 和签名。 |
| `approve_agent` | `approve_agent` | 已对齐 | 本地生成 agent key、聚合注册、返回已配置 trading Client。 |
| `approve_builder_fee` | 同名方法 | 已对齐 | universal EIP-712 签名并同步 Spot/Perps。 |
| referral 写操作 | 无 | 后端阻塞 | Gateway 无 set-referrer。 |
| vault transfer | 无 | 协议不适用 | Sodex 当前无 vault 产品对象。 |
| validator delegation | 无 | 协议不适用 | Hyperliquid staking/validator 协议动作。 |
| multisig conversion/action | 无 | 协议不适用 | Sodex 直接使用 EVM/master/API-key 签名模型。 |
| spot/perp deployer、validator、C-signer actions | 无 | 协议不适用 | 属于 Hyperliquid L1 运维/资产部署能力，不是 Sodex Gateway 用户交易 API。 |
| big blocks、priority bid、noop | 无 | 协议不适用 | 属于 Hyperliquid block-production/action plumbing。 |
| agent/user DEX abstraction | 无 | 协议不适用 | Sodex 当前不存在 DEX abstraction 状态机。 |
| action `expires_after` | 无直接等价字段 | 协议不适用 | Sodex 使用严格时间 nonce window；不能把两种签名过期语义视为同一字段。 |

## 4. WebSocket 能力

| 能力 | Sodex Python API | 状态 | 说明 |
| --- | --- | --- | --- |
| 公共 ticker、mini ticker、best book、trade、L2/L4、candle、mark price | `ws.Client.subscribe(SubscribeParams(...))` | 已对齐 | 支持断线重连和自动重新订阅。 |
| 账户 snapshot、订单更新、fills | `subscribe_account` | 已对齐 | order/fill 回调直接得到 `AccountOrderUpdate` / `AccountTrade`；group `close()` 一次退订。 |
| REST order ID 与 WS order/fill 关联 | typed receipt + typed WS events | 已对齐 | 三处都有 `order_id` / `cl_ord_id`。 |
| 用户 funding / non-funding ledger 专用 stream | 无专用 typed helper | 部分对齐 | 可使用 raw `accountEvent`，但 Gateway 没有与 Hyperliquid 全部 subscription 等价的稳定 typed schema。 |

## 5. Kadima User Flows 覆盖

### 链外充值

1. `get_transfer_configs()` / `get_transfer_route()` 查询支持的 token 和 chain。
2. `custody_available` 与 `bridge_available` 分别判断托管和桥，绝不混用。
3. 托管路线调用 `ensure_deposit_address()`：先查，空地址才通过最新 chain-only v1 API 创建；也可以调用 `create_deposit_addresses()` 批量创建。
4. 用户向返回地址转 token。
5. `get_deposit_status(chain, tx_hash)` 按源链 tx hash 轮询到账状态。

**唯一未闭环项：** Lark ABI 页没有外部源链 bridge deposit 函数 ABI。SDK 可发现 `bridge_address`，但不能可靠构造 calldata；这是 ABI/桥合约文档阻塞，不应猜测。

### 提现

1. `get_transfer_route()` 查询路线、fee 和 minimum。
2. Perps 资金调用 `transfer_perps_to_spot()`，Spot 资金调用 `transfer_spot_to_evm()`。
3. `prepare_evm_withdraw()` 构造 custody 或 bridge 的 `WithdrawToken` permit。
4. `submit_evm_withdraw()` 返回 ValueChain transaction hash。
5. `get_withdraw_status(chain, tx_hash=...)` 查询外部完成进度。

### 交易

1. 主钱包：`Client.from_env()` / `Client.from_private_key()`。
2. API key：主钱包调用 `approve_agent()`，直接得到 ready-to-trade Client。
3. `perps_order()` / `spot_order()` 自动解析 account/symbol，签名并发送。
4. 返回 `PlaceOrderResult.order_id`。
5. `subscribe_account()` 接收 typed order update 和 fill。

## 6. 发布门槛

本分支在合并前必须同时满足：

1. 全部离线单元/contract/user-flow 测试通过，且每个新增测试有路径说明注释。
2. examples 全部可编译；新用户不需要手写 account ID、symbol ID、coin ID 或 treasury ID。
3. wheel 能构建，并能在全新虚拟环境安装和 import。
4. 主网只读 REST 冒烟覆盖资产配置、Spot/Perps meta/ticker/book、position envelope、fee/quota/subaccounts。
5. 主网 WebSocket 至少收到一个公共 push，并验证连接/订阅/关闭。
6. 不执行真实下单、transfer、API-key 注册、充值地址创建或提现等有资金/账户副作用的 live 测试。
7. 所有“后端阻塞”和“协议不适用”项保留在本矩阵，不能写成已对齐。

## 7. 本分支验收记录

| 层级 | 结果 | 覆盖 |
| --- | --- | --- |
| 离线 test suite | **97 passed** | 签名、REST envelope、错误、资金、API key、Spot/Perps order、builder、TWAP、collateral、subaccount transfer、typed WS、user flows。 |
| 静态可用性 | **通过** | 所有 SDK/examples `py_compile`；Ruff `F` 类检查无未使用/未定义符号。 |
| 主网只读 REST | **通过** | 38 个 asset configs、34 个 Spot symbols、87 个 Perps symbols；Spot/Perps depth=5 均返回 5×5；87 个 mark prices；26/81 个有效 Spot/Perps mids；账户/fee/quota/builders/API keys 均成功解码。 |
| 主网公共 WS | **通过** | `ticker`、`l2Book`、`coinPrice` 均收到并解析 snapshot；验证单数 `symbol` ergonomics 会转成 Gateway 要求的 `symbols` wire 字段，正常 close 不产生伪 read error。 |
| 主网账户 WS | **通过** | `subscribe_account()` 收到 `accountState` snapshot，三个 subscription 均无 server error 并可 group close。 |
| ValueChain withdrawal prepare | **通过，未提交** | 真实主网读取 nonce/permit digest；生成 480-byte `cmdData` 和 65-byte signature；没有调用 Gateway submit。 |
| wheel 构建/隔离安装 | **通过** | `sodex_python_sdk-0.2.0-py3-none-any.whl` 在全新 Python 3.9 venv 安装，并从 repo 外 import/构造 Client。 |

Gateway `v1.6.15` 对齐新增离线 contract tests 覆盖：

- chain-only v1、批量 v1 和 partner-quota v2 充值地址创建。
- `get_user_status()`、server time、system status 与上游业务错误原文。
- announcements 和 RWA `trading-hours` / `next-trading-day`。

测试过程中发现并修复的线上可用性问题：

1. REST orderbook 原来发送 `depth`，Gateway 实际只读取 `limit`。
2. Perps positions 原来从 `orders` key 读取，Gateway 实际返回 `positions` envelope，且 position wire fields 已变化。
3. WS `ticker` 原示例发送 `symbol`，主网要求 `symbols`，会返回 `symbols cannot be empty`；现已兼容转换并补 live 回归。
4. Python WS 原来缺 Gateway main 已有的 `coins`、`accountID`、`pushInterval`、`coinPrice` / `allCoinPrice` surface。
