"""Tests for the deck-scoped get_tags action of the tag_management tool."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


def _add_note(deck_name: str, front: str, back: str, tags=None) -> int:
    """Add a Basic note and return its note_id. Tags optional."""
    args = {
        "deck_name": deck_name,
        "model_name": "Basic",
        "fields": {"Front": front, "Back": back},
    }
    if tags is not None:
        args["tags"] = tags
    result = call_tool("add_note", args)
    assert "note_id" in result, result
    return result["note_id"]


def _get_tags(deck: str | None = None) -> dict:
    """Call tag_management's get_tags action, optionally scoped to a deck."""
    params = {"action": "get_tags"}
    if deck is not None:
        params["deck"] = deck
    return call_tool("tag_management", {"params": params})


class TestGetTagsDeckScope:
    """Tests for the optional `deck` param on tag_management's get_tags action."""

    def test_get_tags_deck_scope_dedupes_and_excludes_other_decks(self):
        """get_tags(deck=...) returns the sorted, deduped tags for that deck only.

        Deck contains notes tagged a, b, b, c (b duplicated across two notes to
        prove dedup); a separate deck holds a note tagged z, which must not
        appear in the scoped result.
        """
        uid = unique_id()
        deck_name = f"E2E::GetTagsScope{uid}"
        other_deck_name = f"E2E::GetTagsScopeOther{uid}"
        tag_a = f"aTag{uid}"
        tag_b = f"bTag{uid}"
        tag_c = f"cTag{uid}"
        tag_z = f"zTag{uid}"

        call_tool("create_deck", {"deck_name": deck_name})
        call_tool("create_deck", {"deck_name": other_deck_name})

        _add_note(deck_name, f"Q-a {uid}", f"A-a {uid}", tags=[tag_a])
        _add_note(deck_name, f"Q-b1 {uid}", f"A-b1 {uid}", tags=[tag_b])
        _add_note(deck_name, f"Q-b2 {uid}", f"A-b2 {uid}", tags=[tag_b])
        _add_note(deck_name, f"Q-c {uid}", f"A-c {uid}", tags=[tag_c])
        _add_note(other_deck_name, f"Q-z {uid}", f"A-z {uid}", tags=[tag_z])

        result = _get_tags(deck=deck_name)

        assert result.get("isError") is not True, result
        assert result["tags"] == sorted([tag_a, tag_b, tag_c])
        assert result["count"] == 3
        assert tag_z not in result["tags"]

    def test_get_tags_deck_scope_includes_subdecks(self):
        """get_tags(deck=parent) includes tags from notes in subdecks too."""
        uid = unique_id()
        # create_deck caps nesting at 2 levels (parent::child), so the parent
        # is a top-level deck here rather than under the usual E2E:: namespace,
        # mirroring test_cards_stats_includes_subdecks.
        parent = f"GetTagsParent{uid}"
        child = f"{parent}::Child"
        parent_tag = f"parentTag{uid}"
        child_tag = f"childTag{uid}"

        call_tool("create_deck", {"deck_name": parent})
        call_tool("create_deck", {"deck_name": child})

        _add_note(parent, f"Parent Q {uid}", f"Parent A {uid}", tags=[parent_tag])
        _add_note(child, f"Child Q {uid}", f"Child A {uid}", tags=[child_tag])

        result = _get_tags(deck=parent)

        assert result.get("isError") is not True, result
        assert parent_tag in result["tags"]
        assert child_tag in result["tags"]

    def test_get_tags_unknown_deck_returns_empty_not_error(self):
        """Unlike cards_stats, get_tags treats an unknown deck as a valid empty answer."""
        result = _get_tags(deck=f"NonExistGetTags{unique_id()}")

        assert result.get("isError") is not True, result
        assert result["tags"] == []
        assert result["count"] == 0

    def test_get_tags_without_deck_returns_all_collection_tags(self):
        """Omitting deck (or passing an empty string) preserves the all-collection behavior."""
        uid = unique_id()
        deck_name = f"E2E::GetTagsNoScope{uid}"
        tag_name = f"noScopeTag{uid}"
        call_tool("create_deck", {"deck_name": deck_name})
        _add_note(deck_name, f"Q {uid}", f"A {uid}", tags=[tag_name])

        # No `deck` key at all.
        result_omitted = _get_tags()
        assert result_omitted.get("isError") is not True, result_omitted
        assert tag_name in result_omitted["tags"]

        # Explicit empty string, same all-collection semantics.
        result_empty = _get_tags(deck="")
        assert result_empty.get("isError") is not True, result_empty
        assert tag_name in result_empty["tags"]
