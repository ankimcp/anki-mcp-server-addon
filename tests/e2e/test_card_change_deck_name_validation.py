"""E2E tests: card_change_deck applies the same deck-name rules as create_deck.

An empty level would otherwise make Anki create a deck literally named
"blank", a control character would be stripped (possibly merging two levels),
and a new deck over 10 levels is past the add-on's limit; all are rejected
before any deck is created or card moved. A valid deep name still works.
Existing decks deeper than 10 levels are in test_card_change_deck_existing_deep.py.
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import (
    call_tool,
    card_deck_name,
    create_basic_card,
    deck_names,
    nested_deck_name,
)


class TestCardChangeDeckNameValidation:
    def test_empty_level_rejected_and_nothing_created(self):
        uid = unique_id()
        source = f"E2EMoveEmptySrc{uid}"
        card_id = create_basic_card(uid, source)
        root = f"E2EMoveEmpty{uid}"

        result = call_tool("card_change_deck", {
            "card_ids": [card_id],
            "deck": f"{root}::::Verbs",
        })

        assert result.get("isError") is True
        assert "level 2 of 3 is empty" in str(result)
        names = deck_names()
        assert root not in names
        assert f"{root}::blank::Verbs" not in names
        assert card_deck_name(card_id) == source

    def test_whitespace_level_rejected(self):
        uid = unique_id()
        source = f"E2EMoveSpaceSrc{uid}"
        card_id = create_basic_card(uid, source)
        root = f"E2EMoveSpace{uid}"

        result = call_tool("card_change_deck", {
            "card_ids": [card_id],
            "deck": f"{root}::   ",
        })

        assert result.get("isError") is True
        assert "level 2 of 2 is empty" in str(result)
        assert root not in deck_names()
        assert card_deck_name(card_id) == source

    def test_control_character_rejected(self):
        uid = unique_id()
        source = f"E2EMoveCtlSrc{uid}"
        card_id = create_basic_card(uid, source)
        root = f"E2EMoveCtl{uid}"

        result = call_tool("card_change_deck", {
            "card_ids": [card_id],
            "deck": f"{root}:\x01:Verbs",
        })

        assert result.get("isError") is True
        text = str(result)
        assert "level 1 of 1 contains control character U+0001" in text
        assert "changes where its levels split" in text
        assert not any(name.startswith(root) for name in deck_names())
        assert card_deck_name(card_id) == source

    def test_more_than_ten_levels_rejected(self):
        uid = unique_id()
        source = f"E2EMoveDeepSrc{uid}"
        card_id = create_basic_card(uid, source)
        root = f"E2EMoveDeep{uid}"

        result = call_tool("card_change_deck", {
            "card_ids": [card_id],
            "deck": nested_deck_name(root, 11),
        })

        assert result.get("isError") is True
        text = str(result)
        assert "11 levels" in text
        assert "a new deck can have at most 10" in text
        assert root not in deck_names()
        assert card_deck_name(card_id) == source

    def test_valid_deep_name_moves_card(self):
        uid = unique_id()
        card_id = create_basic_card(uid, f"E2EMoveOkSrc{uid}")
        target = nested_deck_name(f"E2EMoveOk{uid}", 10)

        result = call_tool("card_change_deck", {
            "card_ids": [card_id],
            "deck": target,
        })

        assert result.get("isError") is not True
        assert result["moved"] == 1
        assert result["deck_id"] > 0
        assert target in result["message"]
        assert target in deck_names()
        assert card_deck_name(card_id) == target
