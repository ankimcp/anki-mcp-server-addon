"""Deck description round trips through MCP against disposable Anki decks."""
from __future__ import annotations

import time

import pytest

from .conftest import unique_id
from .helpers import call_tool


@pytest.fixture
def description_deck():
    name = f"Description {unique_id()}::Vocabulary 日本語"
    created = call_tool("create_deck", {"deck_name": name})
    assert created.get("isError") is not True, created
    return name, created["deckId"]


def _read(name):
    result = call_tool("get_deck_description", {"deck_name": name})
    assert result.get("isError") is not True, result
    return result


def _update(name, description, **settings):
    result = call_tool("update_deck_description", {
        "deck_name": name, "description": description, **settings,
    })
    assert result.get("isError") is not True, result
    return result


class TestDeckDescriptions:
    def test_unset_description(self, description_deck):
        name, deck_id = description_deck
        result = _read(name)
        assert result == {
            "deck_id": deck_id, "name": name,
            "description": "", "is_markdown": False,
        }

    @pytest.mark.parametrize(("text", "is_markdown"), [
        (" \n# Vocabulary\n\n**Recall** слова 日本語 & <tags>.\n ", True),
        ('<p class="guide">Слова &amp; 日本語</p>\n<ul><li>One idea</li></ul>', False),
        ("null", False),
        ("0", True),
    ])
    def test_description_round_trips(self, description_deck, text, is_markdown):
        name, deck_id = description_deck
        result = _update(name, text, is_markdown=is_markdown)
        assert result == {
            "deck_id": deck_id, "name": name,
            "description": text, "is_markdown": is_markdown,
        }
        assert _read(name) == result

    @pytest.mark.parametrize("initial_markdown", [False, True])
    @pytest.mark.parametrize("settings", [{}, {"is_markdown": None}])
    def test_omitted_or_null_markdown_preserves_setting(
        self, description_deck, initial_markdown, settings,
    ):
        name, _ = description_deck
        _update(name, "Original", is_markdown=initial_markdown)
        updated = _update(name, "Replacement", **settings)
        assert updated["is_markdown"] is initial_markdown
        assert _read(name) == updated

    def test_disable_markdown_and_clear_description(self, description_deck):
        name, _ = description_deck
        _update(name, "# Original", is_markdown=True)
        updated = _update(name, "", is_markdown=False)
        assert updated["description"] == ""
        assert updated["is_markdown"] is False
        assert _read(name) == updated

    def test_parent_and_child_have_independent_descriptions(self, description_deck):
        child, _ = description_deck
        parent = child.split("::")[0]
        parent_before = _update(parent, "Parent guide", is_markdown=True)
        # A child without a description does not inherit its parent's guide.
        assert _read(child)["description"] == ""
        child_after = _update(child.upper(), "Child guide", is_markdown=False)
        assert child_after["name"] == child
        assert _read(child) == child_after
        assert _read(parent) == parent_before

    def test_update_is_undoable(self, description_deck):
        name, _ = description_deck
        before = _update(name, "Original", is_markdown=True)
        _update(name, "Replacement", is_markdown=False)
        assert _read(name)["description"] == "Replacement"
        undone = call_tool("gui_undo")
        assert undone.get("isError") is not True, undone
        assert undone["undone"] is True
        # gui_undo starts an asynchronous CollectionOp.
        deadline = time.monotonic() + 5
        after = _read(name)
        while after != before and time.monotonic() < deadline:
            time.sleep(0.1)
            after = _read(name)
        assert after == before

    @pytest.mark.parametrize("tool", ["get_deck_description", "update_deck_description"])
    def test_unknown_deck_is_not_created(self, tool):
        name = f"Missing description {unique_id()}"
        before = call_tool("list_decks")["decks"]
        args = {"deck_name": name}
        if tool == "update_deck_description":
            args["description"] = "Should not be written"
        result = call_tool(tool, args)
        assert result.get("isError") is True, result
        assert "not_found" in str(result)
        assert call_tool("list_decks")["decks"] == before

    @pytest.mark.parametrize("tool", ["get_deck_description", "update_deck_description"])
    def test_filtered_deck_is_rejected(self, tool):
        name = f"Filtered description {unique_id()}"
        created = call_tool("filtered_deck_upsert", {
            "name": name,
            "search_terms": [{"search": f"tag:missing-{unique_id()}", "limit": 1}],
            "allow_empty": True,
        })
        assert created.get("isError") is not True, created
        args = {"deck_name": created["name"]}
        if tool == "update_deck_description":
            args["description"] = "Should not be written"
        result = call_tool(tool, args)
        assert result.get("isError") is True, result
        assert "Filtered decks" in str(result)

    @pytest.mark.parametrize("tool", ["get_deck_description", "update_deck_description"])
    @pytest.mark.parametrize("name", ["", " \t\n "])
    def test_blank_deck_name_is_rejected(self, tool, name):
        args = {"deck_name": name}
        if tool == "update_deck_description":
            args["description"] = "Should not be written"
        result = call_tool(tool, args)
        assert result.get("isError") is True, result
        assert "Deck name must not be empty" in str(result)
