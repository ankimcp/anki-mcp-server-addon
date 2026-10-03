"""HTTP startup failure must not take the background loop (and the tunnel) down,
and ConnectionManager must report HTTP / loop state truthfully.

``McpServer._run_http_mode`` is driven on a real asyncio loop with
``uvicorn.Server`` mocked. The socket binder is either stubbed or, for the
real port-clash case, run against a port another socket in this test already
listens on (port 0, never a fixed port).
"""
from __future__ import annotations

import asyncio
import errno
import logging
import socket
import threading
import time
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# conftest.py installs aqt + primitives stubs before this module is collected,
# so the addon imports below are safe even without a running Anki.
from anki_mcp_server import connection_manager as cm_module
from anki_mcp_server import mcp_server as mcp_server_module
from anki_mcp_server.config import Config
from anki_mcp_server.mcp_server import McpServer
from anki_mcp_server.queue_bridge import QueueBridge


_LOGGER_NAME = "anki_mcp_server.mcp_server"


async def _trivial_asgi_app(scope, receive, send) -> None:  # pragma: no cover
    pass


def _make_server(port: int = 0) -> McpServer:
    return McpServer(MagicMock(spec=QueueBridge), Config(http_port=port, http_host="127.0.0.1"))


@pytest.fixture()
def server() -> Iterator[McpServer]:
    s = _make_server()
    yield s
    s._executor.shutdown(wait=False)


@pytest.fixture()
def mock_fastmcp() -> MagicMock:
    mcp = MagicMock()
    mcp.streamable_http_app.return_value = _trivial_asgi_app
    return mcp


@pytest.fixture()
def uvicorn_server() -> Iterator[MagicMock]:
    """The mocked ``uvicorn.Server`` instance ``_run_http_mode`` will build."""
    with patch.object(mcp_server_module.uvicorn, "Server") as server_cls:
        instance = server_cls.return_value
        instance.serve = AsyncMock()
        instance.started = False
        yield instance


@pytest.fixture()
def fake_socket() -> Iterator[MagicMock]:
    sock = MagicMock(spec=socket.socket)
    with patch.object(mcp_server_module, "_bind_http_sockets", return_value=[sock]):
        yield sock


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def _assert_parked_until_shutdown(server: McpServer, task: asyncio.Task) -> None:
    """The task must stay pending (loop kept alive) until _async_shutdown is set."""
    await _settle()
    assert not task.done(), "HTTP failure must not end _run_http_mode (it would end the loop)"
    server._async_shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# 1. Bind failure
# ---------------------------------------------------------------------------

