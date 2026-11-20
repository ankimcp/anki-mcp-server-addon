"""Only ``AuthError`` may leave ``DeviceFlowAuth`` for any server response.

Drives the real ``DeviceFlowAuth`` through ``httpx.MockTransport`` (no
network). The client-patching helper and the environment-isolation fixtures
come from ``test_tunnel_tls.py``.
"""
from __future__ import annotations

# The package __init__ must run before the MCP SDK is imported.
import anki_mcp_server  # noqa: F401

import json
from typing import Any, Callable

import httpx
import pytest

from anki_mcp_server.tunnel import auth as auth_module
from anki_mcp_server.tunnel.auth import AuthError, DeviceCodeResponse

from .test_tunnel_tls import (  # noqa: F401  (autouse fixtures + helpers)
    _clean_tls_environment,
    _DEVICE_CODE_BODY,
    _make_auth,
    _patch_client_with_handler,
    _reset_warned_cert_files,
)

_TOKEN_BODY: dict[str, Any] = {
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "expires_in": 3600,
    "user": {"id": "usr_1", "email": "a@example.com", "tier": "free"},
}

_NON_OBJECT_BODIES = [[], ["x"], "text", 5, 1.5, True, None]
_NON_STRING = [{"a": 1}, ["x"], 5, True, None]


def _respond(
    monkeypatch: pytest.MonkeyPatch, content: Any, status: int = 200, *, raw: bool = False
) -> None:
    """Make every auth request answer ``status`` with ``content`` as JSON."""
    payload = content if raw else json.dumps(content)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, content=payload, headers={"content-type": "application/json"}
        )

    _patch_client_with_handler(monkeypatch, handler)


async def _post(auth: Any = None) -> dict[str, Any]:
    return await (auth or _make_auth())._post(
        "https://tunnel.example/auth/token", form_data={"k": "v"}
    )


class TestInvalidUrl:
    @pytest.mark.asyncio
    async def test_httpx_invalid_url_becomes_auth_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, {})
        auth = _make_auth()
        auth._device_endpoint = "https://tunnel.example:abc/auth/device"

        with pytest.raises(AuthError) as excinfo:
            await auth.request_device_code()

        assert isinstance(excinfo.value.__cause__, httpx.InvalidURL)
        assert "https://tunnel.example:abc/auth/device" in str(excinfo.value)
        assert excinfo.value.error_code is None


class TestNonObjectBody:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 500])
    @pytest.mark.parametrize("body", _NON_OBJECT_BODIES, ids=repr)
    async def test_error_status_quotes_the_body(
        self, body: Any, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, body, status)

        with pytest.raises(AuthError) as excinfo:
            await _post()

        text = json.dumps(body)
        assert str(excinfo.value) == (
            f"Non-object JSON response (HTTP {status}): {text}"
        )
        assert excinfo.value.error_code is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", _NON_OBJECT_BODIES, ids=repr)
    async def test_success_status_names_only_the_json_type(
        self, body: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, body, 200)

        with pytest.raises(AuthError) as excinfo:
            await _post()

        assert str(excinfo.value) == (
            f"Non-object JSON response (HTTP 200): {type(body).__name__}"
        )
        assert excinfo.value.error_code is None

    @pytest.mark.asyncio
    async def test_excerpt_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _respond(monkeypatch, ["x" * 1000])

        with pytest.raises(AuthError) as excinfo:
            await _post()

        assert len(str(excinfo.value)) < 300

    @pytest.mark.asyncio
    async def test_deeply_nested_json_becomes_auth_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, "[" * 1_000_000 + "]" * 1_000_000, raw=True)

        with pytest.raises(AuthError) as excinfo:
            await _post()

        assert str(excinfo.value) == "Non-JSON response (HTTP 200)"


class TestSuccessBodiesAreNeverQuoted:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("content", "raw"),
        [
            (["SECRET-TOKEN-VALUE"], False),
            ("SECRET-TOKEN-VALUE", False),
            ('{"access_token": "SECRET-TOKEN-VALUE", "expires_in": ', True),
            ("SECRET-TOKEN-VALUE not json", True),
        ],
        ids=["list", "str", "truncated-json", "plain-text"],
    )
    async def test_2xx_body_does_not_reach_the_error(
        self, content: Any, raw: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, content, 200, raw=raw)

        with pytest.raises(AuthError) as excinfo:
            await _post()

        assert "SECRET-TOKEN-VALUE" not in str(excinfo.value)
        assert "SECRET-TOKEN-VALUE" not in (excinfo.value.error_code or "")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 502])
    async def test_error_status_body_is_still_excerpted(
        self, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, "visible-body not json", status, raw=True)

        with pytest.raises(AuthError) as excinfo:
            await _post()

        assert str(excinfo.value) == (
            f"Non-JSON response (HTTP {status}): visible-body not json"
        )


