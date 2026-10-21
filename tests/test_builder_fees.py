from urllib.parse import parse_qs, urlsplit

import pytest
import responses

from sodex.client import APIError, BuilderFeeDailyStats, BuilderFeeStats, Client, Config

BASE = "https://gateway.example"
OWNER = "0x1111111111111111111111111111111111111111"
BUILDER_ID = 9007199254740993
SUMMARY = {
    "address": OWNER,
    "name": "Named Builder",
    "builderCode": str(BUILDER_ID),
    "userCount": "18446744073709551615",
    "builderFee": "0.300000000000000001",
    "tradeCount": "9007199254740993",
    "tradeVolume": "300.000000000000000001",
}


@pytest.mark.parametrize("account_id,limit", [(None, None), (BUILDER_ID, 300)])
@responses.activate
# Validates distinct summary/daily payloads, public routes, filters, quoted uint64s and ordered results.
def test_builder_fee_queries(account_id, limit):
    root = f"{BASE}/api/v1/user/{OWNER}/builder"
    daily = [
        {"date": "2026-10-20", "userCount": "1", "builderFee": "0.2", "tradeCount": "4", "tradeVolume": "200"},
        {"date": "2026-10-21", "userCount": "18446744073709551615", "builderFee": "0.100000000000000001", "tradeCount": "9007199254740989", "tradeVolume": "100.000000000000000001"},
    ]
    responses.get(root + "/summary", json={"code": 0, "data": SUMMARY})
    responses.get(root + "/daily-chart", json={"code": 0, "data": daily})
    client = Client(Config(base_url=BASE, account_address=OWNER))
    summary = client.get_builder_fee_summary(OWNER, account_id=account_id)
    chart = client.get_builder_fee_daily_chart(account_id=account_id, limit=limit)
    assert isinstance(summary, BuilderFeeStats)
    assert summary.builder_code == BUILDER_ID
    assert summary.user_count == 18446744073709551615
    assert summary.trade_count == 9007199254740993
    assert summary.name == "Named Builder" and summary.address == OWNER
    assert summary.builder_fee == SUMMARY["builderFee"]
    assert summary.trade_volume == SUMMARY["tradeVolume"]
    assert all(isinstance(point, BuilderFeeDailyStats) for point in chart)
    assert [point.date for point in chart] == ["2026-10-20", "2026-10-21"]
    assert chart[0].user_count == 1
    assert chart[1].user_count == 18446744073709551615
    assert chart[1].trade_count == 9007199254740989
    assert chart[1].builder_fee == "0.100000000000000001"
    assert chart[1].trade_volume == "100.000000000000000001"
    summary_params = {"accountID": [str(account_id)]} if account_id is not None else {}
    chart_params = {**summary_params, "limit": [str(limit)]} if limit is not None else summary_params
    assert [parse_qs(urlsplit(call.request.url).query) for call in responses.calls] == [summary_params, chart_params]
    assert all("X-API-Sign" not in call.request.headers for call in responses.calls)


@pytest.mark.parametrize("resource", ["summary", "daily-chart"])
@responses.activate
# Validates absent summaries and empty charts remain distinct, and API errors propagate.
def test_builder_fee_empty_and_error(resource):
    url = f"{BASE}/api/v1/user/{OWNER}/builder/{resource}"
    empty = None if resource == "summary" else []
    responses.get(url, json={"code": 0, "data": empty})
    responses.get(url, json={"code": -1, "error": "invalid parameter: accountID"})
    client = Client(Config(base_url=BASE))
    read = client.get_builder_fee_summary if resource == "summary" else client.get_builder_fee_daily_chart
    assert read(OWNER) == empty
    with pytest.raises(APIError, match="invalid parameter: accountID"):
        read(OWNER, account_id=BUILDER_ID)


@pytest.mark.parametrize("method", ["get_builder_fee_summary", "get_builder_fee_daily_chart"])
# Validates read-only clients require an owner when no account address is configured.
def test_builder_fee_requires_owner(method):
    with pytest.raises(ValueError, match="user_address is required"):
        getattr(Client(Config(base_url=BASE)), method)()