class TestBindFailure:

    def test_bind_oserror_records_error_and_keeps_loop_alive(
        self,
        server: McpServer,
        mock_fastmcp: MagicMock,
        uvicorn_server: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER_NAME)

        async def scenario() -> None:
            server._async_shutdown = asyncio.Event()
            with patch.object(
                mcp_server_module, "_bind_http_sockets",
                side_effect=OSError(48, "Address already in use"),
            ):
                task = asyncio.create_task(server._run_http_mode(mock_fastmcp))
                await _assert_parked_until_shutdown(server, task)

        asyncio.run(scenario())

        uvicorn_server.serve.assert_not_awaited()
        assert server.http_error is not None
        assert server.http_error == "cannot listen on 127.0.0.1:0: Address already in use"
        assert server.http_started is False

        errors = [r for r in caplog.records if r.name == _LOGGER_NAME and r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert errors[0].getMessage() == (
            "HTTP server failed to start: cannot listen on 127.0.0.1:0: Address already in use"
        )
        assert errors[0].exc_info is None

    def test_real_port_clash(
        self,
        mock_fastmcp: MagicMock,
        uvicorn_server: MagicMock,
    ) -> None:
        """Another socket already listens on the port: the real binder fails."""
        blocker = socket.create_server(("127.0.0.1", 0))
        port = blocker.getsockname()[1]
        server = _make_server(port)
        try:
            async def scenario() -> None:
                server._async_shutdown = asyncio.Event()
                task = asyncio.create_task(server._run_http_mode(mock_fastmcp))
                await _assert_parked_until_shutdown(server, task)

            asyncio.run(scenario())
        finally:
            blocker.close()
            server._executor.shutdown(wait=False)

        uvicorn_server.serve.assert_not_awaited()
        assert server.http_error is not None
        assert server.http_error.count(f"127.0.0.1:{port}") == 1
        assert "while attempting" not in server.http_error
        assert server.http_started is False

    def test_unencodable_host_records_error_and_keeps_loop_alive(
        self,
        mock_fastmcp: MagicMock,
        uvicorn_server: MagicMock,
    ) -> None:
        """A >63-char label makes getaddrinfo raise UnicodeEncodeError, not OSError."""
        host = "a" * 64
        server = McpServer(MagicMock(spec=QueueBridge), Config(http_port=0, http_host=host))
        try:
            async def scenario() -> None:
                server._async_shutdown = asyncio.Event()
                task = asyncio.create_task(server._run_http_mode(mock_fastmcp))
                await _assert_parked_until_shutdown(server, task)

            asyncio.run(scenario())
        finally:
            server._executor.shutdown(wait=False)

        uvicorn_server.serve.assert_not_awaited()
        assert server.http_error is not None
        assert server.http_error.startswith(f"cannot listen on {host}:0: ")
        assert "label too long" in server.http_error
        assert server.http_started is False


# ---------------------------------------------------------------------------
# 2. Backstop — serve() fails before uvicorn reports started
# ---------------------------------------------------------------------------

class TestServeFailsBeforeStarted:

    def test_system_exit_from_serve(
        self,
        server: McpServer,
        mock_fastmcp: MagicMock,
        uvicorn_server: MagicMock,
        fake_socket: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER_NAME)
        uvicorn_server.serve.side_effect = SystemExit(3)

        async def scenario() -> None:
            server._async_shutdown = asyncio.Event()
            task = asyncio.create_task(server._run_http_mode(mock_fastmcp))
            await _assert_parked_until_shutdown(server, task)

        asyncio.run(scenario())

        uvicorn_server.serve.assert_awaited_once_with(sockets=[fake_socket])
        fake_socket.close.assert_called_once_with()
        assert server.http_error is not None
        assert "SystemExit" in server.http_error
        assert server.http_started is False
        errors = [r for r in caplog.records if r.name == _LOGGER_NAME and r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert errors[0].exc_info is None

    def test_uvicorn_error_message_used_as_reason(
        self,
        server: McpServer,
        mock_fastmcp: MagicMock,
        uvicorn_server: MagicMock,
        fake_socket: MagicMock,
    ) -> None:
        uvicorn_error = logging.getLogger("uvicorn.error")
        handlers_before = list(uvicorn_error.handlers)

        async def lifespan_fails(*args, **kwargs) -> None:
            uvicorn_error.error("lifespan startup failed: %s", "boom")
            raise SystemExit(3)

        uvicorn_server.serve.side_effect = lifespan_fails

        async def scenario() -> None:
            server._async_shutdown = asyncio.Event()
            task = asyncio.create_task(server._run_http_mode(mock_fastmcp))
            await _settle()
            assert uvicorn_error.handlers == handlers_before, "capture handler must be detached"
            await _assert_parked_until_shutdown(server, task)

        asyncio.run(scenario())

        assert server.http_error == "HTTP server failed to start: lifespan startup failed: boom"
        assert uvicorn_error.handlers == handlers_before

    def test_multiline_uvicorn_error_reduced_to_last_line(
        self,
        server: McpServer,
        mock_fastmcp: MagicMock,
        uvicorn_server: MagicMock,
        fake_socket: MagicMock,
    ) -> None:
        # Starlette hands uvicorn traceback.format_exc() on lifespan failure.
        traceback_text = (
            "Traceback (most recent call last):\n"
            '  File "starlette/routing.py", line 694, in lifespan\n'
            "    async with self.lifespan_context(app) as maybe_state:\n"
            '  File "mcp/server/streamable_http_manager.py", line 110, in run\n'
            "    raise RuntimeError('session manager boom')\n"
            "RuntimeError: session manager boom\n"
        )

        async def lifespan_fails(*args, **kwargs) -> None:
            logging.getLogger("uvicorn.error").error(traceback_text)
            raise SystemExit(3)

        uvicorn_server.serve.side_effect = lifespan_fails

        async def scenario() -> None:
            server._async_shutdown = asyncio.Event()
            task = asyncio.create_task(server._run_http_mode(mock_fastmcp))
            await _assert_parked_until_shutdown(server, task)

        asyncio.run(scenario())

        assert server.http_error == (
            "HTTP server failed to start: RuntimeError: session manager boom"
        )

    def test_exception_from_serve_logged_with_traceback(
        self,
        server: McpServer,
        mock_fastmcp: MagicMock,
        uvicorn_server: MagicMock,
        fake_socket: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER_NAME)
        uvicorn_server.serve.side_effect = RuntimeError("lifespan exploded")

        async def scenario() -> None:
            server._async_shutdown = asyncio.Event()
            task = asyncio.create_task(server._run_http_mode(mock_fastmcp))
            await _assert_parked_until_shutdown(server, task)

        asyncio.run(scenario())

        fake_socket.close.assert_called_once_with()
        assert "lifespan exploded" in server.http_error
        errors = [r for r in caplog.records if r.name == _LOGGER_NAME and r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert errors[0].exc_info is not None
        assert errors[0].exc_info[0] is RuntimeError

    def test_exception_after_started_propagates(
        self,
        server: McpServer,
        mock_fastmcp: MagicMock,
        uvicorn_server: MagicMock,
        fake_socket: MagicMock,
    ) -> None:
        async def fail_after_start(*args, **kwargs) -> None:
            uvicorn_server.started = True
            raise RuntimeError("died while serving")

        uvicorn_server.serve.side_effect = fail_after_start
        handlers_before = list(logging.getLogger("uvicorn.error").handlers)

        async def scenario() -> None:
            server._async_shutdown = asyncio.Event()
            await asyncio.wait_for(server._run_http_mode(mock_fastmcp), timeout=1.0)

        with pytest.raises(RuntimeError, match="died while serving"):
            asyncio.run(scenario())

        fake_socket.close.assert_called_once_with()
        assert server.http_error is None
        assert logging.getLogger("uvicorn.error").handlers == handlers_before

    def test_cancelled_error_not_swallowed(
        self,
        server: McpServer,
        mock_fastmcp: MagicMock,
        uvicorn_server: MagicMock,
        fake_socket: MagicMock,
    ) -> None:
        uvicorn_server.serve.side_effect = asyncio.CancelledError()
        handlers_before = list(logging.getLogger("uvicorn.error").handlers)

        async def scenario() -> None:
            server._async_shutdown = asyncio.Event()
            task = asyncio.create_task(server._run_http_mode(mock_fastmcp))
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1.0)

        asyncio.run(scenario())

        fake_socket.close.assert_called_once_with()
        assert server.http_error is None
        assert logging.getLogger("uvicorn.error").handlers == handlers_before


# ---------------------------------------------------------------------------
# 3. Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:

    def test_serve_returns_normally(
        self,
        server: McpServer,
        mock_fastmcp: MagicMock,
        uvicorn_server: MagicMock,
        fake_socket: MagicMock,
    ) -> None:
        async def serve(*args, **kwargs) -> None:
            uvicorn_server.started = True

        uvicorn_server.serve.side_effect = serve
        handlers_before = list(logging.getLogger("uvicorn.error").handlers)

        async def scenario() -> None:
            server._async_shutdown = asyncio.Event()
            await asyncio.wait_for(server._run_http_mode(mock_fastmcp), timeout=1.0)

        asyncio.run(scenario())

        uvicorn_server.serve.assert_awaited_once_with(sockets=[fake_socket])
        fake_socket.close.assert_called_once_with()
        assert server.http_error is None
        assert server.http_started is True
        assert logging.getLogger("uvicorn.error").handlers == handlers_before

    def test_http_started_mirrors_uvicorn_started(self, server: McpServer) -> None:
        assert server.http_started is False  # no uvicorn server yet
        server._uvicorn_server = SimpleNamespace(started=False)
        assert server.http_started is False
        server._uvicorn_server = SimpleNamespace(started=True)
        assert server.http_started is True

    def test_real_binder_returns_bound_socket(self) -> None:
        sockets = mcp_server_module._bind_http_sockets("127.0.0.1", 0)
        try:
            assert len(sockets) == 1
            assert sockets[0].getsockname()[0] == "127.0.0.1"
            assert sockets[0].getsockname()[1] != 0
        finally:
            for s in sockets:
                s.close()


# ---------------------------------------------------------------------------
# 3a. Binder socket options (parity with loop.create_server)
# ---------------------------------------------------------------------------

def _ipv6_loopback_available() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


@pytest.fixture()
def bound_sockets() -> Iterator[list[socket.socket]]:
    """Holds the binder's result so every socket is closed even on failure."""
    sockets: list[socket.socket] = []
    yield sockets
    for s in sockets:
        s.close()


class TestBinderSocketOptions:

    @pytest.mark.skipif(
        not mcp_server_module._REUSE_ADDRESS,
        reason="SO_REUSEADDR is only set where asyncio sets it (POSIX, non-cygwin)",
    )
    def test_reuse_address_set_on_posix(self, bound_sockets: list[socket.socket]) -> None:
        bound_sockets.extend(mcp_server_module._bind_http_sockets("127.0.0.1", 0))
        assert len(bound_sockets) == 1
        assert bound_sockets[0].getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) != 0

    def test_reuse_address_not_set_when_disabled(
        self, bound_sockets: list[socket.socket],
    ) -> None:
        with patch.object(mcp_server_module, "_REUSE_ADDRESS", False):
            bound_sockets.extend(mcp_server_module._bind_http_sockets("127.0.0.1", 0))
        assert len(bound_sockets) == 1
        assert bound_sockets[0].getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0

    @pytest.mark.skipif(not _ipv6_loopback_available(), reason="IPv6 loopback unavailable")
    def test_ipv6_v6only_set(self, bound_sockets: list[socket.socket]) -> None:
        bound_sockets.extend(mcp_server_module._bind_http_sockets("::1", 0))
        assert len(bound_sockets) == 1
        assert bound_sockets[0].family == socket.AF_INET6
        assert bound_sockets[0].getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) != 0


