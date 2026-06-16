"""Unit tests for the optional HTTP API-key auth layer.

These tests exercise the pure ``is_authorized`` helper, the raw-ASGI
``ApiKeyAuthMiddleware``, and the advisory ``validate_http_api_key`` misconfig
detector -- all without a running Anki. They lock in the security-critical
behaviors of the layer:

- Empty/missing/wrong tokens never authorize; the Bearer scheme is matched
  case-insensitively; surrounding whitespace is tolerated; non-ASCII tokens
  return False instead of raising.
- On auth failure the middleware returns ``403`` (NOT ``401``) with NO
  ``WWW-Authenticate`` header -- a challenge response would derail MCP clients
  into an OAuth discovery flow.
- EVERY http method (including ``OPTIONS``) requires the key -- there is no
  OPTIONS bypass.
- Authorized requests pass straight through to the wrapped app using the SAME
  ``send`` callable, so SSE streaming is not buffered/broken.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# ``http_auth`` imports ``Config`` only under TYPE_CHECKING, but the test
# constructs ``Config`` directly, which pulls vendored pydantic/dataclasses in.
# Importing ``anki_mcp_server`` first puts ``vendor/shared`` on ``sys.path`` --
# mirrors test_transport_security.py.
# ---------------------------------------------------------------------------
import anki_mcp_server  # noqa: F401 -- triggers vendor path setup

import pytest

from anki_mcp_server.config import Config
from anki_mcp_server.http_auth import (
    ApiKeyAuthMiddleware,
    is_authorized,
    validate_http_api_key,
)


# ---------------------------------------------------------------------------
# is_authorized -- pure header/token parsing + constant-time compare
# ---------------------------------------------------------------------------

class TestIsAuthorized:
    """Tests for the pure ``is_authorized`` helper."""

    def test_valid_bearer(self) -> None:
        """A correct ``Bearer <key>`` header authorizes."""
        assert is_authorized("Bearer secret", "secret") is True

    def test_scheme_case_insensitive(self) -> None:
        """The ``bearer`` scheme is matched case-insensitively."""
        assert is_authorized("bearer secret", "secret") is True

    def test_surrounding_and_internal_whitespace_tolerated(self) -> None:
        """Leading/trailing whitespace and a collapsed inner gap still authorize."""
        assert is_authorized("Bearer  secret  ", "secret") is True

    def test_empty_token_single_space(self) -> None:
        """``Bearer `` (empty token) never authorizes."""
        assert is_authorized("Bearer ", "secret") is False

    def test_empty_token_multiple_spaces(self) -> None:
        """``Bearer   `` (whitespace-only token) never authorizes."""
        assert is_authorized("Bearer   ", "secret") is False

    def test_wrong_scheme(self) -> None:
        """A non-Bearer scheme (``Basic``) does not authorize."""
        assert is_authorized("Basic secret", "secret") is False

    def test_wrong_token(self) -> None:
        """A correct scheme with the wrong token does not authorize."""
        assert is_authorized("Bearer wrong", "secret") is False

    def test_no_scheme(self) -> None:
        """A bare token with no scheme does not authorize."""
        assert is_authorized("secret", "secret") is False

    def test_none_header(self) -> None:
        """A missing header (``None``) does not authorize."""
        assert is_authorized(None, "secret") is False

    def test_empty_header(self) -> None:
        """An empty-string header does not authorize."""
        assert is_authorized("", "secret") is False

    def test_non_ascii_token_does_not_raise(self) -> None:
        """A non-ASCII presented token returns False instead of raising.

        ``hmac.compare_digest`` raises ``TypeError`` on non-ASCII ``str``
        inputs; the helper encodes to bytes first to avoid that.
        """
        assert is_authorized("Bearer é", "secret") is False


# ---------------------------------------------------------------------------
# ASGI test harness for the middleware
# ---------------------------------------------------------------------------

class _RecordingApp:
    """Async fake ASGI app that records invocation and emits a sentinel 200.

    Records every call (so passthrough vs. short-circuit is observable) and,
    when ``emit`` is True, sends a minimal ``200`` response through the SAME
    ``send`` callable it was handed -- letting tests assert the middleware did
    not substitute or buffer ``send``.
    """

    def __init__(self, *, emit: bool = True, chunks: list[bytes] | None = None) -> None:
        self.called = False
        self.call_count = 0
        self.received_scope: dict | None = None
        self.received_send = None
        self._emit = emit
        self._chunks = chunks

    async def __call__(self, scope, receive, send) -> None:
        self.called = True
        self.call_count += 1
        self.received_scope = scope
        self.received_send = send
        if not self._emit:
            return
        if self._chunks is not None:
            # Streaming-style response: multiple body chunks via the same send.
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")],
                }
            )
            for i, chunk in enumerate(self._chunks):
                more = i < len(self._chunks) - 1
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": more}
                )
            return
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})


def _make_scope(method: str, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    """Build a minimal ASGI ``http`` scope.

    Header names are lowercased bytes and values bytes, per the ASGI spec.
    """
    return {
        "type": "http",
        "method": method,
        "headers": headers or [],
    }


async def _noop_receive() -> dict:
    """Async ASGI ``receive`` that yields a single empty request body."""
    return {"type": "http.request", "body": b"", "more_body": False}


class _Sender:
    """Async ASGI ``send`` that appends every emitted message to ``messages``."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)


