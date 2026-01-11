"""E2E tests for MCP tools."""
from __future__ import annotations

import pytest

from .helpers import call_tool, list_tools


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


class TestDeckTools:
    """Tests for deck-related tools."""

    def test_list_decks(self):
        """list_decks should return decks array."""
        result = call_tool("list_decks")
        assert "decks" in result
        assert isinstance(result["decks"], list)
        # Default deck should always exist
        assert len(result["decks"]) >= 1

    def test_create_deck(self):
        """create_deck should create a new deck."""
        deck_name = "Test::E2E"
        result = call_tool("create_deck", {"deck_name": deck_name})
        assert "deckId" in result
        assert result["deckId"] > 0

        # Verify deck exists
        decks = call_tool("list_decks")
        deck_names = [d["name"] for d in decks["decks"]]
        assert deck_name in deck_names


class TestNoteTools:
    """Tests for note-related tools."""

    def test_find_notes_empty_query(self):
        """findNotes with empty collection should return empty list."""
        result = call_tool("findNotes", {"query": "deck:*"})
        assert "noteIds" in result

    def test_find_notes_with_limit(self):
        """findNotes should respect limit parameter."""
        result = call_tool("findNotes", {"query": "deck:*", "limit": "5"})
        # Should not error even if no notes exist
        assert "noteIds" in result
        assert result["limit"] == 5

    def test_find_notes_invalid_limit(self):
        """findNotes with invalid limit should return error."""
        result = call_tool("findNotes", {"query": "deck:*", "limit": "-1"})
        # Should have isError flag set (MCP error response format)
        assert result.get("isError") is True


class TestModelTools:
    """Tests for model/note type tools."""

    def test_model_names(self):
        """modelNames should return list of note types."""
        result = call_tool("modelNames")
        assert "modelNames" in result
        assert len(result["modelNames"]) > 0
