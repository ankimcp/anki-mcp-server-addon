"""Tests for the card_* tool family: registration, card_reposition, card_change_deck."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools

CARD_TOOLS = (
    "card_reposition",
    "card_change_deck",
    "card_bury",
    "card_unbury",
    "card_suspend",
    "card_unsuspend",
    "card_set_flag",
    "card_set_due_date",
    "card_forget",
)


def _create_card(uid: str, deck_name: str) -> int:
    """Create a deck, add a Basic note to it, and return the card ID."""
    call_tool("create_deck", {"deck_name": deck_name})
    note_result = call_tool("add_note", {
        "deck_name": deck_name,
        "model_name": "Basic",
        "fields": {"Front": f"Q {uid}", "Back": f"A {uid}"}
    })
    note_id = note_result["note_id"]
    notes_info = call_tool("notes_info", {"notes": [note_id]})
    return notes_info["notes"][0]["cards"][0]


class TestCardTools:
    """Registration of the card_* tools."""

    def test_card_tools_registered(self):
        """Every card_* tool should be registered and the old dispatcher gone."""
        tools = list_tools()
        tool_names = {t["name"] for t in tools}
        for name in CARD_TOOLS:
            assert name in tool_names, f"{name} should be registered"
        assert "card_management" not in tool_names


class TestCardReposition:
    """Tests for the card_reposition tool."""

    def test_reposition_basic(self):
        """card_reposition should reposition new cards."""
        uid = unique_id()
        deck_name = f"E2E::Reposition{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add 3 notes to create 3 cards
        card_ids = []
        for i in range(3):
            result = call_tool("add_note", {
                "deck_name": deck_name,
                "model_name": "Basic",
                "fields": {
                    "Front": f"Q{i} {uid}",
                    "Back": f"A{i} {uid}"
                }
            })
            # Note creates one card (for Basic model)
            note_id = result["note_id"]
            # Get cards for this note
            notes_info = call_tool("notes_info", {"notes": [note_id]})
            if notes_info and notes_info.get("notes"):
                cards = notes_info["notes"][0].get("cards", [])
                if cards:
                    card_ids.append(cards[0])

        assert len(card_ids) == 3

        # Reposition cards starting at position 100
        result = call_tool("card_reposition", {
            "card_ids": card_ids,
            "starting_from": 100,
            "step_size": 1
        })

        # Should not error
        assert result.get("isError") is not True

        # Should have repositioned field
        assert "repositioned" in result
        assert result["repositioned"] == 3
        assert "message" in result
        assert "100" in result["message"]

    def test_reposition_with_randomize(self):
        """card_reposition should handle the randomize parameter."""
        uid = unique_id()
        deck_name = f"E2E::RepositionRand{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add 2 notes
        card_ids = []
        for i in range(2):
            result = call_tool("add_note", {
                "deck_name": deck_name,
                "model_name": "Basic",
                "fields": {
                    "Front": f"Q{i} {uid}",
                    "Back": f"A{i} {uid}"
                }
            })
            note_id = result["note_id"]
            notes_info = call_tool("notes_info", {"notes": [note_id]})
            if notes_info and notes_info.get("notes"):
                cards = notes_info["notes"][0].get("cards", [])
                if cards:
                    card_ids.append(cards[0])

        # Reposition with randomize
        result = call_tool("card_reposition", {
            "card_ids": card_ids,
            "starting_from": 50,
            "randomize": True
        })

        assert result.get("isError") is not True
        assert result["repositioned"] == 2

    def test_reposition_empty_card_ids(self):
        """card_reposition should error with empty card_ids."""
        result = call_tool("card_reposition", {"card_ids": []})

        assert result.get("isError") is True
        assert "cannot be empty" in str(result)

    def test_reposition_invalid_starting_from(self):
        """card_reposition should error with negative starting_from."""
        uid = unique_id()
        card_id = _create_card(uid, f"E2E::RepositionInvalid{uid}")

        result = call_tool("card_reposition", {
            "card_ids": [card_id],
            "starting_from": -1
        })

        assert result.get("isError") is True
        assert "starting_from must be >= 0" in str(result)

    def test_reposition_invalid_step_size(self):
        """card_reposition should error with invalid step_size."""
        uid = unique_id()
        card_id = _create_card(uid, f"E2E::RepositionStep{uid}")

        result = call_tool("card_reposition", {
            "card_ids": [card_id],
            "step_size": 0
        })

        assert result.get("isError") is True
        assert "step_size must be >= 1" in str(result)


class TestCardChangeDeck:
    """Tests for the card_change_deck tool."""

    def test_change_deck_basic(self):
        """card_change_deck should move cards to the target deck."""
        uid = unique_id()
        source_deck = f"E2E::Source{uid}"
        target_deck = f"E2E::Target{uid}"

        # Create source deck
        call_tool("create_deck", {"deck_name": source_deck})

        # Add 2 notes to source deck
        card_ids = []
        for i in range(2):
            result = call_tool("add_note", {
                "deck_name": source_deck,
                "model_name": "Basic",
                "fields": {
                    "Front": f"Q{i} {uid}",
                    "Back": f"A{i} {uid}"
                }
            })
            note_id = result["note_id"]
            notes_info = call_tool("notes_info", {"notes": [note_id]})
            if notes_info and notes_info.get("notes"):
                cards = notes_info["notes"][0].get("cards", [])
                if cards:
                    card_ids.append(cards[0])

        assert len(card_ids) == 2

        # Move cards to target deck (will be created)
        result = call_tool("card_change_deck", {
            "card_ids": card_ids,
            "deck": target_deck
        })

        # Should not error
        assert result.get("isError") is not True

        # Should have moved field
        assert "moved" in result
        assert result["moved"] == 2
        assert "deck_id" in result
        assert result["deck_id"] > 0
        assert "message" in result
        assert target_deck in result["message"]

        # Verify target deck was created
        decks = call_tool("list_decks")
        deck_names = [d["name"] for d in decks["decks"]]
        assert target_deck in deck_names

    def test_change_deck_nested(self):
        """card_change_deck should handle nested deck names."""
        uid = unique_id()
        target_deck = f"E2E::Parent{uid}::Child{uid}"
        card_id = _create_card(uid, f"E2E::NestedSource{uid}")

        # Move to nested deck
        result = call_tool("card_change_deck", {
            "card_ids": [card_id],
            "deck": target_deck
        })

        assert result.get("isError") is not True
        assert result["moved"] == 1

        # Verify both parent and child decks exist
        decks = call_tool("list_decks")
        deck_names = [d["name"] for d in decks["decks"]]
        assert target_deck in deck_names

    def test_change_deck_empty_card_ids(self):
        """card_change_deck should error with empty card_ids."""
        result = call_tool("card_change_deck", {
            "card_ids": [],
            "deck": "SomeDeck"
        })

        assert result.get("isError") is True
        assert "cannot be empty" in str(result)

    def test_change_deck_missing_deck_param(self):
        """card_change_deck should error without the deck parameter."""
        uid = unique_id()
        card_id = _create_card(uid, f"E2E::NoDeck{uid}")

        # deck is missing - Pydantic validates this
        result = call_tool("card_change_deck", {"card_ids": [card_id]})

        assert result.get("isError") is True
        # Pydantic validation error for missing required field
        assert "deck" in str(result).lower() or "required" in str(result).lower()

    def test_change_deck_empty_deck_name(self):
        """card_change_deck should error with an empty deck name."""
        uid = unique_id()
        card_id = _create_card(uid, f"E2E::EmptyDeck{uid}")

        result = call_tool("card_change_deck", {
            "card_ids": [card_id],
            "deck": "   "  # Whitespace only
        })

        assert result.get("isError") is True
        assert "cannot be empty" in str(result)