_KEY = "supersecretkey123456"
_BEARER = [(b"authorization", b"Bearer " + _KEY.encode("latin-1"))]


def _start_message(messages: list[dict]) -> dict:
    """Return the single ``http.response.start`` message (asserts exactly one)."""
    starts = [m for m in messages if m.get("type") == "http.response.start"]
    assert len(starts) == 1, f"expected exactly one start, got {starts}"
    return starts[0]


def _body_messages(messages: list[dict]) -> list[dict]:
    """Return all ``http.response.body`` messages."""
    return [m for m in messages if m.get("type") == "http.response.body"]


def _header_value(start_message: dict, name: bytes) -> bytes | None:
    """Look up a response header value (case-insensitive on the name)."""
    for hname, hvalue in start_message.get("headers", []):
        if hname.lower() == name.lower():
            return hvalue
    return None


def _has_header(start_message: dict, name: bytes) -> bool:
    """True if a response header with ``name`` (case-insensitive) is present."""
    return any(
        hname.lower() == name.lower()
        for hname, _ in start_message.get("headers", [])
    )


# ---------------------------------------------------------------------------
# ApiKeyAuthMiddleware
# ---------------------------------------------------------------------------

class TestApiKeyAuthMiddleware:
    """Tests for the raw-ASGI ``ApiKeyAuthMiddleware``."""

    @pytest.mark.asyncio
    async def test_authorized_post_passthrough(self) -> None:
        """A POST with the correct Bearer key reaches the wrapped app (no 403)."""
        app = _RecordingApp()
        mw = ApiKeyAuthMiddleware(app, _KEY)
        sender = _Sender()

        await mw(_make_scope("POST", _BEARER), _noop_receive, sender)

        assert app.called is True
        # The recorded messages are the wrapped app's own 200, not a 403 body.
        start = _start_message(sender.messages)
        assert start["status"] == 200
        bodies = _body_messages(sender.messages)
        assert all(m.get("body") != b'{"error": "forbidden"}' for m in bodies)

    @pytest.mark.asyncio
    async def test_unauthorized_post_missing_header_is_403_no_challenge(self) -> None:
        """A POST with NO Authorization header is rejected with a clean 403.

        Security-critical: status is 403 (NOT 401) and there is NO
        ``WWW-Authenticate`` header, so MCP clients do not start an OAuth flow.
        """
        app = _RecordingApp()
        mw = ApiKeyAuthMiddleware(app, _KEY)
        sender = _Sender()

        await mw(_make_scope("POST"), _noop_receive, sender)

        assert app.called is False

        start = _start_message(sender.messages)
        assert start["status"] == 403
        assert start["status"] != 401

        # CRITICAL: no challenge header.
        assert _has_header(start, b"www-authenticate") is False

        bodies = _body_messages(sender.messages)
        assert len(bodies) == 1
        body = bodies[0]["body"]
        assert body == b'{"error": "forbidden"}'

        # content-length header value matches the body length.
        content_length = _header_value(start, b"content-length")
        assert content_length == str(len(body)).encode("latin-1")

    @pytest.mark.asyncio
    async def test_unauthorized_post_wrong_key_is_403(self) -> None:
        """A POST with the WRONG Bearer key yields the same 403 shape."""
        app = _RecordingApp()
        mw = ApiKeyAuthMiddleware(app, _KEY)
        sender = _Sender()

        wrong = [(b"authorization", b"Bearer not-the-key")]
        await mw(_make_scope("POST", wrong), _noop_receive, sender)

        assert app.called is False
        start = _start_message(sender.messages)
        assert start["status"] == 403
        assert _has_header(start, b"www-authenticate") is False
        bodies = _body_messages(sender.messages)
        assert len(bodies) == 1
        assert bodies[0]["body"] == b'{"error": "forbidden"}'

    @pytest.mark.asyncio
    async def test_options_without_key_is_403_no_bypass(self) -> None:
        """REGRESSION GUARD: OPTIONS without a key is 403 (no OPTIONS bypass).

        Earlier designs let ``OPTIONS`` through unauthenticated as a CORS
        preflight shortcut. That bypass was removed -- genuine preflight is
        handled by the outer CORS layer -- so an unauthenticated OPTIONS must
        be rejected like any other method.
        """
        app = _RecordingApp()
        mw = ApiKeyAuthMiddleware(app, _KEY)
        sender = _Sender()

        await mw(_make_scope("OPTIONS"), _noop_receive, sender)

        assert app.called is False
        start = _start_message(sender.messages)
        assert start["status"] == 403
        assert _has_header(start, b"www-authenticate") is False

    @pytest.mark.asyncio
    async def test_authorized_get_streaming_passthrough(self) -> None:
        """An authorized GET (SSE stream) passes through and is NOT buffered.

        The wrapped app emits two body chunks (more_body=True then False) via
        the same ``send`` the middleware forwarded; the test observes both,
        proving the middleware does not buffer the response.
        """
        app = _RecordingApp(chunks=[b"chunk-1", b"chunk-2"])
        mw = ApiKeyAuthMiddleware(app, _KEY)
        sender = _Sender()

        await mw(_make_scope("GET", _BEARER), _noop_receive, sender)

        assert app.called is True
        # Middleware forwarded the SAME send callable to the wrapped app.
        assert app.received_send is sender

        start = _start_message(sender.messages)
        assert start["status"] == 200
        bodies = _body_messages(sender.messages)
        assert [m["body"] for m in bodies] == [b"chunk-1", b"chunk-2"]
        assert bodies[0]["more_body"] is True
        assert bodies[1]["more_body"] is False

    @pytest.mark.asyncio
    async def test_authorized_delete_passthrough(self) -> None:
        """An authorized DELETE reaches the wrapped app."""
        app = _RecordingApp()
        mw = ApiKeyAuthMiddleware(app, _KEY)
        sender = _Sender()

        await mw(_make_scope("DELETE", _BEARER), _noop_receive, sender)

        assert app.called is True
        assert _start_message(sender.messages)["status"] == 200

    @pytest.mark.asyncio
    async def test_lifespan_scope_passthrough_no_auth(self) -> None:
        """A non-http (``lifespan``) scope is forwarded without auth, no 403."""
        app = _RecordingApp(emit=False)
        mw = ApiKeyAuthMiddleware(app, _KEY)
        sender = _Sender()

        scope = {"type": "lifespan"}
        await mw(scope, _noop_receive, sender)

        assert app.called is True
        assert app.received_scope is scope
        # No 403 emitted by the middleware itself.
        assert sender.messages == []

    @pytest.mark.asyncio
    async def test_multiple_authorization_headers_first_match(self) -> None:
        """With multiple ``authorization`` headers, the FIRST is used.

        A first (correct) header authorizes even if a later one is wrong --
        ``_extract_authorization`` returns the first match.
        """
        app = _RecordingApp()
        mw = ApiKeyAuthMiddleware(app, _KEY)
        sender = _Sender()

        headers = [
            (b"authorization", b"Bearer " + _KEY.encode("latin-1")),
            (b"authorization", b"Bearer wrong"),
        ]
        await mw(_make_scope("POST", headers), _noop_receive, sender)

        assert app.called is True
        assert _start_message(sender.messages)["status"] == 200


