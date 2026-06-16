"""Optional shared API-key auth for the HTTP transport (AnkiConnect-style).

This module adds an OPTIONAL second auth layer for the local HTTP transport.
When the operator sets ``Config.http_api_key`` to a non-empty value, every HTTP
request must carry an ``Authorization: Bearer <key>`` header matching that value
(compared in constant time). An empty key disables the layer entirely, so the
default behavior is unchanged.

The pure ``is_authorized`` helper is intentionally side-effect free and free of
any aqt / Qt imports so it can be unit-tested without a running Anki. This layer
is independent of, and orthogonal to, the DNS-rebinding protection in
``transport_security_config.py`` and the tunnel's own OAuth.

Design notes:
- ``ApiKeyAuthMiddleware`` is a RAW ASGI middleware operating directly on
  ``(scope, receive, send)``. It deliberately does NOT use Starlette's
  ``BaseHTTPMiddleware`` — that base class buffers the full response, which
  breaks the MCP app's Server-Sent Events (``text/event-stream``) streaming.
- On auth failure it returns ``403 Forbidden`` with a tiny JSON body and NO
  ``WWW-Authenticate`` header. A ``401``/``WWW-Authenticate`` response would
  make MCP clients launch an OAuth 2.1 discovery/registration flow against the
  addon and fail confusingly, so ``403`` (with no challenge header) is required.
- ALL http methods (including ``OPTIONS``) require the key. Genuine CORS
  preflight is handled by the outer ``CORSMiddleware`` (applied OUTERMOST in
  ``_run_http_mode``), which short-circuits real preflights before they ever
  reach this layer; when CORS is disabled a browser preflight fails regardless,
  so there is nothing for an unauthenticated ``OPTIONS`` bypass to enable here
  except path-existence / allowed-methods disclosure. Hence no bypass.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:  # avoid an import cycle / aqt at runtime
    from .config import Config

# Minimal ASGI type aliases (kept local to avoid importing starlette types here).
Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def is_authorized(authorization_header: str | None, api_key: str) -> bool:
    """Return True iff ``authorization_header`` presents the expected API key.

    Parses a ``Bearer <token>`` value from the header (scheme matched
    case-insensitively, surrounding whitespace tolerated) and compares the
    presented token to ``api_key`` using :func:`hmac.compare_digest` for a
    constant-time comparison (never ``==``).

    A missing or malformed header returns False. This function does not decide
    whether auth is enabled — callers gate on a non-empty ``api_key`` before
    applying the middleware.

    Args:
        authorization_header: Raw value of the HTTP ``Authorization`` header,
            or ``None`` if absent.
        api_key: The expected shared key (assumed non-empty by the caller).

    Returns:
        True if the header carries a Bearer token equal to ``api_key``.

    Examples:
        >>> is_authorized("Bearer secret", "secret")
        True
        >>> is_authorized("bearer  secret  ", "secret")
        True
        >>> is_authorized("Basic secret", "secret")
        False
        >>> is_authorized(None, "secret")
        False
    """
    if not authorization_header:
        return False

    parts = authorization_header.strip().split(None, 1)
    if len(parts) != 2:
        return False

    scheme, token = parts
    if scheme.lower() != "bearer":
        return False

    token = token.strip()
    # Constant-time comparison. Encode to bytes so compare_digest never sees a
    # non-ASCII str (which would raise TypeError).
    return hmac.compare_digest(token.encode("utf-8"), api_key.encode("utf-8"))


def _extract_authorization(scope: Scope) -> str | None:
    """Pull the ``authorization`` request header out of an ASGI HTTP scope.

    ASGI header names are lowercased bytes and values are bytes; decode with
    latin-1 (the ASGI/HTTP byte-to-str convention) so no decode error can
    escape. Returns the first matching value, or ``None`` if absent.
    """
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            return value.decode("latin-1")
    return None


class ApiKeyAuthMiddleware:
    """Raw ASGI middleware enforcing a shared Bearer API key on HTTP requests.

    Wrap the MCP app with this only when ``Config.http_api_key`` is non-empty.
    Non-HTTP scopes (e.g. ``lifespan``) pass through without auth; every HTTP
    request -- including ``OPTIONS`` -- must satisfy :func:`is_authorized` or
    receive a ``403`` JSON response (with no ``WWW-Authenticate`` header). Real
    CORS preflight is handled by the outer ``CORSMiddleware`` and never reaches
    this layer.
    """

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        """Initialize the middleware.

        Args:
            app: The wrapped ASGI application (the MCP Streamable HTTP app).
            api_key: The expected shared key. Callers must pass a non-empty
                value — this middleware should not be applied when auth is off.
        """
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Pass through anything that isn't an HTTP request. This intentionally
        # forwards both ``lifespan`` and ``websocket`` scopes: ``lifespan`` MUST
        # pass through (it carries no request to authenticate), and the MCP
        # streamable-HTTP app exposes NO websocket route, so a ``websocket``
        # scope never occurs in practice. (If a WS route is ever added, this
        # layer would need to gate it explicitly.) ``scope["type"]`` is
        # spec-guaranteed to be present on every ASGI scope.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Every HTTP request -- including OPTIONS -- must carry the key. Genuine
        # CORS preflight is short-circuited by the outer CORS layer and never
        # reaches here (see module docstring). ``scope.get("method")`` is read
        # defensively elsewhere because only http/websocket scopes carry a
        # method; here the scope is already known to be ``http``.
        if not is_authorized(_extract_authorization(scope), self.api_key):
            await self._send_forbidden(send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _send_forbidden(send: Send) -> None:
        """Emit a minimal ``403 Forbidden`` JSON response.

        Deliberately omits ``WWW-Authenticate`` and never uses ``401`` — see the
        module docstring for why a challenge response breaks MCP clients.
        """
        body = b'{"error": "forbidden"}'
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )


# Recommended minimum length for a shared API key, in characters.
_MIN_API_KEY_LENGTH = 16


def validate_http_api_key(config: "Config") -> list[str]:
    """Detect easy misconfigurations of the optional ``http_api_key``.

    Mirrors ``transport_security_config.validate_http_allowlist``: this function
    is pure (no side effects, never prints) and advisory-only -- it never
    rejects or alters the setting. The caller (startup wiring in ``__init__.py``)
    is responsible for surfacing the returned strings.

    Two mistakes are surfaced:

    - Leading/trailing whitespace: the middleware strips the *presented* token
      before comparison (see :func:`is_authorized`), so a configured key that
      itself carries surrounding whitespace can NEVER authenticate. This is a
      hard misconfiguration and warned first.
    - A short key (after stripping, ``< _MIN_API_KEY_LENGTH`` chars) is weak and
      easier to brute-force; a longer key is recommended.

    An empty key means the auth layer is disabled (the expected default) and
    produces no warnings.

    Args:
        config: Addon configuration. ``http_api_key`` holds the optional shared
            Bearer key (empty disables the layer).

    Returns:
        List of human-readable warning messages. Empty when the key is empty or
        well-formed.
    """
    warnings: list[str] = []

    api_key = config.http_api_key
    if not api_key:
        return warnings

    if api_key != api_key.strip():
        warnings.append(
            "http_api_key: the configured key has leading or trailing "
            "whitespace and will NEVER authenticate -- the server strips the "
            "presented token before comparing. Remove the surrounding "
            "whitespace."
        )
    elif len(api_key.strip()) < _MIN_API_KEY_LENGTH:
        warnings.append(
            f"http_api_key: the configured key is short and weak. Use a random "
            f"key of at least {_MIN_API_KEY_LENGTH} characters."
        )

    return warnings
