"""Unit tests for store_media_file and store_media_files tools."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import queue
import socket
import statistics
import threading
import time
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from anki_mcp_server.handler_wrappers import HandlerError, _error_handler, _require_col
from anki_mcp_server.queue_bridge import QueueBridge, ToolRequest
from anki_mcp_server.request_processor import RequestProcessor
from anki_mcp_server.tool_decorator import _write_lock, register_tools
from anki_mcp_server.tunnel.in_memory_transport import InMemoryTransport


@pytest.fixture
def media_tools(real_mcp):
    from anki_mcp_server.handler_registry import _handlers
    from anki_mcp_server.tool_decorator import _registry

    return {
        "store_media_file": _registry["store_media_file"]["original"],
        "store_media_files": _registry["store_media_files"]["original"],
        "store_media_files_handler": _handlers["store_media_files"],
    }


@pytest.fixture
def media_env(install_mw, tmp_path: Path):
    media_dir = tmp_path / "collection.media"
    media_dir.mkdir()
    written: list[tuple[str, bytes]] = []
    dir_calls = 0
    config_calls = 0
    config_data: dict = {}

    def media_dir_fn() -> str:
        nonlocal dir_calls
        dir_calls += 1
        return str(media_dir)

    def write_data_fn(filename: str, data: bytes) -> str:
        written.append((filename, data))
        (media_dir / filename).write_bytes(data)
        return filename

    def get_config_fn(_name: str) -> dict:
        nonlocal config_calls
        config_calls += 1
        return config_data

    col = types.SimpleNamespace(
        db=object(),
        media=types.SimpleNamespace(dir=media_dir_fn, write_data=write_data_fn),
    )
    mw = types.SimpleNamespace(
        col=col,
        addonManager=types.SimpleNamespace(getConfig=get_config_fn),
        reset=lambda: None,
    )
    install_mw(mw)

    return types.SimpleNamespace(
        media_dir=media_dir,
        written=written,
        config_data=config_data,
        counts=lambda: {"dir_calls": dir_calls, "config_calls": config_calls},
    )


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class TestStoreMediaFilesBatch:
    def test_stores_multiple_files_from_mixed_sources(self, media_tools, media_env, tmp_path: Path):
        from anki_mcp_server.primitives.essential.tools.store_media_files_tool import (
            MediaFileEntry,
        )

        src_img = tmp_path / "pic.png"
        src_img.write_bytes(b"\x89PNG\r\n\x1a\n")

        class _FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        entries = [
            MediaFileEntry(filename="clip1.mp3", data=_b64(b"ID3clip1")),
            MediaFileEntry(filename="pic.png", path=str(src_img)),
            MediaFileEntry(filename="clip2.mp3", url="https://cdn.example.com/clip2.mp3"),
        ]

        with patch(
            "anki_mcp_server.media_validators.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))],
        ), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(b"ID3remote"),
        ):
            result = media_tools["store_media_files"](files=entries)

        assert result["stored"] == 3
        assert result["failed"] == 0
        assert result["total_requested"] == 3
        assert result["message"] == "Stored 3 of 3 media files"
        assert [r["status"] for r in result["results"]] == ["stored", "stored", "stored"]
        assert [r["details"]["source_type"] for r in result["results"]] == ["base64", "file", "url"]
        assert [name for name, _ in media_env.written] == ["clip1.mp3", "pic.png", "clip2.mp3"]

    def test_loads_config_and_media_dir_once_per_batch(self, media_tools, media_env):
        from anki_mcp_server.primitives.essential.tools.store_media_files_tool import (
            MediaFileEntry,
        )

        entries = [
            MediaFileEntry(filename=f"f{i}.mp3", data=_b64(f"audio{i}".encode()))
            for i in range(5)
        ]

        result = media_tools["store_media_files"](files=entries)

        assert result["stored"] == 5
        counts = media_env.counts()
        # O(1) per batch regardless of file count: 1 for get_max_notes_per_batch,
        # 1 for _load_config, and 1 for col.media.dir.
        assert counts["config_calls"] == 2
        assert counts["dir_calls"] == 1

    def test_partial_success_records_per_file_failures(self, media_tools, media_env, tmp_path: Path):
        from anki_mcp_server.primitives.essential.tools.store_media_files_tool import (
            MediaFileEntry,
        )

        entries = [
            MediaFileEntry(filename="good1.mp3", data=_b64(b"ID3one")),
            MediaFileEntry(filename="bad.json", data=_b64(b"{}")),
            MediaFileEntry(filename="missing_src.mp3"),
            MediaFileEntry(filename="multi_src.mp3", data=_b64(b"x"), path=str(tmp_path / "x.mp3")),
            MediaFileEntry(filename="empty.mp3", data=""),
            MediaFileEntry(filename="evil.mp3", url="http://127.0.0.1/audio.mp3"),
            MediaFileEntry(filename="", data=_b64(b"x")),
            MediaFileEntry(filename="good2.mp3", data=_b64(b"ID3two")),
        ]

        with patch(
            "anki_mcp_server.media_validators.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 0))],
        ):
            result = media_tools["store_media_files"](files=entries)

        assert result["stored"] == 2
        assert result["failed"] == 6
        assert result["total_requested"] == 8
        assert result["message"] == "Stored 2 of 8 media files (6 failed)"

        by_idx = {r["index"]: r for r in result["results"]}
        assert by_idx[0]["status"] == "stored"
        assert by_idx[7]["status"] == "stored"
        for idx in (1, 2, 3, 4, 5, 6):
            assert by_idx[idx]["status"] == "failed"
            assert "error" in by_idx[idx]

        assert "File type not allowed" in by_idx[1]["error"]
        assert "hint" in by_idx[1]
        assert "exactly one" in by_idx[2]["error"]
        assert "exactly one" in by_idx[3]["error"]
        assert "empty" in by_idx[4]["error"].lower()
        assert "blocked" in by_idx[5]["error"].lower()
        assert "empty" in by_idx[6]["error"].lower()

    def test_empty_files_array_raises_validation_error(self, media_tools, media_env):
        with pytest.raises(HandlerError) as exc_info:
            media_tools["store_media_files"](files=[])

        assert exc_info.value.code == "validation_error"

    def test_batch_limit_exceeded_raises_limit_exceeded(self, media_tools, media_env, monkeypatch):
        from anki_mcp_server.primitives.essential.tools.store_media_files_tool import (
            MediaFileEntry,
        )
        import anki_mcp_server.primitives.essential.tools.store_media_files_tool as mod

        monkeypatch.setattr(mod, "get_max_notes_per_batch", lambda: 2)
        entries = [
            MediaFileEntry(filename=f"f{i}.mp3", data=_b64(b"ID3"))
            for i in range(3)
        ]

        with pytest.raises(HandlerError) as exc_info:
            media_tools["store_media_files"](files=entries)

        assert exc_info.value.code == "limit_exceeded"
        assert exc_info.value.data == {"requested": 3, "maximum": 2}

    def test_sanitizes_filenames_in_batch(self, media_tools, media_env):
        from anki_mcp_server.primitives.essential.tools.store_media_files_tool import (
            MediaFileEntry,
        )

        entries = [MediaFileEntry(filename="../../clip.mp3", data=_b64(b"ID3"))]
        result = media_tools["store_media_files"](files=entries)

        assert result["stored"] == 1
        assert result["results"][0]["filename"] == "clip.mp3"
        assert result["results"][0]["filepath"] == str(media_env.media_dir / "clip.mp3")

    def test_single_file_tool_regression(self, media_tools, media_env):
        result = media_tools["store_media_file"](
            filename="single.mp3",
            data=_b64(b"ID3single"),
        )

        assert result["filename"] == "single.mp3"
        assert result["filepath"] == str(media_env.media_dir / "single.mp3")
        assert result["size"] == len(b"ID3single")
        assert result["details"]["source_type"] == "base64"
        assert 'Successfully stored media file "single.mp3"' in result["message"]


class _SimulatedQtMainThread:
    """Single worker thread executing closures like Anki's mw.taskman.run_on_main."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def schedule(self, fn) -> None:
        self._q.put(fn)

    def _run(self) -> None:
        while True:
            fn = self._q.get()
            if fn is None:
                break
            fn()

    def stop(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=2.0)


