"""Latest Gateway public and user API alignment tests."""

from __future__ import annotations

import pytest
import responses

from sodex.client import APIError, Client, Config


_BASE_URL = "https://gateway.example"
_USER = "0x1111111111111111111111111111111111111111"


def _client() -> Client:
    return Client(Config(base_url=_BASE_URL))


# Validates server time comes from the response envelope while system status comes from data.
@responses.activate
def test_server_time_and_system_status():
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/time",
        json={"code": 0, "timestamp": 1780000000123},
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/status",
        json={"code": 0, "data": "TRADING"},
    )

    client = _client()

    assert client.get_server_time() == 1780000000123
    assert client.get_system_status() == "TRADING"


# Validates v1.6.15 user status preserves a full uint64 user ID and the not-found state.
@responses.activate
def test_get_user_status_decodes_active_and_not_found():
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{_USER}/status",
        json={
            "code": 0,
            "data": {"status": "Active", "userID": 18446744073709551615},
        },
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/user/{_USER}/status",
        json={"code": 0, "data": {"status": "UserNotFound", "userID": 0}},
    )

    client = _client()
    active = client.get_user_status(_USER)
    missing = client.get_user_status(_USER)

    assert active.status == "Active"
    assert active.user_id == 18446744073709551615
    assert missing.status == "UserNotFound"
    assert missing.user_id == 0


# Validates announcement list/detail query mapping and typed article decoding.
@responses.activate
def test_announcements_map_latest_gateway_schema():
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/announcements",
        match=[
            responses.matchers.query_param_matcher(
                {"page": "2", "size": "10", "lang": "zh"}
            )
        ],
        json={
            "code": 0,
            "data": {
                "articles": [
                    {
                        "id": 42,
                        "externalId": "release-42",
                        "style": "release",
                        "title": "Gateway v1.6.15",
                        "label_names": ["gateway"],
                        "startTime": 1,
                        "endTime": 2,
                        "createdAt": 3,
                        "updatedAt": 4,
                    }
                ],
                "page": 2,
                "size": 10,
                "count": 1,
            },
        },
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/announcements/detail/42",
        match=[
            responses.matchers.query_param_matcher(
                {"lang": "en", "plainText": "True"}
            )
        ],
        json={
            "code": 0,
            "data": {
                "id": 42,
                "externalId": "release-42",
                "style": "release",
                "title": "Gateway v1.6.15",
                "label_names": [],
                "startTime": 1,
                "endTime": 2,
                "createdAt": 3,
                "updatedAt": 4,
                "body": "Available now",
            },
        },
    )

    client = _client()
    listing = client.get_announcements(page=2, size=10, lang="zh")
    detail = client.get_announcement_detail(42, lang="en", plain_text=True)

    assert listing.articles[0].article_id == 42
    assert listing.articles[0].label_names == ["gateway"]
    assert listing.count == 1
    assert detail.body == "Available now"


# Validates RWA weekly sessions and Earn next-trading-day responses use exact Gateway fields.
@responses.activate
def test_rwa_trading_calendar_endpoints():
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/public/trading-hours",
        match=[
            responses.matchers.query_param_matcher(
                {"market": "US", "timestamp": "1785247200000"}
            )
        ],
        json={
            "code": 0,
            "data": {
                "market": "US",
                "timezone": "America/New_York",
                "referenceTimestamp": 1785247200000,
                "weekStartDate": "2026-07-27",
                "weekEndDate": "2026-08-02",
                "currentSession": "REGULAR",
                "holidayCalendarYear": 2026,
                "tradingHours": [
                    {
                        "tradingDate": "2026-07-27",
                        "isHoliday": False,
                        "sessions": [
                            {
                                "session": "REGULAR",
                                "startTimestamp": 1785245400000,
                                "endTimestamp": 1785268800000,
                            }
                        ],
                    }
                ],
            },
        },
    )
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/public/next-trading-day",
        match=[
            responses.matchers.query_param_matcher(
                {
                    "type": "vault",
                    "coin": "CXMT",
                    "timestamp": "1785211200000",
                }
            )
        ],
        json={
            "code": 0,
            "data": {
                "coin": "CXMT",
                "market": "CN",
                "tradingDate": "2026-07-29",
                "tradingStartTimestamp": 1785285000000,
            },
        },
    )

    client = _client()
    hours = client.get_trading_hours("US", 1785247200000)
    next_day = client.get_next_trading_day("CXMT", 1785211200000)

    assert hours.current_session == "REGULAR"
    assert hours.trading_hours[0].sessions[0].session == "REGULAR"
    assert next_day.market == "CN"
    assert next_day.trading_start_timestamp == 1785285000000


# Validates Gateway's latest error field is surfaced unchanged as APIError.message.
@responses.activate
def test_gateway_business_error_message_is_preserved():
    responses.add(
        responses.GET,
        f"{_BASE_URL}/api/v1/status",
        json={"code": -1, "error": "upstream engine rejected request"},
    )

    with pytest.raises(APIError) as exc_info:
        _client().get_system_status()

    assert exc_info.value.message == "upstream engine rejected request"