# ---------------------------------------------------------------------------
# 3b. Binder per-address skip rules (parity with loop.create_server)
# ---------------------------------------------------------------------------

def _inet_info(addr: str, port: int = 0) -> tuple:
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (addr, port))


@pytest.fixture()
def created_sockets() -> Iterator[list[socket.socket]]:
    """Every socket the binder creates, so a test can assert none leaked."""
    real_socket = socket.socket
    created: list[socket.socket] = []

    def make(*args, **kwargs) -> socket.socket:
        sock = real_socket(*args, **kwargs)
        created.append(sock)
        return sock

    with patch.object(mcp_server_module.socket, "socket", side_effect=make):
        yield created
    for sock in created:
        sock.close()


class TestBinderSkipRules:

    def test_unavailable_address_is_skipped(
        self, created_sockets: list[socket.socket],
    ) -> None:
        # 192.0.2.1 is TEST-NET-1: not configured on any local interface, so
        # bind() fails with EADDRNOTAVAIL, which asyncio skips (bpo-30945).
        infos = [_inet_info("127.0.0.1"), _inet_info("192.0.2.1")]
        with patch.object(mcp_server_module.socket, "getaddrinfo", return_value=infos):
            sockets = mcp_server_module._bind_http_sockets("example.invalid", 0)
        try:
            assert len(sockets) == 1
            assert sockets[0].getsockname()[0] == "127.0.0.1"
            skipped = [s for s in created_sockets if s not in sockets]
            assert skipped and all(s.fileno() == -1 for s in skipped)
        finally:
            for s in sockets:
                s.close()

    def test_no_address_bindable_raises_and_leaks_nothing(
        self, created_sockets: list[socket.socket],
    ) -> None:
        infos = [_inet_info("192.0.2.1"), _inet_info("198.51.100.1")]
        with patch.object(mcp_server_module.socket, "getaddrinfo", return_value=infos), \
                pytest.raises(OSError, match="could not bind on any address"):
            mcp_server_module._bind_http_sockets("example.invalid", 0)
        assert len(created_sockets) == 2
        assert all(s.fileno() == -1 for s in created_sockets)

    def test_socket_creation_failure_is_skipped(self) -> None:
        real_socket = socket.socket

        def make(family, *args, **kwargs) -> socket.socket:
            if family == socket.AF_INET6:
                raise OSError(errno.EAFNOSUPPORT, "Address family not supported")
            return real_socket(family, *args, **kwargs)

        infos = [
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::1", 0, 0, 0)),
            _inet_info("127.0.0.1"),
        ]
        with patch.object(mcp_server_module.socket, "getaddrinfo", return_value=infos), \
                patch.object(mcp_server_module.socket, "socket", side_effect=make):
            sockets = mcp_server_module._bind_http_sockets("localhost", 0)
        try:
            assert [s.getsockname()[0] for s in sockets] == ["127.0.0.1"]
        finally:
            for s in sockets:
                s.close()

    def test_other_bind_error_closes_bound_sockets_and_raises(
        self, created_sockets: list[socket.socket],
    ) -> None:
        blocker = socket.create_server(("127.0.0.1", 0))
        try:
            busy_port = blocker.getsockname()[1]
            infos = [_inet_info("127.0.0.1", 0), _inet_info("127.0.0.1", busy_port)]
            with patch.object(mcp_server_module.socket, "getaddrinfo", return_value=infos), \
                    pytest.raises(OSError) as excinfo:
                mcp_server_module._bind_http_sockets("127.0.0.1", busy_port)
        finally:
            blocker.close()
        assert excinfo.value.errno == errno.EADDRINUSE
        # socket.create_server resolves the patched name too, so drop the blocker.
        ours = [s for s in created_sockets if s is not blocker]
        assert len(ours) == 2
        assert all(s.fileno() == -1 for s in ours)


