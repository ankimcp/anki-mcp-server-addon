"""E2E tests for the ``render_card`` tool (phase 1: text mode).

Two modes are covered:

* ``note_id`` -- render a card of an EXISTING note through the reviewer path.
* ``model_name`` + ``fields`` -- render UNSAVED content. The critical assertion
  here is that nothing lands in the collection: a marker string unique to the
  render is searched with ``find_notes`` before and after, and must stay at
  zero hits.

Collection hygiene: every test works against uniquely-named scratch note types
and decks created by the module fixtures, so no shared model ("Basic") is ever
touched. Scratch NOTES are deleted in fixture teardown. The scratch note types
and decks are left behind on purpose -- there is no delete-model MCP tool, and
uniquely-named note types no other test looks up are inert (same rationale as
test_change_note_type.py).
"""
from __future__ import annotations

import pytest

from .conftest import unique_id
from .helpers import call_tool, list_tools


def _uid() -> str:
    """Short hyphen-free unique suffix, safe to embed in a find_notes query."""
    return unique_id().replace("-", "")[:12]


def _create_deck(uid: str) -> str:
    deck_name = f"E2E::RenderCard{uid}"
    result = call_tool("create_deck", {"deck_name": deck_name})
    assert result.get("isError") is not True, f"create_deck failed: {result}"
    return deck_name


def _add_note(deck_name: str, model_name: str, fields: dict[str, str]) -> int:
    result = call_tool("add_note", {
        "deck_name": deck_name,
        "model_name": model_name,
        "fields": fields,
    })
    assert result.get("isError") is not True, f"add_note failed: {result}"
    return result["note_id"]


def _delete_notes(note_ids: list[int]) -> None:
    """Delete scratch notes, reporting (never raising on) cleanup failures."""
    if not note_ids:
        return
    result = call_tool("delete_notes", {
        "notes": note_ids,
        "confirmDeletion": True,
    })
    if result.get("isError"):
        print(f"cleanup failed: could not delete notes {note_ids}: {result}")


def _find_total(query: str) -> int:
    """Return how many notes match a query."""
    result = call_tool("find_notes", {"query": query, "limit": 5})
    assert result.get("isError") is not True, f"find_notes failed: {result}"
    return result["total"]


@pytest.fixture(scope="module")
def basic_scratch():
    """A two-template scratch note type with one note in a scratch deck."""
    uid = _uid()
    model_name = f"RenderCard{uid}"
    css_marker = f"RCCSS{uid}"
    front_marker = f"RCFRONT{uid}"
    back_marker = f"RCBACK{uid}"

    result = call_tool("create_model", {
        "model_name": model_name,
        "in_order_fields": ["Front", "Back"],
        "card_templates": [
            {
                "Name": "Card 1",
                "Front": "{{Front}}",
                "Back": "{{FrontSide}}<hr id=\"answer\">{{Back}}",
            },
            {
                "Name": "Card 2",
                "Front": "{{Back}}",
                "Back": "{{FrontSide}}<hr id=\"answer\">{{Front}}",
            },
        ],
        "css": f".card {{ color: black; }} /* {css_marker} */",
    })
    assert result.get("isError") is not True, f"create_model failed: {result}"

    deck_name = _create_deck(uid)
    note_id = _add_note(deck_name, model_name, {
        "Front": front_marker,
        "Back": back_marker,
    })

    yield {
        "uid": uid,
        "model_name": model_name,
        "deck_name": deck_name,
        "note_id": note_id,
        "css_marker": css_marker,
        "front_marker": front_marker,
        "back_marker": back_marker,
    }

    _delete_notes([note_id])


@pytest.fixture(scope="module")
def cloze_scratch():
    """A scratch cloze note type (no notes -- unsaved rendering only)."""
    uid = _uid()
    model_name = f"RenderCardCloze{uid}"

    result = call_tool("create_model", {
        "model_name": model_name,
        "in_order_fields": ["Text", "Extra"],
        "card_templates": [
            {
                "Name": "Cloze",
                "Front": "{{cloze:Text}}",
                "Back": "{{cloze:Text}}<br>{{Extra}}",
            },
        ],
        "is_cloze": True,
    })
    assert result.get("isError") is not True, f"create_model failed: {result}"

    return {"uid": uid, "model_name": model_name}


class TestRenderCardDiscovery:
    """The tool must be registered and visible to MCP clients."""

    def test_tool_appears_in_tools_list(self):
        tool_names = [t["name"] for t in list_tools()]
        assert "render_card" in tool_names


