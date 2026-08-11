"""Tests for patch mode (old_str/new_str) on the three write tools.

``update_model_styling``, ``update_model_templates`` and ``update_note_fields``
each accept an alternative "patch" input mode that mirrors a text editor's
find-and-replace: ``old_str`` must match the current content EXACTLY ONCE.

Every tool is exercised against the same four scenarios, because the contract is
deliberately identical across all three:

1. happy path -- the snippet is replaced and the change round-trips through a read
2. zero matches -- error, an excerpt of the CURRENT content is returned so the
   caller can re-sync, and nothing is written
3. multiple matches -- error naming the match count, and nothing is written
4. mixed mode -- full-content params combined with patch params are rejected,
   and nothing is written

"nothing is written" is asserted by reading the content back after every
expected error: a rejected patch that still mutated the collection would show up
there.

Models are DISPOSABLE and uniquely named (same rationale as
test_update_model_styling.py) so the shared "Basic" model is never dirtied.
Notes are created in a unique deck and deleted in a ``finally``.

Strings used as ``old_str`` deliberately avoid '=' characters: the Inspector CLI
passes tool args as ``key=value`` pairs, so an '=' in the value is needless risk.
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool

# --- update_model_styling fixtures -------------------------------------------
# Short enough that the whole stylesheet fits inside a 200-char error excerpt.
CSS_SINGLE = ".card { color: blue; font-size: 20px; }"
CSS_DUPLICATE = ".card { color: blue; } .extra { color: blue; }"

# --- update_model_templates fixtures -----------------------------------------
TEMPLATE_FRONT_SINGLE = "<div>{{Front}} MARKER-ALPHA</div>"
TEMPLATE_FRONT_DUPLICATE = "<div>{{Front}} MARKER-ALPHA MARKER-ALPHA</div>"
TEMPLATE_BACK = "{{FrontSide}}<hr>{{Back}}"
CARD_NAME = "Card A"


def _create_styling_model(css: str) -> str:
    """Create a disposable model carrying the given CSS."""
    model_name = f"PatchStyling{unique_id()}"
    result = call_tool("create_model", {
        "model_name": model_name,
        "in_order_fields": ["Front", "Back"],
        "card_templates": [
            {"Name": CARD_NAME, "Front": "{{Front}}", "Back": TEMPLATE_BACK},
        ],
        "css": css,
    })
    assert result.get("isError") is not True, f"create_model failed: {result}"
    return model_name


def _create_template_model(front: str) -> str:
    """Create a disposable model whose single template carries the given Front."""
    model_name = f"PatchTemplate{unique_id()}"
    result = call_tool("create_model", {
        "model_name": model_name,
        "in_order_fields": ["Front", "Back"],
        "card_templates": [
            {"Name": CARD_NAME, "Front": front, "Back": TEMPLATE_BACK},
        ],
    })
    assert result.get("isError") is not True, f"create_model failed: {result}"
    return model_name


def _read_css(model_name: str) -> str:
    result = call_tool("model_styling", {"model_name": model_name})
    assert result.get("isError") is not True, f"model_styling failed: {result}"
    return result["css"]


def _read_front(model_name: str) -> str:
    result = call_tool("model_templates", {"model_name": model_name})
    assert result.get("isError") is not True, f"model_templates failed: {result}"
    return result["templates"][CARD_NAME]["Front"]


def _create_note(front: str, back: str) -> int:
    """Create a note in a unique deck and return its note ID."""
    uid = unique_id()
    deck_name = f"E2E::Patch{uid}"
    call_tool("create_deck", {"deck_name": deck_name})
    result = call_tool("add_note", {
        "deck_name": deck_name,
        "model_name": "Basic",
        "fields": {"Front": front, "Back": back},
    })
    assert "note_id" in result, f"add_note failed: {result}"
    return result["note_id"]


def _delete_note(note_id: int) -> None:
    call_tool("delete_notes", {"notes": [note_id], "confirmDeletion": True})


def _read_field(note_id: int, field_name: str) -> str:
    info = call_tool("notes_info", {"notes": [note_id]})
    assert info.get("isError") is not True, f"notes_info failed: {info}"
    return info["notes"][0]["fields"][field_name]["value"]


class TestPatchModelStyling:
    """Patch mode on update_model_styling (old_str/new_str over the model CSS)."""

    def test_patch_replaces_snippet(self):
        """A unique old_str is replaced in place and the rest of the CSS survives."""
        model_name = _create_styling_model(CSS_SINGLE)

        result = call_tool("update_model_styling", {
            "model_name": model_name,
            "old_str": "color: blue",
            "new_str": "color: green",
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["patched"] is True

        after = _read_css(model_name)
        assert after == ".card { color: green; font-size: 20px; }"

    def test_patch_zero_match_errors_and_writes_nothing(self):
        """0 matches must error, echo a current-content excerpt, and write nothing."""
        model_name = _create_styling_model(CSS_SINGLE)

        result = call_tool("update_model_styling", {
            "model_name": model_name,
            "old_str": "color: magenta",
            "new_str": "color: green",
        })

        assert result.get("isError") is True, f"Expected an error, got: {result}"
        text = str(result)
        assert "0 matches" in text
        # The excerpt lets the caller re-sync without a separate read.
        assert "current_content_excerpt" in text
        assert "font-size: 20px" in text

        assert _read_css(model_name) == CSS_SINGLE

    def test_patch_multiple_matches_errors_and_writes_nothing(self):
        """2+ matches must error with the count and write nothing."""
        model_name = _create_styling_model(CSS_DUPLICATE)

        result = call_tool("update_model_styling", {
            "model_name": model_name,
            "old_str": "color: blue",
            "new_str": "color: green",
        })

        assert result.get("isError") is True, f"Expected an error, got: {result}"
        assert "2 times" in str(result)

        assert _read_css(model_name) == CSS_DUPLICATE

    def test_mixed_mode_rejected(self):
        """css combined with old_str/new_str is rejected and writes nothing."""
        model_name = _create_styling_model(CSS_SINGLE)

        result = call_tool("update_model_styling", {
            "model_name": model_name,
            "css": ".card { color: black; }",
            "old_str": "color: blue",
            "new_str": "color: green",
        })

        assert result.get("isError") is True, f"Expected an error, got: {result}"
        assert "mutually exclusive" in str(result).lower()

        assert _read_css(model_name) == CSS_SINGLE

    def test_no_input_rejected(self):
        """Neither css nor a patch pair (nor LaTeX fields) is rejected."""
        model_name = _create_styling_model(CSS_SINGLE)

        result = call_tool("update_model_styling", {"model_name": model_name})

        assert result.get("isError") is True, f"Expected an error, got: {result}"
        assert _read_css(model_name) == CSS_SINGLE


class TestPatchModelTemplates:
    """Patch mode on update_model_templates (one side of one card template)."""

    def test_patch_replaces_snippet(self):
        """A unique old_str is replaced in the named template side."""
        model_name = _create_template_model(TEMPLATE_FRONT_SINGLE)

        result = call_tool("update_model_templates", {
            "model_name": model_name,
            "template_name": CARD_NAME,
            "side": "Front",
            "old_str": "MARKER-ALPHA",
            "new_str": "MARKER-BETA",
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["patched"] is True
        assert result["side"] == "Front"
        assert result["updated_templates"] == [CARD_NAME]

        assert _read_front(model_name) == "<div>{{Front}} MARKER-BETA</div>"

    def test_patch_zero_match_errors_and_writes_nothing(self):
        """0 matches must error, echo a current-content excerpt, and write nothing."""
        model_name = _create_template_model(TEMPLATE_FRONT_SINGLE)

        result = call_tool("update_model_templates", {
            "model_name": model_name,
            "template_name": CARD_NAME,
            "side": "Front",
            "old_str": "MARKER-OMEGA",
            "new_str": "MARKER-BETA",
        })

        assert result.get("isError") is True, f"Expected an error, got: {result}"
        text = str(result)
        assert "0 matches" in text
        assert "current_content_excerpt" in text
        assert "MARKER-ALPHA" in text

        assert _read_front(model_name) == TEMPLATE_FRONT_SINGLE

    def test_patch_multiple_matches_errors_and_writes_nothing(self):
        """2+ matches must error with the count and write nothing."""
        model_name = _create_template_model(TEMPLATE_FRONT_DUPLICATE)

        result = call_tool("update_model_templates", {
            "model_name": model_name,
            "template_name": CARD_NAME,
            "side": "Front",
            "old_str": "MARKER-ALPHA",
            "new_str": "MARKER-BETA",
        })

        assert result.get("isError") is True, f"Expected an error, got: {result}"
        assert "2 times" in str(result)

        assert _read_front(model_name) == TEMPLATE_FRONT_DUPLICATE

    def test_mixed_mode_rejected(self):
        """templates combined with patch params is rejected and writes nothing."""
        model_name = _create_template_model(TEMPLATE_FRONT_SINGLE)

        result = call_tool("update_model_templates", {
            "model_name": model_name,
            "templates": {CARD_NAME: {"Front": "<div>clobbered</div>"}},
            "template_name": CARD_NAME,
            "side": "Front",
            "old_str": "MARKER-ALPHA",
            "new_str": "MARKER-BETA",
        })

        assert result.get("isError") is True, f"Expected an error, got: {result}"
        assert "mutually exclusive" in str(result).lower()

        assert _read_front(model_name) == TEMPLATE_FRONT_SINGLE

    def test_unknown_template_name_rejected(self):
        """Patch mode validates template_name before touching anything."""
        model_name = _create_template_model(TEMPLATE_FRONT_SINGLE)

        result = call_tool("update_model_templates", {
            "model_name": model_name,
            "template_name": f"NoSuchCard{unique_id()}",
            "side": "Front",
            "old_str": "MARKER-ALPHA",
            "new_str": "MARKER-BETA",
        })

        assert result.get("isError") is True, f"Expected an error, got: {result}"
        assert "not found" in str(result).lower()
        assert _read_front(model_name) == TEMPLATE_FRONT_SINGLE

    def test_invalid_side_rejected(self):
        """side must be exactly 'Front' or 'Back'."""
        model_name = _create_template_model(TEMPLATE_FRONT_SINGLE)

        result = call_tool("update_model_templates", {
            "model_name": model_name,
            "template_name": CARD_NAME,
            "side": "front",
            "old_str": "MARKER-ALPHA",
            "new_str": "MARKER-BETA",
        })

        assert result.get("isError") is True, f"Expected an error, got: {result}"
        assert _read_front(model_name) == TEMPLATE_FRONT_SINGLE


class TestPatchNoteFields:
    """Patch mode on update_note_fields (one field of one note)."""

    def test_patch_replaces_snippet(self):
        """A unique old_str is replaced in the named field."""
        uid = unique_id()
        original = f"Original Alpha {uid}"
        note_id = _create_note(original, f"Back {uid}")

        try:
            result = call_tool("update_note_fields", {
                "id": note_id,
                "field_name": "Front",
                "old_str": "Alpha",
                "new_str": "Beta",
            })

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["patched"] is True
            assert result["updatedFields"] == ["Front"]
            assert result["fieldCount"] == 1

            assert _read_field(note_id, "Front") == f"Original Beta {uid}"
            # The untouched field must stay untouched.
            assert _read_field(note_id, "Back") == f"Back {uid}"
        finally:
            _delete_note(note_id)

    def test_patch_zero_match_errors_and_writes_nothing(self):
        """0 matches must error, echo a current-content excerpt, and write nothing."""
        uid = unique_id()
        original = f"Original Alpha {uid}"
        note_id = _create_note(original, f"Back {uid}")

        try:
            result = call_tool("update_note_fields", {
                "id": note_id,
                "field_name": "Front",
                "old_str": "Omega",
                "new_str": "Beta",
            })

            assert result.get("isError") is True, f"Expected an error, got: {result}"
            text = str(result)
            assert "0 matches" in text
            assert "current_content_excerpt" in text
            assert "Original Alpha" in text

            assert _read_field(note_id, "Front") == original
        finally:
            _delete_note(note_id)

    def test_patch_multiple_matches_errors_and_writes_nothing(self):
        """2+ matches must error with the count and write nothing."""
        uid = unique_id()
        original = f"Alpha and Alpha again {uid}"
        note_id = _create_note(original, f"Back {uid}")

        try:
            result = call_tool("update_note_fields", {
                "id": note_id,
                "field_name": "Front",
                "old_str": "Alpha",
                "new_str": "Beta",
            })

            assert result.get("isError") is True, f"Expected an error, got: {result}"
            assert "2 times" in str(result)

            assert _read_field(note_id, "Front") == original
        finally:
            _delete_note(note_id)

    def test_mixed_mode_rejected(self):
        """fields combined with patch params is rejected and writes nothing."""
        uid = unique_id()
        original = f"Original Alpha {uid}"
        note_id = _create_note(original, f"Back {uid}")

        try:
            result = call_tool("update_note_fields", {
                "id": note_id,
                "fields": {"Front": f"Clobbered {uid}"},
                "field_name": "Front",
                "old_str": "Alpha",
                "new_str": "Beta",
            })

            assert result.get("isError") is True, f"Expected an error, got: {result}"
            assert "mutually exclusive" in str(result).lower()

            assert _read_field(note_id, "Front") == original
        finally:
            _delete_note(note_id)

    def test_unknown_field_name_rejected(self):
        """Patch mode validates field_name against the note's model."""
        uid = unique_id()
        original = f"Original Alpha {uid}"
        note_id = _create_note(original, f"Back {uid}")

        try:
            result = call_tool("update_note_fields", {
                "id": note_id,
                "field_name": "NoSuchField",
                "old_str": "Alpha",
                "new_str": "Beta",
            })

            assert result.get("isError") is True, f"Expected an error, got: {result}"
            assert _read_field(note_id, "Front") == original
        finally:
            _delete_note(note_id)

    def test_half_specified_patch_rejected(self):
        """old_str without new_str is rejected rather than silently ignored."""
        uid = unique_id()
        original = f"Original Alpha {uid}"
        note_id = _create_note(original, f"Back {uid}")

        try:
            result = call_tool("update_note_fields", {
                "id": note_id,
                "field_name": "Front",
                "old_str": "Alpha",
            })

            assert result.get("isError") is True, f"Expected an error, got: {result}"
            assert "new_str" in str(result)

            assert _read_field(note_id, "Front") == original
        finally:
            _delete_note(note_id)
