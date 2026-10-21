"""Interacting with UPS."""

import time
import uuid
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import niquests

from .models import Token, TrackApiResponse, TrackResponse

PRODUCTION_URL = "https://onlinetools.ups.com"
#: UPS's Customer Integration Environment, which answers test data for test numbers.
TESTING_URL = "https://wwwcie.ups.com"

RETRY_ATTEMPTS = 3
DEFAULT_RETRY_AFTER_SECONDS = 5.0
MAX_RETRY_AFTER_SECONDS = 60.0
#: How long before it expires a token is replaced, so a request never goes out on one about to lapse.
TOKEN_REFRESH_MARGIN_SECONDS = 60.0


def _retry_after_seconds(response: niquests.Response) -> float:
    """Determine how long to sleep before retrying a rate-limited request.

    Parameters
    ----------
    response
        The 429 response, whose ``Retry-After`` header is honored in either RFC 9110
        form -- delta-seconds or an HTTP-date -- capped at ``MAX_RETRY_AFTER_SECONDS``.
    """
    header = response.headers.get("Retry-After")
    if header is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        seconds = float(header)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(header) - datetime.now(tz=UTC)).total_seconds()
        except TypeError, ValueError:
            return DEFAULT_RETRY_AFTER_SECONDS
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


class UPSClient:
    """A class wrapping UPS's REST APIs, signed in with an app's OAuth client credentials."""

    def __init__(  # noqa: PLR0913 - each is a separate setting of the app or the connection
        self,
        client_id: str,
        client_secret: str,
        *,
        base_url: str = PRODUCTION_URL,
        timeout: float = 60.0,
        merchant_id: str | None = None,
        transaction_source: str = "tailstory-ups",
    ) -> None:
        """Initialize the UPSClient class.

        Parameters
        ----------
        client_id
            The app's client ID, from its page on developer.ups.com.
        client_secret
            The app's client secret.
        base_url
            The API host: :data:`PRODUCTION_URL`, or :data:`TESTING_URL` for UPS's test environment.
        timeout
            The request timeout in seconds.
        merchant_id
            The 6-character UPS account number to sign in for, when the app serves several.
        transaction_source
            What UPS is told is calling, sent as the ``transactionSrc`` header.
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.merchant_id = merchant_id
        self.transaction_source = transaction_source
        self.session = niquests.Session(base_url=base_url, timeout=timeout)
        self._token: str | None = None
        self._token_expires_at = 0.0

    def fetch_token(self) -> Token:
        """Get a new access token with the client credentials.

        Raises
        ------
        niquests.HTTPError
            If UPS refused the credentials.
        """
        headers = {} if self.merchant_id is None else {"x-merchant-id": self.merchant_id}
        response = self.session.post(
            "/security/v1/oauth/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            headers=headers,
        )
        response.raise_for_status()
        token = Token.model_validate(response.json())
        self._token = token.access_token
        self._token_expires_at = time.monotonic() + token.expires_in - TOKEN_REFRESH_MARGIN_SECONDS
        return token

    def _access_token(self) -> str:
        """Answer the current token, getting a new one when there is none or it is about to expire."""
        if self._token is None or time.monotonic() >= self._token_expires_at:
            self.fetch_token()
        return str(self._token)

    def make_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> niquests.Response:
        """Make a signed-in request to UPS.

        A ``401`` gets one retry on a new token, since UPS can revoke a token before it expires.
        A ``429`` is retried up to ``RETRY_ATTEMPTS`` total attempts, sleeping the response's
        ``Retry-After`` (``DEFAULT_RETRY_AFTER_SECONDS`` when absent).
        """
        args: dict[str, Any] = {"url": path, "method": method}
        if params is not None:
            args["params"] = params
        if json is not None:
            args["json"] = json

        def send() -> niquests.Response:
            headers = {
                "Authorization": f"Bearer {self._access_token()}",
                "transId": uuid.uuid4().hex,
                "transactionSrc": self.transaction_source,
            }
            return self.session.request(**args, headers=headers)

        response = send()
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            self._token = None
            response = send()
        for _attempt in range(RETRY_ATTEMPTS - 1):
            if response.status_code != HTTPStatus.TOO_MANY_REQUESTS:
                break
            time.sleep(_retry_after_seconds(response))
            response = send()
        return response

    def get_tracking(
        self,
        tracking_number: str,
        *,
        locale: str = "en_US",
        milestones: bool = False,
    ) -> TrackResponse:
        """Get a package's tracking: its current status, delivery dates and activity.

        Parameters
        ----------
        tracking_number
            The UPS tracking number (an inquiry number of 7 to 34 characters).
        locale
            The language and country UPS describes statuses in.
        milestones
            Whether to ask for the package's milestones as well.

        Raises
        ------
        niquests.HTTPError
            If UPS refused the request, including a ``404`` for a number it does not know.
        """
        response = self.make_request(
            "GET",
            f"/api/track/v1/details/{quote(tracking_number, safe='')}",
            params={"locale": locale, "returnMilestones": str(milestones).lower()},
        )
        response.raise_for_status()
        return TrackApiResponse.model_validate(response.json()).track_response