class TestExistingNoteMode:
    """Rendering a card of a note that already exists."""

    def test_round_trips_field_content(self, basic_scratch):
        """Field content of the saved note must show up in the rendered HTML."""
        result = call_tool("render_card", {"note_id": basic_scratch["note_id"]})

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["mode"] == "note"
        assert result["note_id"] == basic_scratch["note_id"]
        assert isinstance(result["card_id"], int)
        assert result["card_count"] == 2
        assert result["model_name"] == basic_scratch["model_name"]
        assert result["card_ord"] == 0
        assert result["card_ordinal"] == 0
        assert result["template_name"] == "Card 1"
        assert result["is_cloze"] is False

        # Card 1 front is {{Front}}, its back adds {{Back}}.
        assert basic_scratch["front_marker"] in result["question_html"]
        assert basic_scratch["back_marker"] not in result["question_html"]
        assert basic_scratch["back_marker"] in result["answer_html"]

    def test_css_returned_separately_and_embedded(self, basic_scratch):
        """CSS comes back raw AND embedded in a leading <style> block."""
        result = call_tool("render_card", {"note_id": basic_scratch["note_id"]})

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert basic_scratch["css_marker"] in result["css"]
        assert result["question_html"].startswith("<style>")
        assert basic_scratch["css_marker"] in result["question_html"]
        assert result["answer_html"].startswith("<style>")

    def test_card_ord_selects_second_card(self, basic_scratch):
        """card_ord indexes the note's cards; Card 2 fronts the Back field."""
        result = call_tool("render_card", {
            "note_id": basic_scratch["note_id"],
            "card_ord": 1,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["card_ord"] == 1
        assert result["card_ordinal"] == 1
        assert result["template_name"] == "Card 2"
        assert basic_scratch["back_marker"] in result["question_html"]
        assert basic_scratch["front_marker"] not in result["question_html"]

    def test_side_question_only(self, basic_scratch):
        result = call_tool("render_card", {
            "note_id": basic_scratch["note_id"],
            "side": "question",
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert "question_html" in result
        assert "answer_html" not in result

    def test_side_answer_only(self, basic_scratch):
        result = call_tool("render_card", {
            "note_id": basic_scratch["note_id"],
            "side": "answer",
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert "answer_html" in result
        assert "question_html" not in result

    def test_unknown_note_id_errors(self):
        result = call_tool("render_card", {"note_id": 1})

        assert result.get("isError") is True
        assert "not found" in str(result).lower()

    def test_card_ord_out_of_range_errors(self, basic_scratch):
        """The scratch note has 2 cards, so card_ord=5 must be rejected."""
        result = call_tool("render_card", {
            "note_id": basic_scratch["note_id"],
            "card_ord": 5,
        })

        assert result.get("isError") is True
        assert "out of range" in str(result).lower()

    def test_negative_card_ord_errors(self, basic_scratch):
        result = call_tool("render_card", {
            "note_id": basic_scratch["note_id"],
            "card_ord": -1,
        })

        assert result.get("isError") is True


class TestUnsavedMode:
    """Rendering model_name + fields without touching the collection."""

    def test_renders_without_creating_a_note(self, basic_scratch):
        """The rendered content must never become a note."""
        marker = f"RCUNSAVED{_uid()}"

        # Control: a marker that IS saved is findable this way, so a zero hit
        # below really means "not in the collection", not "search can't see it".
        assert _find_total(basic_scratch["front_marker"]) == 1
        assert _find_total(marker) == 0, "marker must not exist before the render"
        total_before = _find_total("deck:*")

        result = call_tool("render_card", {
            "model_name": basic_scratch["model_name"],
            "fields": {"Front": marker, "Back": "unsaved back"},
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["mode"] == "unsaved"
        assert marker in result["question_html"]
        assert "unsaved back" in result["answer_html"]
        # No collection identity is echoed, because none exists.
        assert "note_id" not in result
        assert "card_id" not in result

        assert _find_total(marker) == 0, "render_card must not persist the note"
        assert _find_total("deck:*") == total_before, "note count must be unchanged"

    def test_reports_model_and_template_metadata(self, basic_scratch):
        result = call_tool("render_card", {
            "model_name": basic_scratch["model_name"],
            "fields": {"Front": "q", "Back": "a"},
            "card_ord": 1,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["model_name"] == basic_scratch["model_name"]
        assert result["card_ord"] == 1
        assert result["card_ordinal"] == 1
        assert result["template_name"] == "Card 2"
        assert result["is_cloze"] is False
        assert basic_scratch["css_marker"] in result["css"]

    def test_omitted_fields_render_empty(self, basic_scratch):
        """Only the fields that are given need to be supplied."""
        marker = f"RCPARTIAL{_uid()}"
        result = call_tool("render_card", {
            "model_name": basic_scratch["model_name"],
            "fields": {"Front": marker},
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert marker in result["question_html"]
        assert _find_total(marker) == 0

    def test_side_selection(self, basic_scratch):
        result = call_tool("render_card", {
            "model_name": basic_scratch["model_name"],
            "fields": {"Front": "q", "Back": "a"},
            "side": "question",
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert "question_html" in result
        assert "answer_html" not in result

    def test_unknown_model_errors(self):
        result = call_tool("render_card", {
            "model_name": f"NoSuchModel{_uid()}",
            "fields": {"Front": "x"},
        })

        assert result.get("isError") is True
        assert "not found" in str(result).lower()

    def test_unknown_field_errors_and_lists_valid_names(self, basic_scratch):
        result = call_tool("render_card", {
            "model_name": basic_scratch["model_name"],
            "fields": {"Frnot": "typo"},
        })

        assert result.get("isError") is True
        text = str(result)
        assert "Frnot" in text
        # The hint must name the fields the note type actually has.
        assert "Front" in text
        assert "Back" in text

    def test_card_ord_out_of_range_errors(self, basic_scratch):
        """The scratch note type has 2 templates, so card_ord=5 is invalid."""
        result = call_tool("render_card", {
            "model_name": basic_scratch["model_name"],
            "fields": {"Front": "q", "Back": "a"},
            "card_ord": 5,
        })

        assert result.get("isError") is True
        assert "out of range" in str(result).lower()


class TestModeValidation:
    """Exactly one of the two modes must be requested."""

    def test_both_modes_errors(self, basic_scratch):
        result = call_tool("render_card", {
            "note_id": basic_scratch["note_id"],
            "model_name": basic_scratch["model_name"],
            "fields": {"Front": "q", "Back": "a"},
        })

        assert result.get("isError") is True
        assert "cannot be combined" in str(result).lower()

    def test_no_mode_errors(self):
        result = call_tool("render_card", {})

        assert result.get("isError") is True
        assert "no render target" in str(result).lower()

    def test_fields_without_model_errors(self):
        result = call_tool("render_card", {"fields": {"Front": "q"}})

        assert result.get("isError") is True
        assert "model_name" in str(result).lower()

    def test_invalid_side_errors(self, basic_scratch):
        """side is a closed set -- an out-of-schema value must be rejected."""
        result = call_tool("render_card", {
            "note_id": basic_scratch["note_id"],
            "side": "front",
        })

        assert result.get("isError") is True


class TestClozeRendering:
    """Cloze note types: card_ord is the cloze index (cloze number - 1)."""

    def test_renders_first_cloze(self, cloze_scratch):
        marker = f"RCCLOZE{_uid()}"
        result = call_tool("render_card", {
            "model_name": cloze_scratch["model_name"],
            "fields": {
                "Text": f"{{{{c1::{marker}}}}} lives in {{{{c2::Paris}}}}",
                "Extra": "",
            },
            "card_ord": 0,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["is_cloze"] is True
        assert result["card_ord"] == 0
        assert result["card_ordinal"] == 0
        assert result["template_name"] == "Cloze"
        # Cloze 1 is BLANKED on the question and revealed on the answer;
        # cloze 2's text stays visible on both sides. Modern Anki renders the
        # active cloze as
        # `<span class="cloze" data-cloze="{content}" data-ordinal="1">[...]</span>`
        # -- the hidden text is embedded in the data-cloze attribute (for
        # interactive reveal), so the marker IS present in the markup while
        # being visually hidden. Assert on what the tool guarantees: the blank
        # is shown and the marker never appears as visible element text (in the
        # attribute form it is always preceded by `"`, never by `>`).
        assert "[...]" in result["question_html"]
        assert f">{marker}" not in result["question_html"]
        assert 'data-cloze="' in result["question_html"]
        assert "Paris" in result["question_html"]
        assert marker in result["answer_html"]
        assert _find_total(marker) == 0

    def test_renders_second_cloze(self, cloze_scratch):
        marker = f"RCCLOZE{_uid()}"
        result = call_tool("render_card", {
            "model_name": cloze_scratch["model_name"],
            "fields": {
                "Text": f"Alpha is {{{{c1::one}}}} and beta is {{{{c2::{marker}}}}}",
                "Extra": "",
            },
            "card_ord": 1,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["card_ordinal"] == 1
        # c2 is the active cloze here: blanked on the question, its text carried
        # in the data-cloze attribute (see test_renders_first_cloze) rather than
        # as visible element text.
        assert "[...]" in result["question_html"]
        assert f">{marker}" not in result["question_html"]
        assert 'data-cloze="' in result["question_html"]
        assert "one" in result["question_html"]
        assert marker in result["answer_html"]

    def test_missing_cloze_number_errors(self, cloze_scratch):
        """card_ord must select a cloze number the fields actually contain."""
        result = call_tool("render_card", {
            "model_name": cloze_scratch["model_name"],
            "fields": {"Text": "Only {{c1::one}} here", "Extra": ""},
            "card_ord": 4,
        })

        assert result.get("isError") is True
        text = str(result).lower()
        assert "cloze" in text
        # The hint lists the cloze numbers that do exist.
        assert "[1]" in str(result)

    def test_no_cloze_deletions_errors(self, cloze_scratch):
        result = call_tool("render_card", {
            "model_name": cloze_scratch["model_name"],
            "fields": {"Text": "plain text without deletions", "Extra": ""},
            "card_ord": 0,
        })

        assert result.get("isError") is True
        assert "no cloze deletions" in str(result).lower()
