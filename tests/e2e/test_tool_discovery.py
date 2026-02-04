"""Tests for tool listing and discovery."""
from __future__ import annotations

from .helpers import list_tools


class TestToolDiscovery:
    """Tests for tool listing and discovery."""

    def test_tools_list_returns_tools(self):
        """Server should return a list of tools."""
        tools = list_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_list_decks_tool_exists(self):
        """list_decks tool should be registered."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "list_decks" in tool_names

    def test_find_notes_tool_exists(self):
        """findNotes tool should be registered."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "findNotes" in tool_names
