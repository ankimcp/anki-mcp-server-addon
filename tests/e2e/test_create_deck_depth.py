"""E2E tests: create_deck at any depth up to 10 levels.

Covers the response shape (decks, depth, createdParentDecks; no
parentDeck/childDeck), which parent decks a call reports as created, the
10-level limit for new decks, and rejection of blank names and empty levels.
Existing decks deeper than 10 levels are in test_create_deck_existing_deep.py.
"""
from __future__ import annotations

import pytest

from .conftest import unique_id
from .helpers import call_tool, deck_names, deck_path, nested_deck_name


class TestCreateDeckDepth:
    def test_single_level(self):
        name = f"E2EDepthOne{unique_id()}"

        result = call_tool("create_deck", {"deck_name": name})

        assert result.get("isError") is not True
        assert result["created"] is True
        assert result["deckName"] == name
        assert result["decks"] == [name]
        assert result["depth"] == 1
        assert result["createdParentDecks"] == []
        assert result["message"] == f'Successfully created deck "{name}"'
        assert "exists" not in result
        assert "parentDeck" not in result
        assert "childDeck" not in result

    def test_three_levels_creates_all_parents(self):
        root = f"E2EDepthThree{unique_id()}"
        name = f"{root}::Verbs::Irregular"

        result = call_tool("create_deck", {"deck_name": name})

        assert result.get("isError") is not True
        assert result["created"] is True
        assert result["deckName"] == name
        assert result["decks"] == [root, f"{root}::Verbs", name]
        assert result["depth"] == 3
        assert result["createdParentDecks"] == [root, f"{root}::Verbs"]
        assert result["message"] == (
            f'Successfully created deck "{name}" '
            f'and parent decks "{root}", "{root}::Verbs"'
        )
        assert "parentDeck" not in result
        assert "childDeck" not in result
        assert set(result["decks"]) <= deck_names()

    def test_six_levels(self):
        name = nested_deck_name(f"E2EDepthSix{unique_id()}", 6)

        result = call_tool("create_deck", {"deck_name": name})

        assert result.get("isError") is not True
        assert result["created"] is True
        assert result["decks"] == deck_path(name)
        assert result["depth"] == 6
        assert result["createdParentDecks"] == deck_path(name)[:-1]
        assert "parentDeck" not in result
        assert "childDeck" not in result
        assert set(result["decks"]) <= deck_names()

    def test_only_missing_parents_reported(self):
        root = f"E2EDepthPartial{unique_id()}"
        existing = f"{root}::A"
        call_tool("create_deck", {"deck_name": existing})
        name = f"{existing}::B::C"

        result = call_tool("create_deck", {"deck_name": name})

        assert result.get("isError") is not True
        assert result["created"] is True
        assert result["decks"] == [root, existing, f"{existing}::B", name]
        assert result["depth"] == 4
        assert result["createdParentDecks"] == [f"{existing}::B"]
        assert result["message"] == (
            f'Successfully created deck "{name}" and parent deck "{existing}::B"'
        )

    def test_existing_deep_deck(self):
        name = nested_deck_name(f"E2EDepthExisting{unique_id()}", 5)
        first = call_tool("create_deck", {"deck_name": name})
        assert first["created"] is True

        second = call_tool("create_deck", {"deck_name": name})

        assert second.get("isError") is not True
        assert second["created"] is False
        assert second["exists"] is True
        assert second["deckId"] == first["deckId"]
        assert second["decks"] == deck_path(name)
        assert second["depth"] == 5
        assert second["createdParentDecks"] == []
        assert second["message"] == f'Deck "{name}" already exists'

    def test_ten_levels_accepted(self):
        name = nested_deck_name(f"E2EDepthTen{unique_id()}", 10)

        result = call_tool("create_deck", {"deck_name": name})

        assert result.get("isError") is not True
        assert result["created"] is True
        assert result["depth"] == 10
        assert result["decks"] == deck_path(name)
        assert len(result["createdParentDecks"]) == 9
        assert name in deck_names()

    def test_eleven_levels_rejected(self):
        root = f"E2EDepthEleven{unique_id()}"
        name = nested_deck_name(root, 11)

        result = call_tool("create_deck", {"deck_name": name})

        assert result.get("isError") is True
        text = str(result)
        assert "11 levels" in text
        assert "a new deck can have at most 10" in text
        assert root not in deck_names()

    def test_empty_level_rejected(self):
        root = f"E2EDepthEmpty{unique_id()}"

        result = call_tool("create_deck", {"deck_name": f"{root}::::Verbs"})

        assert result.get("isError") is True
        text = str(result)
        assert "level 2 of 3 is empty" in text
        assert "at most 10" not in text
        names = deck_names()
        assert root not in names
        assert f"{root}::blank::Verbs" not in names

    @pytest.mark.parametrize("name", ["", ":", ":::"], ids=["empty", "colon", "colons"])
    def test_blank_name_rejected_and_nothing_created(self, name):
        before = deck_names()

        result = call_tool("create_deck", {"deck_name": name})

        assert result.get("isError") is True
        text = str(result)
        assert "Deck name cannot be empty" in text
        assert "validation_error" in text
        assert "level 1" not in text
        assert deck_names() == before

    def test_existing_parent_spelling_wins(self):
        root = f"E2EDepthCase{unique_id()}"
        call_tool("create_deck", {"deck_name": f"{root}::Verbs"})
        lower = f"{root.lower()}::verbs::Irregular"

        result = call_tool("create_deck", {"deck_name": lower})

        assert result.get("isError") is not True
        assert result["created"] is True
        stored = f"{root}::Verbs::Irregular"
        assert result["deckName"] == stored
        assert result["decks"] == [root, f"{root}::Verbs", stored]
        assert result["createdParentDecks"] == []
        assert result["message"] == f'Successfully created deck "{stored}"'
