#!/usr/bin/env python3
"""One JSON interface for SoDEX reads, reviewed plans, execution and recovery."""

import argparse
import json
from pathlib import Path
import sys
import time

from eth_account import Account
from sodex.client import APIError
from sodex.client.client import NonceManager
from sodex.client.types import HistoryFilter

from cli_state import InputError, State, encode, plain, require, secret
from cli_trading import client, normalize, preflight


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(
            "Invalid command arguments; use --help for the command schema."
        )


def parser():
    p = Parser(description=__doc__)
    p.add_argument(
        "--state-dir", default=str(Path.home() / ".local" / "share" / "sodex-skill")
    )
    p.add_argument("--profile")
    sub = p.add_subparsers(dest="command", required=True)
    profile = sub.add_parser("profile").add_subparsers(
        dest="profile_command", required=True
    )
    init = profile.add_parser(
        "init",
        help="Create a local encrypted or watch-only profile; never registers a key.",
    )
    init.add_argument("name")
    init.add_argument("--network", choices=("mainnet", "testnet"), required=True)
    init.add_argument("--owner", required=True)
    init.add_argument("--account-id", type=int)
    init.add_argument("--api-key-name", default="")
    init.add_argument("--watch-only", action="store_true")
    init.add_argument(
        "--key-env", help="Name of an environment variable, not its secret value."
    )
    init.add_argument(
        "--password-env", help="Name of an environment variable, not its secret value."
    )
    profile.add_parser("show")
    read = sub.add_parser("read")
    read.add_argument(
        "resource",
        choices=(
            "symbols",
            "quote",
            "book",
            "candles",
            "account",
            "balances",
            "positions",
            "orders",
            "history",
            "fills",
        ),
    )
    read.add_argument("--network", choices=("mainnet", "testnet"))
    read.add_argument("--market", choices=("spot", "perps"), default="perps")
    read.add_argument("--symbol")
    read.add_argument("--owner")
    read.add_argument("--account-id", type=int)
    read.add_argument("--interval", default="1h")
    read.add_argument("--start-time", type=int)
    read.add_argument("--end-time", type=int)
    read.add_argument("--limit", type=int, default=100)
    plan = sub.add_parser(
        "plan", help="Validate and persist a JSON intent without signing."
    )
    plan.add_argument("--file", required=True, help="JSON intent file or - for stdin.")
    execute = sub.add_parser(
        "execute", help="Execute a prepared operation once, after fresh preflight."
    )
    execute.add_argument("operation_id")
    execute.add_argument(
        "--confirm",
        required=True,
        help="Repeat the exact operation ID after reviewing its plan.",
    )
    execute.add_argument("--password-env")
    ops = sub.add_parser("ops").add_subparsers(dest="ops_command", required=True)
    ops.add_parser("list")
    for name in ("show", "reconcile"):
        cmd = ops.add_parser(name)
        cmd.add_argument("operation_id")
    return p


def read(api, profile, args):
    market, resource = args.market, args.resource
    require(
        1 <= args.limit <= (500 if resource in ("orders", "positions") else 1000),
        "Limit is outside the supported range.",
    )
    if resource in ("symbols", "quote"):
        return getattr(
            api, market + ("_symbols" if resource == "symbols" else "_tickers")
        )(args.symbol)
    if resource in ("book", "candles"):
        require(bool(args.symbol), "This query requires --symbol.")
        if resource == "book":
            return getattr(api, market + "_order_book")(args.symbol, depth=args.limit)
        return getattr(api, market + "_klines")(
            args.symbol,
            args.interval,
            HistoryFilter(
                start_time=args.start_time, end_time=args.end_time, limit=args.limit
            ),
        )
    owner = profile.get("owner")
    require(bool(owner), "Account queries require an owner address or a local profile.")
    account_id = profile.get("account_id")
    if resource in ("history", "fills"):
        return getattr(
            api,
            market + ("_orders_history" if resource == "history" else "_user_trades"),
        )(
            owner,
            HistoryFilter(
                symbol=args.symbol,
                account_id=account_id,
                start_time=args.start_time,
                end_time=args.end_time,
                limit=args.limit,
            ),
        )
    if resource in ("orders", "positions"):
        require(
            resource != "positions" or market == "perps",
            "Positions are available only on Perps.",
        )
        return getattr(api, market + "_" + resource)(
            owner, symbol=args.symbol, account_id=account_id, limit=args.limit
        )
    return getattr(
        api, market + ("_account_state" if resource == "account" else "_balances")
    )(owner, account_id)


