"""E2E tests for media security — path traversal and SSRF prevention."""
from __future__ import annotations

import pytest

from .helpers import call_tool


# 1x1 transparent PNG, valid base64
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
    "DUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class TestPathTraversalBlocked:
    """Verify that store_media_file rejects dangerous path values.

    Validation happens BEFORE any file I/O, so these tests work even though
    the referenced files don't exist inside the Docker container.
    """

    @pytest.mark.parametrize(
        "path, reason",
        [
            ("/etc/passwd", "no media extension"),
            ("/home/user/.ssh/id_rsa", "no extension"),
            ("/home/user/.env", "dotfile, no media MIME type"),
            ("/home/user/secrets.json", "JSON not allowed"),
            ("/home/user/data.csv", "CSV not allowed"),
            ("/tmp/noextension", "no extension at all"),
        ],
        ids=[
            "etc_passwd",
            "ssh_key",
            "dotfile",
            "json",
            "csv",
            "no_extension",
        ],
    )
    def test_path_rejected(self, path: str, reason: str):
        """Path with non-media MIME type should be rejected."""
        result = call_tool("store_media_file", {
            "filename": "test.jpg",
            "path": path,
        })
        assert result.get("isError") is True, (
            f"Expected error for path={path!r} ({reason}), got: {result}"
        )
        # Verify rejection is from MIME validator, not file-not-found
        result_str = str(result).lower()
        assert "not allowed" in result_str or "file type" in result_str, (
            f"Expected MIME type rejection but got: {result}"
        )


class TestSsrfBlocked:
    """Verify that store_media_file rejects dangerous URL schemes.

    Private IP blocking is covered by unit tests — it requires DNS resolution
    that may behave differently inside Docker. Scheme validation is deterministic.
    """

    @pytest.mark.parametrize(
        "url, reason",
        [
            ("file:///etc/passwd", "file:// scheme"),
            ("ftp://evil.com/file.jpg", "ftp:// scheme"),
            ("gopher://evil.com/", "gopher:// scheme"),
        ],
        ids=["file_scheme", "ftp_scheme", "gopher_scheme"],
    )
    def test_url_scheme_rejected(self, url: str, reason: str):
        """Non-HTTP(S) URL schemes should be rejected."""
        result = call_tool("store_media_file", {
            "filename": "test.jpg",
            "url": url,
        })
        assert result.get("isError") is True, (
            f"Expected error for url={url!r} ({reason}), got: {result}"
        )

    def test_loopback_url_blocked(self):
        """http://127.0.0.1 should be blocked by IP range check, not just scheme."""
        result = call_tool("store_media_file", {
            "filename": "test.jpg",
            "url": "http://127.0.0.1/image.jpg",
        })
        assert result.get("isError") is True


class TestFilenameSanitization:
    """Verify that filenames with traversal sequences are sanitized."""

    def test_dotdot_stripped_from_filename(self):
        """Filename '../../evil.png' should be sanitized — no '..' or '/' in result."""
        result = call_tool("store_media_file", {
            "filename": "../../evil.png",
            "data": TINY_PNG_B64,
        })
        assert result.get("isError") is not True, (
            f"Store should succeed after sanitization, got: {result}"
        )
        actual_name = result["filename"]
        assert ".." not in actual_name, f"Traversal sequence in filename: {actual_name}"
        assert "/" not in actual_name, f"Path separator in filename: {actual_name}"

    def test_slashes_stripped_from_filename(self):
        """Filename 'path/to/file.png' should have slashes removed."""
        result = call_tool("store_media_file", {
            "filename": "path/to/file.png",
            "data": TINY_PNG_B64,
        })
        assert result.get("isError") is not True, (
            f"Store should succeed after sanitization, got: {result}"
        )
        actual_name = result["filename"]
        assert "/" not in actual_name, f"Path separator in filename: {actual_name}"