# ---------------------------------------------------------------------------
# validate_http_api_key -- advisory misconfig detector
# ---------------------------------------------------------------------------

class TestValidateHttpApiKey:
    """Tests for the advisory ``validate_http_api_key`` detector."""

    def test_empty_key_no_warnings(self) -> None:
        """An empty key (auth disabled, the default) produces no warnings."""
        assert validate_http_api_key(Config()) == []

    def test_long_clean_key_no_warnings(self) -> None:
        """A clean key >= the minimum length produces no warnings."""
        assert validate_http_api_key(Config(http_api_key="x" * 20)) == []

    def test_short_key_one_warning(self) -> None:
        """A short key produces exactly one warning mentioning the field."""
        warnings = validate_http_api_key(Config(http_api_key="abc"))
        assert len(warnings) == 1
        assert "http_api_key" in warnings[0]

    def test_leading_whitespace_one_warning(self) -> None:
        """A key with leading whitespace warns about whitespace (never auths)."""
        warnings = validate_http_api_key(Config(http_api_key=" " + "x" * 20))
        assert len(warnings) == 1
        assert "http_api_key" in warnings[0]
        assert "whitespace" in warnings[0].lower()

    def test_trailing_newline_one_warning(self) -> None:
        """A key with a trailing newline warns about whitespace."""
        warnings = validate_http_api_key(Config(http_api_key="x" * 20 + "\n"))
        assert len(warnings) == 1
        assert "whitespace" in warnings[0].lower()