def prepare(state, profile, intent, api=None):
    normalized = normalize(intent)
    request, method, report = preflight(api or client(profile), profile, normalized)
    context = state.public_profile(profile)
    context["account_id"] = report["account_id"]
    return state.create(
        dict(
            profile=context,
            intent=normalized,
            method=method,
            payload=request.to_json_payload(),
            preflight=report,
        )
    )


def receipt_state(result, payload, targets=()):
    if not isinstance(result, list):
        return "rejected" if getattr(result, "code", 0) != 0 else "accepted"
    if not result:
        return "unknown"
    # A single code-only error can reject the whole batch before individual validation.
    if (
        len(result) == 1
        and result[0].code is not None
        and result[0].code != 0
        and not result[0].cl_ord_id
    ):
        return "rejected"
    expected = payload.get("orders", payload.get("cancels", []))
    if len(result) != len(expected) or any(r.code is None for r in result):
        return "unknown"
    if any(r.code == 0 and not r.order_id for r in result):
        return "unknown"
    # Correlate every item; cardinality alone cannot distinguish duplicate or unrelated receipts.
    if expected and "clOrdID" in expected[0]:
        if {r.cl_ord_id for r in result} != {o["clOrdID"] for o in expected}:
            return "unknown"
    else:
        original_ids = {o["cl_ord_id"]: o["order_id"] for o in targets}
        observed = {
            r.order_id if r.order_id else original_ids.get(r.cl_ord_id) for r in result
        }
        if observed != {o["orderID"] for o in expected}:
            return "unknown"
    successful = sum(r.code == 0 for r in result)
    return (
        "accepted"
        if successful == len(result)
        else ("partial" if successful else "rejected")
    )


def execute(state, operation_id, profile, password, api_factory=client):
    operation = state.get(operation_id)
    require(
        operation["state"] == "prepared",
        "Operation has already been attempted; use ops reconcile. Never replay it.",
    )
    current = state.public_profile(profile)
    if current["account_id"] is None:
        current["account_id"] = operation["profile"]["account_id"]
    require(
        current == operation["profile"],
        "Execution profile differs from the saved network/account/signer context.",
    )
    require(
        "keystore" in profile,
        "This profile is watch-only; create a signing profile and a new plan.",
    )
    try:
        key = Account.decrypt(profile["keystore"], password)
    except (ValueError, TypeError, KeyError):
        raise InputError("Cannot unlock the local keystore.") from None
    require(
        Account.from_key(key).address == profile["signer"],
        "Keystore signer does not match the profile.",
    )
    with state.signing_lock(profile):
        # Resolve the exact saved account, even if the owner's primary account has changed.
        request, method, report = preflight(
            api_factory(operation["profile"]), operation["profile"], operation["intent"]
        )
        require(
            method == operation["method"]
            and request.to_json_payload() == operation["payload"],
            "The fresh request differs from the reviewed plan; prepare a new plan.",
        )
        nonce_key = profile["network"] + ":" + profile["signer"].lower()
        signing_api = api_factory(
            operation["profile"],
            private_key=key,
            api_key_name=profile["api_key_name"],
            nonce_manager=NonceManager(clock=lambda: state.next_nonce(nonce_key)),
        )
        state.transition(operation_id, "prepared", "submitting", {"preflight": report})
        try:
            result = getattr(signing_api, method)(request)
            status = receipt_state(
                result, operation["payload"], operation["preflight"].get("targets", [])
            )
            state.transition(
                operation_id, "submitting", status, {"receipt": plain(result)}
            )
        except APIError as exc:
            # Do not persist response messages or exception text containing transport details.
            state.transition(
                operation_id,
                "submitting",
                "rejected",
                {"error_type": "APIError", "code": exc.code},
            )
        except (Exception, KeyboardInterrupt) as exc:
            state.transition(
                operation_id,
                "submitting",
                "unknown",
                {"error_type": type(exc).__name__},
            )
        return state.get(operation_id)


