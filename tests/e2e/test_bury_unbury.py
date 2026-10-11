"""Tests for the card_bury and card_unbury tools."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


class TestBuryUnbury:
    """Tests for the card_bury and card_unbury tools."""

    def test_bury_cards(self):
        """card_bury should bury cards and remove them from the queue."""
        uid = unique_id()
        deck_name = f"E2E::Bury{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add a note
        call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Get the card via get_due_cards
        get_result = call_tool("get_due_cards", {"deck_name": deck_name})
        assert get_result.get("isError") is not True
        assert len(get_result["cards"]) == 1
        card_id = get_result["cards"][0]["cardId"]

        # Bury the card
        result = call_tool("card_bury", {"card_ids": [card_id]})

        # Should succeed
        assert result.get("isError") is not True
        assert "buried_count" in result
        assert result["buried_count"] == 1
        assert "message" in result

        # Card should not appear in get_due_cards now
        get_result2 = call_tool("get_due_cards", {"deck_name": deck_name})
        assert get_result2.get("isError") is not True
        assert len(get_result2["cards"]) == 0  # Card is buried

    def test_bury_empty_card_ids_fails(self):
        """card_bury should error with empty card_ids."""
        result = call_tool("card_bury", {"card_ids": []})

        assert result.get("isError") is True
        assert "cannot be empty" in str(result)

    def test_unbury_deck(self):
        """card_unbury should restore buried cards to the queue."""
        uid = unique_id()
        deck_name = f"E2E::Unbury{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add a note
        call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Get the card
        get_result = call_tool("get_due_cards", {"deck_name": deck_name})
        assert get_result.get("isError") is not True
        card_id = get_result["cards"][0]["cardId"]

        # Bury the card
        bury_result = call_tool("card_bury", {"card_ids": [card_id]})
        assert bury_result.get("isError") is not True

        # Verify card is gone
        get_result2 = call_tool("get_due_cards", {"deck_name": deck_name})
        assert len(get_result2["cards"]) == 0

        # Unbury the deck
        unbury_result = call_tool("card_unbury", {"deck_name": deck_name})

        # Should succeed
        assert unbury_result.get("isError") is not True
        assert "message" in unbury_result

        # Card should be back in queue
        get_result3 = call_tool("get_due_cards", {"deck_name": deck_name})
        assert get_result3.get("isError") is not True
        assert len(get_result3["cards"]) == 1

    def test_unbury_invalid_deck_fails(self):
        """card_unbury should error with an invalid deck name."""
        result = call_tool("card_unbury", {"deck_name": "NonExistentDeck123XYZ"})

        assert result.get("isError") is True
        # Should mention deck not found or similar
        assert "deck" in str(result).lower() or "not found" in str(result).lower()

    def test_unbury_deck_with_no_buried_cards(self):
        """card_unbury should succeed gracefully when no cards are buried."""
        uid = unique_id()
        deck_name = f"E2E::UnburyEmpty{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add a note (not buried)
        call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Call unbury on deck with no buried cards
        result = call_tool("card_unbury", {"deck_name": deck_name})

        # Should succeed without error (no-op)
        assert result.get("isError") is not True
        assert "message" in result
