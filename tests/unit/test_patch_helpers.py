"""Unit tests for _patch_helpers -- the old_str/new_str patch contract.

``_patch_helpers`` is the shared safety layer behind the patch input mode on
``update_model_styling``, ``update_model_templates`` and ``update_note_fields``.
Three tools depend on it behaving identically, and the whole point of the module
is that a bad patch writes NOTHING -- so its rules deserve direct tests rather
than only being exercised through E2E writes against a real collection.

These are pure-logic tests: no Docker, no running Anki, no collection at all.
The module imports only ``typing`` and ``HandlerError``, and
``handler_wrappers`` imports ``aqt`` lazily (inside ``_get_mw``), so nothing
here ever reaches Anki -- the ``aqt`` stub in ``tests/unit/conftest.py`` is
never even touched by this file's imports.

Because ``conftest.py`` stubs ``anki_mcp_server.primitives`` as a non-package
(to suppress tool auto-discovery), the helper is loaded as a single file via
``importlib`` under its real dotted name rather than through the normal dotted
path -- exactly as ``test_model_helpers.py`` does.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from anki_mcp_server.handler_wrappers import HandlerError

_HELPER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "anki_mcp_server" / "primitives" / "essential" / "tools" / "_patch_helpers.py"
)
_spec = importlib.util.spec_from_file_location(
    "anki_mcp_server.primitives.essential.tools._patch_helpers", _HELPER_PATH
)
_patch_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_patch_helpers)

apply_patch = _patch_helpers.apply_patch
build_excerpt = _patch_helpers.build_excerpt
reject_empty_full_input = _patch_helpers.reject_empty_full_input
require_patch_pair = _patch_helpers.require_patch_pair
select_mode = _patch_helpers.select_mode

_EXCERPT_LEN = _patch_helpers._EXCERPT_LEN
_EXCERPT_LEAD = _patch_helpers._EXCERPT_LEAD


def _patch(content: str, old: str, new: str, **kwargs):
    """Call apply_patch with the two required labels defaulted."""
    return apply_patch(
        content, old, new,
        target=kwargs.pop("target", 'CSS of model "Basic"'),
        read_hint=kwargs.pop("read_hint", "model_styling"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# apply_patch -- the exactly-once success path
# ---------------------------------------------------------------------------


class TestApplyPatchSuccess:
    """A unique match is replaced and the rest of the content is untouched."""

    def test_unique_match_is_replaced(self):
        result = _patch(".card { color: red; }", "red", "blue")
        assert result == ".card { color: blue; }"

    def test_only_the_match_changes(self):
        content = "A\nB\nTARGET\nC\nD"
        assert _patch(content, "TARGET", "REPLACED") == "A\nB\nREPLACED\nC\nD"

    def test_empty_new_str_deletes_the_match(self):
        """new_str="" is legal -- it is how a caller deletes a snippet."""
        assert _patch("keep <b>drop</b> keep", "<b>drop</b> ", "") == "keep keep"

    def test_multiline_and_whitespace_are_matched_verbatim(self):
        content = ".card {\n    font-size: 20px;\n}\n"
        result = _patch(content, "    font-size: 20px;\n", "    font-size: 32px;\n")
        assert result == ".card {\n    font-size: 32px;\n}\n"

    def test_new_str_containing_old_str_does_not_re_expand(self):
        """Replacement is a single pass -- the inserted text is not rescanned."""
        assert _patch("size: 20px", "20px", "20px !important") == "size: 20px !important"

    def test_content_is_not_mutated_in_place(self):
        """The function is pure: it returns a new string, strings being immutable."""
        content = "alpha beta"
        result = _patch(content, "beta", "gamma")
        assert content == "alpha beta"
        assert result == "alpha gamma"


# ---------------------------------------------------------------------------
# apply_patch -- rejection paths (nothing may be written)
# ---------------------------------------------------------------------------


class TestApplyPatchEmptyOldStr:
    """An empty old_str is meaningless -- "" occurs everywhere."""

    def test_empty_old_str_is_rejected(self):
        with pytest.raises(HandlerError) as exc:
            _patch("anything", "", "x")
        assert exc.value.code == "validation_error"

    def test_rejection_carries_caller_context(self):
        with pytest.raises(HandlerError) as exc:
            _patch("anything", "", "x", model_name="Basic")
        assert exc.value.data["model_name"] == "Basic"


class TestApplyPatchNoMatch:
    """Zero matches means the caller's view of the content is stale."""

    def test_zero_match_raises_with_its_own_code(self):
        with pytest.raises(HandlerError) as exc:
            _patch("the actual content", "absent", "x")
        assert exc.value.code == "patch_no_match"

    def test_error_reports_the_content_length(self):
        content = "the actual content"
        with pytest.raises(HandlerError) as exc:
            _patch(content, "absent", "x")
        assert exc.value.data["content_length"] == len(content)

    def test_error_carries_an_excerpt_for_resync(self):
        """The excerpt is the whole point: it saves a separate read call."""
        with pytest.raises(HandlerError) as exc:
            _patch(".card { color: red; }", "colour", "x")
        assert exc.value.data["current_content_excerpt"] == ".card { color: red; }"

    def test_hint_names_the_read_tool_to_resync_with(self):
        with pytest.raises(HandlerError) as exc:
            _patch("content", "absent", "x", read_hint="notes_info")
        assert "notes_info" in exc.value.hint

    def test_message_names_the_target(self):
        with pytest.raises(HandlerError) as exc:
            _patch("content", "absent", "x", target='field "Front" of note 5')
        assert 'field "Front" of note 5' in exc.value.message

    def test_near_miss_on_whitespace_still_fails(self):
        """Whitespace is significant -- a near match is not a match."""
        with pytest.raises(HandlerError) as exc:
            _patch("color: red;", "color:red;", "x")
        assert exc.value.code == "patch_no_match"

    def test_empty_content_yields_an_empty_excerpt(self):
        with pytest.raises(HandlerError) as exc:
            _patch("", "anything", "x")
        assert exc.value.data["current_content_excerpt"] == ""
        assert exc.value.data["content_length"] == 0


