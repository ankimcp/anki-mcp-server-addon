"""E2E tests for the ``change_note_type`` tool.

IMPORTANT -- DESTRUCTIVE TOOL, HIDDEN BY DEFAULT
------------------------------------------------
``change_note_type`` is declared ``@Tool(..., destructive=True)``, so the whole
tool is hidden from ``tools/list`` unless the operator opts in via the
``enabled_destructive_tools`` addon config containing ``"change_note_type"``.

The DEFAULT e2e container (port 3141, ``.docker/config.json``) opts it in, so
these tests run there. The ``_require_change_note_type`` autouse fixture skips
the file at runtime on any server that does not expose the tool (same
runtime-skip style as test_model_fields_remove.py), so pointing the suite at a
differently-configured server degrades to a skip rather than a wall of failures.

The HIDE half of the coverage -- the tool being absent when NOT opted in -- lives
in ``test_tool_filtering_e2e.py`` (filtered container, port 3142, whose config
does not list it).

Every test builds its own uniquely-named, disposable source and target note
types via create_model, so no shared model ("Basic") is ever dirtied and tests
cannot interfere with each other. Uniquely-named models no other test touches
are safe to leave behind (same rationale as test_model_fields_remove.py); the
scratch decks and notes created here are likewise inert.
"""
from __future__ import annotations

import pytest

from .conftest import unique_id
from .helpers import call_tool, list_tools


@pytest.fixture(autouse=True)
def _require_change_note_type():
    """Skip when the destructive change_note_type tool is not opted in."""
    names = {t["name"] for t in list_tools()}
    if "change_note_type" not in names:
        pytest.skip(
            "change_note_type is destructive and hidden unless "
            "enabled_destructive_tools includes 'change_note_type'"
        )


# ---------------------------------------------------------------------------
# Scratch collection helpers
# ---------------------------------------------------------------------------

def _create_model(fields: list[str], uid: str, role: str) -> str:
    """Create a disposable note type whose single template uses the first field."""
    model_name = f"ChangeNoteType{role}{uid}"
    first = fields[0]
    result = call_tool("create_model", {
        "model_name": model_name,
        "in_order_fields": fields,
        "card_templates": [
            {
                "Name": "Card 1",
                "Front": f"{{{{{first}}}}}",
                "Back": f"{{{{FrontSide}}}}<hr id=\"answer\">{{{{{first}}}}}",
            },
        ],
    })
    assert result.get("isError") is not True, f"create_model failed: {result}"
    return model_name


def _create_deck(uid: str) -> str:
    deck_name = f"E2E::ChangeNoteType{uid}"
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


def _note_info(note_id: int) -> dict:
    result = call_tool("notes_info", {"notes": [note_id]})
    assert result.get("isError") is not True, f"notes_info failed: {result}"
    assert result["notes"], f"note {note_id} vanished: {result}"
    return result["notes"][0]


def _field_values(note_id: int) -> dict[str, str]:
    """Flatten notes_info's field dicts to {field name: value}."""
    return {
        name: payload["value"]
        for name, payload in _note_info(note_id)["fields"].items()
    }


def _model_name_of(note_id: int) -> str:
    return _note_info(note_id)["modelName"]


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

