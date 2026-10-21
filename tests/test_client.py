"""Tests for the UPS client. No call leaves the machine; numbers and names are invented."""

import json
from datetime import UTC, date, datetime, time, timedelta, timezone
from email.utils import format_datetime
from http import HTTPStatus
from typing import Any

import pytest
from niquests import HTTPError, Response

from ups_sdk import UPSClient
from ups_sdk.client import (
    DEFAULT_RETRY_AFTER_SECONDS,
    MAX_RETRY_AFTER_SECONDS,
    RETRY_ATTEMPTS,
    TOKEN_REFRESH_MARGIN_SECONDS,
    _retry_after_seconds,
)

TRACKING_NUMBER = "1Z999AA10123456784"

TOKEN: dict[str, Any] = {"access_token": "token-one", "token_type": "Bearer", "expires_in": "14399"}

TRACKED: dict[str, Any] = {
    "trackResponse": {
        "shipment": [
            {
                "inquiryNumber": TRACKING_NUMBER,
                "package": [
                    {
                        "trackingNumber": TRACKING_NUMBER,
                        "currentStatus": {"type": "I", "description": "On the Way", "code": "OR"},
                        "deliveryDate": [{"type": "SDD", "date": "20261024"}],
                        "deliveryTime": {"type": "EOD", "endTime": "235900"},
                        "activity": [
                            {
                                "location": {"address": {"city": "Springfield", "stateProvince": "IL"}},
                                "status": {"type": "I", "description": "Departed from Facility", "code": "DP"},
                                "date": "20261021",
                                "time": "043000",
                                "gmtOffset": "-05:00",
                            },
                        ],
                        "packageCount": 1,
                    },
                ],
            },
        ],
    },
}