def _setup_mcp_pipeline():
    """Build an in-memory FastMCP -> ThreadPoolExecutor(4) -> QueueBridge -> RequestProcessor stack."""
    from mcp.server.fastmcp import FastMCP

    bridge = QueueBridge()
    bridge_requests = 0
    orig_send_request = bridge.send_request

    def counted_send_request(req: ToolRequest):
        nonlocal bridge_requests
        bridge_requests += 1
        return orig_send_request(req)

    bridge.send_request = counted_send_request  # type: ignore[method-assign]

    qt_thread = _SimulatedQtMainThread()
    processor = RequestProcessor(bridge, schedule_on_main=qt_thread.schedule)
    processor.start()
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bench-bridge")

    async def call_main_thread(tool_name: str, arguments: dict):
        request = ToolRequest(
            request_id=str(uuid.uuid4()),
            tool_name=tool_name,
            arguments=arguments,
        )
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(executor, bridge.send_request, request)
        if not response.success:
            raise RuntimeError(response.error or "Bridge error")
        return response.result

    mcp = FastMCP("anki-mcp-benchmark", stateless_http=True)
    register_tools(mcp, call_main_thread)

    def get_bridge_requests() -> int:
        return bridge_requests

    def reset_bridge_requests() -> None:
        nonlocal bridge_requests
        bridge_requests = 0

    def close() -> None:
        bridge.shutdown()
        processor.stop()
        executor.shutdown(wait=False)
        qt_thread.stop()

    return mcp, get_bridge_requests, reset_bridge_requests, close


