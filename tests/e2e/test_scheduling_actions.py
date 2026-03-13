"""Tests for set_due_date and forget_cards card_management actions."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


class TestSetDueDate:
    """Tests for set_due_date action in card_management tool."""

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

    def test_set_due_date_today(self):
        """set_due_date with days='0' should set cards due today."""
        uid = unique_id()
        deck_name = f"E2E::DueDate{uid}"
        card_id = self._create_card(uid, deck_name)

        result = call_tool("card_management", {
            "params": {
                "action": "set_due_date",
                "card_ids": [card_id],
                "days": "0",
            }
        })

        assert result.get("isError") is not True
        assert result["affected_count"] == 1
        assert "message" in result

    def test_set_due_date_with_range(self):
        """set_due_date with days='1-3' should accept a date range."""
        uid = unique_id()
        deck_name = f"E2E::DueDateRange{uid}"
        card_id = self._create_card(uid, deck_name)

        result = call_tool("card_management", {
            "params": {
                "action": "set_due_date",
                "card_ids": [card_id],
                "days": "1-3",
            }
        })

        assert result.get("isError") is not True
        assert result["affected_count"] == 1
        assert "message" in result

    def test_set_due_date_with_interval_reset(self):
        """set_due_date with days='5!' should set due date and reset interval."""
        uid = unique_id()
        deck_name = f"E2E::DueDateReset{uid}"
        card_id = self._create_card(uid, deck_name)

        result = call_tool("card_management", {
            "params": {
                "action": "set_due_date",
                "card_ids": [card_id],
                "days": "5!",
            }
        })

        assert result.get("isError") is not True
        assert result["affected_count"] == 1
        assert result["days"] == "5!"

    def test_set_due_date_empty_card_ids(self):
        """set_due_date should error with empty card_ids."""
        result = call_tool("card_management", {
            "params": {
                "action": "set_due_date",
                "card_ids": [],
                "days": "0",
            }
        })

        assert result.get("isError") is True
        assert "cannot be empty" in str(result)

    def test_set_due_date_empty_days(self):
        """set_due_date should error with empty days string."""
        uid = unique_id()
        deck_name = f"E2E::DueDateEmpty{uid}"
        card_id = self._create_card(uid, deck_name)

        result = call_tool("card_management", {
            "params": {
                "action": "set_due_date",
                "card_ids": [card_id],
                "days": "",
            }
        })

        assert result.get("isError") is True


class TestForgetCards:
    """Tests for forget_cards action in card_management tool."""

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

    def test_forget_cards_basic(self):
        """forget_cards should reset cards to new state."""
        uid = unique_id()
        deck_name = f"E2E::Forget{uid}"
        card_id = self._create_card(uid, deck_name)

        result = call_tool("card_management", {
            "params": {
                "action": "forget_cards",
                "card_ids": [card_id],
            }
        })

        assert result.get("isError") is not True
        assert result["affected_count"] == 1
        assert "message" in result

    def test_forget_cards_with_options(self):
        """forget_cards should accept restore_position and reset_counts options."""
        uid = unique_id()
        deck_name = f"E2E::ForgetOpts{uid}"
        card_id = self._create_card(uid, deck_name)

        result = call_tool("card_management", {
            "params": {
                "action": "forget_cards",
                "card_ids": [card_id],
                "restore_position": False,
                "reset_counts": True,
            }
        })

        assert result.get("isError") is not True
        assert result["affected_count"] == 1
        assert "message" in result

    def test_forget_cards_empty_card_ids(self):
        """forget_cards should error with empty card_ids."""
        result = call_tool("card_management", {
            "params": {
                "action": "forget_cards",
                "card_ids": [],
            }
        })

        assert result.get("isError") is True
        assert "cannot be empty" in str(result)
