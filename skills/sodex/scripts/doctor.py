"""Read a live quote without loading wallet credentials or submitting writes."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from importlib.metadata import version
import json
import sys

from sodex.client import Client, Config


def check(network: str, market: str, symbol: str) -> dict:
    testnet = network == "testnet"
    client = Client(Config(
        base_url=Client.TESTNET_BASE_URL if testnet else Client.DEFAULT_BASE_URL,
        chain_id=Client.TESTNET_CHAIN_ID if testnet else Client.DEFAULT_CHAIN_ID,
        timeout=10,
    ))
    tickers = client.spot_tickers(symbol) if market == "spot" else client.perps_tickers(symbol)
    ticker = next((item for item in tickers if item.symbol == symbol), None)
    if ticker is None:
        raise ValueError("The requested symbol has no ticker on this network")
    price = Decimal(ticker.last_price)
    if not price.is_finite() or price <= 0:
        raise ValueError("Gateway did not return a usable last price")
    return {
        "status": "ok", "source": "SoDEX", "sdk_version": version("sodex-python-sdk"),
        "network": network, "market": market, "endpoint": client.base_url,
        "observed_at": datetime.now(timezone.utc).isoformat(), "ticker": asdict(ticker),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", choices=("mainnet", "testnet"), default="mainnet")
    parser.add_argument("--market", choices=("spot", "perps"), default="perps")
    parser.add_argument("--symbol", help="Required for Spot; defaults to BTC-USD for Perps")
    args = parser.parse_args()
    if args.market == "spot" and not args.symbol:
        parser.error("Spot requires --symbol from Client.spot_symbols() on the selected network")
    symbol = args.symbol or "BTC-USD"
    try:
        print(json.dumps(check(args.network, args.market, symbol), indent=2))
        return 0
    except Exception as exc:
        # Arbitrary transport exception messages can include proxy credentials.
        print(json.dumps({
            "status": "error", "network": args.network, "market": args.market,
            "symbol": symbol, "error_type": type(exc).__name__,
            "hint": "Check Gateway reachability and symbol availability; retry this read only.",
        }), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