class TestDryRun:
    """dry_run=true must resolve the whole plan and write absolutely nothing."""

    def test_dry_run_returns_plan_and_leaves_notes_untouched(self):
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back", "Extra"], uid, "SrcDry")
        target = _create_model(["Text", "BackExtra"], uid, "DstDry")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}",
            "Back": f"back {uid}",
            "Extra": f"extra {uid}",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_id],
            "new_model_name": target,
            "field_mapping": {"Front": "Text", "Back": "BackExtra", "Extra": None},
            "dry_run": True,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["dry_run"] is True
        assert result["note_count"] == 1
        assert result["old_model"] == source
        assert result["new_model"] == target
        # Both mapping views are reported, in input shape and in Anki's shape.
        assert result["field_mapping"] == {
            "Front": "Text", "Back": "BackExtra", "Extra": None,
        }
        assert result["new_field_sources"] == {"Text": "Front", "BackExtra": "Back"}
        assert result["dropped_fields"] == ["Extra"]
        assert result["dropped_fields_with_content"] == {"Extra": 1}
        # A single same-named template on both sides maps cleanly: no card loss.
        assert result["templates"]["remapped"] is True
        assert result["templates"]["removed_templates"] == []
        assert result["cards_to_remove"] == 0
        # Statically true: change_notetype always marks the schema modified, so
        # the PREDICTION for a real run is unconditional...
        assert result["would_force_full_sync"] is True
        # ...while will_force_full_sync keeps its contract of reporting the
        # collection's ACTUAL state, which a dry run did not change. Whether it
        # is currently true depends on what else touched this collection, so
        # only the type is asserted.
        assert isinstance(result["will_force_full_sync"], bool)

        # Nothing was written.
        assert _model_name_of(note_id) == source
        assert _field_values(note_id) == {
            "Front": f"front {uid}",
            "Back": f"back {uid}",
            "Extra": f"extra {uid}",
        }

    def test_dry_run_allowed_when_default_mapping_would_drop_content(self):
        """The refusal is a REAL-RUN guard; a dry run must still show the plan."""
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back", "Extra"], uid, "SrcDrop")
        target = _create_model(["Front", "Back"], uid, "DstDrop")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}",
            "Back": f"back {uid}",
            "Extra": f"extra {uid}",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_id],
            "new_model_name": target,
            "dry_run": True,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["field_mapping"] == {
            "Front": "Front", "Back": "Back", "Extra": None,
        }
        assert result["dropped_fields_with_content"] == {"Extra": 1}
        assert "warning" in result
        assert _model_name_of(note_id) == source


# ---------------------------------------------------------------------------
# Real runs
# ---------------------------------------------------------------------------

class TestExplicitMapping:
    """A real run with an explicit mapping moves content where it was told to."""

    def test_content_moves_and_cards_survive(self):
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back", "Extra"], uid, "SrcMap")
        target = _create_model(["Text", "BackExtra"], uid, "DstMap")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}",
            "Back": f"back {uid}",
            "Extra": f"extra {uid}",
        })
        cards_before = _note_info(note_id)["cards"]

        result = call_tool("change_note_type", {
            "note_ids": [note_id],
            "new_model_name": target,
            "field_mapping": {"Front": "Text", "Back": "BackExtra", "Extra": None},
            "confirm": True,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["dry_run"] is False
        assert result["notes_changed"] == 1
        assert result["cards_removed"] == 0
        assert result["will_force_full_sync"] is True
        # Dropping a non-empty field must be called out even when requested.
        assert "Extra" in result.get("warning", "")

        assert _model_name_of(note_id) == target
        assert _field_values(note_id) == {
            "Text": f"front {uid}",
            "BackExtra": f"back {uid}",
        }
        # Same card ids => the cards (and their review history) were kept, not
        # deleted and regenerated.
        assert _note_info(note_id)["cards"] == cards_before


class TestDefaultNameMatch:
    """With no field_mapping, same-named fields carry over and nothing is lost."""

    def test_name_match_moves_content_and_leaves_new_field_empty(self):
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back"], uid, "SrcName")
        target = _create_model(["Front", "Back", "Bonus"], uid, "DstName")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}",
            "Back": f"back {uid}",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_id],
            "new_model_name": target,
            "confirm": True,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["notes_changed"] == 1
        assert result["field_mapping"] == {"Front": "Front", "Back": "Back"}
        assert result["new_field_sources"] == {
            "Front": "Front", "Back": "Back", "Bonus": None,
        }
        assert result["dropped_fields"] == []
        assert result["will_force_full_sync"] is True

        assert _model_name_of(note_id) == target
        assert _field_values(note_id) == {
            "Front": f"front {uid}",
            "Back": f"back {uid}",
            "Bonus": "",
        }

    def test_empty_unmatched_field_does_not_block_the_run(self):
        """An unmapped field that is empty everywhere loses nothing, so it passes."""
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back", "Extra"], uid, "SrcEmpty")
        target = _create_model(["Front", "Back"], uid, "DstEmpty")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}",
            "Back": f"back {uid}",
            "Extra": "",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_id],
            "new_model_name": target,
            "confirm": True,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["dropped_fields"] == ["Extra"]
        assert result["dropped_fields_with_content"] == {}
        assert _model_name_of(note_id) == target