class TestUnicodeErrorFromRequest:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [UnicodeError("label empty or too long"),
         UnicodeEncodeError("idna", "x", 0, 1, "bad")],
        ids=["UnicodeError", "UnicodeEncodeError"],
    )
    async def test_becomes_auth_error(
        self, error: Exception, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise error

        _patch_client_with_handler(monkeypatch, handler)

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().request_device_code()

        assert isinstance(excinfo.value.__cause__, UnicodeError)
        assert "Invalid auth URL" in str(excinfo.value)
        assert excinfo.value.error_code is None


class TestErrorFields:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("error", _NON_STRING, ids=repr)
    async def test_non_string_error_code_is_dropped(
        self, error: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, {"error": error, "error_description": "boom"}, 400)

        with pytest.raises(AuthError) as excinfo:
            await _post()

        assert excinfo.value.error_code is None
        assert str(excinfo.value) == "boom"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("description", _NON_STRING, ids=repr)
    async def test_non_string_description_falls_back_to_status(
        self, description: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(
            monkeypatch,
            {"error": "invalid_grant", "error_description": description},
            400,
        )

        with pytest.raises(AuthError) as excinfo:
            await _post()

        assert excinfo.value.error_code == "invalid_grant"
        assert type(str(excinfo.value)) is str
        assert str(excinfo.value) == "HTTP 400"

    @pytest.mark.asyncio
    async def test_well_formed_error_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(
            monkeypatch,
            {"error": "invalid_grant", "error_description": "Token revoked"},
            400,
        )

        with pytest.raises(AuthError) as excinfo:
            await _post()

        assert str(excinfo.value) == "Token revoked"
        assert excinfo.value.error_code == "invalid_grant"

    @pytest.mark.asyncio
    async def test_error_object_without_fields_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, {}, 503)

        with pytest.raises(AuthError) as excinfo:
            await _post()

        assert str(excinfo.value) == "HTTP 503"
        assert excinfo.value.error_code == "unknown_error"

    @pytest.mark.asyncio
    async def test_non_json_body_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, "<html>bad gateway</html>", 502, raw=True)

        with pytest.raises(AuthError) as excinfo:
            await _post()

        assert str(excinfo.value) == (
            "Non-JSON response (HTTP 502): <html>bad gateway</html>"
        )