# ---------------------------------------------------------------------------
# 4. start_tunnel return value
# ---------------------------------------------------------------------------

class TestStartTunnelReturn:

    def test_false_without_loop(self, server: McpServer) -> None:
        assert server.start_tunnel(MagicMock(), MagicMock()) is False

    def test_false_with_closed_loop(self, server: McpServer) -> None:
        loop = asyncio.new_event_loop()
        loop.close()
        server._loop = loop
        assert server.start_tunnel(MagicMock(), MagicMock()) is False

    def test_true_when_scheduled(self, server: McpServer) -> None:
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        try:
            server._loop = loop
            with patch.object(server, "_start_tunnel_async", new=AsyncMock()) as start_async:
                assert server.start_tunnel(MagicMock(), MagicMock()) is True
                deadline = time.monotonic() + 2.0
                while start_async.await_count == 0 and time.monotonic() < deadline:
                    time.sleep(0.01)
                start_async.assert_awaited_once()
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2.0)
            loop.close()


# ---------------------------------------------------------------------------
# 5. loop_alive / fatal_error from the thread entry point
# ---------------------------------------------------------------------------

class TestThreadDeath:

    def test_run_records_fatal_error_and_loop_dies(
        self,
        server: McpServer,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.ERROR, logger=_LOGGER_NAME)
        running = threading.Event()
        release = threading.Event()

        async def fake_main() -> None:
            server._loop = asyncio.get_running_loop()
            running.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            raise RuntimeError("boom")

        with patch.object(server, "_async_main", new=fake_main):
            server.start()  # real thread running the real _run
            try:
                assert running.wait(timeout=2.0)
                assert server.loop_alive is True
                assert server.fatal_error is None
            finally:
                release.set()
                server._thread.join(timeout=2.0)

        assert not server._thread.is_alive()
        assert server.loop_alive is False
        assert server.fatal_error is not None
        assert "boom" in server.fatal_error
        assert any("background thread failed" in r.getMessage() for r in caplog.records)

    def test_run_does_not_raise(self, server: McpServer) -> None:
        with patch.object(server, "_async_main", new=AsyncMock(side_effect=RuntimeError("boom"))):
            server._run()  # must not raise
        assert server.fatal_error == "RuntimeError: boom"
        assert server.loop_alive is False


