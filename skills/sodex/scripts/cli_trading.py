"""Compile reviewed JSON intents into SDK requests and perform unsigned preflight."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext
import re
import time
import uuid

from sodex.client import Client, Config
from sodex.common.enums import (
    MarginMode,
    OrderModifier,
    OrderSide,
    OrderType,
    StopType,
    TimeInForce,
    TriggerType,
)
from sodex.common.types import ReplaceOrderRequest, ReplaceParams
from sodex.perps import types as perps
from sodex.spot import types as spot

from cli_state import InputError, plain, require


def client(profile, **kwargs):
    testnet = profile["network"] == "testnet"
    return Client(
        Config(
            base_url=Client.TESTNET_BASE_URL if testnet else Client.DEFAULT_BASE_URL,
            chain_id=Client.TESTNET_CHAIN_ID if testnet else Client.DEFAULT_CHAIN_ID,
            account_address=profile.get("owner"),
            **kwargs,
        )
    )


def amount(value, field="amount", signed=False):
    require(
        isinstance(value, str) and len(value) <= 80,
        field + " must be a decimal string.",
    )
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise InputError(field + " must be a decimal string.") from None
    require(
        result.is_finite() and result != 0 and (signed or result > 0),
        field + " must be finite and " + ("nonzero." if signed else "positive."),
    )
    require(
        len(result.as_tuple().digits) <= 36 and abs(result.as_tuple().exponent) <= 36,
        field + " exceeds supported precision.",
    )
    return result


def fields(data, allowed, required=()):
    require(isinstance(data, dict), "Expected a JSON object.")
    require(
        not set(data) - set(allowed),
        "Unsupported fields in the intent; consult the command reference.",
    )
    require(set(required) <= set(data), "Required intent fields are missing.")


def normalize(data):
    data = deepcopy(data)
    fields(
        data,
        (
            "action",
            "market",
            "symbol",
            "orders",
            "entry",
            "take_profit",
            "stop_loss",
            "leverage",
            "margin_mode",
            "amount",
        ),
        ("action", "market", "symbol"),
    )
    action = data["action"]
    require(
        action
        in ("orders", "bracket", "tpsl", "amend", "cancel", "leverage", "margin"),
        "Unknown action.",
    )
    require(data["market"] in ("spot", "perps"), "Choose spot or perps.")
    require(
        isinstance(data["symbol"], str) and bool(data["symbol"]),
        "A symbol is required.",
    )
    common = {"action", "market", "symbol"}
    extras = {
        "orders": {"orders"},
        "tpsl": {"orders"},
        "amend": {"orders"},
        "cancel": {"orders"},
        "bracket": {"entry", "take_profit", "stop_loss"},
        "leverage": {"leverage", "margin_mode"},
        "margin": {"amount"},
    }[action]
    fields(data, common | extras)
    if action in ("bracket", "tpsl", "leverage", "margin"):
        require(data["market"] == "perps", "This action is available only on Perps.")
    if action == "bracket":
        require(
            "entry" in data and ("take_profit" in data or "stop_loss" in data),
            "A bracket needs an entry and at least one exit.",
        )
        entries = [data["entry"]]
    elif action in ("orders", "tpsl", "amend", "cancel"):
        require(
            isinstance(data.get("orders"), list) and 1 <= len(data["orders"]) <= 100,
            "A batch must contain 1 to 100 orders.",
        )
        entries = data["orders"]
    else:
        entries = []
    for item in entries:
        allowed = {
            "orders": (
                "side",
                "type",
                "time_in_force",
                "quantity",
                "price",
                "reduce_only",
                "client_order_id",
            ),
            "bracket": (
                "side",
                "type",
                "time_in_force",
                "quantity",
                "price",
                "client_order_id",
            ),
            "tpsl": ("stop_type", "stop_price", "quantity", "client_order_id"),
            "amend": (
                "order_id",
                "original_client_order_id",
                "price",
                "quantity",
                "stop_price",
            ),
            "cancel": ("order_id", "original_client_order_id", "client_order_id"),
        }[action]
        fields(item, allowed)
        item.setdefault("client_order_id", "sk-" + uuid.uuid4().hex[:28])
        require(
            isinstance(item["client_order_id"], str)
            and bool(re.fullmatch(r"[0-9a-zA-Z_-]{1,36}", item["client_order_id"])),
            "Invalid client order ID.",
        )
        if action in ("amend", "cancel"):
            require(
                ("order_id" in item) != ("original_client_order_id" in item),
                "Use exactly one original order selector.",
            )
        for key in ("quantity", "price", "stop_price"):
            if key in item:
                amount(item[key], key)
    if action == "bracket":
        # Child IDs remain stable across execute/reconcile; they are derived from the entry ID.
        require(
            len(data["entry"]["client_order_id"]) <= 33,
            "Bracket entry client ID must have at most 33 characters.",
        )
    if action == "leverage":
        fields(data, common | extras, common | extras)
    if action == "margin":
        fields(data, common | extras, common | extras)
    return data


def bound(value, low, high, label):
    if low and Decimal(low) != 0:
        require(value >= Decimal(low), label + " is below the minimum.")
    if high and Decimal(high) != 0:
        require(value <= Decimal(high), label + " exceeds the maximum.")


def grid(value, precision, step, low, high, label):
    require(
        value % Decimal(1).scaleb(-precision) == 0,
        label + " exceeds decimal precision.",
    )
    require(bool(step) and Decimal(step) > 0, "Symbol grid metadata is unavailable.")
    require(
        value % Decimal(step) == 0,
        label + " is off the symbol grid; values are never rounded.",
    )
    bound(value, low, high, label)


def named(value):
    return str(value).upper()


def preflight(api, profile, intent):
    with localcontext() as context:
        context.prec = 80
        return _preflight(api, profile, intent)


def _preflight(api, profile, intent):
    market, name, action = intent["market"], intent["symbol"], intent["action"]
    accounts = api.get_subaccounts(profile["owner"])
    account_id = profile["account_id"] or accounts.primary_account_id
    require(
        account_id > 0
        and account_id
        in {accounts.primary_account_id, *(s.account_id for s in accounts.subaccounts)},
        "Account does not belong to the profile owner.",
    )
    if profile.get("api_key_name"):
        registered = api.get_api_keys(
            profile["owner"], account_id=account_id, name=profile["api_key_name"]
        )
        key = next(
            (
                k
                for k in getattr(registered, market)
                if k.name == profile["api_key_name"]
            ),
            None,
        )
        require(
            key is not None and key.public_key.lower() == profile["signer"].lower(),
            "Registered API key does not match the local signer on this engine.",
        )
        require(
            key.expires_at > int(time.time() * 1000), "Registered API key has expired."
        )
        disabled = key.permissions or 0
        allowed = not (disabled & 1) or (action == "cancel" and not (disabled & 2))
        require(allowed, "API key does not permit this operation.")
    symbols = getattr(api, market + "_symbols")(name)
    symbol = next((s for s in symbols if s.symbol == name), None)
    require(
        symbol is not None,
        "Exact symbol not found on this network; query symbols first.",
    )
    if action != "cancel":
        require(symbol.status == "TRADING", "Symbol is not trading.")
    state = getattr(api, market + "_account_state")(profile["owner"], account_id)
    orders = getattr(api, market + "_orders")(
        profile["owner"], symbol=name, account_id=account_id, limit=500
    )
    positions = (
        api.perps_positions(profile["owner"], symbol=name, account_id=account_id)
        if market == "perps"
        else []
    )
    positions = [p for p in positions if Decimal(p.size) != 0]
    require(
        all(named(p.position_side) in ("BOTH", "1") for p in positions),
        "This CLI supports the documented one-way position mode only.",
    )
    position = positions[0] if positions else None
    config = next((s for s in state.get("S", []) if s["s"] == name), None)
    warnings = [
        "Preflight is a snapshot, not a reservation. The engine rechecks fees, risk tiers, price bands and available funds."
    ]
    if len(orders) == 500:
        warnings.append(
            "Open-order snapshot reached 500 records; duplicate-ID and target checks may be incomplete."
        )
    report = dict(
        network=profile["network"],
        owner=profile["owner"],
        account_id=account_id,
        symbol=plain(symbol),
        observed_at=int(time.time() * 1000),
        warnings=warnings,
        account_state=state,
        positions=plain(positions),
        estimates=[],
    )
    sid = symbol.symbol_id

    def select(item):
        if "order_id" in item:
            require(
                str(item["order_id"]).isdigit(), "Order ID must be an unsigned integer."
            )
            matches = [o for o in orders if o.order_id == int(item["order_id"])]
        else:
            matches = [
                o for o in orders if o.cl_ord_id == item["original_client_order_id"]
            ]
        require(
            len(matches) == 1,
            "Target order is not uniquely present in open orders; reconcile first.",
        )
        return matches[0]

    if action == "cancel":
        targets = [select(i) for i in intent["orders"]]
        require(
            len({o.order_id for o in targets}) == len(targets),
            "Duplicate cancellation target.",
        )
        report["targets"] = plain(targets)
        if market == "perps":
            req = perps.CancelOrderRequest(
                account_id,
                [perps.CancelOrder(sid, order_id=o.order_id) for o in targets],
            )
        else:
            req = spot.BatchCancelOrderRequest(
                account_id,
                [
                    spot.BatchCancelOrderItem(
                        sid, i["client_order_id"], order_id=o.order_id
                    )
                    for i, o in zip(intent["orders"], targets)
                ],
            )
        return req, "cancel_" + market + "_orders", report

    if action == "leverage":
        leverage = intent.get("leverage")
        mode = intent.get("margin_mode")
        require(
            type(leverage) is int
            and symbol.max_leverage is not None
            and 1 <= leverage <= symbol.max_leverage,
            "Leverage must be an integer within the symbol maximum.",
        )
        require(mode in ("cross", "isolated"), "Choose cross or isolated margin mode.")
        target_mode = MarginMode[mode.upper()]
        existing_mode = (
            config["m"] if config else (position.margin_mode if position else None)
        )
        if orders or position:
            require(
                named(existing_mode) in (target_mode.name, str(int(target_mode))),
                "Cannot change margin mode with open orders or positions; current mode must be known.",
            )
        return (
            perps.UpdateLeverageRequest(account_id, sid, leverage, target_mode),
            "update_leverage",
            report,
        )

    if action == "margin":
        value = amount(intent.get("amount"), signed=True)
        require(
            position is not None and named(position.margin_mode) in ("ISOLATED", "1"),
            "Margin adjustments require an isolated position.",
        )
        coin = next(
            (
                c
                for c in api.perps_coins(symbol.quote_asset)
                if c.coin == symbol.quote_asset
            ),
            None,
        )
        require(
            coin is not None and value % Decimal(1).scaleb(-coin.precision) == 0,
            "Margin amount exceeds settlement coin precision.",
        )
        if value > 0:
            require(
                "ami" in state and value <= Decimal(state["ami"]),
                "Insufficient or unavailable isolated margin balance.",
            )
        else:
            require(
                bool(position.initial_margin)
                and -value < Decimal(position.initial_margin),
                "Cannot remove the entire isolated margin.",
            )
            warnings.append(
                "Withdrawable isolated margin depends on live maintenance requirements; the engine decides the final maximum."
            )
        return (
            perps.UpdateMarginRequest(account_id, sid, value),
            "update_margin",
            report,
        )

    tickers = getattr(api, market + "_tickers")(name)
    ticker = next((t for t in tickers if t.symbol == name), None)
    require(ticker is not None, "No current ticker available.")
    last = amount(ticker.last_price, "last price")
    mark = amount(ticker.mark_price, "mark price") if market == "perps" else last
    report["ticker"] = plain(ticker)

    def validate(price, quantity, typ, reduce_only=False, trigger=False, side=None):
        if price is not None:
            grid(
                price,
                symbol.price_precision,
                symbol.tick_size,
                symbol.min_price,
                symbol.max_price,
                "Price",
            )
        if quantity is not None:
            grid(
                quantity,
                symbol.quantity_precision,
                symbol.step_size,
                symbol.min_quantity,
                symbol.max_quantity,
                "Quantity",
            )
            if typ == OrderType.MARKET:
                bound(
                    quantity,
                    symbol.market_min_quantity,
                    symbol.market_max_quantity,
                    "Market quantity",
                )
            notional = quantity * (price if typ == OrderType.LIMIT else last)
            if not reduce_only:
                bound(notional, symbol.min_notional, symbol.max_notional, "Notional")
        else:
            notional = None
        if typ == OrderType.LIMIT and not trigger and side:
            ratio = (
                symbol.buy_limit_up_ratio
                if side == OrderSide.BUY
                else symbol.sell_limit_down_ratio
            )
            require(bool(ratio), "Price-band metadata is unavailable.")
            limit = mark * (
                1 + Decimal(ratio) if side == OrderSide.BUY else 1 - Decimal(ratio)
            )
            require(
                price <= limit if side == OrderSide.BUY else price >= limit,
                "Limit price exceeds the current price band.",
            )
        return notional

    if action == "amend":
        targets = [select(i) for i in intent["orders"]]
        require(
            len({o.order_id for o in targets}) == len(targets),
            "Duplicate amendment target.",
        )
        report["targets"] = plain(targets)
        replacements = []
        for item, target in zip(intent["orders"], targets):
            require(
                any(k in item for k in ("price", "quantity", "stop_price")),
                "An amendment needs at least one changed field.",
            )
            is_stop = bool(target.stop_price and Decimal(target.stop_price))
            require(
                is_stop or "stop_price" not in item,
                "A regular order cannot be converted into a TP/SL order.",
            )
            price = (
                amount(item.get("price", target.price), "price")
                if item.get("price", target.price)
                else None
            )
            qty = (
                amount(item.get("quantity", target.orig_qty), "quantity")
                if item.get("quantity", target.orig_qty)
                else None
            )
            typ = (
                OrderType[named(target.type)]
                if named(target.type) in OrderType.__members__
                else OrderType(int(target.type))
            )
            validate(price, qty, typ, trigger=is_stop)
            if is_stop:
                require(
                    market == "perps" and len(targets) == 1,
                    "Amend TP/SL one at a time.",
                )
                if "stop_price" in item:
                    grid(
                        amount(item["stop_price"]),
                        symbol.price_precision,
                        symbol.tick_size,
                        symbol.min_price,
                        symbol.max_price,
                        "Stop price",
                    )
                report["retained_client_order_id"] = target.cl_ord_id
                return (
                    perps.ModifyOrderRequest(
                        account_id,
                        sid,
                        order_id=target.order_id,
                        **{
                            k: amount(item[k], k)
                            for k in ("price", "quantity", "stop_price")
                            if k in item
                        },
                    ),
                    "modify_perps_order",
                    report,
                )
            require(
                typ == OrderType.LIMIT
                and named(target.time_in_force) in ("GTC", "GTX", "1", "4")
                and named(target.status) in ("NEW", "PARTIALLY_FILLED", "1", "2"),
                "Only resting GTC/GTX non-TP/SL limit orders can be replaced.",
            )
            replacements.append(
                ReplaceParams(
                    sid,
                    item["client_order_id"],
                    orig_order_id=target.order_id,
                    **{
                        k: amount(item[k], k)
                        for k in ("price", "quantity")
                        if k in item
                    },
                )
            )
        require(
            len({i.cl_ord_id for i in replacements}) == len(replacements)
            and not {i.cl_ord_id for i in replacements} & {o.cl_ord_id for o in orders},
            "Replacement client IDs must be unique and unused.",
        )
        warnings.append(
            "Replacement balance and margin requirements are checked by the engine, using the released original order reserves."
        )
        return (
            ReplaceOrderRequest(account_id, replacements),
            "replace_" + market + "_orders",
            report,
        )

    def normal(item, modifier=OrderModifier.NORMAL):
        require(
            item.get("side") in ("buy", "sell")
            and item.get("type") in ("limit", "market"),
            "Order side and type are required.",
        )
        side, typ = OrderSide[item["side"].upper()], OrderType[item["type"].upper()]
        tif = item.get("time_in_force", "ioc" if typ == OrderType.MARKET else "gtc")
        require(
            tif in (("ioc",) if typ == OrderType.MARKET else ("gtc", "ioc", "gtx")),
            "Unsupported time in force for this order type.",
        )
        qty = amount(item.get("quantity"), "quantity")
        price = amount(item["price"], "price") if "price" in item else None
        require(
            typ != OrderType.LIMIT or price is not None, "Limit orders require a price."
        )
        reduce_only = item.get("reduce_only", False)
        require(
            type(reduce_only) is bool and (market == "perps" or not reduce_only),
            "reduce_only must be a Perps boolean.",
        )
        if reduce_only:
            require(
                position is not None
                and (side == OrderSide.SELL) == (Decimal(position.size) > 0)
                and qty <= abs(Decimal(position.size)),
                "Reduce-only order must reduce an existing position without exceeding its size.",
            )
        notional = validate(price, qty, typ, reduce_only, side=side)
        report["estimates"].append(
            dict(
                client_order_id=item["client_order_id"],
                notional=str(notional),
                reduce_only=reduce_only,
            )
        )
        args = dict(
            cl_ord_id=item["client_order_id"],
            side=side,
            type=typ,
            time_in_force=TimeInForce[tif.upper()],
            price=price,
            quantity=qty,
        )
        return (
            perps.RawOrder(modifier=modifier, reduce_only=reduce_only, **args)
            if market == "perps"
            else spot.BatchNewOrderItem(symbol_id=sid, **args)
        )

    def stop(cid, stop_type, stop_price, side, quantity, modifier, reference):
        require(
            stop_type in ("take_profit", "stop_loss"),
            "Choose take_profit or stop_loss.",
        )
        px = amount(stop_price, "stop price")
        grid(
            px,
            symbol.price_precision,
            symbol.tick_size,
            symbol.min_price,
            symbol.max_price,
            "Stop price",
        )
        above = (side == OrderSide.SELL) == (stop_type == "take_profit")
        require(
            px > reference if above else px < reference,
            "TP/SL trigger is on the wrong side of the reference price.",
        )
        validate(None, quantity, OrderType.MARKET, True, True)
        return perps.RawOrder(
            cid,
            modifier,
            side,
            OrderType.MARKET,
            TimeInForce.IOC,
            quantity=quantity,
            stop_price=px,
            stop_type=StopType[stop_type.upper()],
            trigger_type=TriggerType.MARK_PRICE,
            reduce_only=True,
        )

    if action == "bracket":
        parent = normal(intent["entry"], OrderModifier.BRACKET)
        side = OrderSide.SELL if parent.side == OrderSide.BUY else OrderSide.BUY
        batch = [parent] + [
            stop(
                parent.cl_ord_id + suffix,
                key,
                intent[key],
                side,
                parent.quantity,
                OrderModifier.ATTACHED_STOP,
                parent.price if parent.type == OrderType.LIMIT else mark,
            )
            for key, suffix in (("take_profit", "-tp"), ("stop_loss", "-sl"))
            if key in intent
        ]
        warnings.append(
            "Attached exits activate after full parent fill, or system cancellation for insufficient margin after partial fill. Manually canceling a partially filled parent also cancels its exits."
        )
    elif action == "tpsl":
        require(position is not None, "Position TP/SL requires an existing position.")
        side = OrderSide.SELL if Decimal(position.size) > 0 else OrderSide.BUY
        batch = []
        for item in intent["orders"]:
            qty = amount(item["quantity"], "quantity") if "quantity" in item else None
            require(
                qty is None or qty <= abs(Decimal(position.size)),
                "TP/SL quantity exceeds the position size.",
            )
            batch.append(
                stop(
                    item["client_order_id"],
                    item.get("stop_type"),
                    item.get("stop_price"),
                    side,
                    qty,
                    OrderModifier.STOP,
                    mark,
                )
            )
        warnings.append(
            "Position TP/SL orders are independent reduce-only exits; do not assume they form an OCO pair. Omitted quantity means the full position at trigger time."
        )
    else:
        batch = [normal(i) for i in intent["orders"]]
    ids = [o.cl_ord_id for o in batch]
    require(
        len(set(ids)) == len(ids) and not set(ids) & {o.cl_ord_id for o in orders},
        "Client order IDs must be unique and unused among open orders.",
    )

    if action in ("orders", "bracket"):
        opening = [o for o in batch if not getattr(o, "reduce_only", False)]
        fee = max(Decimal(symbol.taker_fee or "0"), Decimal(0))
        if market == "spot":
            balances = api.spot_balances(profile["owner"], account_id)
            available = {b.coin: Decimal(b.total) - Decimal(b.locked) for b in balances}
            costs = {}
            for order in opening:
                asset = (
                    symbol.quote_asset
                    if order.side == OrderSide.BUY
                    else symbol.base_asset
                )
                px = (
                    order.price
                    if order.type == OrderType.LIMIT
                    else last * (1 + Decimal(symbol.market_deviation_ratio or "0"))
                )
                value = (
                    order.quantity * px * (1 + fee)
                    if order.side == OrderSide.BUY
                    else order.quantity
                )
                costs[asset] = costs.get(asset, Decimal(0)) + value
            for asset, value in costs.items():
                require(
                    value <= available.get(asset, Decimal(0)),
                    "Estimated batch cost exceeds available Spot balance.",
                )
            report["estimated_reserve"] = plain(costs)
        elif opening:
            leverage = (
                config.get("l") if config else (position.leverage if position else None)
            )
            mode = (
                config.get("m")
                if config
                else (position.margin_mode if position else None)
            )
            if leverage and mode is not None:
                isolated = named(mode) in ("ISOLATED", "1")
                available = state.get("ami" if isolated else "am")
                require(available is not None, "Available margin is unavailable.")
                cost = sum(
                    o.quantity
                    * (o.price if o.type == OrderType.LIMIT else mark)
                    * (Decimal(1) / leverage + fee)
                    for o in opening
                )
                require(
                    cost <= Decimal(available),
                    "Conservative batch margin estimate exceeds available margin.",
                )
                report["estimated_margin"] = str(cost)
            else:
                warnings.append(
                    "No established symbol leverage/margin configuration: local margin estimate unavailable; configure leverage first for a complete estimate."
                )
        warnings.append(
            "Cost estimates use symbol taker fees; fills, fee overrides and position netting can change the final requirement."
        )
    req = (
        perps.NewOrderRequest(account_id, sid, batch)
        if market == "perps"
        else spot.BatchNewOrderRequest(account_id, batch)
    )
    return (
        req,
        "place_perps_order" if market == "perps" else "place_spot_orders",
        report,
    )
