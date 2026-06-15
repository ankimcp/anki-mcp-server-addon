"""E2E tests for DNS-rebinding protection on the HTTP transport.

Advisory: GHSA-j9xx-59ph-wmr6. The HTTP listener (default 127.0.0.1:3141)
currently builds ``TransportSecuritySettings(enable_dns_rebinding_protection=
False)``, which disables Host/Origin validation. A DNS-rebound browser can then
reach the MCP tools.

RED-UNTIL-FIX STATUS
--------------------
Most cases in this file are EXPECTED TO FAIL right now, on purpose. The TDD
staging commit adds the policy builder (``transport_security_config.py``) and
these tests, but does NOT wire the builder into ``mcp_server.py``'s ``run()``.
Today the server still returns 200 for an attacker Host/Origin because the SDK
middleware early-returns when protection is disabled.

They turn GREEN in the later commit that wires ``build_transport_security`` into
``run()`` (re-enabling protection with the loopback allowlist + the
``http_allowed_hosts`` / ``http_allowed_origins`` escape hatch).

Cases that pass BOTH before and after the fix (loopback Host with allowed/absent
Origin -> 200) act as regression guards that non-browser clients keep working.

These use stdlib ``http.client`` (NOT the Inspector CLI, which cannot set
arbitrary Host/Origin headers) and parse SSE ``data:`` frames like the advisory
PoC.
"""

from __future__ import annotations

import http.client
import json
from urllib.parse import urlparse

import pytest

from .conftest import SERVER_URL

# Resolve host/port/path from the same env-driven URL the rest of the suite uses
# so the attacker Host reuses the real port (don't hardcode 3141).
_PARSED = urlparse(SERVER_URL)
SERVER_HOST = _PARSED.hostname or "localhost"
SERVER_PORT = _PARSED.port or 3141
# Default config serves the MCP Streamable HTTP endpoint at root.
MCP_PATH = "/"

# A loopback Host header (host:port) that the allowlist should accept.
LOOPBACK_HOST = f"127.0.0.1:{SERVER_PORT}"
# An attacker-controlled name resolving (via rebinding) to the loopback IP.
ATTACKER_HOST = f"attacker.example:{SERVER_PORT}"


def _mcp_body(method: str, params: dict | None = None, *, request_id: int = 1) -> str:
    """Build a JSON-RPC 2.0 request body."""
    msg: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _initialize_params() -> dict:
    """Minimal MCP ``initialize`` params."""
    return {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "dns-rebinding-test", "version": "0.0.0"},
    }


