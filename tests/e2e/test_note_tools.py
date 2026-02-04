"""Tests for note-related tools."""
from __future__ import annotations

from .helpers import call_tool


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