# ---------------------------------------------------------------------------
# 6. ConnectionManager — truthful HTTP state + loud tunnel failure
# ---------------------------------------------------------------------------

class _FakeTunnelLog:
    """Records entries; stands in for the QObject-based TunnelLog."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def info(self, message: str) -> None:
        self.entries.append(("info", message))

    def error(self, message: str) -> None:
        self.entries.append(("error", message))


class _LiveThread:
    """A real, alive thread for the duration of a test."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._stop.wait, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self._stop.set()
        self.thread.join(timeout=2.0)


@pytest.fixture()
def live_thread() -> Iterator[threading.Thread]:
    t = _LiveThread()
    yield t.thread
    t.close()


@pytest.fixture()
def open_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture()
def dead_thread() -> threading.Thread:
    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()
    return t


@pytest.fixture()
def closed_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    loop.close()
    return loop


@pytest.fixture()
def manager(monkeypatch: pytest.MonkeyPatch) -> Iterator[cm_module.ConnectionManager]:
    monkeypatch.setattr(cm_module, "TunnelLog", _FakeTunnelLog)
    monkeypatch.setattr(cm_module, "CredentialsManager", MagicMock)
    monkeypatch.setattr(cm_module, "DeviceFlowAuth", MagicMock)
    m = cm_module.ConnectionManager(Config(http_enabled=True))
    m._processor = MagicMock()
    yield m
    if m._server is not None:
        m._server._executor.shutdown(wait=False)