def _response(
    status_code: int,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """Build a niquests Response without touching the network."""
    response = Response()
    response.status_code = status_code
    response._content = json.dumps(body or {}).encode()  # noqa: SLF001 -- no public way to seed a canned body
    response.headers.update(headers or {})
    return response


class _SessionRecorder:
    """Stand-in for ``Session.request`` serving canned responses and recording calls."""

    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:  # noqa: ANN401 -- mirrors Session.request
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def _record(client: UPSClient, monkeypatch: pytest.MonkeyPatch, responses: list[Response]) -> _SessionRecorder:
    """Route the client's session requests, token requests included, through a recorder."""
    recorder = _SessionRecorder(responses)
    monkeypatch.setattr(client.session, "request", recorder.request)
    return recorder


def _client() -> UPSClient:
    return UPSClient("client-id", "client-secret")


def test_tracking_signs_in_with_the_client_credentials_then_reads_the_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first call gets a token with the client credentials, then reads the number with it."""
    client = _client()
    recorder = _record(client, monkeypatch, [_response(HTTPStatus.OK, TOKEN), _response(HTTPStatus.OK, TRACKED)])

    client.get_tracking(TRACKING_NUMBER)

    token_call, track_call = recorder.calls
    assert token_call["method"] == "POST"
    assert token_call["url"] == "/security/v1/oauth/token"
    assert token_call["data"] == {"grant_type": "client_credentials"}
    assert token_call["auth"] == ("client-id", "client-secret")
    assert track_call["method"] == "GET"
    assert track_call["url"] == f"/api/track/v1/details/{TRACKING_NUMBER}"
    assert track_call["params"] == {"locale": "en_US", "returnMilestones": "false"}
    assert track_call["headers"]["Authorization"] == "Bearer token-one"
    assert track_call["headers"]["transId"]
    assert track_call["headers"]["transactionSrc"]


def test_the_token_is_reused_until_it_is_about_to_expire(monkeypatch: pytest.MonkeyPatch) -> None:
    """One token serves every call until it is within the refresh margin of expiring."""
    clock = [1000.0]
    monkeypatch.setattr("ups_sdk.client.time.monotonic", lambda: clock[0])
    client = _client()
    second = {**TOKEN, "access_token": "token-two"}
    recorder = _record(
        client,
        monkeypatch,
        [
            _response(HTTPStatus.OK, TOKEN),
            _response(HTTPStatus.OK, TRACKED),
            _response(HTTPStatus.OK, TRACKED),
            _response(HTTPStatus.OK, second),
            _response(HTTPStatus.OK, TRACKED),
        ],
    )

    client.get_tracking(TRACKING_NUMBER)
    client.get_tracking(TRACKING_NUMBER)
    clock[0] += float(TOKEN["expires_in"]) - TOKEN_REFRESH_MARGIN_SECONDS
    client.get_tracking(TRACKING_NUMBER)

    urls = [call["url"] for call in recorder.calls]
    assert urls.count("/security/v1/oauth/token") == 2
    assert recorder.calls[-1]["headers"]["Authorization"] == "Bearer token-two"


def test_a_refused_token_is_replaced_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 on a request gets one retry on a new token."""
    client = _client()
    second = {**TOKEN, "access_token": "token-two"}
    recorder = _record(
        client,
        monkeypatch,
        [
            _response(HTTPStatus.OK, TOKEN),
            _response(HTTPStatus.UNAUTHORIZED),
            _response(HTTPStatus.OK, second),
            _response(HTTPStatus.OK, TRACKED),
        ],
    )

    client.get_tracking(TRACKING_NUMBER)

    assert recorder.calls[-1]["headers"]["Authorization"] == "Bearer token-two"


def test_refused_credentials_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """UPS refusing the client credentials raises."""
    client = _client()
    _record(client, monkeypatch, [_response(HTTPStatus.UNAUTHORIZED)])

    with pytest.raises(HTTPError):
        client.get_tracking(TRACKING_NUMBER)


def test_a_rate_limited_request_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 is retried after the Retry-After it names."""
    slept: list[float] = []
    monkeypatch.setattr("ups_sdk.client.time.sleep", slept.append)
    client = _client()
    _record(
        client,
        monkeypatch,
        [
            _response(HTTPStatus.OK, TOKEN),
            _response(HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "2"}),
            _response(HTTPStatus.OK, TRACKED),
        ],
    )

    client.get_tracking(TRACKING_NUMBER)

    assert slept == [2.0]


def test_rate_limiting_gives_up_after_the_last_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request still rate-limited on its last attempt raises."""
    monkeypatch.setattr("ups_sdk.client.time.sleep", lambda _seconds: None)
    client = _client()
    limited = [_response(HTTPStatus.TOO_MANY_REQUESTS) for _ in range(RETRY_ATTEMPTS)]
    _record(client, monkeypatch, [_response(HTTPStatus.OK, TOKEN), *limited])

    with pytest.raises(HTTPError):
        client.get_tracking(TRACKING_NUMBER)


def test_a_number_ups_does_not_know_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """UPS's 404 for an unknown number raises."""
    client = _client()
    _record(client, monkeypatch, [_response(HTTPStatus.OK, TOKEN), _response(HTTPStatus.NOT_FOUND)])

    with pytest.raises(HTTPError):
        client.get_tracking(TRACKING_NUMBER)


def test_the_tracking_number_is_quoted_into_the_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A number is quoted into the path, so it cannot reach another route."""
    client = _client()
    recorder = _record(client, monkeypatch, [_response(HTTPStatus.OK, TOKEN), _response(HTTPStatus.OK, TRACKED)])

    client.get_tracking("1Z/../x", milestones=True)

    assert recorder.calls[1]["url"] == "/api/track/v1/details/1Z%2F..%2Fx"
    assert recorder.calls[1]["params"]["returnMilestones"] == "true"


def test_a_merchant_id_is_sent_with_the_token_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A merchant id rides on the token request."""
    client = UPSClient("client-id", "client-secret", merchant_id="A1B2C3")
    recorder = _record(client, monkeypatch, [_response(HTTPStatus.OK, TOKEN)])

    client.fetch_token()

    assert recorder.calls[0]["headers"] == {"x-merchant-id": "A1B2C3"}


def test_tracking_reads_status_dates_and_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The status, delivery dates and activity are read from UPS's answer."""
    client = _client()
    _record(client, monkeypatch, [_response(HTTPStatus.OK, TOKEN), _response(HTTPStatus.OK, TRACKED)])

    tracked = client.get_tracking(TRACKING_NUMBER)

    (package,) = tracked.shipment[0].package
    assert package.current_status is not None
    assert package.current_status.type == "I"
    assert [(row.type, row.date) for row in package.delivery_date] == [("SDD", date(2026, 10, 24))]
    assert package.delivery_time is not None
    assert package.delivery_time.end_time == time(23, 59)
    (activity,) = package.activity
    assert activity.occurred_at == datetime(2026, 10, 21, 4, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert activity.location is not None
    assert activity.location.address is not None
    assert activity.location.address.city == "Springfield"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, DEFAULT_RETRY_AFTER_SECONDS),
        ({"Retry-After": "3"}, 3.0),
        ({"Retry-After": "-4"}, 0.0),
        ({"Retry-After": "9999"}, MAX_RETRY_AFTER_SECONDS),
        ({"Retry-After": "soon"}, DEFAULT_RETRY_AFTER_SECONDS),
    ],
)
def test_retry_after_reads_seconds(headers: dict[str, str], expected: float) -> None:
    """Retry-After seconds are read and clamped."""
    assert _retry_after_seconds(_response(HTTPStatus.TOO_MANY_REQUESTS, headers=headers)) == expected


def test_retry_after_reads_an_http_date() -> None:
    """A Retry-After HTTP date is read as the seconds until it."""
    when = format_datetime(datetime.now(tz=UTC) + timedelta(seconds=30), usegmt=True)

    seconds = _retry_after_seconds(_response(HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": when}))

    assert 0.0 < seconds <= 30.0
