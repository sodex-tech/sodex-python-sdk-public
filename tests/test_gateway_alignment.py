"""Gateway user-status coverage used by the trading example."""

import responses

from sodex.client import Client, Config


_BASE_URL = "https://gateway.example"
_USER = "0x1111111111111111111111111111111111111111"


# Validates the trading example preserves a full uint64 user ID and handles an unregistered wallet.
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

    client = Client(Config(base_url=_BASE_URL))
    active = client.get_user_status(_USER)
    missing = client.get_user_status(_USER)

    assert active.status == "Active"
    assert active.user_id == 18446744073709551615
    assert missing.status == "UserNotFound"
    assert missing.user_id == 0