def _attach_server(
    manager: cm_module.ConnectionManager,
    *,
    loop: asyncio.AbstractEventLoop | None,
    thread: threading.Thread | None,
    started: bool | None,
    http_error: str | None = None,
    fatal_error: str | None = None,
) -> McpServer:
    s = _make_server()
    s._loop = loop
    s._thread = thread
    s._uvicorn_server = None if started is None else SimpleNamespace(started=started)
    s._http_error = http_error
    s._fatal_error = fatal_error
    manager._server = s
    return s


class TestConnectionManagerHttpState:

    def test_healthy(self, manager, open_loop, live_thread) -> None:
        _attach_server(manager, loop=open_loop, thread=live_thread, started=True)
        assert manager.http_running is True
        assert manager.http_error is None

    def test_dead_loop_after_started_is_not_running(
        self, manager, closed_loop, dead_thread,
    ) -> None:
        # uvicorn.Server.started is never reset, so the thread dying after a
        # successful start leaves started=True behind.
        _attach_server(
            manager, loop=closed_loop, thread=dead_thread, started=True,
            fatal_error="RuntimeError: boom",
        )
        assert manager.is_running is True
        assert manager.http_running is False
        assert manager.http_error == "RuntimeError: boom"

    def test_http_failed_loop_alive(self, manager, open_loop, live_thread) -> None:
        reason = "cannot listen on 127.0.0.1:3141: Address already in use"
        _attach_server(
            manager, loop=open_loop, thread=live_thread, started=None, http_error=reason,
        )
        assert manager.http_running is False
        assert manager.http_error == reason

    def test_http_error_takes_precedence_over_fatal(
        self, manager, closed_loop, dead_thread,
    ) -> None:
        _attach_server(
            manager, loop=closed_loop, thread=dead_thread, started=None,
            http_error="cannot listen", fatal_error="RuntimeError: boom",
        )
        assert manager.http_error == "cannot listen"

    def test_fatal_error_hidden_while_loop_alive(
        self, manager, open_loop, live_thread,
    ) -> None:
        _attach_server(
            manager, loop=open_loop, thread=live_thread, started=True,
            fatal_error="stale",
        )
        assert manager.http_error is None

    def test_http_disabled(self, manager, open_loop, live_thread) -> None:
        manager._config = Config(http_enabled=False)
        _attach_server(manager, loop=open_loop, thread=live_thread, started=True)
        assert manager.http_running is False


