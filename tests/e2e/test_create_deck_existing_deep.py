"""E2E tests: create_deck's 10-level limit applies only to creating a deck.

An existing deck deeper than 10 levels (shared, imported, made in Anki's UI)
is reported as existing, while a name over 10 levels that doesn't exist yet is
rejected even when its parent does. The deep deck is built with
create_deck_past_create_limit (see its docstring for why that takes a
filtered deck).
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, create_deck_past_create_limit, deck_names, deck_path


class TestCreateDeckExistingDeep:
    def test_existing_eleven_level_deck_reported_as_existing(self):
        deep = create_deck_past_create_limit(f"E2EDeepExists{unique_id()}")

        result = call_tool("create_deck", {"deck_name": deep})

        assert result.get("isError") is not True
        assert result["created"] is False
        assert result["exists"] is True
        assert result["deckName"] == deep
        assert result["decks"] == deck_path(deep)
        assert result["depth"] == 11
        assert result["createdParentDecks"] == []
        assert result["message"] == f'Deck "{deep}" already exists'

    def test_existing_eleven_level_deck_matched_case_insensitively(self):
        deep = create_deck_past_create_limit(f"E2EDeepCase{unique_id()}")

        result = call_tool("create_deck", {"deck_name": deep.lower()})

        assert result.get("isError") is not True
        assert result["created"] is False
        assert result["exists"] is True
        assert result["deckName"] == deep
        assert result["decks"] == deck_path(deep)

    def test_new_eleven_level_deck_under_existing_parent_rejected(self):
        deep = create_deck_past_create_limit(f"E2EDeepSibling{unique_id()}")
        sibling = deep.rsplit("::", 1)[0] + "::Other"

        result = call_tool("create_deck", {"deck_name": sibling})

        assert result.get("isError") is True
        text = str(result)
        assert "11 levels" in text
        assert "a new deck can have at most 10" in text
        assert sibling not in deck_names()

    def test_new_twelve_level_deck_under_existing_deep_deck_rejected(self):
        deep = create_deck_past_create_limit(f"E2EDeepChild{unique_id()}")
        child = f"{deep}::L12"

        result = call_tool("create_deck", {"deck_name": child})

        assert result.get("isError") is True
        assert "12 levels" in str(result)
        assert child not in deck_names()
