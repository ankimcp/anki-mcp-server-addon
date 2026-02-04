"""Tests for card_management multi-action tool."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools


class TestCardManagement:
    """Tests for card_management multi-action tool."""

    def test_card_management_tool_exists(self):
        """card_management tool should be registered."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "card_management" in tool_names

    def test_reposition_action_basic(self):
        """reposition action should reposition new cards."""
        uid = unique_id()
        deck_name = f"E2E::Reposition{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add 3 notes to create 3 cards
        card_ids = []
        for i in range(3):
            result = call_tool("addNote", {
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
            notes_info = call_tool("notesInfo", {"notes": [note_id]})
            if notes_info and notes_info.get("notes"):
                cards = notes_info["notes"][0].get("cards", [])
                if cards:
                    card_ids.append(cards[0])

        assert len(card_ids) == 3

        # Reposition cards starting at position 100
        result = call_tool("card_management", {
            "params": {
                "action": "reposition",
                "card_ids": card_ids,
                "starting_from": 100,
                "step_size": 1
            }
        })

        # Should not error
        assert result.get("isError") is not True

        # Should have repositioned field
        assert "repositioned" in result
        assert result["repositioned"] == 3
        assert "message" in result
        assert "100" in result["message"]

    def test_reposition_action_with_randomize(self):
        """reposition action should handle randomize parameter."""
        uid = unique_id()
        deck_name = f"E2E::RepositionRand{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add 2 notes
        card_ids = []
        for i in range(2):
            result = call_tool("addNote", {
                "deck_name": deck_name,
                "model_name": "Basic",
                "fields": {
                    "Front": f"Q{i} {uid}",
                    "Back": f"A{i} {uid}"
                }
            })
            note_id = result["note_id"]
            notes_info = call_tool("notesInfo", {"notes": [note_id]})
            if notes_info and notes_info.get("notes"):
                cards = notes_info["notes"][0].get("cards", [])
                if cards:
                    card_ids.append(cards[0])

        # Reposition with randomize
        result = call_tool("card_management", {
            "params": {
                "action": "reposition",
                "card_ids": card_ids,
                "starting_from": 50,
                "randomize": True
            }
        })

        assert result.get("isError") is not True
        assert result["repositioned"] == 2

    def test_reposition_empty_card_ids(self):
        """reposition action should error with empty card_ids."""
        result = call_tool("card_management", {
            "params": {
                "action": "reposition",
                "card_ids": []
            }
        })

        assert result.get("isError") is True
        assert "cannot be empty" in str(result)

    def test_reposition_invalid_starting_from(self):
        """reposition action should error with negative starting_from."""
        uid = unique_id()
        deck_name = f"E2E::RepositionInvalid{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add one note
        note_result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {"Front": f"Q {uid}", "Back": f"A {uid}"}
        })
        note_id = note_result["note_id"]
        notes_info = call_tool("notesInfo", {"notes": [note_id]})
        card_id = notes_info["notes"][0]["cards"][0]

        # Try reposition with negative starting_from
        result = call_tool("card_management", {
            "params": {
                "action": "reposition",
                "card_ids": [card_id],
                "starting_from": -1
            }
        })

        assert result.get("isError") is True
        assert "starting_from must be >= 0" in str(result)

    def test_reposition_invalid_step_size(self):
        """reposition action should error with invalid step_size."""
        uid = unique_id()
        deck_name = f"E2E::RepositionStep{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add one note
        note_result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {"Front": f"Q {uid}", "Back": f"A {uid}"}
        })
        note_id = note_result["note_id"]
        notes_info = call_tool("notesInfo", {"notes": [note_id]})
        card_id = notes_info["notes"][0]["cards"][0]

        # Try reposition with step_size = 0
        result = call_tool("card_management", {
            "params": {
                "action": "reposition",
                "card_ids": [card_id],
                "step_size": 0
            }
        })

        assert result.get("isError") is True
        assert "step_size must be >= 1" in str(result)

    def test_change_deck_basic(self):
        """changeDeck action should move cards to target deck."""
        uid = unique_id()
        source_deck = f"E2E::Source{uid}"
        target_deck = f"E2E::Target{uid}"

        # Create source deck
        call_tool("create_deck", {"deck_name": source_deck})

        # Add 2 notes to source deck
        card_ids = []
        for i in range(2):
            result = call_tool("addNote", {
                "deck_name": source_deck,
                "model_name": "Basic",
                "fields": {
                    "Front": f"Q{i} {uid}",
                    "Back": f"A{i} {uid}"
                }
            })
            note_id = result["note_id"]
            notes_info = call_tool("notesInfo", {"notes": [note_id]})
            if notes_info and notes_info.get("notes"):
                cards = notes_info["notes"][0].get("cards", [])
                if cards:
                    card_ids.append(cards[0])

        assert len(card_ids) == 2

        # Move cards to target deck (will be created)
        result = call_tool("card_management", {
            "params": {
                "action": "changeDeck",
                "card_ids": card_ids,
                "deck": target_deck
            }
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
        """changeDeck action should handle nested deck names."""
        uid = unique_id()
        source_deck = f"E2E::NestedSource{uid}"
        target_deck = f"E2E::Parent{uid}::Child{uid}"

        call_tool("create_deck", {"deck_name": source_deck})

        # Add one note
        result = call_tool("addNote", {
            "deck_name": source_deck,
            "model_name": "Basic",
            "fields": {"Front": f"Q {uid}", "Back": f"A {uid}"}
        })
        note_id = result["note_id"]
        notes_info = call_tool("notesInfo", {"notes": [note_id]})
        card_id = notes_info["notes"][0]["cards"][0]

        # Move to nested deck
        result = call_tool("card_management", {
            "params": {
                "action": "changeDeck",
                "card_ids": [card_id],
                "deck": target_deck
            }
        })

        assert result.get("isError") is not True
        assert result["moved"] == 1

        # Verify both parent and child decks exist
        decks = call_tool("list_decks")
        deck_names = [d["name"] for d in decks["decks"]]
        assert target_deck in deck_names

    def test_change_deck_empty_card_ids(self):
        """changeDeck action should error with empty card_ids."""
        result = call_tool("card_management", {
            "params": {
                "action": "changeDeck",
                "card_ids": [],
                "deck": "SomeDeck"
            }
        })

        assert result.get("isError") is True
        assert "cannot be empty" in str(result)

    def test_change_deck_missing_deck_param(self):
        """changeDeck action should error without deck parameter."""
        uid = unique_id()
        deck_name = f"E2E::NoDeck{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add one note
        note_result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {"Front": f"Q {uid}", "Back": f"A {uid}"}
        })
        note_id = note_result["note_id"]
        notes_info = call_tool("notesInfo", {"notes": [note_id]})
        card_id = notes_info["notes"][0]["cards"][0]

        # Try changeDeck without deck param - Pydantic validates this
        result = call_tool("card_management", {
            "params": {
                "action": "changeDeck",
                "card_ids": [card_id]
                # deck is missing - should fail validation
            }
        })

        assert result.get("isError") is True
        # Pydantic validation error for missing required field
        assert "deck" in str(result).lower() or "required" in str(result).lower()

    def test_change_deck_empty_deck_name(self):
        """changeDeck action should error with empty deck name."""
        uid = unique_id()
        deck_name = f"E2E::EmptyDeck{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add one note
        note_result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {"Front": f"Q {uid}", "Back": f"A {uid}"}
        })
        note_id = note_result["note_id"]
        notes_info = call_tool("notesInfo", {"notes": [note_id]})
        card_id = notes_info["notes"][0]["cards"][0]

        # Try changeDeck with whitespace-only string
        result = call_tool("card_management", {
            "params": {
                "action": "changeDeck",
                "card_ids": [card_id],
                "deck": "   "  # Whitespace only
            }
        })

        assert result.get("isError") is True
        assert "cannot be empty" in str(result)
