"""Unit tests for tool filtering helpers in tool_decorator.py.

These tests exercise the pure-Python filtering logic used by the
disabled_tools config feature. They do NOT require Anki or aqt -- just
pydantic and the standard library.
"""
from __future__ import annotations

from typing import Annotated, ClassVar, Literal, Union

import pytest
from pydantic import BaseModel, Field

from anki_mcp_server.tool_decorator import (
    _build_dynamic_description,
    _filter_union_type,
    _get_action_literal,
    _is_annotated_union,
    _parse_disabled,
)


# ---------------------------------------------------------------------------
# Minimal test models (mirror the pattern in card_management_tool.py)
# ---------------------------------------------------------------------------


class FooParams(BaseModel):
    _tool_description: ClassVar[str] = "foo: Do foo things."
    action: Literal["foo"]
    value: int = 0


class BarParams(BaseModel):
    _tool_description: ClassVar[str] = "bar: Do bar things."
    action: Literal["bar"]
    name: str = ""


class BazParams(BaseModel):
    _tool_description: ClassVar[str] = "baz: Do baz things."
    action: Literal["baz"]


class NoDescriptionParams(BaseModel):
    """Model without _tool_description."""
    action: Literal["no_desc"]


class NoActionFieldParams(BaseModel):
    """Model without an action field at all."""
    value: int = 0


TestUnion = Annotated[
    Union[FooParams, BarParams, BazParams],
    Field(discriminator="action"),
]


# ===========================================================================
# _parse_disabled
# ===========================================================================


class TestParseDisabled:
    """Tests for _parse_disabled()."""

    def test_empty_list(self):
        whole, actions = _parse_disabled([])
        assert whole == set()
        assert actions == {}

    def test_whole_tools_only(self):
        whole, actions = _parse_disabled(["sync", "list_decks"])
        assert whole == {"sync", "list_decks"}
        assert actions == {}

    def test_action_entries_only(self):
        whole, actions = _parse_disabled([
            "card_management:bury",
            "card_management:unbury",
        ])
        assert whole == set()
        assert actions == {"card_management": {"bury", "unbury"}}

    def test_mixed_entries(self):
        whole, actions = _parse_disabled([
            "sync",
            "card_management:bury",
            "filtered_deck:rebuild",
        ])
        assert whole == {"sync"}
        assert actions == {
            "card_management": {"bury"},
            "filtered_deck": {"rebuild"},
        }

    def test_colon_in_action_name(self):
        """An entry like 'tool:action:extra' should split on first colon only."""
        whole, actions = _parse_disabled(["tool:action:extra"])
        assert whole == set()
        assert actions == {"tool": {"action:extra"}}

    def test_duplicate_entries(self):
        """Duplicate entries are deduplicated via set semantics."""
        whole, actions = _parse_disabled(["sync", "sync", "card_management:bury", "card_management:bury"])
        assert whole == {"sync"}
        assert actions == {"card_management": {"bury"}}

    def test_multiple_actions_different_tools(self):
        whole, actions = _parse_disabled([
            "card_management:bury",
            "card_management:suspend",
            "filtered_deck:rebuild",
        ])
        assert whole == set()
        assert actions == {
            "card_management": {"bury", "suspend"},
            "filtered_deck": {"rebuild"},
        }


# ===========================================================================
# _get_action_literal
# ===========================================================================


class TestGetActionLiteral:
    """Tests for _get_action_literal()."""

    def test_standard_model(self):
        assert _get_action_literal(FooParams) == "foo"

    def test_another_model(self):
        assert _get_action_literal(BarParams) == "bar"

    def test_model_without_action_field(self):
        assert _get_action_literal(NoActionFieldParams) is None

    def test_all_test_models(self):
        """Verify all test models return the expected literal."""
        assert _get_action_literal(BazParams) == "baz"
        assert _get_action_literal(NoDescriptionParams) == "no_desc"


# ===========================================================================
# _is_annotated_union
# ===========================================================================


class TestIsAnnotatedUnion:
    """Tests for _is_annotated_union()."""

    def test_annotated_union_returns_true(self):
        assert _is_annotated_union(TestUnion) is True

    def test_plain_int_returns_false(self):
        assert _is_annotated_union(int) is False

    def test_plain_str_returns_false(self):
        assert _is_annotated_union(str) is False

    def test_bare_union_returns_false(self):
        """Union without Annotated wrapper is not detected."""
        bare = Union[FooParams, BarParams]
        assert _is_annotated_union(bare) is False

    def test_annotated_non_union_returns_false(self):
        """Annotated[int, Field()] is not a union."""
        ann = Annotated[int, Field(description="just an int")]
        assert _is_annotated_union(ann) is False

    def test_none_returns_false(self):
        assert _is_annotated_union(None) is False

    def test_list_type_returns_false(self):
        assert _is_annotated_union(list[int]) is False