async def _call_mcp_tool(transport: InMemoryTransport, name: str, arguments: dict, req_id: int) -> dict:
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    raw = await transport.handle_request(payload)
    assert raw is not None
    resp = json.loads(raw)
    assert "result" in resp, f"MCP error calling {name}: {resp}"
    assert resp["result"].get("isError") is not True, f"Tool error calling {name}: {resp}"
    return resp["result"]


class TestStoreMediaFilesPerformanceBenchmark:
    """Benchmarks comparing single-file store_media_file vs batch store_media_files
    and refresh_ui=True vs refresh_ui=False across the full MCP + QueueBridge +
    RequestProcessor pipeline. Run with `-s` to inspect the timing tables."""

    def test_benchmark_local_files_mcp_bridge_overhead(self, real_mcp, install_mw, tmp_path: Path):
        n_files = 100
        src_dir = tmp_path / "src_mp3s"
        src_dir.mkdir()
        media_dir = tmp_path / "collection.media"
        media_dir.mkdir()

        sample_mp3 = b"ID3" + b"\x00" * 4096
        file_entries: list[dict[str, str]] = []
        for i in range(n_files):
            filename = f"clip_{i:03d}.mp3"
            file_path = src_dir / filename
            file_path.write_bytes(sample_mp3)
            file_entries.append({"filename": filename, "path": str(file_path)})

        counters = {"dir_calls": 0, "config_calls": 0, "reset_calls": 0, "written": 0}

        def media_dir_fn() -> str:
            counters["dir_calls"] += 1
            return str(media_dir)

        def write_data_fn(filename: str, data: bytes) -> str:
            counters["written"] += 1
            (media_dir / filename).write_bytes(data)
            return filename

        def get_config_fn(_name: str) -> dict:
            counters["config_calls"] += 1
            return {}

        def reset_fn() -> None:
            counters["reset_calls"] += 1

        col = types.SimpleNamespace(
            db=object(),
            media=types.SimpleNamespace(dir=media_dir_fn, write_data=write_data_fn),
        )
        mw = types.SimpleNamespace(
            col=col,
            addonManager=types.SimpleNamespace(getConfig=get_config_fn),
            reset=reset_fn,
        )
        install_mw(mw)

        mcp, get_bridge_reqs, reset_bridge_reqs, close_pipeline = _setup_mcp_pipeline()

        async def _run_benchmarks():
            transport = InMemoryTransport(mcp._mcp_server)
            await transport.start()
            try:
                # Warm up FastMCP / Pydantic once before timing
                await _call_mcp_tool(transport, "store_media_file", file_entries[0], req_id=1)
                await _call_mcp_tool(transport, "store_media_files", {"files": file_entries[:1]}, req_id=2)

                single_times: list[float] = []
                batch_times: list[float] = []
                single_counts: dict[str, int] = {}
                batch_counts: dict[str, int] = {}

                for run_idx in range(3):
                    for k in counters:
                        counters[k] = 0
                    reset_bridge_reqs()
                    t0 = time.perf_counter()
                    for idx, entry in enumerate(file_entries):
                        await _call_mcp_tool(
                            transport,
                            "store_media_file",
                            entry,
                            req_id=1000 * (run_idx + 1) + idx,
                        )
                    single_times.append(time.perf_counter() - t0)
                    single_counts = {**counters, "bridge_requests": get_bridge_reqs()}

                    for k in counters:
                        counters[k] = 0
                    reset_bridge_reqs()
                    t0 = time.perf_counter()
                    await _call_mcp_tool(
                        transport,
                        "store_media_files",
                        {"files": file_entries},
                        req_id=9000 + run_idx,
                    )
                    batch_times.append(time.perf_counter() - t0)
                    batch_counts = {**counters, "bridge_requests": get_bridge_reqs()}

                return single_times, batch_times, single_counts, batch_counts
            finally:
                await transport.stop()

        try:
            single_times, batch_times, single_counts, batch_counts = asyncio.run(_run_benchmarks())
        finally:
            close_pipeline()

        single_median = statistics.median(single_times)
        batch_median = statistics.median(batch_times)
        speedup = single_median / batch_median

        print(
            f"\n[Benchmark: {n_files} local MP3 files via MCP + QueueBridge + RequestProcessor]\n"
            f"  100x store_media_file  : {single_median * 1000:7.2f} ms median "
            f"(bridge={single_counts['bridge_requests']}, config={single_counts['config_calls']}, "
            f"dir={single_counts['dir_calls']}, reset={single_counts['reset_calls']})\n"
            f"  1x   store_media_files : {batch_median * 1000:7.2f} ms median "
            f"(bridge={batch_counts['bridge_requests']}, config={batch_counts['config_calls']}, "
            f"dir={batch_counts['dir_calls']}, reset={batch_counts['reset_calls']})\n"
            f"  Speedup                : {speedup:6.1f}x faster"
        )

        assert single_counts == {
            "bridge_requests": n_files,
            "config_calls": n_files,
            "dir_calls": n_files,
            "reset_calls": 0,
            "written": n_files,
        }
        assert batch_counts == {
            "bridge_requests": 1,
            "config_calls": 2,
            "dir_calls": 1,
            "reset_calls": 0,
            "written": n_files,
        }
        assert batch_median < single_median / 5

    def test_benchmark_simulated_mw_reset_and_batch_speedup(self, real_mcp, install_mw, monkeypatch, tmp_path: Path):
        from anki_mcp_server.handler_registry import _handlers

        n_files = 100
        media_dir = tmp_path / "collection.media"
        media_dir.mkdir()
        payload_b64 = _b64(b"ID3" + b"\x00" * 4096)
        b64_entries = [{"filename": f"b64_{i:03d}.mp3", "data": payload_b64} for i in range(n_files)]

        counters = {"reset_calls": 0, "written": 0}

        def reset_fn() -> None:
            counters["reset_calls"] += 1
            # Lightweight 1ms simulation of Qt main-thread mw.reset() view rebuild.
            time.sleep(0.001)

        def write_data_fn(filename: str, data: bytes) -> str:
            counters["written"] += 1
            (media_dir / filename).write_bytes(data)
            return filename

        col = types.SimpleNamespace(
            db=object(),
            media=types.SimpleNamespace(dir=lambda: str(media_dir), write_data=write_data_fn),
        )
        mw = types.SimpleNamespace(
            col=col,
            addonManager=types.SimpleNamespace(getConfig=lambda _name: {}),
            reset=reset_fn,
        )
        install_mw(mw)

        from anki_mcp_server.tool_decorator import _registry

        legacy_handler = _error_handler(
            _require_col(_write_lock(_registry["store_media_file"]["original"], refresh_ui=True))
        )
        optimized_single_handler = _handlers["store_media_file"]

        mcp, get_bridge_reqs, reset_bridge_reqs, close_pipeline = _setup_mcp_pipeline()

        async def _run_three_modes():
            transport = InMemoryTransport(mcp._mcp_server)
            await transport.start()
            try:
                # Warm up once without hitting simulated reset delay
                await _call_mcp_tool(transport, "store_media_file", b64_entries[0], req_id=1)
                await _call_mcp_tool(transport, "store_media_files", {"files": b64_entries[:1]}, req_id=2)

                # 1. Legacy single-file (refresh_ui=True -> 100x mw.reset())
                monkeypatch.setitem(_handlers, "store_media_file", legacy_handler)
                counters["reset_calls"] = 0
                counters["written"] = 0
                reset_bridge_reqs()
                t0 = time.perf_counter()
                for idx, entry in enumerate(b64_entries):
                    await _call_mcp_tool(transport, "store_media_file", entry, req_id=1000 + idx)
                legacy_elapsed = time.perf_counter() - t0
                legacy_resets = counters["reset_calls"]
                legacy_bridge = get_bridge_reqs()
                legacy_written = counters["written"]

                # 2. Optimized single-file (refresh_ui=False -> 0x mw.reset())
                monkeypatch.setitem(_handlers, "store_media_file", optimized_single_handler)
                counters["reset_calls"] = 0
                counters["written"] = 0
                reset_bridge_reqs()
                t0 = time.perf_counter()
                for idx, entry in enumerate(b64_entries):
                    await _call_mcp_tool(transport, "store_media_file", entry, req_id=2000 + idx)
                single_no_reset_elapsed = time.perf_counter() - t0
                single_resets = counters["reset_calls"]
                single_bridge = get_bridge_reqs()
                single_written = counters["written"]

                # 3. Bulk upload (store_media_files, refresh_ui=False -> 1 MCP call, 0x mw.reset())
                counters["reset_calls"] = 0
                counters["written"] = 0
                reset_bridge_reqs()
                t0 = time.perf_counter()
                await _call_mcp_tool(transport, "store_media_files", {"files": b64_entries}, req_id=3000)
                batch_elapsed = time.perf_counter() - t0
                batch_resets = counters["reset_calls"]
                batch_bridge = get_bridge_reqs()
                batch_written = counters["written"]

                return (
                    (legacy_elapsed, legacy_resets, legacy_bridge, legacy_written),
                    (single_no_reset_elapsed, single_resets, single_bridge, single_written),
                    (batch_elapsed, batch_resets, batch_bridge, batch_written),
                )
            finally:
                await transport.stop()

        try:
            legacy_stats, single_stats, batch_stats = asyncio.run(_run_three_modes())
        finally:
            close_pipeline()

        legacy_elapsed, legacy_resets, legacy_bridge, legacy_written = legacy_stats
        single_elapsed, single_resets, single_bridge, single_written = single_stats
        batch_elapsed, batch_resets, batch_bridge, batch_written = batch_stats

        print(
            f"\n[Benchmark: {n_files} base64 MP3 files with 1ms simulated mw.reset()]\n"
            f"  Legacy single (refresh_ui=True)     : {legacy_elapsed * 1000:7.2f} ms "
            f"(bridge={legacy_bridge}, reset={legacy_resets})\n"
            f"  Single no-reset (refresh_ui=False)  : {single_elapsed * 1000:7.2f} ms "
            f"(bridge={single_bridge}, reset={single_resets})\n"
            f"  Bulk store_media_files              : {batch_elapsed * 1000:7.2f} ms "
            f"(bridge={batch_bridge}, reset={batch_resets})\n"
            f"  Total speedup vs legacy             : {legacy_elapsed / batch_elapsed:6.1f}x faster"
        )

        assert legacy_resets == n_files and legacy_bridge == n_files and legacy_written == n_files
        assert single_resets == 0 and single_bridge == n_files and single_written == n_files
        assert batch_resets == 0 and batch_bridge == 1 and batch_written == n_files
        assert legacy_elapsed > single_elapsed > batch_elapsed
        assert batch_elapsed < legacy_elapsed / 10