class TestApplyPatchMultipleMatches:
    """Two or more matches make the edit ambiguous, so nothing is written."""

    def test_multiple_matches_raise_with_their_own_code(self):
        with pytest.raises(HandlerError) as exc:
            _patch("red red", "red", "blue")
        assert exc.value.code == "patch_multiple_matches"

    def test_error_reports_the_match_count(self):
        with pytest.raises(HandlerError) as exc:
            _patch("x x x x", "x", "y")
        assert exc.value.data["match_count"] == 4
        assert "4 times" in exc.value.message

    def test_hint_tells_the_caller_to_extend_old_str(self):
        with pytest.raises(HandlerError) as exc:
            _patch("red red", "red", "blue")
        assert "old_str" in exc.value.hint

    def test_extending_old_str_disambiguates(self):
        """The advice in the hint actually works."""
        content = "a: red;\nb: red;"
        assert _patch(content, "b: red;", "b: blue;") == "a: red;\nb: blue;"


class TestApplyPatchOverlappingOccurrences:
    """Document the ACTUAL semantics of self-overlapping old_str values.

    ``str.count`` counts NON-OVERLAPPING occurrences and ``str.replace`` scans in
    the same left-to-right order, so a self-overlapping needle can pass the
    exactly-once gate and be applied to the LEFTMOST occurrence. These tests pin
    that behavior down so a future "tighten the check" change is a deliberate,
    visible decision rather than an accident.
    """

    def test_overlapping_pair_counts_as_one_and_patches_leftmost(self):
        # "aaa" contains "aa" twice if overlaps count, once if they do not.
        assert "aaa".count("aa") == 1
        assert _patch("aaa", "aa", "X") == "Xa"

    def test_non_overlapping_repeat_is_still_caught_as_ambiguous(self):
        # "aaaa" -> two disjoint "aa", which the multi-match guard does reject.
        assert "aaaa".count("aa") == 2
        with pytest.raises(HandlerError) as exc:
            _patch("aaaa", "aa", "X")
        assert exc.value.code == "patch_multiple_matches"

    def test_longer_overlapping_needle_behaves_the_same(self):
        assert _patch("abababa", "ababa", "Z") == "Zba"


# ---------------------------------------------------------------------------
# build_excerpt -- anchoring behavior including the prefix-probe fallbacks
# ---------------------------------------------------------------------------