def reconcile(state, operation_id, api=None):
    operation = state.get(operation_id)
    profile, intent = operation["profile"], operation["intent"]
    api = api or client(profile)
    market, symbol = intent["market"], intent["symbol"]
    owner, account_id = profile["owner"], profile["account_id"]
    target_times = [
        o["created_at"]
        for o in operation["preflight"].get("targets", [])
        if o["created_at"] > 0
    ]
    start = max(0, min([operation["created_at"]] + target_times) - 60000)
    end = int(time.time() * 1000)
    history_filter = HistoryFilter(
        symbol=symbol, account_id=account_id, start_time=start, end_time=end, limit=1000
    )
    opened = getattr(api, market + "_orders")(
        owner, symbol=symbol, account_id=account_id, limit=500
    )
    history = getattr(api, market + "_orders_history")(owner, history_filter)
    fills = getattr(api, market + "_user_trades")(owner, history_filter)
    payload = operation["payload"]
    cids = {o["clOrdID"] for o in payload.get("orders", []) if "clOrdID" in o}
    ids = {o["order_id"] for o in operation["preflight"].get("targets", [])}
    matched = {
        o.order_id: o
        for o in history + opened
        if o.cl_ord_id in cids or o.order_id in ids
    }
    data = dict(
        observed_at=end,
        orders=plain(list(matched.values())),
        fills=plain([f for f in fills if f.order_id in matched]),
        missing_client_order_ids=sorted(cids - {o.cl_ord_id for o in matched.values()}),
        missing_order_ids=sorted(ids - set(matched)),
        account_state=getattr(api, market + "_account_state")(owner, account_id),
        history_window={"start_time": start, "end_time": end, "limit": 1000},
        history_may_be_truncated=len(history) == 1000 or len(fills) == 1000,
        open_orders_may_be_truncated=len(opened) == 500,
        note="Observation only: no writes or retries. Missing records do not prove rejection. Current leverage/margin cannot prove whether an earlier adjustment executed.",
    )
    with state.db:
        state.event(operation_id, "reconciled", data)
    return state.get(operation_id)


def output_safe(value):
    # JSON clients using IEEE-754 numbers must not lose uint64 order/account identifiers.
    if type(value) is int and abs(value) > 2**53 - 1:
        return str(value)
    if isinstance(value, dict):
        return {k: output_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [output_safe(v) for v in value]
    return value


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        state = State(args.state_dir)
        if args.command == "profile":
            if args.profile_command == "show":
                result = state.public_profile(state.profile(args.profile))
            else:
                require(
                    not args.watch_only
                    or not (args.key_env or args.password_env or args.api_key_name),
                    "Watch-only profiles cannot contain signing options.",
                )
                key = (
                    None
                    if args.watch_only
                    else secret(args.key_env, "Private key (hidden): ")
                )
                password = (
                    None
                    if args.watch_only
                    else secret(args.password_env, "Keystore password (hidden): ")
                )
                result = state.add_profile(
                    args.name,
                    args.network,
                    args.owner,
                    args.account_id,
                    args.api_key_name,
                    key,
                    password,
                )
        elif args.command == "read":
            profile = (
                state.public_profile(state.profile(args.profile))
                if args.profile
                else dict(
                    network=args.network, owner=args.owner, account_id=args.account_id
                )
            )
            require(bool(profile["network"]), "Specify --network or select a profile.")
            if args.profile:
                require(
                    not any(
                        v is not None and v != profile[k]
                        for k, v in (
                            ("network", args.network),
                            ("owner", args.owner),
                            ("account_id", args.account_id),
                        )
                    ),
                    "Read options conflict with the selected profile.",
                )
            result = dict(
                network=profile["network"],
                market=args.market,
                observed_at=int(time.time() * 1000),
                data=read(client(profile), profile, args),
            )
        elif args.command == "plan":
            raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
            try:
                intent = json.loads(raw)
            except ValueError:
                raise InputError("Intent must be valid JSON.") from None
            result = prepare(state, state.profile(args.profile), intent)
        elif args.command == "execute":
            require(
                args.confirm == args.operation_id,
                "--confirm must match the reviewed operation ID.",
            )
            result = execute(
                state,
                args.operation_id,
                state.profile(args.profile),
                secret(args.password_env, "Keystore password (hidden): "),
            )
        elif args.ops_command == "list":
            result = state.recent()
        elif args.ops_command == "show":
            result = state.get(args.operation_id)
        else:
            result = reconcile(state, args.operation_id)
        success = not (args.command == "execute" and result["state"] != "accepted")
        print(
            encode(
                output_safe(plain(dict(schema_version=1, ok=success, result=result)))
            )
        )
        return 0 if success else 1
    except Exception as exc:
        error = dict(
            type=type(exc).__name__,
            message=(
                str(exc)
                if isinstance(exc, InputError)
                else "Command failed; inspect the operation journal before retrying a write."
            ),
        )
        if isinstance(exc, APIError):
            error["code"] = exc.code
        print(encode(dict(schema_version=1, ok=False, error=error)))
        return 2 if isinstance(exc, InputError) else 1


if __name__ == "__main__":
    sys.exit(main())