# ---------------------------------------------------------------------------
# Refusals -- each must leave the collection untouched
# ---------------------------------------------------------------------------

class TestRefusals:

    def test_real_run_without_confirm_is_refused(self):
        """dry_run=false requires confirm=true -- the per-call safety interlock.

        Same shape as delete_notes' confirmDeletion: the description telling the
        client to dry-run first is not enforcement, so a real run must carry an
        explicit acknowledgement.
        """
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back"], uid, "SrcNoConfirm")
        target = _create_model(["Front", "Back"], uid, "DstNoConfirm")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}", "Back": f"back {uid}",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_id],
            "new_model_name": target,
        })

        assert result.get("isError") is True, f"Expected a refusal, got: {result}"
        assert "confirm" in str(result), result

        assert _model_name_of(note_id) == source
        assert _field_values(note_id) == {
            "Front": f"front {uid}", "Back": f"back {uid}",
        }

    def test_dry_run_does_not_need_confirm(self):
        """confirm is ignored on dry runs -- they write nothing to acknowledge."""
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back"], uid, "SrcDryNoConf")
        target = _create_model(["Front", "Back"], uid, "DstDryNoConf")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}", "Back": f"back {uid}",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_id],
            "new_model_name": target,
            "dry_run": True,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["dry_run"] is True
        assert _model_name_of(note_id) == source

    def test_duplicate_note_ids_are_rejected(self):
        """A repeated id would be remapped TWICE, scrambling that note.

        rslib's update_notes_for_new_notetype_and_generate_cards re-reads each
        note from storage inside its loop, so the second visit applies the index
        map to the ALREADY-converted fields. The backend does not check this, so
        the tool must -- and it rejects rather than silently de-duplicating.
        """
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back"], uid, "SrcDupIds")
        target = _create_model(["Text", "BackExtra"], uid, "DstDupIds")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}", "Back": f"back {uid}",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_id, note_id],
            "new_model_name": target,
            "field_mapping": {"Front": "Text", "Back": "BackExtra"},
            "confirm": True,
        })

        assert result.get("isError") is True, f"Expected a refusal, got: {result}"
        assert str(note_id) in str(result), result

        # Untouched: still the old note type, with both field values intact.
        assert _model_name_of(note_id) == source
        assert _field_values(note_id) == {
            "Front": f"front {uid}", "Back": f"back {uid}",
        }

    def test_duplicate_note_ids_are_rejected_on_dry_runs_too(self):
        """The duplicate check is an input-shape check, not a write guard."""
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back"], uid, "SrcDupDry")
        target = _create_model(["Front", "Back"], uid, "DstDupDry")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}", "Back": f"back {uid}",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_id, note_id],
            "new_model_name": target,
            "dry_run": True,
        })

        assert result.get("isError") is True, f"Expected a refusal, got: {result}"
        assert _model_name_of(note_id) == source

    def test_default_mapping_dropping_content_is_refused(self):
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back", "Extra"], uid, "SrcRefuse")
        target = _create_model(["Front", "Back"], uid, "DstRefuse")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}",
            "Back": f"back {uid}",
            "Extra": f"extra {uid}",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_id],
            "new_model_name": target,
            "confirm": True,
        })

        assert result.get("isError") is True, f"Expected a refusal, got: {result}"
        error_text = str(result)
        assert "Extra" in error_text, error_text
        assert "field_mapping" in error_text, error_text

        assert _model_name_of(note_id) == source
        assert _field_values(note_id)["Extra"] == f"extra {uid}"

    def test_unknown_field_names_are_rejected(self):
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back"], uid, "SrcUnknown")
        target = _create_model(["Text"], uid, "DstUnknown")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}",
            "Back": f"back {uid}",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_id],
            "new_model_name": target,
            "field_mapping": {"Nope": "Text", "Front": "AlsoNope"},
            "confirm": True,
        })

        assert result.get("isError") is True, f"Expected a refusal, got: {result}"
        error_text = str(result)
        assert "Nope" in error_text, error_text
        assert "AlsoNope" in error_text, error_text
        assert _model_name_of(note_id) == source

    def test_two_source_fields_targeting_one_field_are_rejected(self):
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back"], uid, "SrcDup")
        target = _create_model(["Text"], uid, "DstDup")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}",
            "Back": f"back {uid}",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_id],
            "new_model_name": target,
            "field_mapping": {"Front": "Text", "Back": "Text"},
            "confirm": True,
        })

        assert result.get("isError") is True, f"Expected a refusal, got: {result}"
        assert "Text" in str(result)
        assert _model_name_of(note_id) == source

    def test_mixed_source_note_types_are_rejected(self):
        """The index-based mapping is meaningless across note types -- reject it.

        The backend does NOT check this (it takes a single old_notetype_id and
        never verifies each note has it), so the tool must.
        """
        uid = unique_id()
        deck = _create_deck(uid)
        source_a = _create_model(["Front", "Back"], uid, "SrcMixA")
        source_b = _create_model(["Front", "Back"], uid, "SrcMixB")
        target = _create_model(["Front", "Back"], uid, "DstMix")
        note_a = _add_note(deck, source_a, {
            "Front": f"a front {uid}", "Back": f"a back {uid}",
        })
        note_b = _add_note(deck, source_b, {
            "Front": f"b front {uid}", "Back": f"b back {uid}",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_a, note_b],
            "new_model_name": target,
            "confirm": True,
        })

        assert result.get("isError") is True, f"Expected a refusal, got: {result}"
        error_text = str(result)
        assert source_a in error_text, error_text
        assert source_b in error_text, error_text

        assert _model_name_of(note_a) == source_a
        assert _model_name_of(note_b) == source_b

    def test_unknown_target_note_type_is_rejected(self):
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back"], uid, "SrcNoModel")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}", "Back": f"back {uid}",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_id],
            "new_model_name": f"NoSuchModel{uid}",
            "confirm": True,
        })

        assert result.get("isError") is True, f"Expected a refusal, got: {result}"
        assert _model_name_of(note_id) == source

    def test_missing_note_ids_are_rejected_without_partial_write(self):
        """A nonexistent id aborts the whole batch -- no half-migrated notes."""
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back"], uid, "SrcMissing")
        target = _create_model(["Front", "Back"], uid, "DstMissing")
        note_id = _add_note(deck, source, {
            "Front": f"front {uid}", "Back": f"back {uid}",
        })

        result = call_tool("change_note_type", {
            "note_ids": [note_id, 1],
            "new_model_name": target,
            "confirm": True,
        })

        assert result.get("isError") is True, f"Expected a refusal, got: {result}"
        assert _model_name_of(note_id) == source

    def test_empty_note_ids_are_rejected(self):
        uid = unique_id()
        target = _create_model(["Front", "Back"], uid, "DstEmptyIds")

        result = call_tool("change_note_type", {
            "note_ids": [],
            "new_model_name": target,
            "confirm": True,
        })

        assert result.get("isError") is True, f"Expected a refusal, got: {result}"


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

class TestBatch:

    def test_multiple_notes_of_one_type_move_together(self):
        uid = unique_id()
        deck = _create_deck(uid)
        source = _create_model(["Front", "Back"], uid, "SrcBatch")
        target = _create_model(["Text", "BackExtra"], uid, "DstBatch")
        note_ids = [
            _add_note(deck, source, {
                "Front": f"front {i} {uid}",
                "Back": f"back {i} {uid}",
            })
            for i in range(3)
        ]

        result = call_tool("change_note_type", {
            "note_ids": note_ids,
            "new_model_name": target,
            "field_mapping": {"Front": "Text", "Back": "BackExtra"},
            "confirm": True,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["notes_changed"] == 3
        assert result["note_count"] == 3

        for i, note_id in enumerate(note_ids):
            assert _model_name_of(note_id) == target
            assert _field_values(note_id) == {
                "Text": f"front {i} {uid}",
                "BackExtra": f"back {i} {uid}",
            }
