"""E2E tests for media tools — store, list, delete."""
from __future__ import annotations

import pytest

from .conftest import unique_id
from .helpers import call_tool


# 1x1 transparent PNG, valid base64
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
    "DUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _store_test_file(filename: str | None = None) -> dict:
    """Store a tiny PNG and return the result. Generates a unique filename if none given."""
    if filename is None:
        filename = f"e2e_test_{unique_id()}.png"
    result = call_tool("store_media_file", {
        "filename": filename,
        "data": TINY_PNG_B64,
    })
    assert result.get("isError") is not True, f"Failed to store test file: {result}"
    return result


class TestStoreMediaFile:
    """Tests for store_media_file tool."""

    def test_store_via_base64(self):
        """Storing a valid base64 image should return filename and size."""
        result = _store_test_file()
        assert "filename" in result
        assert result["size"] > 0
        assert "message" in result

    @pytest.mark.skip(reason="MCP Inspector CLI rejects empty --tool-arg values before they reach the server")
    def test_store_empty_filename_fails(self):
        """Empty filename should be rejected."""
        result = call_tool("store_media_file", {
            "filename": "",
            "data": TINY_PNG_B64,
        })
        assert result.get("isError") is True

    def test_store_no_source_fails(self):
        """Calling without data, path, or url should error."""
        result = call_tool("store_media_file", {
            "filename": "orphan.png",
        })
        assert result.get("isError") is True

    def test_store_multiple_sources_fails(self):
        """Providing both data AND path should error."""
        result = call_tool("store_media_file", {
            "filename": "multi.png",
            "data": TINY_PNG_B64,
            "path": "/tmp/fake.png",
        })
        assert result.get("isError") is True


class TestGetMediaFilesNames:
    """Tests for get_media_files_names tool."""

    def test_list_media_files(self):
        """After storing a file, listing should include it."""
        stored = _store_test_file()
        stored_name = stored["filename"]

        result = call_tool("get_media_files_names", {})
        assert result["total"] > 0
        assert stored_name in result["files"]

    def test_list_with_pattern(self):
        """Pattern filter should match our uniquely-named file."""
        uid = unique_id()
        filename = f"e2e_pattern_{uid}.png"
        _store_test_file(filename)

        result = call_tool("get_media_files_names", {
            "pattern": f"*{uid}*",
        })
        assert result["total"] == 1
        assert filename in result["files"]

    def test_list_with_no_matches(self):
        """Pattern that matches nothing should return empty list."""
        result = call_tool("get_media_files_names", {
            "pattern": "zzz_nonexistent_pattern_zzz_*.xyz",
        })
        assert result["total"] == 0
        assert result["files"] == []


class TestDeleteMediaFile:
    """Tests for delete_media_file tool."""

    def test_delete_stored_file(self):
        """Deleting a stored file should succeed and mention trash."""
        stored = _store_test_file()
        filename = stored["filename"]

        result = call_tool("delete_media_file", {"filename": filename})
        assert result.get("isError") is not True, f"Delete failed: {result}"
        assert "trash" in result["message"].lower()

        # Verify file is actually gone from media listing
        listing = call_tool("get_media_files_names", {"pattern": filename})
        assert filename not in listing.get("files", [])

    def test_delete_nonexistent_file(self):
        """Deleting a file that doesn't exist should error."""
        result = call_tool("delete_media_file", {
            "filename": f"nonexistent_{unique_id()}.png",
        })
        assert result.get("isError") is True

    @pytest.mark.skip(reason="MCP Inspector CLI rejects empty --tool-arg values before they reach the server")
    def test_delete_empty_filename_fails(self):
        """Empty filename should be rejected."""
        result = call_tool("delete_media_file", {"filename": ""})
        assert result.get("isError") is True
