"""E2E tests: create_deck reports the names Anki actually stores.

Anki splits a name on "::", then per level strips ASCII control characters
and trims whitespace and ':' from both ends (rslib/src/decks/name.rs,
normalized_deck_name_component). A single ':' inside a level is kept.
Control characters are rejected up front, since stripping them can merge two
levels into one (``A:\\x01:B`` would be stored as the one-level deck ``A::B``).
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, deck_names


class TestCreateDeckNameNormalization:
    def test_whitespace_around_levels_is_trimmed(self):
        root = f"E2ENormSpace{unique_id()}"

        result = call_tool("create_deck", {"deck_name": f"  {root}  ::  Child  "})

        assert result.get("isError") is not True
        assert result["created"] is True
        stored = f"{root}::Child"
        assert result["deckName"] == stored
        assert result["decks"] == [root, stored]
        assert result["depth"] == 2
        assert result["createdParentDecks"] == [root]
        assert result["message"] == (
            f'Successfully created deck "{stored}" and parent deck "{root}"'
        )
        assert stored in deck_names()

    def test_whitespace_input_matches_existing_deck(self):
        name = f"E2ENormSpaceExisting{unique_id()}::Child"
        first = call_tool("create_deck", {"deck_name": name})

        second = call_tool("create_deck", {"deck_name": f" {name.replace('::', ' :: ')} "})

        assert second.get("isError") is not True
        assert second["created"] is False
        assert second["exists"] is True
        assert second["deckId"] == first["deckId"]
        assert second["deckName"] == name

    def test_single_colon_inside_a_level_is_kept(self):
        root = f"E2ENormColon{unique_id()}"
        name = f"{root}::Verbs:Irregular"

        result = call_tool("create_deck", {"deck_name": name})

        assert result.get("isError") is not True
        assert result["created"] is True
        assert result["deckName"] == name
        assert result["decks"] == [root, name]
        assert result["depth"] == 2
        assert result["createdParentDecks"] == [root]
        assert name in deck_names()

    def test_trailing_colon_of_a_level_is_trimmed(self):
        root = f"E2ENormTrailColon{unique_id()}"

        result = call_tool("create_deck", {"deck_name": f"{root}:::Verbs:"})

        assert result.get("isError") is not True
        stored = f"{root}::Verbs"
        assert result["deckName"] == stored
        assert result["decks"] == [root, stored]
        assert result["createdParentDecks"] == [root]

    def test_unicode_name(self):
        root = f"E2ENormÜnïcødé{unique_id()}"
        name = f"{root}::日本語::Español"

        result = call_tool("create_deck", {"deck_name": name})

        assert result.get("isError") is not True
        assert result["created"] is True
        assert result["deckName"] == name
        assert result["decks"] == [root, f"{root}::日本語", name]
        assert result["depth"] == 3
        assert result["createdParentDecks"] == [root, f"{root}::日本語"]
        assert name in deck_names()

    def test_control_character_rejected_and_nothing_created(self):
        root = f"E2ENormCtl{unique_id()}"

        result = call_tool("create_deck", {"deck_name": f"{root}:\x01:Child"})

        assert result.get("isError") is True
        text = str(result)
        assert "level 1 of 1 contains control character U+0001" in text
        assert "changes where its levels split" in text
        assert not any(name.startswith(root) for name in deck_names())

    def test_tab_rejected_and_nothing_created(self):
        root = f"E2ENormTab{unique_id()}"

        result = call_tool("create_deck", {"deck_name": f"{root}::Child\t"})

        assert result.get("isError") is True
        text = str(result)
        assert "level 2 of 2 contains control character U+0009" in text
        assert f'without them the name is "{root}::Child", send that' in text
        assert not any(name.startswith(root) for name in deck_names())
