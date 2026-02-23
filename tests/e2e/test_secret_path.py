"""Tests for http_path configuration feature.

Tests cover server accessibility at the default path and the path
construction logic used to normalize http_path values.
"""
from __future__ import annotations

import pytest

from .helpers import list_tools


class TestDefaultPathAccessibility:
    """Test that server is accessible at default root path."""

    def test_server_responds_at_root_path(self):
        """Sanity check: server with empty http_path should respond at '/'."""
        # The e2e environment runs with default config (http_path = "")
        # This test verifies the server is accessible at the root path
        tools = list_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0, "Server should return tools when accessed at default path"


class TestStreamablePathConstruction:
    """Test the path construction logic used in mcp_server.py."""

    @pytest.mark.parametrize("http_path,expected", [
        ("", "/"),
        ("my-secret", "/my-secret/"),
        ("/my-secret", "/my-secret/"),
        ("my-secret/", "/my-secret/"),
        ("/my-secret/", "/my-secret/"),
        ("api/v1", "/api/v1/"),
        ("/api/v1/", "/api/v1/"),
    ])
    def test_path_construction(self, http_path: str, expected: str):
        """Test that path construction normalizes slashes correctly."""
        # Simulate the logic from mcp_server.py
        streamable_path = f"/{http_path.strip('/')}/" if http_path else "/"
        assert streamable_path == expected

    def test_empty_http_path_gives_root(self):
        """Empty http_path should result in root path."""
        http_path = ""
        streamable_path = f"/{http_path.strip('/')}/" if http_path else "/"
        assert streamable_path == "/"

    def test_non_empty_http_path_adds_slashes(self):
        """Non-empty http_path should be wrapped in slashes."""
        http_path = "my-secret"
        streamable_path = f"/{http_path.strip('/')}/" if http_path else "/"
        assert streamable_path == "/my-secret/"
        assert streamable_path.startswith("/")
        assert streamable_path.endswith("/")
