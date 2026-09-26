"""Boundary tests for the installable skill's credential-free connectivity check."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
import requests
import responses


def load_script(name):
    path = Path(__file__).resolve().parents[1] / "skills" / "sodex" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"skill_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


doctor = load_script("doctor")
bootstrap = load_script("bootstrap")


@pytest.mark.parametrize("network,market,symbol", [
    ("mainnet", "perps", "BTC-USD"),
    ("testnet", "spot", "vBTC_vUSDC"),
])
@responses.activate
# Validate the selected endpoint, exact price, and unsigned GET despite ambient credentials.
def test_doctor_ignores_wallet_environment(monkeypatch, network, market, symbol):
    monkeypatch.setenv("SODEX_PRIVATE_KEY", "invalid-secret-that-must-not-be-loaded")
    monkeypatch.setenv("SODEX_API_KEY_NAME", "ambient-key")
    monkeypatch.setenv("SODEX_NETWORK", "testnet" if network == "mainnet" else "mainnet")
    responses.get(
        f"https://{network}-gw.sodex.dev/api/v1/{market}/markets/tickers",
        json={"code": 0, "data": [{"symbol": symbol, "lastPx": "83890.123456789123456789"}]},
        match=[responses.matchers.query_param_matcher({"symbol": symbol})],
    )
    result = doctor.check(network, market, symbol)
    assert result["network"] == network
    assert result["ticker"]["last_price"] == "83890.123456789123456789"
    assert len(responses.calls) == 1
    assert not any(name.startswith("X-API-") for name in responses.calls[0].request.headers)


@pytest.mark.parametrize("data", [
    [], [{"symbol": "ETH-USD", "lastPx": "100"}],
    *[[{"symbol": "BTC-USD", "lastPx": price}] for price in ("", "0", "-1", "NaN", "Infinity")],
])
@responses.activate
# Reject missing, mismatched, nonnumeric and nonpositive quotes instead of claiming connectivity.
def test_doctor_rejects_unusable_quote(monkeypatch, capsys, data):
    monkeypatch.setattr("sys.argv", ["doctor.py"])
    responses.get(
        "https://mainnet-gw.sodex.dev/api/v1/perps/markets/tickers",
        json={"code": 0, "data": data},
    )
    assert doctor.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["status"] == "error"


@responses.activate
# Report transport failures without exposing credentials that may occur inside proxy error text.
def test_doctor_redacts_transport_errors(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["doctor.py"])
    responses.get(
        "https://mainnet-gw.sodex.dev/api/v1/perps/markets/tickers",
        body=requests.exceptions.ProxyError("https://user:private-proxy-password@proxy"),
    )
    assert doctor.main() == 1
    output = capsys.readouterr()
    assert "private-proxy-password" not in output.err + output.out
    assert json.loads(output.err)["error_type"] == "ProxyError"


@responses.activate
# A Gateway error in HTTP 200 must fail the CLI rather than be reported as a valid quote.
def test_doctor_handles_api_rejection(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["doctor.py"])
    responses.get(
        "https://mainnet-gw.sodex.dev/api/v1/perps/markets/tickers",
        json={"code": 4001, "message": "rate limited"},
    )
    assert doctor.main() == 1
    assert json.loads(capsys.readouterr().err)["error_type"] == "APIError"


# Require a discovered Spot symbol instead of sending an example-only alias to Gateway.
def test_doctor_requires_explicit_spot_symbol(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["doctor.py", "--market", "spot"])
    with pytest.raises(SystemExit) as error:
        doctor.main()
    assert error.value.code == 2
    assert "Client.spot_symbols()" in capsys.readouterr().err


@pytest.mark.parametrize("modified", [False, True])
# Preserve a wrong-revision or locally edited checkout without installing or overwriting it.
def test_bootstrap_preserves_existing_checkout(tmp_path, monkeypatch, modified):
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    subprocess.run(["git", "init", "--quiet", str(sdk)], check=True)
    content = sdk / "example.py"
    content.write_text("original = True\n")
    subprocess.run(["git", "-C", str(sdk), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(sdk), "-c", "user.name=Skill Test",
        "-c", "user.email=skill-test@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "--quiet", "-m", "fixture",
    ], check=True)
    if modified:
        revision = subprocess.check_output(["git", "-C", str(sdk), "rev-parse", "HEAD"], text=True).strip()
        monkeypatch.setattr(bootstrap, "SDK_REVISION", revision)
        content.write_text("local_edit = True\n")
    before = content.read_text()
    with pytest.raises(RuntimeError, match="new empty directory"):
        bootstrap.install(tmp_path)
    assert content.read_text() == before
    assert not (tmp_path / ".venv").exists()
