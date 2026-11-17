"""Unit tests for store_media_file and store_media_files tools."""

from __future__ import annotations

import base64
import io
import socket
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from anki_mcp_server.handler_wrappers import HandlerError


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
