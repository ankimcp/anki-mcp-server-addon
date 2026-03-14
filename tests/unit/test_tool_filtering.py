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
    _make_mcp_tool,
    _parse_disabled,
    _registry,
    _validate_disabled_entries,
    validate_disabled_tools,
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

    def test_single_member_no_discriminator(self):
        """When filtering leaves exactly 1 member, no discriminator should be set.

        Union[tuple([X])] collapses to just X in Python's type system, so
        Annotated[X, Field(discriminator="action")] would break Pydantic
        schema generation. The fix uses Field() without a discriminator.
        """
        from typing import get_args

        result_ann, enabled = _filter_union_type(TestUnion, {"foo", "bar"})

        # Should have exactly one enabled model
        assert len(enabled) == 1
        assert enabled[0] is BazParams

        # The FieldInfo metadata should NOT have a discriminator
        args = get_args(result_ann)
        field_info = args[1]
        assert field_info.discriminator is None

        # The annotation should produce a valid Pydantic schema
        class TempModel(BaseModel):
            params: result_ann  # type: ignore[valid-type]

        schema = TempModel.model_json_schema()
        assert "properties" in schema
        assert "params" in schema["properties"]


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
        """Models without _tool_description raise ValueError."""
        with pytest.raises(ValueError, match="missing _tool_description"):
            _build_dynamic_description("Test", [FooParams, NoDescriptionParams])

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


# ===========================================================================
# _make_mcp_tool validation
# ===========================================================================


class TestMakeMcpToolValidation:
    """Tests for _make_mcp_tool() error handling around _BASE_DESCRIPTION."""

    def _make_multi_action_meta(self, func):
        """Build a registry-style meta dict for a multi-action tool.

        Sets __annotations__ explicitly on *func* so that the union type is
        available at runtime (``from __future__ import annotations`` would
        stringify it, which _is_annotated_union cannot introspect).
        """
        func.__annotations__ = {
            "params": Annotated[
                Union[FooParams, BarParams, BazParams],
                Field(discriminator="action"),
            ],
        }
        return {
            "name": "test_multi",
            "description": "placeholder",
            "original": func,
        }

    def _make_mock_mcp(self):
        """Return a minimal mock MCP object whose .tool() returns a decorator."""
        class _MockMCP:
            def tool(self, *, description):
                def decorator(fn):
                    return fn
                return decorator
        return _MockMCP()

    def test_raises_when_base_description_missing(self):
        """_make_mcp_tool raises ValueError when the module has no _BASE_DESCRIPTION."""
        def handler(params):
            return {}

        meta = self._make_multi_action_meta(handler)
        mcp = self._make_mock_mcp()

        async def call_main_thread(name, kwargs):
            return {}

        with pytest.raises(ValueError, match="missing _BASE_DESCRIPTION"):
            _make_mcp_tool(mcp, call_main_thread, "test_multi", meta)

    def test_raises_when_tool_description_missing(self, monkeypatch):
        """_make_mcp_tool raises ValueError when a Params model lacks _tool_description."""

        class MissingDescParams(BaseModel):
            action: Literal["missing_desc"]
            value: int = 0

        def handler(params):
            return {}

        handler.__annotations__ = {
            "params": Annotated[
                Union[FooParams, MissingDescParams],
                Field(discriminator="action"),
            ],
        }

        meta = {
            "name": "test_missing_desc",
            "description": "placeholder",
            "original": handler,
        }
        mcp = self._make_mock_mcp()

        async def call_main_thread(name, kwargs):
            return {}

        # Patch _BASE_DESCRIPTION so it doesn't fail on that check first
        import tests.unit.test_tool_filtering as this_module
        monkeypatch.setattr(this_module, "_BASE_DESCRIPTION", "Test base", raising=False)

        with pytest.raises(ValueError, match="missing _tool_description"):
            _make_mcp_tool(mcp, call_main_thread, "test_missing_desc", meta)

    def test_no_error_when_base_description_present(self, monkeypatch):
        """_make_mcp_tool succeeds when _BASE_DESCRIPTION exists in the module."""
        def handler(params):
            return {}

        meta = self._make_multi_action_meta(handler)
        mcp = self._make_mock_mcp()

        async def call_main_thread(name, kwargs):
            return {}

        # handler is defined in this test file, so patch _BASE_DESCRIPTION
        # onto this module.
        import tests.unit.test_tool_filtering as this_module
        monkeypatch.setattr(this_module, "_BASE_DESCRIPTION", "Manage test actions", raising=False)

        # Should not raise
        _make_mcp_tool(mcp, call_main_thread, "test_multi", meta)


# ===========================================================================
# _validate_disabled_entries
# ===========================================================================


