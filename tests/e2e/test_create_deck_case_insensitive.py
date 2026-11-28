"""E2E test: create_deck treats a case-different name as the existing deck.

Anki matches deck names case-insensitively, so create_deck("e2e::casedeck")
with an existing "E2E::CaseDeck" creates nothing and must say so.
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


class TestCreateDeckCaseInsensitive:
    def test_case_different_name_reports_existing_deck(self):
        deck_name = f"E2E::CaseDeck{unique_id()}"
        first = call_tool("create_deck", {"deck_name": deck_name})
        assert first["created"] is True

        second = call_tool("create_deck", {"deck_name": deck_name.lower()})

        assert second.get("isError") is not True
        assert second["created"] is False
        assert second["exists"] is True
        assert second["deckId"] == first["deckId"]
        assert second["deckName"] == first["deckName"]
        assert second["deckName"] != deck_name.lower()
        assert first["deckName"] in second["message"]
        assert second["decks"] == first["decks"]
        assert second["depth"] == 2
        assert second["createdParentDecks"] == []

    def test_case_different_partly_existing_path(self):
        """Only the top level exists: its spelling wins, the rest is created
        in the input's spelling."""
        root = f"E2ECaseRoot{unique_id()}"
        call_tool("create_deck", {"deck_name": root})

        result = call_tool("create_deck", {"deck_name": f"{root.lower()}::new::Leaf"})

        assert result.get("isError") is not True
        assert result["created"] is True
        stored = f"{root}::new::Leaf"
        assert result["deckName"] == stored
        assert result["decks"] == [root, f"{root}::new", stored]
        assert result["depth"] == 3
        assert result["createdParentDecks"] == [f"{root}::new"]
        assert result["message"] == (
            f'Successfully created deck "{stored}" and parent deck "{root}::new"'
        )