# ===========================================================================
# _filter_union_type
# ===========================================================================


class TestFilterUnionType:
    """Tests for _filter_union_type()."""

    def test_nothing_disabled_returns_original(self):
        """When no actions are disabled, original annotation is returned as-is."""
        result_ann, enabled = _filter_union_type(TestUnion, set())
        assert result_ann is TestUnion
        assert set(enabled) == {FooParams, BarParams, BazParams}

    def test_disable_one_action(self):
        """Disabling one action removes it from the union."""
        result_ann, enabled = _filter_union_type(TestUnion, {"foo"})
        assert result_ann is not None
        assert result_ann is not TestUnion  # rebuilt
        assert FooParams not in enabled
        assert BarParams in enabled
        assert BazParams in enabled
        assert len(enabled) == 2

    def test_disable_two_actions(self):
        """Disabling two actions leaves only one."""
        result_ann, enabled = _filter_union_type(TestUnion, {"foo", "bar"})
        assert result_ann is not None
        assert len(enabled) == 1
        assert BazParams in enabled

    def test_disable_all_actions(self):
        """Disabling all actions returns (None, [])."""
        result_ann, enabled = _filter_union_type(TestUnion, {"foo", "bar", "baz"})
        assert result_ann is None
        assert enabled == []

    def test_disable_nonexistent_action(self):
        """Disabling an action name that doesn't match any model is a no-op."""
        result_ann, enabled = _filter_union_type(TestUnion, {"nonexistent"})
        assert result_ann is TestUnion
        assert len(enabled) == 3

    def test_filtered_annotation_is_valid_annotated(self):
        """The rebuilt annotation should still be an Annotated[Union[...], Field(discriminator=...)]."""
        result_ann, enabled = _filter_union_type(TestUnion, {"baz"})
        assert result_ann is not None
        assert _is_annotated_union(result_ann)

    def test_filtered_annotation_preserves_discriminator(self):
        """The rebuilt annotation should still use discriminator='action'."""
        from typing import get_args
        result_ann, _ = _filter_union_type(TestUnion, {"baz"})
        args = get_args(result_ann)
        # args[1] should be the FieldInfo with discriminator
        field_info = args[1]
        assert field_info.discriminator == "action"

    def test_two_member_union_after_filtering(self):
        """Verify that a two-member union is valid Pydantic after filtering."""
        result_ann, enabled = _filter_union_type(TestUnion, {"baz"})
        assert len(enabled) == 2
        assert {FooParams, BarParams} == set(enabled)


# ===========================================================================
# _build_dynamic_description
# ===========================================================================


class TestBuildDynamicDescription:
    """Tests for _build_dynamic_description()."""

    def test_all_models(self):
        desc = _build_dynamic_description("Manage cards", [FooParams, BarParams, BazParams])
        assert "Manage cards with 3 actions:" in desc
        assert "foo: Do foo things." in desc
        assert "bar: Do bar things." in desc
        assert "baz: Do baz things." in desc

    def test_single_model(self):
        desc = _build_dynamic_description("Manage cards", [FooParams])
        assert "Manage cards with 1 action:" in desc  # singular
        assert "foo: Do foo things." in desc

    def test_empty_models(self):
        desc = _build_dynamic_description("Manage cards", [])
        assert "Manage cards with 0 actions:" in desc

    def test_model_without_description(self):
        """Models without _tool_description are silently skipped in the listing."""
        desc = _build_dynamic_description("Test", [FooParams, NoDescriptionParams])
        # NoDescriptionParams has no _tool_description, so only FooParams line appears
        assert "with 1 action:" in desc
        assert "foo: Do foo things." in desc

    def test_description_format(self):
        """Each action line should be indented with '    - '."""
        desc = _build_dynamic_description("Header", [FooParams, BarParams])
        lines = desc.split("\n")
        # First line is header
        assert lines[0] == "Header with 2 actions:"
        # Action lines should start with '    - '
        action_lines = [l for l in lines if l.strip().startswith("- ")]
        assert len(action_lines) == 2

    def test_base_description_preserved(self):
        """The base description header text is preserved verbatim."""
        desc = _build_dynamic_description("Custom base text here", [BazParams])
        assert desc.startswith("Custom base text here with 1 action:")
