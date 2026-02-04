"""Tests for deck-related tools."""
from __future__ import annotations

from .helpers import call_tool


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
