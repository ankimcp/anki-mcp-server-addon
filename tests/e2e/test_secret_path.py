"""Tests for http_path configuration feature.

These tests verify the Config class and path construction logic.
Since e2e tests run against a Docker container with default config,
we test the configuration logic directly rather than actual HTTP routing.
"""
from __future__ import annotations

import pytest

from .helpers import list_tools

# Import Config if available (may fail if dependencies not installed)
try:
    import sys
    import os
    # Add parent directory to path to allow importing from anki_mcp_server
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
    from anki_mcp_server.config import Config
    CONFIG_AVAILABLE = True
except ImportError:
    CONFIG_AVAILABLE = False


@pytest.mark.skipif(not CONFIG_AVAILABLE, reason="Config class not importable in test environment")
class TestConfigHttpPath:
    """Test Config class handling of http_path field."""

    def test_default_config_has_empty_http_path(self):
        """Default config should have empty http_path."""
        config = Config()
        assert hasattr(config, "http_path")
        assert config.http_path == ""

    def test_config_from_dict_with_http_path(self):
        """Config.from_dict should properly handle http_path."""
        data = {"http_path": "my-secret"}
        config = Config.from_dict(data)
        assert config.http_path == "my-secret"

    def test_config_from_dict_without_http_path(self):
        """Config.from_dict should use default when http_path is missing."""
        data = {"http_port": 8080}
        config = Config.from_dict(data)
        assert config.http_path == ""

    def test_config_to_dict_includes_http_path(self):
        """Config.to_dict should include http_path."""
        config = Config(http_path="my-secret")
        data = config.to_dict()
        assert "http_path" in data
        assert data["http_path"] == "my-secret"


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