class TestWellFormedSuccessUnchanged:
    @pytest.mark.asyncio
    async def test_post_returns_the_object(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, {"a": 1})

        assert await _post() == {"a": 1}

    @pytest.mark.asyncio
    async def test_device_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _respond(monkeypatch, {**_DEVICE_CODE_BODY, "verification_uri_complete": "u?c=1"})

        assert await _make_auth().request_device_code() == DeviceCodeResponse(
            device_code="dev-123",
            user_code="ABCD-EFGH",
            verification_uri="https://tunnel.example/verify",
            verification_uri_complete="u?c=1",
            expires_in=600,
            interval=5,
        )

    @pytest.mark.asyncio
    async def test_refresh_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _respond(monkeypatch, _TOKEN_BODY)

        credentials = await _make_auth().refresh_token("old")

        assert credentials.access_token == "access-1"
        assert credentials.refresh_token == "refresh-1"
        assert credentials.user == _TOKEN_BODY["user"]

    @pytest.mark.asyncio
    async def test_poll_pending_and_slow_down_still_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr(auth_module.anyio, "sleep", fake_sleep)
        answers = iter([
            (400, {"error": "authorization_pending"}),
            (400, {"error": "slow_down"}),
            (200, _TOKEN_BODY),
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            status, body = next(answers)
            return httpx.Response(status, json=body)

        _patch_client_with_handler(monkeypatch, handler)

        credentials = await _make_auth().poll_for_token("dev", 1, 600)

        assert credentials.access_token == "access-1"
        assert slept == [1, 1, 6]


def _without(body: dict[str, Any], key: str) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k != key}


_BAD_DEVICE_BODIES = {
    **{f"missing {k}": _without(_DEVICE_CODE_BODY, k)
       for k in ("device_code", "user_code", "verification_uri", "expires_in")},
    "expires_in not numeric": {**_DEVICE_CODE_BODY, "expires_in": "abc"},
    "expires_in null": {**_DEVICE_CODE_BODY, "expires_in": None},
    "expires_in list": {**_DEVICE_CODE_BODY, "expires_in": [1]},
    "interval not numeric": {**_DEVICE_CODE_BODY, "interval": "abc"},
    "device_code object": {**_DEVICE_CODE_BODY, "device_code": {"a": 1}},
    "user_code int": {**_DEVICE_CODE_BODY, "user_code": 7},
    "verification_uri null": {**_DEVICE_CODE_BODY, "verification_uri": None},
    "verification_uri_complete int": {**_DEVICE_CODE_BODY, "verification_uri_complete": 7},
}

_BAD_TOKEN_BODIES = {
    **{f"missing {k}": _without(_TOKEN_BODY, k)
       for k in ("access_token", "refresh_token", "expires_in", "user")},
    "expires_in not numeric": {**_TOKEN_BODY, "expires_in": "abc"},
    "expires_in null": {**_TOKEN_BODY, "expires_in": None},
    "expires_in absurdly large": {**_TOKEN_BODY, "expires_in": 10**20},
    "access_token int": {**_TOKEN_BODY, "access_token": 5},
    "refresh_token list": {**_TOKEN_BODY, "refresh_token": ["r"]},
    "user list": {**_TOKEN_BODY, "user": ["a"]},
    "user null": {**_TOKEN_BODY, "user": None},
}


def _ids(bodies: dict[str, Any]) -> list[str]:
    return list(bodies)


class TestMalformedSuccessBodies:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", _BAD_DEVICE_BODIES.values(), ids=_ids(_BAD_DEVICE_BODIES))
    async def test_request_device_code(
        self, body: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, body)

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().request_device_code()

        assert excinfo.value.error_code == "invalid_response"
        assert str(excinfo.value).startswith("Malformed device code response: ")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["expires_in", "interval"])
    async def test_request_device_code_bad_number_is_not_echoed(
        self, field: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, {**_DEVICE_CODE_BODY, field: "SECRET-TOKEN-VALUE"})

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().request_device_code()

        assert "SECRET-TOKEN-VALUE" not in str(excinfo.value)
        assert str(excinfo.value) == "Malformed device code response: ValueError"
        assert isinstance(excinfo.value.__cause__, ValueError)

    @pytest.mark.asyncio
    async def test_token_bad_expires_in_is_not_echoed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, {**_TOKEN_BODY, "expires_in": "SECRET-TOKEN-VALUE"})

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().refresh_token("old")

        assert "SECRET-TOKEN-VALUE" not in str(excinfo.value)
        assert str(excinfo.value) == "Malformed token response: ValueError"

    @pytest.mark.asyncio
    async def test_request_device_code_infinite_expires_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, '{"device_code":"d","user_code":"u",'
                 '"verification_uri":"v","expires_in":1e999}', raw=True)

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().request_device_code()

        assert excinfo.value.error_code == "invalid_response"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", _BAD_TOKEN_BODIES.values(), ids=_ids(_BAD_TOKEN_BODIES))
    async def test_refresh_token(
        self, body: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, body)

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().refresh_token("old")

        assert excinfo.value.error_code == "invalid_response"
        assert str(excinfo.value).startswith("Malformed token response: ")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", _BAD_TOKEN_BODIES.values(), ids=_ids(_BAD_TOKEN_BODIES))
    async def test_poll_for_token(
        self, body: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, body)

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().poll_for_token("dev", 0, 600)

        assert excinfo.value.error_code == "invalid_response"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [200, 400])
    async def test_poll_for_token_with_non_object_body(
        self, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _respond(monkeypatch, ["not", "an", "object"], status)

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().poll_for_token("dev", 0, 600)

        assert excinfo.value.error_code is None
        assert "Non-object JSON response" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_poll_for_token_non_string_error_is_terminal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(400, json={"error": ["authorization_pending"]})

        _patch_client_with_handler(monkeypatch, handler)

        with pytest.raises(AuthError) as excinfo:
            await _make_auth().poll_for_token("dev", 0, 600)

        assert excinfo.value.error_code is None
        assert len(calls) == 1