@pytest.fixture(autouse=False)
def fake_registry(monkeypatch):
    """Populate _registry with fake tools for validation tests.

    Creates:
      - "sync": a simple single-action tool (no union param)
      - "multi_tool": a multi-action tool with FooParams/BarParams/BazParams
    """
    saved = dict(_registry)
    _registry.clear()

    # Simple tool (no union annotation)
    def simple_handler() -> dict:
        return {}

    _registry["sync"] = {
        "name": "sync",
        "description": "Sync collection",
        "original": simple_handler,
    }

    # Multi-action tool with a union annotation.
    # We must set __annotations__ explicitly because `from __future__ import
    # annotations` turns them into strings, which _is_annotated_union can't
    # introspect at runtime (same as real tool code which doesn't use the
    # future import).
    def multi_handler(params):  # type: ignore[no-untyped-def]
        return {}

    multi_handler.__annotations__ = {
        "params": Annotated[
            Union[FooParams, BarParams, BazParams],
            Field(discriminator="action"),
        ],
    }

    _registry["multi_tool"] = {
        "name": "multi_tool",
        "description": "Multi-action tool",
        "original": multi_handler,
    }

    yield

    _registry.clear()
    _registry.update(saved)


class TestValidateDisabledEntries:
    """Tests for _validate_disabled_entries()."""

    def test_empty_sets_return_no_warnings(self, fake_registry):
        assert _validate_disabled_entries(set(), {}) == []

    def test_valid_whole_tool_returns_no_warnings(self, fake_registry):
        assert _validate_disabled_entries({"sync"}, {}) == []

    def test_unknown_whole_tool_returns_warning(self, fake_registry):
        warnings = _validate_disabled_entries({"nonexistent"}, {})
        assert len(warnings) == 1
        assert "'nonexistent'" in warnings[0]
        assert "typo" in warnings[0]

    def test_unknown_tool_in_action_filter_returns_warning(self, fake_registry):
        warnings = _validate_disabled_entries(set(), {"unknown_tool": {"some_action"}})
        assert len(warnings) == 1
        assert "'unknown_tool'" in warnings[0]

    def test_action_on_non_multi_tool_returns_warning(self, fake_registry):
        """Trying to disable an action on a tool that has no union param."""
        warnings = _validate_disabled_entries(set(), {"sync": {"rebuild"}})
        assert len(warnings) == 1
        assert "not a multi-action tool" in warnings[0]
        assert "'sync:rebuild'" in warnings[0]

    def test_valid_action_returns_no_warnings(self, fake_registry):
        warnings = _validate_disabled_entries(set(), {"multi_tool": {"foo"}})
        assert warnings == []

    def test_unknown_action_returns_warning_with_available(self, fake_registry):
        warnings = _validate_disabled_entries(set(), {"multi_tool": {"nonexistent"}})
        assert len(warnings) == 1
        assert "'nonexistent'" in warnings[0]
        assert "not found" in warnings[0]
        # Should list available actions
        assert "bar" in warnings[0]
        assert "baz" in warnings[0]
        assert "foo" in warnings[0]

    def test_multiple_issues_return_multiple_warnings(self, fake_registry):
        warnings = _validate_disabled_entries(
            {"bad_tool"},
            {"multi_tool": {"bad_action"}},
        )
        assert len(warnings) == 2

    def test_warnings_are_sorted(self, fake_registry):
        """Warnings for whole tools should come in sorted order."""
        warnings = _validate_disabled_entries({"zzz", "aaa"}, {})
        assert len(warnings) == 2
        assert "'aaa'" in warnings[0]
        assert "'zzz'" in warnings[1]


# ===========================================================================
# validate_disabled_tools (public API)
# ===========================================================================


class TestValidateDisabledTools:
    """Tests for validate_disabled_tools()."""

    def test_empty_list_returns_no_warnings(self, fake_registry):
        assert validate_disabled_tools([]) == []

    def test_valid_entries_return_no_warnings(self, fake_registry):
        assert validate_disabled_tools(["sync", "multi_tool:foo"]) == []

    def test_typo_in_tool_name(self, fake_registry):
        warnings = validate_disabled_tools(["syncc"])
        assert len(warnings) == 1
        assert "'syncc'" in warnings[0]

    def test_typo_in_action_name(self, fake_registry):
        warnings = validate_disabled_tools(["multi_tool:fooo"])
        assert len(warnings) == 1
        assert "'fooo'" in warnings[0]

    def test_mixed_valid_and_invalid(self, fake_registry):
        """Valid entries produce no warnings, invalid ones do."""
        warnings = validate_disabled_tools([
            "sync",                    # valid whole tool
            "multi_tool:foo",          # valid action
            "nonexistent",             # invalid whole tool
            "multi_tool:bad_action",   # invalid action
        ])
        assert len(warnings) == 2

    def test_action_on_simple_tool(self, fake_registry):
        warnings = validate_disabled_tools(["sync:something"])
        assert len(warnings) == 1
        assert "not a multi-action tool" in warnings[0]
