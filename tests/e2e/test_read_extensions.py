"""E2E tests for the read-tool extensions.

Covers two additions that let a client identify notes cheaply:

* ``notes_info(excerpt_chars=N)`` -- cap every returned field value at N
  characters and annotate each field with ``truncated`` / ``fullLength``.
* ``find_notes(include_first_field=true)`` -- add a ``noteLabels`` array of
  ``{noteId, firstField, truncated, fullLength}`` labels alongside the
  existing ``noteIds`` list.
"""
from __future__ import annotations

import pytest

from .conftest import unique_id
from .helpers import call_tool

# Longer than find_notes' 250-char first-field budget, so the same note
# exercises truncation on both tools.
LONG_FIELD_LENGTH = 400

# find_notes truncates firstField at this many characters.
FIRST_FIELD_EXCERPT_CHARS = 250


@pytest.fixture
def scratch_notes():
    """Collect created note IDs and delete them once the test finishes."""
    created: list[int] = []
    yield created
    if not created:
        return
    try:
        result = call_tool("delete_notes", {
            "notes": created,
            "confirmDeletion": True,
        })
    except Exception as e:  # cleanup must never mask the real assertion
        print(f"cleanup failed: could not delete notes {created}: {e}")
        return
    if result.get("isError"):
        print(f"cleanup failed: could not delete notes {created}: {result}")


def _create_deck(uid: str) -> str:
    """Create a scratch deck and return its name."""
    deck_name = f"E2E::ReadExt{uid}"
    call_tool("create_deck", {"deck_name": deck_name})
    return deck_name


def _add_note(deck_name: str, front: str, back: str) -> int:
    """Add a Basic note to a deck and return its note ID."""
    result = call_tool("add_note", {
        "deck_name": deck_name,
        "model_name": "Basic",
        "fields": {"Front": front, "Back": back},
    })
    assert "note_id" in result, f"Failed to create test note: {result}"
    return result["note_id"]


def _fields_of(note_id: int, **extra) -> dict:
    """Read one note through notes_info and return its fields dict."""
    result = call_tool("notes_info", {"notes": [note_id], **extra})
    assert result.get("isError") is not True, f"Unexpected error: {result}"
    assert result["count"] == 1, f"Expected exactly one note: {result}"
    return result["notes"][0]["fields"]


