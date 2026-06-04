"""Pin down the dual-transport concurrency invariant.

The addon exposes the *same* MCP collection over two transports at once:

* the local HTTP transport — ``StreamableHTTPSessionManager(app=mcp._mcp_server,
  stateless=True)``, which per request calls ``mcp._mcp_server.run(read, write,
  init_options, stateless=True)`` (see ``mcp_server.py`` and the vendored
  ``streamable_http_manager._handle_stateless_request``); and
* the WebSocket tunnel — ``InMemoryTransport(mcp._mcp_server)``, which runs the
  *identical* call ``await self._server.run(read, write, init_options,
  stateless=True)`` (see ``tunnel/in_memory_transport.py``).

Both run ``Server.run()`` against ONE shared ``FastMCP._mcp_server`` object.
This file documents and guards what that sharing actually relies on.

WHAT IS — AND IS NOT — PROVEN HERE
----------------------------------
A subtle point that this file is careful NOT to misrepresent: at the
``Server.run`` layer, ``stateless=True`` is **not** what prevents one client's
response from being delivered to another client. Response routing is structural
— ``Server.run`` captures its ``write_stream`` parameter inside a per-call
``ServerSession`` and only ever writes there; two concurrent runs cannot route
to each other's stream regardless of the flag. The historical response-swapping
bug (issue #21) lived in ``StreamableHTTPSessionManager``'s *stateful* session
routing and is an E2E concern, covered by
``tests/e2e/test_concurrent_sessions.py``. We do **not** assert that "stateless
prevents response swapping" here, because at this layer it would be a fake
assertion.

What ``stateless=True`` is genuinely load-bearing for, and what this file
verifies:

1. Object identity — the tunnel's transport and the HTTP session manager hold
   the *same* ``_mcp_server`` object (``test_object_identity``).
2. Shared-object re-entrancy — running ``Server.run`` twice concurrently on the
   one shared instance does not cross-contaminate responses, because the shared
   ``Server`` holds no per-request state on the instance; it's all per-session.
   This guards against a regression that moves session state onto ``Server``
   (``test_concurrent_runs_no_crosstalk``).
3. The init lifecycle — ``stateless=True`` is what lets each ``Server.run``
   accept a bare ``tools/call`` with no per-connection initialize handshake,
   which is precisely what makes per-request HTTP runs and tunnel multiplexing
   viable on one shared server. The same bare call returns a success result
   under ``stateless=True`` but a JSON-RPC error (pre-init rejection) under
   ``stateless=False`` (``TestStatelessFlagIsLoadBearing``).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Module aliasing – must happen before InMemoryTransport / FastMCP are imported.
#
# The transport uses *relative* imports (``from ..vendor.shared.mcp.types …``)
# while the MCP Server internally uses *absolute* imports (``from mcp.types …``).
# Python treats these as two distinct module objects, which breaks ``isinstance``
# checks inside the transport's ``_read_responses`` reader.
#
# The fix: after ``anki_mcp_server.__init__`` adds ``vendor/shared`` to
# ``sys.path`` (giving us ``mcp.*``), we create aliases so that
# ``anki_mcp_server.vendor.shared.mcp.*`` points to the **same** module objects.
# This must run before the transport module is imported.
#
# (Copied verbatim from ``test_in_memory_transport.py`` — keep it in sync.)
# ---------------------------------------------------------------------------
import sys

import anki_mcp_server  # noqa: F401 – triggers vendor path setup

for _key, _mod in list(sys.modules.items()):
    if _key.startswith("mcp.") or _key == "mcp":
        _alias = f"anki_mcp_server.vendor.shared.{_key}"
        if _alias not in sys.modules:
            sys.modules[_alias] = _mod

# ---------------------------------------------------------------------------
# Now safe to import the transport and the rest of the MCP SDK.
# ---------------------------------------------------------------------------
import asyncio
import json

import anyio
import pytest
import pytest_asyncio

from mcp.server.fastmcp import FastMCP
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from anki_mcp_server.tunnel.in_memory_transport import InMemoryTransport


# ---------------------------------------------------------------------------
# Helpers (mirrors test_in_memory_transport.py)
# ---------------------------------------------------------------------------

def _make_request(method: str, params: dict | None = None, *, request_id: int = 1) -> str:
    """Build a JSON-RPC 2.0 request string."""
    msg: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _tool_call(name: str, arguments: dict, *, request_id: int) -> str:
    """Build a ``tools/call`` JSON-RPC request string."""
    return _make_request(
        "tools/call",
        {"name": name, "arguments": arguments},
        request_id=request_id,
    )


def _result_text(resp: dict) -> str:
    """Join all text content blocks of a ``tools/call`` result into one string."""
    content = resp.get("result", {}).get("content", [])
    return " ".join(str(c.get("text", "")) for c in content)


def _build_mcp() -> FastMCP:
    """Create a FastMCP instance configured exactly like the addon's HTTP path.

    Two tools with DISTINCT, identifiable payloads so any cross-talk between
    concurrent runs is detectable by content (mirrors the assertion philosophy
    of ``tests/e2e/test_concurrent_sessions.py``, which uses distinct top-level
    keys to detect response swapping):

    * ``echo_ping`` returns a string tagged ``ping:<msg>``.
    * ``echo_pong`` returns a string tagged ``pong:<msg>``.

    ``stateless_http=True`` mirrors ``mcp_server.py``'s FastMCP construction so
    the session-manager identity assertions reflect production wiring.
    """
    mcp = FastMCP("anki-mcp-test", stateless_http=True)

    @mcp.tool()
    def echo_ping(msg: str) -> str:
        """Return the message tagged as a ping."""
        return f"ping:{msg}"

    @mcp.tool()
    def echo_pong(msg: str) -> str:
        """Return the message tagged as a pong."""
        return f"pong:{msg}"

    return mcp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def mcp() -> FastMCP:
    """A real FastMCP instance with two distinct tools."""
    return _build_mcp()


@pytest_asyncio.fixture
async def tunnel_transport(mcp: FastMCP):
    """The tunnel's InMemoryTransport, backed by ``mcp._mcp_server``.

    This is the *exact* construction the tunnel uses in ``mcp_server.py``
    (``InMemoryTransport(self._mcp_instance._mcp_server)``).
    """
    t = InMemoryTransport(mcp._mcp_server)
    await t.start()
    yield t
    await t.stop()


# ---------------------------------------------------------------------------
# 1. Object identity
# ---------------------------------------------------------------------------

class TestObjectIdentity:
    """Both transports must operate on the SAME lowlevel ``Server`` object."""

    @pytest.mark.asyncio
    async def test_tunnel_runs_against_shared_mcp_server(self, mcp: FastMCP) -> None:
        """The tunnel's InMemoryTransport holds ``mcp._mcp_server`` itself.

        ``InMemoryTransport._run_server`` calls ``self._server.run(...)``; the
        ``self._server`` it was constructed with must be the very object the
        HTTP side would also run, not a copy.
        """
        transport = InMemoryTransport(mcp._mcp_server)
        assert transport._server is mcp._mcp_server

    @pytest.mark.asyncio
    async def test_http_session_manager_uses_shared_mcp_server(self, mcp: FastMCP) -> None:
        """The HTTP ``StreamableHTTPSessionManager`` wraps the same object.

        ``streamable_http_app()`` lazily builds the session manager with
        ``app=mcp._mcp_server`` (see vendored ``fastmcp/server.py``). After
        building the app, ``mcp._session_manager.app`` is that same object, so
        ``_handle_stateless_request`` runs ``mcp._mcp_server.run(...)`` — the
        identical object the tunnel runs.
        """
        # Building the app is what instantiates the session manager.
        mcp.streamable_http_app()

        assert mcp._session_manager is not None
        assert mcp._session_manager.app is mcp._mcp_server

    @pytest.mark.asyncio
    async def test_both_transports_share_one_object(self, mcp: FastMCP) -> None:
        """Tie it together: tunnel transport and HTTP manager share one object."""
        mcp.streamable_http_app()
        transport = InMemoryTransport(mcp._mcp_server)

        assert mcp._session_manager is not None
        assert transport._server is mcp._session_manager.app


# ---------------------------------------------------------------------------
# 2. Stateless flag is engaged on the HTTP path
# ---------------------------------------------------------------------------

class TestStatelessEngaged:
    """The HTTP session manager must actually be in stateless mode."""

    @pytest.mark.asyncio
    async def test_session_manager_is_stateless(self, mcp: FastMCP) -> None:
        """``stateless_http=True`` on FastMCP propagates to the session manager.

        If this ever flips to ``False``, the HTTP path would create one
        long-lived stateful ``ServerSession`` per connection, which is
        incompatible with sharing the server with the tunnel — this is the
        configuration half of the invariant.
        """
        mcp.streamable_http_app()

        assert mcp._session_manager is not None
        assert mcp._session_manager.stateless is True


# ---------------------------------------------------------------------------
# 3. Concurrent runs on the shared object do not cross-contaminate
# ---------------------------------------------------------------------------

class _RawRunner:
    """Drive ``Server.run()`` directly over an anyio memory stream pair.

    With ``stateless=True`` this reproduces the *core* of the HTTP path's
    ``_handle_stateless_request``: it calls
    ``app.run(read, write, app.create_initialization_options(),
    stateless=True)`` on the shared ``_mcp_server``, fed by an anyio in-memory
    stream pair created exactly the way the SDK transports do
    (``anyio.create_memory_object_stream``). HTTP framing (SSE / ASGI) is
    deliberately omitted — it is irrelevant to the object-sharing invariant and
    is exercised end-to-end by ``tests/e2e/test_concurrent_sessions.py``.

    The ``stateless`` flag is a constructor parameter precisely so the same
    harness can also drive the ``stateless=False`` path for the load-bearing
    negative test below.

    Responses are matched back to callers by JSON-RPC ``id``, the same approach
    ``InMemoryTransport`` uses. Note that a stateful run does NOT raise on a
    pre-init request — the vendored ``BaseSession._receive_loop`` catches the
    ``RuntimeError`` and replies with a JSON-RPC error response (see
    ``shared/session.py``), so the response still arrives here, keyed by id.
    """

    def __init__(self, server, *, stateless: bool) -> None:
        self._server = server
        self._stateless = stateless
        self._read_send = None
        self._write_recv = None
        self._server_task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[object, asyncio.Future] = {}

    async def start(self) -> None:
        our_send, our_recv = anyio.create_memory_object_stream[SessionMessage | Exception](10)
        sw_send, sw_recv = anyio.create_memory_object_stream[SessionMessage](10)
        self._read_send = our_send
        self._write_recv = sw_recv

        init_options = self._server.create_initialization_options()
        self._server_task = asyncio.create_task(
            self._run(our_recv, sw_send, init_options)
        )
        self._reader_task = asyncio.create_task(self._read_responses())

    async def _run(self, read_stream, write_stream, init_options) -> None:
        try:
            # stateless flag mirrors the call site under test. The HTTP
            # _handle_stateless_request and the tunnel both pass stateless=True;
            # the negative test passes stateless=False to prove the flag matters.
            await self._server.run(
                read_stream, write_stream, init_options, stateless=self._stateless
            )
        except asyncio.CancelledError:
            pass
        except Exception:  # pragma: no cover - surfaced via pending futures
            pass

    async def _read_responses(self) -> None:
        try:
            async for session_message in self._write_recv:
                root = session_message.message.root
                req_id = getattr(root, "id", None)
                if req_id is not None:
                    fut = self._pending.pop(req_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result(session_message)
        except (asyncio.CancelledError, anyio.ClosedResourceError, anyio.EndOfStream):
            pass

    async def call(self, body: str, *, timeout: float = 2.0) -> dict:
        assert self._read_send is not None
        raw = json.loads(body)
        message = JSONRPCMessage.model_validate(raw)
        request_id = message.root.id  # type: ignore[union-attr]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[request_id] = fut
        await self._read_send.send(SessionMessage(message=message))
        resp = await asyncio.wait_for(fut, timeout=timeout)
        return json.loads(resp.message.model_dump_json(by_alias=True, exclude_none=True))

    async def stop(self) -> None:
        if self._read_send is not None:
            await self._read_send.aclose()
            self._read_send = None
        if self._server_task is not None:
            try:
                await asyncio.wait_for(self._server_task, timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._server_task.cancel()
                try:
                    await self._server_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._server_task = None
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        if self._write_recv is not None:
            await self._write_recv.aclose()
            self._write_recv = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("runner stopped"))
        self._pending.clear()


class TestConcurrentRunsNoCrosstalk:
    """Two concurrent ``Server.run(stateless=True)`` on the shared object.

    One run is the real tunnel transport; the other is an independent raw
    stateless run that mirrors the HTTP per-request path. Both target the SAME
    ``mcp._mcp_server``. We fire many interleaved calls and assert every
    response matches its own request id AND its own tool, so a routing
    regression (response bleed-through, id mismatch) would fail loudly.

    What this guards: the shared ``Server`` instance must hold no per-request
    state on the object itself — all request state lives in the per-call
    ``ServerSession`` / context vars. If someone moved session state onto
    ``Server``, the two concurrent runs would corrupt each other and this test
    would catch it.
    """

    @pytest.mark.asyncio
    async def test_concurrent_runs_no_crosstalk(
        self, mcp: FastMCP, tunnel_transport: InMemoryTransport
    ) -> None:
        http_runner = _RawRunner(mcp._mcp_server, stateless=True)
        await http_runner.start()
        try:
            n = 25

            async def via_tunnel(i: int) -> None:
                req_id = 1000 + i
                body = _tool_call("echo_ping", {"msg": f"tunnel-{i}"}, request_id=req_id)
                resp = json.loads(await tunnel_transport.handle_request(body))
                assert resp["id"] == req_id, f"id mismatch on tunnel call {i}: {resp}"
                text = _result_text(resp)
                # Must carry THIS tool's payload and THIS call's argument, and
                # must NOT carry the other transport's payload (bleed-through).
                assert f"ping:tunnel-{i}" in text, (
                    f"tunnel call {i} missing its own payload: {text!r}"
                )
                assert "pong" not in text and "http-" not in text, (
                    f"tunnel call {i} got cross-contaminated payload: {text!r}"
                )

            async def via_http(i: int) -> None:
                req_id = 2000 + i
                body = _tool_call("echo_pong", {"msg": f"http-{i}"}, request_id=req_id)
                resp = await http_runner.call(body)
                assert resp["id"] == req_id, f"id mismatch on http call {i}: {resp}"
                text = _result_text(resp)
                assert f"pong:http-{i}" in text, (
                    f"http call {i} missing its own payload: {text!r}"
                )
                assert "ping" not in text and "tunnel-" not in text, (
                    f"http call {i} got cross-contaminated payload: {text!r}"
                )

            # Interleave the two transports so calls genuinely overlap.
            tasks = []
            for i in range(n):
                tasks.append(asyncio.create_task(via_tunnel(i)))
                tasks.append(asyncio.create_task(via_http(i)))
            await asyncio.gather(*tasks)
        finally:
            await http_runner.stop()


# ---------------------------------------------------------------------------
# 4. The stateless flag is load-bearing for the init lifecycle
# ---------------------------------------------------------------------------

class TestStatelessFlagIsLoadBearing:
    """Prove the flag matters — via the initialize lifecycle, not cross-talk.

    Mechanism (verified against vendored ``session.py``): a ``ServerSession``
    starts ``Initialized`` when ``stateless=True`` and ``NotInitialized``
    otherwise. For a non-initialize, non-ping request,
    ``ServerSession._received_request`` raises
    ``RuntimeError("Received request before initialization was complete")``.
    That ``RuntimeError`` does NOT crash ``Server.run`` — the surrounding
    ``BaseSession._receive_loop`` catches it in its request-validation
    ``except`` block, logs a warning, and replies with a JSON-RPC error
    (``code=INVALID_PARAMS``, ``message="Invalid request parameters"``). So both
    paths return a response keyed by the request id; they differ only in
    success vs. error. (Note: ``raise_exceptions`` does not gate this — it only
    affects handler-level errors during dispatch, which a pre-init request never
    reaches.)

    The SAME bare ``tools/call`` therefore yields:

    * stateless=True  -> a success ``result`` (per-request init handshake waived);
    * stateless=False -> a JSON-RPC ``error`` (pre-init rejection).

    This is the contrast that makes the flag's role observable and deterministic
    — same request, opposite flag, opposite outcome — without relying on raises,
    hangs, or timing.
    """

    @pytest.mark.asyncio
    async def test_stateful_run_rejects_bare_tool_call(self, mcp: FastMCP) -> None:
        """stateless=False: a bare tools/call comes back as a JSON-RPC error."""
        runner = _RawRunner(mcp._mcp_server, stateless=False)
        await runner.start()
        try:
            body = _tool_call("echo_ping", {"msg": "no-init"}, request_id=42)
            resp = await runner.call(body)

            assert resp["id"] == 42
            assert "error" in resp, (
                f"stateful run should reject a pre-init request, got: {resp}"
            )
            assert "result" not in resp, (
                f"stateful run unexpectedly answered a pre-init request: {resp}"
            )
        finally:
            await runner.stop()

    @pytest.mark.asyncio
    async def test_stateless_run_accepts_bare_tool_call(
        self, tunnel_transport: InMemoryTransport
    ) -> None:
        """stateless=True: the identical bare call returns a success result.

        Contrast partner to the negative test above — same request, opposite
        ``stateless`` flag, opposite outcome. The tunnel transport runs
        ``stateless=True``, which is exactly what the HTTP path does too.
        """
        body = _tool_call("echo_ping", {"msg": "no-init"}, request_id=42)
        resp = json.loads(await tunnel_transport.handle_request(body))

        assert resp["id"] == 42
        assert "result" in resp, f"expected success, got: {resp}"
        assert "error" not in resp
        assert "ping:no-init" in _result_text(resp)
