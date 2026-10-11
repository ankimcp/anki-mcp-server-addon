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

    def test_tool_count_on_default_server(self):
        """64 tools are registered. The default server (.docker/config.json)
        opts in change_note_type, so the three gated tools it leaves hidden
        are model_field_remove (destructive) and gui_deck_review /
        gui_answer_card (opt-in): 64 - 3 = 61.
        """
        names = [t["name"] for t in list_tools()]
        assert len(names) == len(set(names))
        assert len(names) == 61, sorted(names)

    def test_list_decks_tool_exists(self):
        """list_decks tool should be registered."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "list_decks" in tool_names

    def test_find_notes_tool_exists(self):
        """find_notes tool should be registered."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "find_notes" in tool_names