class TestBuildExcerpt:
    """The excerpt must point at where old_str almost matched, and say so."""

    def test_short_content_is_returned_whole_without_ellipses(self):
        assert build_excerpt("short content", "missing") == "short content"

    def test_empty_content_yields_empty_string(self):
        assert build_excerpt("", "anything") == ""

    def test_long_content_is_capped_and_marked_truncated(self):
        content = "z" * 1000
        excerpt = build_excerpt(content, "missing")
        # No anchor found -> window starts at the head, so only a trailing mark.
        assert excerpt == "z" * _EXCERPT_LEN + "..."

    def test_window_is_anchored_near_a_long_prefix_match(self):
        """A 60-char prefix hit moves the window to the near-miss site."""
        needle = "N" * 60 + "MISMATCH"
        content = "lead " * 200 + needle[:60] + " trailing content"
        excerpt = build_excerpt(content, needle)

        anchor = content.find("N" * 60)
        assert anchor > _EXCERPT_LEAD  # the interesting case: not at the head
        assert excerpt.startswith("...")
        assert "N" * 60 in excerpt

    def test_falls_back_to_a_shorter_prefix_probe(self):
        """When 60 chars miss, the 30-char probe still finds the near-miss."""
        needle = "P" * 30 + "Q" * 30 + "tail"
        content = "x" * 300 + "P" * 30 + "DIVERGES HERE" + "y" * 300
        excerpt = build_excerpt(content, needle)

        assert "P" * 30 in excerpt
        assert "DIVERGES HERE" in excerpt

    def test_falls_back_to_the_shortest_prefix_probe(self):
        needle = "R" * 15 + "S" * 60
        content = "x" * 300 + "R" * 15 + "DIVERGES" + "y" * 300
        excerpt = build_excerpt(content, needle)

        assert "R" * 15 in excerpt
        assert "DIVERGES" in excerpt

    def test_short_old_str_gets_exactly_one_probe_its_full_self(self):
        """An old_str shorter than every configured probe has no fallback.

        ``_find_anchor`` degrades to ``probes = [len(old_str)]``, i.e. the whole
        needle -- there is no shorter prefix to try. So a short old_str anchors
        only when it is present verbatim; otherwise the excerpt starts at the
        head of the content.
        """
        content = "x" * 300 + "NEEDLE" + "y" * 300

        anchored = build_excerpt(content, "NEEDLE")  # 6 chars, present
        assert "NEEDLE" in anchored
        assert anchored.startswith("...")

        unanchored = build_excerpt(content, "NEEDLE!")  # 7 chars, absent
        assert unanchored.startswith("x" * _EXCERPT_LEN)

    def test_no_prefix_match_falls_back_to_the_head_of_the_content(self):
        content = "HEAD MARKER " + "z" * 1000
        excerpt = build_excerpt(content, "!!!nothing like this!!!")
        assert excerpt.startswith("HEAD MARKER ")

    def test_anchor_at_the_start_produces_no_leading_ellipsis(self):
        """start clamps to 0, so there is nothing elided on the left."""
        content = "ANCHOR" + "z" * 1000
        excerpt = build_excerpt(content, "ANCHOR")  # anchors at index 0
        assert not excerpt.startswith("...")
        assert excerpt.endswith("...")

    def test_excerpt_body_never_exceeds_the_budget(self):
        content = "q" * 5000
        excerpt = build_excerpt(content, "q" * 60 + "ZZZ")
        body = excerpt.strip(".")
        assert len(body) <= _EXCERPT_LEN


# ---------------------------------------------------------------------------
# require_patch_pair -- half-specified patches
# ---------------------------------------------------------------------------


class TestRequirePatchPair:
    """old_str and new_str must arrive together, or not at all."""

    def test_both_present_is_accepted(self):
        assert require_patch_pair("a", "b") is None

    def test_both_absent_is_accepted(self):
        """Absence is how a caller selects full-replace mode."""
        assert require_patch_pair(None, None) is None

    def test_both_empty_strings_are_accepted_here(self):
        """Emptiness is apply_patch's problem; presence is all this checks."""
        assert require_patch_pair("", "") is None

    def test_missing_new_str_is_rejected(self):
        with pytest.raises(HandlerError) as exc:
            require_patch_pair("find me", None)
        assert exc.value.code == "validation_error"
        assert "new_str is missing" in exc.value.message

    def test_missing_old_str_is_rejected(self):
        with pytest.raises(HandlerError) as exc:
            require_patch_pair(None, "replacement")
        assert "old_str is missing" in exc.value.message

    def test_empty_new_str_with_old_str_is_accepted(self):
        """A deletion must not be mistaken for a missing argument."""
        assert require_patch_pair("find me", "") is None

    def test_hint_mentions_the_json_null_transport_hazard(self):
        """A value of exactly `null` is decoded away in transit -- say so."""
        with pytest.raises(HandlerError) as exc:
            require_patch_pair("find me", None)
        assert "null" in exc.value.hint

    def test_rejection_carries_caller_context(self):
        with pytest.raises(HandlerError) as exc:
            require_patch_pair("find me", None, note_id=42)
        assert exc.value.data["note_id"] == 42


