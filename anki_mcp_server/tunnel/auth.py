"""Async OAuth 2.0 Device Authorization Grant client for AnkiMCP tunnel.

Handles the device flow against auth endpoints proxied through the tunnel
server. This module is responsible ONLY for HTTP auth calls — no WebSocket
logic, no credentials file I/O. The caller is responsible for persisting
the returned ``Credentials``.

Auth endpoints are derived from the tunnel WebSocket URL by replacing
the scheme (``wss:`` -> ``https:``, ``ws:`` -> ``http:``).

Uses httpx (vendored) for async HTTP and anyio (vendored) for
cancellation-safe sleep.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

import anyio
import httpx

from ..credentials import Credentials

logger = logging.getLogger(__name__)

# Timeout for individual HTTP requests to auth endpoints.
_HTTP_TIMEOUT = 15.0  # seconds


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------

class AuthError(Exception):
    """Raised when an auth operation fails.

    Attributes:
        error_code: OAuth error code from the server (e.g. ``"invalid_grant"``,
            ``"expired_token"``), or ``None`` for transport-level failures.
    """

    def __init__(self, message: str, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


# --------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------

@dataclass
class DeviceCodeResponse:
    """Successful response from the device authorization endpoint.

    Attributes:
        device_code: Opaque code used to poll for the token.
        user_code: Short code the user enters on the verification page.
        verification_uri: URL where the user authenticates.
        verification_uri_complete: Pre-filled URL (includes user_code), if
            provided by the server. ``None`` otherwise.
        expires_in: Seconds until ``device_code`` expires.
        interval: Minimum polling interval in seconds.
    """

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int
    interval: int


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _ws_url_to_http(ws_url: str) -> str:
    """Convert a WebSocket URL to its HTTP equivalent.

    ``wss://host/path`` becomes ``https://host/path``,
    ``ws://host/path`` becomes ``http://host/path``.

    Raises:
        ValueError: If the URL scheme is not ``ws`` or ``wss``.
    """
    parsed = urlparse(ws_url)
    match parsed.scheme:
        case "wss":
            http_scheme = "https"
        case "ws":
            http_scheme = "http"
        case _:
            raise ValueError(
                f"Expected ws:// or wss:// URL, got {parsed.scheme!r}"
            )

    return urlunparse(parsed._replace(scheme=http_scheme))


def _build_credentials(data: dict[str, Any]) -> Credentials:
    """Build a ``Credentials`` instance from a token endpoint response.

    The ``expires_at`` timestamp is calculated client-side from the
    ``expires_in`` field, because the server response only includes the
    relative TTL.

    Args:
        data: Parsed JSON body from the token endpoint.

    Returns:
        Populated ``Credentials`` dataclass.

    Raises:
        AuthError: If required fields are missing from the response.
    """
    try:
        expires_in = int(data["expires_in"])
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        return Credentials(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=expires_at.isoformat(),
            user=data["user"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError(
            f"Malformed token response: {exc}", error_code="invalid_response"
        ) from exc


# --------------------------------------------------------------------------
# Device Flow Auth Client
# --------------------------------------------------------------------------

class DeviceFlowAuth:
    """Async client for OAuth 2.0 Device Authorization Grant.

    Talks to auth endpoints proxied through the tunnel server. All methods
    are async and designed to be called from an ``anyio``/``asyncio``
    event loop.

    Example::

        auth = DeviceFlowAuth(
            server_url="wss://tunnel.ankimcp.ai",
            client_id="ankimcp-cli",
        )

        # Step 1: Request a device code
        device = await auth.request_device_code()
        print(f"Go to {device.verification_uri} and enter: {device.user_code}")

        # Step 2: Poll until the user completes auth
        credentials = await auth.poll_for_token(
            device.device_code, device.interval
        )

        # Step 3: Later, refresh the token
        new_credentials = await auth.refresh_token(credentials.refresh_token)
    """

    def __init__(self, server_url: str, client_id: str) -> None:
        """Initialize the auth client.

        Args:
            server_url: Tunnel server WebSocket URL
                (e.g. ``wss://tunnel.ankimcp.ai``).
            client_id: OAuth client identifier
                (e.g. ``ankimcp-cli``).

        Raises:
            ValueError: If ``server_url`` is not a ws:// or wss:// URL.
        """
        http_url = _ws_url_to_http(server_url)
        self._device_endpoint = f"{http_url}/auth/device"
        self._token_endpoint = f"{http_url}/auth/token"
        self._client_id = client_id

        logger.debug(
            "DeviceFlowAuth initialized: device=%s, token=%s, client_id=%s",
            self._device_endpoint,
            self._token_endpoint,
            self._client_id,
        )

    # ------------------------------------------------------------------
    # request_device_code
    # ------------------------------------------------------------------

    async def request_device_code(self) -> DeviceCodeResponse:
        """Start the device authorization flow.

        POSTs to ``/auth/device`` and returns the device code that the
        user must enter at the verification URL.

        Returns:
            Device code response with user instructions.

        Raises:
            AuthError: On HTTP errors or malformed responses.
        """
        logger.debug("Requesting device code from %s", self._device_endpoint)

        data = await self._post(
            self._device_endpoint,
            form_data={"client_id": self._client_id},
        )

        try:
            return DeviceCodeResponse(
                device_code=data["device_code"],
                user_code=data["user_code"],
                verification_uri=data["verification_uri"],
                verification_uri_complete=data.get("verification_uri_complete"),
                expires_in=int(data["expires_in"]),
                interval=int(data.get("interval", 5)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthError(
                f"Malformed device code response: {exc}",
                error_code="invalid_response",
            ) from exc

    # ------------------------------------------------------------------
    # poll_for_token
    # ------------------------------------------------------------------

    async def poll_for_token(
        self, device_code: str, interval: int
    ) -> Credentials:
        """Poll the token endpoint until the user completes authorization.

        This is a long-running loop that sleeps between polls. It uses
        ``anyio.sleep()`` so it can be cancelled cleanly via task group
        cancellation or ``asyncio.Task.cancel()``.

        Args:
            device_code: The ``device_code`` from :meth:`request_device_code`.
            interval: Initial polling interval in seconds.

        Returns:
            OAuth credentials on successful authorization.

        Raises:
            AuthError: On terminal errors (``expired_token``, ``access_denied``,
                or unexpected error codes).
        """
        poll_interval = interval

        logger.debug(
            "Starting token poll (interval=%ds, endpoint=%s)",
            poll_interval,
            self._token_endpoint,
        )

        while True:
            # Sleep first — the spec says to wait before the initial poll.
            # anyio.sleep is cancellation-safe (raises Cancelled on cancel).
            await anyio.sleep(poll_interval)

            form_data = {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": self._client_id,
            }

            try:
                data = await self._post(self._token_endpoint, form_data=form_data)
            except AuthError as exc:
                match exc.error_code:
                    case "authorization_pending":
                        # User hasn't authorized yet — keep polling.
                        logger.debug("Authorization pending, polling again...")
                        continue

                    case "slow_down":
                        # Server asks us to back off.
                        poll_interval += 5
                        logger.debug(
                            "Slow down requested, new interval=%ds",
                            poll_interval,
                        )
                        continue

                    case _:
                        # Terminal error — propagate to caller.
                        raise

            # Success — build and return credentials.
            logger.debug("Token poll succeeded, building credentials")
            return _build_credentials(data)

    # ------------------------------------------------------------------
    # refresh_token
    # ------------------------------------------------------------------

    async def refresh_token(self, refresh_token_str: str) -> Credentials:
        """Exchange a refresh token for new credentials.

        Args:
            refresh_token_str: The refresh token from a previous auth flow.

        Returns:
            Fresh OAuth credentials.

        Raises:
            AuthError: On failure, including ``invalid_grant`` which means
                the refresh token was revoked.
        """
        logger.debug("Refreshing token via %s", self._token_endpoint)

        data = await self._post(
            self._token_endpoint,
            form_data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token_str,
                "client_id": self._client_id,
            },
        )

        logger.debug("Token refresh succeeded")
        return _build_credentials(data)

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    async def _post(
        self, url: str, *, form_data: dict[str, str]
    ) -> dict[str, Any]:
        """POST form-encoded data and return parsed JSON.

        Handles HTTP errors and OAuth error responses uniformly.

        Args:
            url: Full endpoint URL.
            form_data: Key-value pairs to send as
                ``application/x-www-form-urlencoded``.

        Returns:
            Parsed JSON response body as a dict.

        Raises:
            AuthError: On HTTP errors, non-JSON responses, or OAuth error
                responses (those containing an ``error`` field).
        """
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.post(url, data=form_data)
        except httpx.TimeoutException as exc:
            raise AuthError(f"Request timed out: {url}") from exc
        except httpx.HTTPError as exc:
            raise AuthError(f"HTTP request failed: {exc}") from exc

        # Parse JSON body.
        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            raise AuthError(
                f"Non-JSON response (HTTP {response.status_code}): "
                f"{response.text[:200]}",
            ) from exc

        # Check for OAuth error response (HTTP 400/401 with error field).
        if not response.is_success:
            error_code = data.get("error", "unknown_error")
            error_desc = data.get(
                "error_description", f"HTTP {response.status_code}"
            )
            logger.debug(
                "Auth error: code=%s, description=%s", error_code, error_desc
            )
            raise AuthError(error_desc, error_code=error_code)

        return data