class TestConnectionManagerLoopError:

    def test_none_without_server(self, manager) -> None:
        assert manager._server is None
        assert manager.loop_error is None

    def test_none_while_loop_alive_despite_http_failure(
        self, manager, open_loop, live_thread,
    ) -> None:
        _attach_server(
            manager, loop=open_loop, thread=live_thread, started=None,
            http_error="cannot listen on 127.0.0.1:3141: Address already in use",
            fatal_error="stale",
        )
        assert manager.loop_error is None

    def test_fatal_error_when_loop_dead(self, manager, closed_loop, dead_thread) -> None:
        _attach_server(
            manager, loop=closed_loop, thread=dead_thread, started=None,
            http_error="cannot listen", fatal_error="RuntimeError: boom",
        )
        assert manager.loop_error == "RuntimeError: boom"


class TestConnectionManagerConnectTunnel:

    def test_dead_loop_writes_error_and_does_not_schedule(
        self, manager, closed_loop, dead_thread, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.WARNING, logger="anki_mcp_server.connection_manager")
        server = _attach_server(
            manager, loop=closed_loop, thread=dead_thread, started=True,
            fatal_error="RuntimeError: boom",
        )
        with patch.object(server, "start_tunnel") as start_tunnel:
            manager.connect_tunnel()

        start_tunnel.assert_not_called()
        entries = manager.tunnel_log.entries
        assert not any(msg == "Connecting to tunnel..." for _, msg in entries)
        assert entries[-1][0] == "error"
        assert "background loop is not running" in entries[-1][1]
        assert "RuntimeError: boom" in entries[-1][1]
        assert any(
            r.levelno == logging.WARNING and "background loop" in r.getMessage()
            for r in caplog.records
        )

    def test_start_tunnel_false_writes_error(
        self, manager, open_loop, live_thread,
    ) -> None:
        server = _attach_server(manager, loop=open_loop, thread=live_thread, started=True)
        with patch.object(server, "start_tunnel", return_value=False):
            manager.connect_tunnel()

        entries = manager.tunnel_log.entries
        assert ("info", "Connecting to tunnel...") in entries
        assert entries[-1][0] == "error"

    def test_scheduled_leaves_connecting_line_last(
        self, manager, open_loop, live_thread,
    ) -> None:
        server = _attach_server(manager, loop=open_loop, thread=live_thread, started=True)
        with patch.object(server, "start_tunnel", return_value=True) as start_tunnel:
            manager.connect_tunnel()

        start_tunnel.assert_called_once()
        assert manager.tunnel_log.entries[-1] == ("info", "Connecting to tunnel...")