class TestNotesInfoExcerpt:
    """Tests for the excerpt_chars parameter on notes_info."""

    def test_long_value_is_truncated_and_marked(self, scratch_notes):
        """A value longer than the budget is sliced and marked truncated."""
        uid = unique_id()
        deck = _create_deck(uid)
        front = "F" * LONG_FIELD_LENGTH
        note_id = _add_note(deck, front, f"Back {uid}")
        scratch_notes.append(note_id)

        fields = _fields_of(note_id, excerpt_chars="10")

        assert fields["Front"]["value"] == front[:10]
        assert len(fields["Front"]["value"]) == 10
        assert fields["Front"]["truncated"] is True
        assert fields["Front"]["fullLength"] == LONG_FIELD_LENGTH

    def test_short_value_is_untouched_but_marked(self, scratch_notes):
        """A value that fits keeps its content and reports truncated=false."""
        uid = unique_id()
        deck = _create_deck(uid)
        back = f"Back {uid}"
        note_id = _add_note(deck, f"Front {uid}", back)
        scratch_notes.append(note_id)

        fields = _fields_of(note_id, excerpt_chars="1000")

        assert fields["Back"]["value"] == back
        assert fields["Back"]["truncated"] is False
        assert fields["Back"]["fullLength"] == len(back)

    def test_budget_equal_to_length_is_not_truncated(self, scratch_notes):
        """A value exactly at the budget fits -- no truncation at the boundary."""
        uid = unique_id()
        deck = _create_deck(uid)
        front = "B" * 32
        note_id = _add_note(deck, front, f"Back {uid}")
        scratch_notes.append(note_id)

        fields = _fields_of(note_id, excerpt_chars="32")

        assert fields["Front"]["value"] == front
        assert fields["Front"]["truncated"] is False
        assert fields["Front"]["fullLength"] == 32

    def test_full_length_matches_untruncated_read(self, scratch_notes):
        """fullLength equals the length of the value returned without excerpting."""
        uid = unique_id()
        deck = _create_deck(uid)
        note_id = _add_note(deck, "F" * LONG_FIELD_LENGTH, f"Back {uid}")
        scratch_notes.append(note_id)

        full_fields = _fields_of(note_id)
        excerpt_fields = _fields_of(note_id, excerpt_chars="25")

        for name, entry in excerpt_fields.items():
            assert entry["fullLength"] == len(full_fields[name]["value"]), (
                f"fullLength mismatch for field {name}"
            )
            assert full_fields[name]["value"].startswith(entry["value"])

    def test_omitted_excerpt_adds_no_markers(self, scratch_notes):
        """Without excerpt_chars the field entries keep their original shape."""
        uid = unique_id()
        deck = _create_deck(uid)
        note_id = _add_note(deck, "F" * LONG_FIELD_LENGTH, f"Back {uid}")
        scratch_notes.append(note_id)

        fields = _fields_of(note_id)

        for name, entry in fields.items():
            assert "truncated" not in entry, f"Unexpected marker on field {name}"
            assert "fullLength" not in entry, f"Unexpected marker on field {name}"
            assert set(entry) == {"value", "order", "description"}
        assert len(fields["Front"]["value"]) == LONG_FIELD_LENGTH

    def test_excerpt_composes_with_include_fields(self, scratch_notes):
        """Excerpting applies to the fields that survive include_fields."""
        uid = unique_id()
        deck = _create_deck(uid)
        note_id = _add_note(deck, "F" * LONG_FIELD_LENGTH, "B" * LONG_FIELD_LENGTH)
        scratch_notes.append(note_id)

        fields = _fields_of(note_id, include_fields=["Front"], excerpt_chars="5")

        assert set(fields) == {"Front"}
        assert len(fields["Front"]["value"]) == 5
        assert fields["Front"]["truncated"] is True

    def test_excerpt_composes_with_exclude_fields(self, scratch_notes):
        """Excerpting applies to the fields that survive exclude_fields."""
        uid = unique_id()
        deck = _create_deck(uid)
        note_id = _add_note(deck, "F" * LONG_FIELD_LENGTH, "B" * LONG_FIELD_LENGTH)
        scratch_notes.append(note_id)

        fields = _fields_of(note_id, exclude_fields=["Front"], excerpt_chars="5")

        assert set(fields) == {"Back"}
        assert len(fields["Back"]["value"]) == 5
        assert fields["Back"]["truncated"] is True
        assert fields["Back"]["fullLength"] == LONG_FIELD_LENGTH

    def test_excerpt_applies_to_every_note_in_batch(self, scratch_notes):
        """Every note in a batch gets excerpted values and markers."""
        uid = unique_id()
        deck = _create_deck(uid)
        ids = [
            _add_note(deck, "F" * LONG_FIELD_LENGTH + f"a{uid}", f"Back a {uid}"),
            _add_note(deck, "F" * LONG_FIELD_LENGTH + f"b{uid}", f"Back b {uid}"),
        ]
        scratch_notes.extend(ids)

        result = call_tool("notes_info", {"notes": ids, "excerpt_chars": "8"})

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["count"] == 2
        for note in result["notes"]:
            front = note["fields"]["Front"]
            assert len(front["value"]) == 8
            assert front["truncated"] is True
            assert front["fullLength"] > 8

    def test_excerpt_chars_zero_rejected(self, scratch_notes):
        """excerpt_chars=0 is a validation error, not an empty-string excerpt."""
        uid = unique_id()
        deck = _create_deck(uid)
        note_id = _add_note(deck, f"Front {uid}", f"Back {uid}")
        scratch_notes.append(note_id)

        result = call_tool("notes_info", {"notes": [note_id], "excerpt_chars": "0"})

        assert result.get("isError") is True, f"Expected an error result: {result}"

    def test_excerpt_chars_negative_rejected(self, scratch_notes):
        """A negative excerpt_chars is a validation error."""
        uid = unique_id()
        deck = _create_deck(uid)
        note_id = _add_note(deck, f"Front {uid}", f"Back {uid}")
        scratch_notes.append(note_id)

        result = call_tool("notes_info", {"notes": [note_id], "excerpt_chars": "-1"})

        assert result.get("isError") is True, f"Expected an error result: {result}"


