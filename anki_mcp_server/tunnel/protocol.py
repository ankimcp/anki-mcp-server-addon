"""Tunnel protocol types, constants, and message parser.

Pure data module — zero I/O, zero state, zero side effects.
Defines the WebSocket protocol between the AnkiMCP addon (client) and
the tunnel relay server (SaaS). Message types mirror the SaaS
shared-types/websocket.ts definitions.

All messages are JSON objects with a "type" discriminator field.
Server-to-client and client-to-server messages use separate type sets.
"""

import json
from typing import NotRequired, TypedDict


# ------------------------------------------------------------------------------
# WebSocket close codes (matching SaaS shared-types/websocket.ts)
# ------------------------------------------------------------------------------

class CloseCodes:
    """WebSocket close codes used by the tunnel relay server.

    Standard codes (1000-1999) follow RFC 6455.
    Application codes (4000-4999) are tunnel-specific.
    """

    NORMAL = 1000
    GOING_AWAY = 1001
    AUTH_FAILED = 4001
    TOKEN_REVOKED = 4002
    TUNNEL_AUTH_FAILED = 4003
    ACCOUNT_DELETED = 4004
    SESSION_REPLACED = 4005
    URL_REGENERATED = 4006
    SERVICE_UNAVAILABLE = 4008
    SHUTDOWN = 4009


# Close codes that mean "don't bother reconnecting"
_NO_RECONNECT: set[int] = {
    CloseCodes.NORMAL,
    CloseCodes.TOKEN_REVOKED,
    CloseCodes.ACCOUNT_DELETED,
    CloseCodes.SESSION_REPLACED,
}

# Close codes that mean "get a fresh token before reconnecting"
_REFRESH_TOKEN: set[int] = {
    CloseCodes.AUTH_FAILED,
    CloseCodes.TUNNEL_AUTH_FAILED,
}


# ------------------------------------------------------------------------------
# Protocol constants
# ------------------------------------------------------------------------------

CONNECTION_TIMEOUT = 10.0       # seconds to wait for tunnel_established
REQUEST_TIMEOUT = 30.0          # seconds to wait for local HTTP response
HEARTBEAT_INTERVAL = 30.0       # server sends ping every 30s
HEALTH_CHECK_TIMEOUT = 60.0     # terminate if no server ping in 60s

RECONNECT_INITIAL_DELAY = 1.0   # seconds
RECONNECT_MAX_DELAY = 30.0      # seconds
RECONNECT_MAX_ATTEMPTS = 10
RECONNECT_JITTER_FACTOR = 0.3


# ------------------------------------------------------------------------------
# Server -> Client message types
# ------------------------------------------------------------------------------

class TunnelEstablished(TypedDict):
    """Server confirms tunnel is active and provides the public URL."""

    type: str  # "tunnel_established"
    url: str
    expiresAt: str | None


class TunnelRequest(TypedDict):
    """Server forwards an HTTP request from an AI client through the tunnel.

    The ``body`` field is optional — omitted for GET/HEAD requests.
    Body is always a JSON-stringified string per protocol contract.
    """

    type: str                       # "request"
    requestId: str
    method: str
    path: str
    headers: dict[str, str]
    body: NotRequired[str | None]


class TunnelPing(TypedDict):
    """Server heartbeat. Client must reply with TunnelPong."""

    type: str  # "ping"
    timestamp: int  # unix milliseconds


class TunnelError(TypedDict):
    """Server-side error notification.

    The ``details`` field is optional — only present for some error types.
    """

    type: str    # "error"
    code: str
    message: str
    details: NotRequired[dict | None]


class TunnelUrlChanged(TypedDict):
    """Server notifies that the tunnel URL has changed (admin action)."""

    type: str  # "url_changed"
    oldUrl: str
    newUrl: str


ServerMessage = (
    TunnelEstablished
    | TunnelRequest
    | TunnelPing
    | TunnelError
    | TunnelUrlChanged
)


# ------------------------------------------------------------------------------
# Client -> Server message types
# ------------------------------------------------------------------------------

class TunnelResponse(TypedDict):
    """Client sends the result of processing a TunnelRequest.

    The ``body`` field is optional — omitted when the response has no body.
    """

    type: str                           # "response"
    requestId: str
    statusCode: int
    headers: dict[str, str]
    body: NotRequired[str | None]


class TunnelPong(TypedDict):
    """Client heartbeat reply to TunnelPing."""

    type: str  # "pong"
    timestamp: int  # echo back the server's timestamp


# ------------------------------------------------------------------------------
# Message type registry (type discriminator -> TypedDict class)
# ------------------------------------------------------------------------------

_SERVER_MESSAGE_TYPES: dict[str, type] = {
    "tunnel_established": TunnelEstablished,
    "request": TunnelRequest,
    "ping": TunnelPing,
    "error": TunnelError,
    "url_changed": TunnelUrlChanged,
}


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def parse_server_message(raw: str) -> ServerMessage:
    """Parse a raw JSON string from the server into a typed message.

    Reads the ``type`` field to determine the message variant and validates
    that it is a known server message type.

    Args:
        raw: JSON-encoded string received over the WebSocket.

    Returns:
        The parsed message as the appropriate TypedDict.

    Raises:
        ValueError: If the JSON is invalid, missing a ``type`` field,
            or the ``type`` is not a known server message type.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    msg_type = data.get("type")
    if msg_type is None:
        raise ValueError("Message missing 'type' field")

    if msg_type not in _SERVER_MESSAGE_TYPES:
        raise ValueError(f"Unknown server message type: {msg_type!r}")

    # The dict is already the right shape — TypedDict is a structural type,
    # not a runtime class. We just return the parsed dict.
    return data  # type: ignore[return-value]


def should_reconnect(close_code: int) -> bool:
    """Whether the client should attempt reconnection for this close code.

    Permanent failures (normal close, token revoked, account deleted,
    session replaced) should NOT trigger reconnection. Everything else
    — including unknown codes — should.

    Args:
        close_code: WebSocket close code from the server.

    Returns:
        True if the client should attempt to reconnect.
    """
    return close_code not in _NO_RECONNECT


def should_refresh_token(close_code: int) -> bool:
    """Whether the client should refresh the auth token before reconnecting.

    Auth-related failures (AUTH_FAILED, TUNNEL_AUTH_FAILED) indicate the
    current token is expired or invalid. The client should obtain a fresh
    token before the next connection attempt.

    Args:
        close_code: WebSocket close code from the server.

    Returns:
        True if the client should refresh its auth token.
    """
    return close_code in _REFRESH_TOKEN
