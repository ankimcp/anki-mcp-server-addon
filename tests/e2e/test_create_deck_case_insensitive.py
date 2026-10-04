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