class TestFindNotesFirstField:
    """Tests for the include_first_field parameter on find_notes."""

    def test_flag_off_returns_ids_only(self, scratch_notes):
        """Omitting include_first_field keeps the original response shape."""
        uid = unique_id()
        deck = _create_deck(uid)
        note_id = _add_note(deck, f"Front {uid}", f"Back {uid}")
        scratch_notes.append(note_id)

        result = call_tool("find_notes", {"query": f'deck:"{deck}"'})

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["noteIds"] == [note_id]
        assert result["count"] == 1
        assert result["total"] == 1
        assert "noteLabels" not in result

    def test_flag_on_returns_labels_aligned_with_ids(self, scratch_notes):
        """The noteLabels array matches noteIds one-for-one, in the same order."""
        uid = unique_id()
        deck = _create_deck(uid)
        ids = [
            _add_note(deck, f"Front a {uid}", f"Back a {uid}"),
            _add_note(deck, f"Front b {uid}", f"Back b {uid}"),
            _add_note(deck, f"Front c {uid}", f"Back c {uid}"),
        ]
        scratch_notes.extend(ids)

        result = call_tool("find_notes", {
            "query": f'deck:"{deck}"',
            "include_first_field": True,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert sorted(result["noteIds"]) == sorted(ids)
        labels = result["noteLabels"]
        assert [entry["noteId"] for entry in labels] == result["noteIds"]

    def test_first_field_matches_the_notes_actual_first_field(self, scratch_notes):
        """firstField carries the value of the note's lowest-order field."""
        uid = unique_id()
        deck = _create_deck(uid)
        front = f"Front label {uid}"
        note_id = _add_note(deck, front, f"Back {uid}")
        scratch_notes.append(note_id)

        result = call_tool("find_notes", {
            "query": f'deck:"{deck}"',
            "include_first_field": True,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        label = result["noteLabels"][0]
        assert label["noteId"] == note_id

        fields = _fields_of(note_id)
        first_field_name = min(fields, key=lambda name: fields[name]["order"])
        assert first_field_name == "Front"
        assert label["firstField"] == fields["Front"]["value"] == front
        assert label["truncated"] is False
        assert label["fullLength"] == len(front)

    def test_long_first_field_is_truncated_and_marked(self, scratch_notes):
        """A first field over the budget is sliced and reports its full length."""
        uid = unique_id()
        deck = _create_deck(uid)
        front = "F" * LONG_FIELD_LENGTH + uid
        note_id = _add_note(deck, front, f"Back {uid}")
        scratch_notes.append(note_id)

        result = call_tool("find_notes", {
            "query": f'deck:"{deck}"',
            "include_first_field": True,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        label = result["noteLabels"][0]
        assert label["firstField"] == front[:FIRST_FIELD_EXCERPT_CHARS]
        assert len(label["firstField"]) == FIRST_FIELD_EXCERPT_CHARS
        assert label["truncated"] is True
        assert label["fullLength"] == len(front)

    def test_labels_respect_the_limit_parameter(self, scratch_notes):
        """Only the current page of results is labeled."""
        uid = unique_id()
        deck = _create_deck(uid)
        ids = [
            _add_note(deck, f"Front a {uid}", f"Back a {uid}"),
            _add_note(deck, f"Front b {uid}", f"Back b {uid}"),
        ]
        scratch_notes.extend(ids)

        result = call_tool("find_notes", {
            "query": f'deck:"{deck}"',
            "limit": "1",
            "include_first_field": True,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["count"] == 1
        assert result["total"] == 2
        assert len(result["noteLabels"]) == 1
        assert result["noteLabels"][0]["noteId"] == result["noteIds"][0]

    def test_no_matches_returns_empty_labels(self):
        """A query with no hits still answers with an empty noteLabels array."""
        uid = unique_id()

        result = call_tool("find_notes", {
            "query": f'deck:"E2E::NoSuchDeck{uid}"',
            "include_first_field": True,
        })

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        assert result["noteIds"] == []
        assert result["total"] == 0
        assert result["noteLabels"] == []
