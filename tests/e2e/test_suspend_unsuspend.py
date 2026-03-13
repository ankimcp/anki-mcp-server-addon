"""Tests for suspend/unsuspend actions in card_management tool."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


class TestSuspend:
    """Tests for suspend action in card_management tool."""

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

    def test_suspend_card(self):
        """suspend action should suspend cards."""
        uid = unique_id()
        deck_name = f"E2E::Suspend{uid}"
        card_id = self._create_card(uid, deck_name)

        result = call_tool("card_management", {
            "params": {
                "action": "suspend",
                "card_ids": [card_id],
            }
        })

        assert result.get("isError") is not True
        assert "suspended_count" in result
        assert result["suspended_count"] == 1
        assert "message" in result

    def test_suspend_empty_card_ids(self):
        """suspend action should error with empty card_ids."""
        result = call_tool("card_management", {
            "params": {
                "action": "suspend",
                "card_ids": [],
            }
        })

        assert result.get("isError") is True
        assert "cannot be empty" in str(result)


class TestUnsuspend:
    """Tests for unsuspend action in card_management tool."""

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

    def test_unsuspend_card(self):
        """unsuspend action should unsuspend previously suspended cards."""
        uid = unique_id()
        deck_name = f"E2E::Unsuspend{uid}"
        card_id = self._create_card(uid, deck_name)

        # First suspend the card
        suspend_result = call_tool("card_management", {
            "params": {
                "action": "suspend",
                "card_ids": [card_id],
            }
        })
        assert suspend_result.get("isError") is not True

        # Now unsuspend it
        result = call_tool("card_management", {
            "params": {
                "action": "unsuspend",
                "card_ids": [card_id],
            }
        })

        assert result.get("isError") is not True
        assert "unsuspended_count" in result
        assert result["unsuspended_count"] == 1
        assert "message" in result

    def test_unsuspend_empty_card_ids(self):
        """unsuspend action should error with empty card_ids."""
        result = call_tool("card_management", {
            "params": {
                "action": "unsuspend",
                "card_ids": [],
            }
        })

        assert result.get("isError") is True
        assert "cannot be empty" in str(result)
