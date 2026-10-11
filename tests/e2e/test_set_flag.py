"""Tests for the card_set_flag tool."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


class TestSetFlag:
    """Tests for the card_set_flag tool."""

    def _create_card(self, uid: str, deck_name: str) -> int:
        """Helper to create a deck, add a note, and return the card ID."""
        call_tool("create_deck", {"deck_name": deck_name})
        note_result = call_tool("add_note", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}",
            }
        })
        note_id = note_result["note_id"]
        notes_info = call_tool("notes_info", {"notes": [note_id]})
        return notes_info["notes"][0]["cards"][0]

    def test_set_flag_basic(self):
        """card_set_flag should set a red flag on a card."""
        uid = unique_id()
        deck_name = f"E2E::SetFlag{uid}"
        card_id = self._create_card(uid, deck_name)

        result = call_tool("card_set_flag", {
            "card_ids": [card_id],
            "flag": 1,
        })

        assert result.get("isError") is not True
        assert result["flagged_count"] == 1
        assert result["flag"] == 1
        assert card_id in result["card_ids"]
        assert "message" in result

    def test_remove_flag(self):
        """card_set_flag with flag=0 should remove an existing flag."""
        uid = unique_id()
        deck_name = f"E2E::RemoveFlag{uid}"
        card_id = self._create_card(uid, deck_name)

        # First set a flag
        set_result = call_tool("card_set_flag", {
            "card_ids": [card_id],
            "flag": 2,
        })
        assert set_result.get("isError") is not True

        # Now remove the flag
        result = call_tool("card_set_flag", {
            "card_ids": [card_id],
            "flag": 0,
        })

        assert result.get("isError") is not True
        assert result["flagged_count"] == 1
        assert result["flag"] == 0
        assert "message" in result

    def test_set_flag_invalid_value(self):
        """card_set_flag should error when the flag value is out of range."""
        uid = unique_id()
        deck_name = f"E2E::FlagInvalid{uid}"
        card_id = self._create_card(uid, deck_name)

        result = call_tool("card_set_flag", {
            "card_ids": [card_id],
            "flag": 8,
        })

        assert result.get("isError") is True
        assert "invalid flag" in str(result).lower() or "flag" in str(result).lower()

    def test_set_flag_negative_value(self):
        """card_set_flag should error when the flag value is negative."""
        uid = unique_id()
        deck_name = f"E2E::FlagNeg{uid}"
        card_id = self._create_card(uid, deck_name)

        result = call_tool("card_set_flag", {
            "card_ids": [card_id],
            "flag": -1,
        })
        assert result.get("isError") is True

    def test_set_flag_empty_card_ids(self):
        """card_set_flag should error with empty card_ids."""
        result = call_tool("card_set_flag", {
            "card_ids": [],
            "flag": 1,
        })

        assert result.get("isError") is True
        assert "cannot be empty" in str(result)
