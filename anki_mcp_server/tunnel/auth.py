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
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx

from ..credentials import Credentials
from .protocol import parse_tunnel_server_url
from .proxy import (
    PROXY_FIX_HINT,
    PROXY_UNSUPPORTED,
    ProxyRoute,
    ProxySetting,
    TunnelProxyError,
    mask_url_credentials,
    resolve_route,
)
from .tls import (
    TLSContextError,
    certificate_failure_message,
    create_tls_context,
    find_certificate_error,
)

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


class ProxyUnusableError(AuthError):
    """The ``tunnel_proxy`` setting (or the system proxy) cannot be used.

    A local condition, never something a server reports: callers branch on
    this class, not on ``error_code``, which shares a namespace with the
    OAuth codes a server may send. ``error_code`` is still ``"proxy_unsupported"``.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, error_code=PROXY_UNSUPPORTED)


class CertificateVerificationError(AuthError):
    """A TLS certificate could not be verified (possibly not the server's own).

    A local condition, never something a server reports: callers branch on this
    class, and ``error_code`` stays ``None``. ``str()`` is the full user-facing
    message, free of credentials.
    """


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

def _str_field(data: dict[str, Any], key: str) -> str:
    """``data[key]``, which must be a string (``KeyError``/``TypeError`` otherwise)."""
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"{key!r} must be a string, got {type(value).__name__}")
    return value


def _malformed_detail(exc: Exception) -> str:
    """Error text for a malformed response field, safe to show and log.

    ``int()`` puts the offending server-supplied value into its ``ValueError``
    text, so that one is reported by type name only. The other exceptions name
    a key or a type, never a value.
    """
    return type(exc).__name__ if isinstance(exc, ValueError) else str(exc)


def _new_http_client(route: ProxyRoute) -> httpx.AsyncClient:
    """Construct the auth HTTP client for ``route``, verifying via :func:`create_tls_context`.

    The route is decided by :func:`resolve_route`, never by httpx: ``trust_env``
    is off for a direct route and an explicit ``proxy=`` replaces the
    environment lookup for a proxied one.

    Raises:
        TLSContextError: If the TLS context can't be built.
        ProxyUnusableError: If httpx rejects the proxy address
            (``InvalidURL``). ``UnicodeError`` is caught as a safeguard only:
            proxy.py rejects non-ASCII hosts first. The route only ever
            carries http(s) proxies, so these are the construction failures
            left (``ValueError`` for a scheme and ``ImportError`` for socksio
            need a SOCKS proxy).
    """
    ssl_context = create_tls_context()
    try:
        if route.proxy_url is None:
            return httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT, verify=ssl_context, trust_env=False
            )
        proxy: str | httpx.Proxy = route.proxy_url
        if route.uses_https_proxy:
            proxy = httpx.Proxy(route.proxy_url, ssl_context=create_tls_context())
        return httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT, verify=ssl_context, proxy=proxy
        )
    except (httpx.InvalidURL, UnicodeError):
        raise ProxyUnusableError(
            f"The proxy address was rejected by the HTTP client. {PROXY_FIX_HINT}"
        ) from None


def _raise_if_certificate_failure(
    exc: BaseException, url: str, route: ProxyRoute
) -> None:
    """Raise :class:`CertificateVerificationError` when ``exc`` wraps one.

    Chained from the ssl error alone: the wrapping httpx errors may quote the
    proxy URL.
    """
    cert_error = find_certificate_error(exc)
    if cert_error is None:
        return
    raise CertificateVerificationError(
        certificate_failure_message(
            cert_error,
            host=urlsplit(url).hostname,
            proxy_route=route.proxied_display(),
        )
    ) from cert_error


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

        user = data["user"]
        if not isinstance(user, dict):
            raise TypeError(f"'user' must be an object, got {type(user).__name__}")

        return Credentials(
            access_token=_str_field(data, "access_token"),
            refresh_token=_str_field(data, "refresh_token"),
            expires_at=expires_at.isoformat(),
            user=user,
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise AuthError(
            f"Malformed token response: {_malformed_detail(exc)}",
            error_code="invalid_response",
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
            proxy=AUTO_PROXY,
        )

        # Step 1: Request a device code
        device = await auth.request_device_code()
        print(f"Go to {device.verification_uri} and enter: {device.user_code}")

        # Step 2: Poll until the user completes auth
        credentials = await auth.poll_for_token(
            device.device_code, device.interval, device.expires_in
        )

        # Step 3: Later, refresh the token
        new_credentials = await auth.refresh_token(credentials.refresh_token)
    """

    def __init__(
        self, server_url: str, client_id: str, *, proxy: ProxySetting
    ) -> None:
        """Initialize the auth client.

        Args:
            server_url: Tunnel server WebSocket URL
                (e.g. ``wss://tunnel.ankimcp.ai``).
            client_id: OAuth client identifier
                (e.g. ``ankimcp-cli``).
            proxy: The ``tunnel_proxy`` setting. The route is resolved again
                for every request, so a VPN toggled meanwhile applies to the
                next one.

        Raises:
            ValueError: If ``server_url`` is not a valid relay URL (see
                :func:`parse_tunnel_server_url`); the message is the reason.
        """
        parsed = parse_tunnel_server_url(server_url)
        if isinstance(parsed, str):
            raise ValueError(parsed)
        self._device_endpoint = f"{parsed.http_url}/auth/device"
        self._token_endpoint = f"{parsed.http_url}/auth/token"
        self._client_id = client_id
        self._proxy = proxy

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
            form_data={
                "client_id": self._client_id,
                # offline_access -> Keycloak issues an OFFLINE refresh token
                # (long-lived, survives logout). openid is included for verbatim
                # parity with the TS client even though it's functionally inert
                # here. Must match device-flow.service.ts exactly.
                "scope": "openid offline_access",
            },
        )

        try:
            verification_uri_complete = data.get("verification_uri_complete")
            if verification_uri_complete is not None and not isinstance(
                verification_uri_complete, str
            ):
                raise TypeError("'verification_uri_complete' must be a string")
            return DeviceCodeResponse(
                device_code=_str_field(data, "device_code"),
                user_code=_str_field(data, "user_code"),
                verification_uri=_str_field(data, "verification_uri"),
                verification_uri_complete=verification_uri_complete,
                expires_in=int(data["expires_in"]),
                interval=int(data.get("interval", 5)),
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise AuthError(
                f"Malformed device code response: {_malformed_detail(exc)}",
                error_code="invalid_response",
            ) from exc

    # ------------------------------------------------------------------
    # poll_for_token
    # ------------------------------------------------------------------

    async def poll_for_token(
        self, device_code: str, interval: int, expires_in: int
    ) -> Credentials:
        """Poll the token endpoint until the user completes authorization.

        This is a long-running loop that sleeps between polls. It uses
        ``anyio.sleep()`` so it can be cancelled cleanly via task group
        cancellation or ``asyncio.Task.cancel()``.

        The loop is self-bounded by ``expires_in``: once that many seconds
        have elapsed (measured against a monotonic clock) it gives up with
        an ``expired_token`` :class:`AuthError`, mirroring the server-side
        ``expired_token`` case so the caller handles both identically.

        Args:
            device_code: The ``device_code`` from :meth:`request_device_code`.
            interval: Initial polling interval in seconds.
            expires_in: Lifetime of ``device_code`` in seconds; the poll loop
                stops once this elapses.

        Returns:
            OAuth credentials on successful authorization.

        Raises:
            AuthError: On terminal errors (``expired_token``, ``access_denied``,
                or unexpected error codes).
        """
        poll_interval = interval

        # Monotonic deadline — immune to wall-clock changes (NTP, DST, manual
        # adjustment) during the flow.
        deadline = time.monotonic() + expires_in

        logger.debug(
            "Starting token poll (interval=%ds, expires_in=%ds, endpoint=%s)",
            poll_interval,
            expires_in,
            self._token_endpoint,
        )

        while time.monotonic() < deadline:
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

        # Deadline reached without the server returning expired_token first.
        # Surface the same terminal error as the server-side expired_token
        # case so the caller handles both paths identically.
        logger.debug("Polling deadline reached (%ds), giving up", expires_in)
        raise AuthError(
            "Device code expired before authorization. Please restart login.",
            error_code="expired_token",
        )

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

        Handles HTTP errors and OAuth error responses uniformly. Whatever the
        server sends, only :class:`AuthError` leaves this method.

        Args:
            url: Full endpoint URL.
            form_data: Key-value pairs to send as
                ``application/x-www-form-urlencoded``.

        Returns:
            Parsed JSON response body as a dict.

        Raises:
            AuthError: On HTTP errors, non-JSON or non-object responses, or
                OAuth error responses (a non-2xx status). ``error_code`` is
                the response's ``error`` field when it is a string, else None.
                A proxy setting that cannot be used raises the subclass
                :class:`ProxyUnusableError` (see :func:`resolve_route`).
        """
        try:
            route = resolve_route(self._proxy, url)
        except TunnelProxyError as exc:
            raise ProxyUnusableError(str(exc)) from exc
        logger.debug("Auth request route: %s", route.display())

        try:
            async with _new_http_client(route) as client:
                response = await client.post(url, data=form_data)
        except TLSContextError as exc:
            raise AuthError(str(exc)) from exc
        except (httpx.InvalidURL, UnicodeError) as exc:
            raise AuthError(f"Invalid auth URL {url!r}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise AuthError(f"Request timed out: {url}") from exc
        except httpx.HTTPError as exc:
            _raise_if_certificate_failure(exc, url, route)
            raise AuthError(
                f"HTTP request failed: {mask_url_credentials(str(exc))}"
            ) from exc
        except OSError as exc:
            _raise_if_certificate_failure(exc, url, route)
            raise AuthError(
                f"HTTP request failed: {type(exc).__name__}: "
                f"{mask_url_credentials(str(exc))}"
            ) from exc

        # Parse JSON body. A 2xx body may carry tokens, so it is never quoted.
        try:
            data = response.json()
        except (ValueError, TypeError, RecursionError) as exc:
            detail = "" if response.is_success else f": {response.text[:200]}"
            raise AuthError(
                f"Non-JSON response (HTTP {response.status_code}){detail}",
            ) from exc

        if not isinstance(data, dict):
            detail = (
                f": {type(data).__name__}"
                if response.is_success
                else f": {response.text[:200]}"
            )
            raise AuthError(
                f"Non-object JSON response (HTTP {response.status_code}){detail}",
            )

        # Check for OAuth error response (HTTP 400/401 with error field).
        if not response.is_success:
            raw_code = data.get("error", "unknown_error")
            error_code = raw_code if isinstance(raw_code, str) else None
            raw_desc = data.get("error_description")
            error_desc = (
                raw_desc if isinstance(raw_desc, str)
                else f"HTTP {response.status_code}"
            )
            logger.debug(
                "Auth error: code=%s, description=%s", error_code, error_desc
            )
            raise AuthError(error_desc, error_code=error_code)

        return data
