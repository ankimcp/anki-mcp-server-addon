"""E2E tests for MCP tools."""
from __future__ import annotations

import time
import pytest

from .helpers import call_tool, list_tools


def unique_id() -> str:
    """Generate unique suffix to avoid duplicate conflicts."""
    return str(int(time.time() * 1000))[-8:]


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


class TestGetDueCards:
    """Tests for get_due_cards tool."""

    def test_get_due_cards_tool_exists(self):
        """get_due_cards tool should be registered."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "get_due_cards" in tool_names

    def test_get_due_cards_requires_deck_name(self):
        """get_due_cards should error without deck_name parameter."""
        result = call_tool("get_due_cards", {})
        assert result.get("isError") is True

    def test_get_due_cards_invalid_deck(self):
        """get_due_cards should error for non-existent deck."""
        result = call_tool("get_due_cards", {
            "deck_name": f"NonExist{unique_id()}"
        })
        assert result.get("isError") is True

    def test_get_due_cards_empty_deck(self):
        """get_due_cards on empty deck should return empty cards with counts."""
        uid = unique_id()
        deck_name = f"E2E::EmptyDue{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        result = call_tool("get_due_cards", {"deck_name": deck_name})

        # Should not error
        assert result.get("isError") is not True

        # Should have required fields
        assert "cards" in result
        assert "counts" in result
        assert "total" in result
        assert "returned" in result
        assert "message" in result

        # Empty deck should have no cards
        assert result["cards"] == []
        assert result["total"] == 0
        assert result["returned"] == 0

        # Counts should be present (even if zero)
        assert "new" in result["counts"]
        assert "learning" in result["counts"]
        assert "review" in result["counts"]

    def test_get_due_cards_returns_new_card(self):
        """get_due_cards should return newly created card."""
        uid = unique_id()
        deck_name = f"E2E::Due{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add a note (creates a card)
        note_result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Test Question {uid}",
                "Back": f"Test Answer {uid}"
            }
        })
        assert "note_id" in note_result

        # Get due cards
        result = call_tool("get_due_cards", {"deck_name": deck_name})

        # Should have one card
        assert len(result["cards"]) == 1
        assert result["returned"] == 1
        assert result["total"] >= 1

        # Verify card structure
        card = result["cards"][0]
        assert "cardId" in card
        assert "front" in card
        assert "back" in card
        assert "deckName" in card
        assert "modelName" in card
        assert "queueType" in card
        assert "due" in card
        assert "interval" in card
        assert "factor" in card

        # Verify content
        assert f"Test Question {uid}" in card["front"]
        assert f"Test Answer {uid}" in card["back"]
        assert card["deckName"] == deck_name
        assert card["modelName"] == "Basic"
        # New cards should be in "new" queue
        assert card["queueType"] == "new"

    def test_get_due_cards_response_structure(self):
        """get_due_cards response should have all expected fields."""
        uid = unique_id()
        deck_name = f"E2E::Struct{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add a card to ensure non-empty response
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {"Front": "Q", "Back": "A"}
        })

        result = call_tool("get_due_cards", {"deck_name": deck_name})

        # Top-level fields
        assert "cards" in result
        assert "counts" in result
        assert "total" in result
        assert "returned" in result
        assert "message" in result

        # Verify types
        assert isinstance(result["cards"], list)
        assert isinstance(result["counts"], dict)
        assert isinstance(result["total"], int)
        assert isinstance(result["returned"], int)
        assert isinstance(result["message"], str)

        # Counts structure
        assert "new" in result["counts"]
        assert "learning" in result["counts"]
        assert "review" in result["counts"]
        assert isinstance(result["counts"]["new"], int)
        assert isinstance(result["counts"]["learning"], int)
        assert isinstance(result["counts"]["review"], int)

        # If cards exist, verify card structure
        if result["cards"]:
            card = result["cards"][0]
            assert isinstance(card["cardId"], int)
            assert isinstance(card["front"], str)
            assert isinstance(card["back"], str)
            assert isinstance(card["deckName"], str)
            assert isinstance(card["modelName"], str)
            assert isinstance(card["queueType"], str)
            assert isinstance(card["due"], int)
            assert isinstance(card["interval"], int)
            assert isinstance(card["factor"], int)