# ---------------------------------------------------------------------------
# select_mode -- mutually exclusive input modes
# ---------------------------------------------------------------------------


_MODE_LABELS = {"full_params": "'fields'", "patch_params": "'old_str'/'new_str'"}


class TestSelectMode:
    """Exactly one of the two input modes, unless neither is explicitly allowed."""

    def test_full_only_selects_full(self):
        assert select_mode(
            full_present=True, patch_present=False, **_MODE_LABELS
        ) == "full"

    def test_patch_only_selects_patch(self):
        assert select_mode(
            full_present=False, patch_present=True, **_MODE_LABELS
        ) == "patch"

    def test_both_modes_are_rejected(self):
        with pytest.raises(HandlerError) as exc:
            select_mode(full_present=True, patch_present=True, **_MODE_LABELS)
        assert exc.value.code == "validation_error"
        assert "mutually exclusive" in exc.value.message

    def test_neither_mode_is_rejected_by_default(self):
        with pytest.raises(HandlerError) as exc:
            select_mode(full_present=False, patch_present=False, **_MODE_LABELS)
        assert exc.value.code == "validation_error"
        assert "No input provided" in exc.value.message

    def test_neither_mode_returns_none_when_allowed(self):
        """update_model_styling's LaTeX-only call touches no content at all."""
        assert select_mode(
            full_present=False,
            patch_present=False,
            allow_neither=True,
            **_MODE_LABELS,
        ) == "none"

    def test_allow_neither_does_not_weaken_the_both_check(self):
        with pytest.raises(HandlerError) as exc:
            select_mode(
                full_present=True,
                patch_present=True,
                allow_neither=True,
                **_MODE_LABELS,
            )
        assert "mutually exclusive" in exc.value.message

    def test_allow_neither_still_selects_full_when_full_is_present(self):
        assert select_mode(
            full_present=True,
            patch_present=False,
            allow_neither=True,
            **_MODE_LABELS,
        ) == "full"

    def test_errors_name_both_parameter_groups(self):
        with pytest.raises(HandlerError) as exc:
            select_mode(full_present=True, patch_present=True, **_MODE_LABELS)
        assert "'fields'" in exc.value.message
        assert "'old_str'/'new_str'" in exc.value.message

    def test_rejection_carries_caller_context(self):
        with pytest.raises(HandlerError) as exc:
            select_mode(
                full_present=False,
                patch_present=False,
                model_name="Basic",
                **_MODE_LABELS,
            )
        assert exc.value.data["model_name"] == "Basic"


# ---------------------------------------------------------------------------
# reject_empty_full_input -- the {} case both write tools now share
# ---------------------------------------------------------------------------


class TestRejectEmptyFullInput:
    """`{}` selects full mode (presence, not truthiness) and is rejected there."""

    def test_non_empty_mapping_is_accepted(self):
        assert reject_empty_full_input(
            {"Front": "x"}, full_params="'fields'", what="field name"
        ) is None

    def test_empty_mapping_is_rejected(self):
        with pytest.raises(HandlerError) as exc:
            reject_empty_full_input({}, full_params="'fields'", what="field name")
        assert exc.value.code == "validation_error"

    def test_message_names_the_parameter_and_the_actual_mistake(self):
        with pytest.raises(HandlerError) as exc:
            reject_empty_full_input({}, full_params="'templates'", what="x")
        assert "'templates'" in exc.value.message
        assert "empty" in exc.value.message

    def test_rejection_carries_caller_context(self):
        with pytest.raises(HandlerError) as exc:
            reject_empty_full_input(
                {}, full_params="'fields'", what="field name", note_id=7
            )
        assert exc.value.data["note_id"] == 7