def _parse_sse_or_json(raw: str):
    """Parse a Streamable-HTTP response body (SSE ``data:`` frames or JSON).

    Returns the first decoded JSON-RPC object, or None if nothing parseable.
    """
    if not raw:
        return None
    # SSE: lines like "event: message" / "data: {...}". Concatenate data lines.
    if "data:" in raw:
        data_chunks = [
            line[len("data:"):].strip()
            for line in raw.splitlines()
            if line.startswith("data:")
        ]
        for chunk in data_chunks:
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue
        return None
    # Plain JSON body.
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _post_mcp(
    body: str,
    *,
    host_header: str,
    origin_header: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, object]:
    """Send a raw MCP POST with explicit Host/Origin/Content-Type headers.

    Connects to the real loopback IP:port (TCP), but sends ``host_header`` as the
    HTTP ``Host`` header -- this simulates a DNS-rebound request whose Host points
    at an attacker-controlled name while the socket lands on the local server.

    Returns ``(status_code, parsed_body_or_none)``.
    """
    conn = http.client.HTTPConnection(SERVER_HOST, SERVER_PORT, timeout=30)
    try:
        headers = {
            "Host": host_header,
            "Content-Type": content_type,
            # Streamable HTTP requires the client to accept both.
            "Accept": "application/json, text/event-stream",
        }
        if origin_header is not None:
            headers["Origin"] = origin_header

        conn.request("POST", MCP_PATH, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        return resp.status, _parse_sse_or_json(raw)
    finally:
        conn.close()


class TestDnsRebindingProtection:
    """Host/Origin validation for the local HTTP transport (advisory PoC)."""

    def test_initialize_with_attacker_host_rejected_421(self) -> None:
        """initialize with an attacker Host -> 421 Invalid Host header."""
        status, _ = _post_mcp(
            _mcp_body("initialize", _initialize_params()),
            host_header=ATTACKER_HOST,
        )
        assert status == 421, (
            f"Expected 421 for attacker Host {ATTACKER_HOST!r}, got {status}. "
            "RED until the run() wiring re-enables DNS-rebinding protection."
        )

    def test_tools_call_with_attacker_host_rejected_421(self) -> None:
        """tools/call (list_decks) with attacker Host -> 421 before dispatch."""
        status, _ = _post_mcp(
            _mcp_body(
                "tools/call",
                {"name": "list_decks", "arguments": {}},
                request_id=2,
            ),
            host_header=ATTACKER_HOST,
        )
        assert status == 421, (
            f"Expected 421 (rejected before dispatch) for attacker Host, got {status}. "
            "RED until the run() wiring re-enables DNS-rebinding protection."
        )

    def test_loopback_host_disallowed_origin_rejected_403(self) -> None:
        """Valid loopback Host + disallowed Origin -> 403 Invalid Origin header.

        Content-Type is valid JSON and Host is loopback so validation reaches the
        Origin check (order is Content-Type -> Host -> Origin).
        """
        status, _ = _post_mcp(
            _mcp_body("initialize", _initialize_params(), request_id=3),
            host_header=LOOPBACK_HOST,
            origin_header=f"http://attacker.example:{SERVER_PORT}",
        )
        assert status == 403, (
            f"Expected 403 for disallowed Origin, got {status}. "
            "RED until the run() wiring re-enables DNS-rebinding protection."
        )

    def test_loopback_host_no_origin_allowed_200(self) -> None:
        """Loopback Host, no Origin header -> 200 (non-browser clients keep working).

        Regression guard: 200 both before the fix (checks skipped) and after
        (loopback allowed + absent Origin passes).
        """
        status, parsed = _post_mcp(
            _mcp_body("initialize", _initialize_params(), request_id=4),
            host_header=LOOPBACK_HOST,
        )
        assert status == 200, f"Expected 200 for loopback Host with no Origin, got {status}"
        assert parsed is not None and "result" in parsed, (
            f"Expected a JSON-RPC result for a valid loopback request, got {parsed!r}"
        )

    def test_loopback_host_loopback_origin_allowed_200(self) -> None:
        """Loopback Host + loopback Origin -> 200."""
        status, _ = _post_mcp(
            _mcp_body("initialize", _initialize_params(), request_id=5),
            host_header=LOOPBACK_HOST,
            origin_header=f"http://127.0.0.1:{SERVER_PORT}",
        )
        assert status == 200, f"Expected 200 for loopback Host + loopback Origin, got {status}"

    def test_ipv6_loopback_host_allowed_200(self) -> None:
        """IPv6 loopback Host '[::1]:<port>' -> 200 (bracket handling)."""
        status, _ = _post_mcp(
            _mcp_body("initialize", _initialize_params(), request_id=6),
            host_header=f"[::1]:{SERVER_PORT}",
        )
        assert status == 200, f"Expected 200 for IPv6 loopback Host, got {status}"

    def test_absent_host_header_rejected_421(self) -> None:
        """Absent Host header -> 421 (post-fix; missing Host is rejected).

        http.client always sends a Host header, so we send an explicit empty
        value to simulate the absent/blank case the SDK treats as missing.
        """
        status, _ = _post_mcp(
            _mcp_body("initialize", _initialize_params(), request_id=7),
            host_header="",
        )
        assert status == 421, (
            f"Expected 421 for absent/blank Host header, got {status}. "
            "RED until the run() wiring re-enables DNS-rebinding protection."
        )

    @pytest.mark.skip(
        reason="requires server configured with http_allowed_hosts; "
        "covered by unit test test_transport_security"
    )
    def test_configured_extra_host_allowed_200(self) -> None:
        """Configured extra Host (http_allowed_hosts) -> 200.

        Not automated end-to-end: the default Docker compose does not start the
        server with ``http_allowed_hosts`` set, so we cannot exercise the merged
        allowlist here. The merge behavior is unit-tested in
        ``tests/unit/test_transport_security.py``. This placeholder documents the
        intent so it isn't silently omitted.
        """
        raise AssertionError("placeholder; see docstring")
