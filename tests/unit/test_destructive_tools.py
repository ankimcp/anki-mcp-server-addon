"""Unit tests for the destructive-tools opt-in mechanism in tool_decorator.py.

Covers GitHub issue #46: high-risk tools/actions ship hidden by default and
must be explicitly opted in via the ``enabled_destructive_tools`` config
allow-list.

These tests exercise the pure-Python gating logic and require no Anki/aqt --
just pydantic and the standard library. They mirror the fixture style of
``test_tool_filtering.py`` (the sibling suite for ``disabled_tools``):

  * Minimal Pydantic Params models with a ``Literal`` action field.
  * A ``_destructive: ClassVar[bool] = True`` marker on the action models
    that opt in to the destructive gate.
  * A ``_MockMCP`` recorder whose ``.tool()`` captures what got registered,
    so we can assert on which tools/actions reached ``tools/list``.

IMPORTANT fixture note: the real ``_registry`` meta dicts (and the ones built
by ``register_tools``/``validate_enabled_destructive_tools``) carry a
``"destructive"`` key. The existing ``test_tool_filtering.py`` fakes predate
that key and omit it. Any fake meta dict that flows through ``register_tools``
or ``validate_enabled_destructive_tools`` here MUST include ``"destructive"``
(and, when relevant, action models marked ``_destructive``), because those
code paths read ``meta["destructive"]`` directly.
"""
from __future__ import annotations

from typing import Annotated, ClassVar, Literal, Union

import pytest
from pydantic import BaseModel, Field

from anki_mcp_server.tool_decorator import (
    Tool,
    _get_destructive_actions,
    _registry,
    _validate_disabled_entries,
    register_tools,
    validate_disabled_tools,
    validate_enabled_destructive_tools,
)


# ---------------------------------------------------------------------------
# Minimal test models (mirror the pattern in card_management_tool.py)
#
# SafeAParams / SafeBParams      -> non-destructive actions
# DestructiveParams / DropParams -> actions marked _destructive=True
# ---------------------------------------------------------------------------


class SafeAParams(BaseModel):
    _tool_description: ClassVar[str] = "safe_a: A safe action."
    action: Literal["safe_a"]
    value: int = 0


class SafeBParams(BaseModel):
    _tool_description: ClassVar[str] = "safe_b: Another safe action."
    action: Literal["safe_b"]
    name: str = ""


class DestructiveParams(BaseModel):
    _tool_description: ClassVar[str] = "wipe: Destroy things."
    _destructive: ClassVar[bool] = True
    action: Literal["wipe"]


class DropParams(BaseModel):
    _tool_description: ClassVar[str] = "drop: Drop things."
    _destructive: ClassVar[bool] = True
    action: Literal["drop"]


# A multi-action union: two safe actions + one destructive action.
MixedUnion = Annotated[
    Union[SafeAParams, SafeBParams, DestructiveParams],
    Field(discriminator="action"),
]

# A union where every action is safe (no _destructive markers).
AllSafeUnion = Annotated[
    Union[SafeAParams, SafeBParams],
    Field(discriminator="action"),
]

# A union with two destructive actions among the safe ones.
TwoDestructiveUnion = Annotated[
    Union[SafeAParams, DestructiveParams, DropParams],
    Field(discriminator="action"),
]


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _MockMCP:
    """Recorder MCP whose .tool() captures (description, wrapper) per tool.

    ``register_tools`` -> ``_make_mcp_tool`` calls ``mcp.tool(description=...)``
    and applies the returned decorator to the async wrapper. We capture both
    so tests can assert which tools registered and inspect the rebuilt
    discriminated-union schema for per-action gating.
    """

    def __init__(self) -> None:
        self.registered: list[dict] = []

    def tool(self, *, description):
        def decorator(fn):
            self.registered.append(
                {
                    "name": getattr(fn, "__name__", None),
                    "description": description,
                    "annotations": getattr(fn, "__annotations__", {}),
                    "fn": fn,
                }
            )
            return fn

        return decorator

    # --- assertion helpers -------------------------------------------------

    def names(self) -> set[str]:
        return {entry["name"] for entry in self.registered}

    def by_name(self, name: str) -> dict | None:
        for entry in self.registered:
            if entry["name"] == name:
                return entry
        return None


async def _call_main_thread(name, kwargs):  # pragma: no cover - never awaited
    return {}


def _schema_actions(entry: dict) -> set[str]:
    """Extract action literals from a registered multi-action tool entry.

    Reads the rebuilt ``params`` annotation that ``_make_mcp_tool`` placed on
    the wrapper's ``__annotations__`` and pulls the ``Literal`` value out of
    each union member's ``action`` field. This reflects exactly what reaches
    the MCP schema (the union is what FastMCP turns into the JSON schema).
    """
    from typing import get_args

    ann = entry["annotations"].get("params")
    assert ann is not None, "expected a 'params' annotation on the wrapper"
    union_args = get_args(ann)  # (Union[...], FieldInfo) OR (SingleModel, FieldInfo)
    inner = union_args[0]
    members = get_args(inner)
    if not members:
        # Single-member case: Annotated[X, Field()] collapses the union away.
        members = (inner,)
    actions: set[str] = set()
    for m in members:
        field = m.model_fields.get("action")
        if field and hasattr(field.annotation, "__args__"):
            actions.add(field.annotation.__args__[0])
    return actions


# ---------------------------------------------------------------------------
# Registry-mutation fixtures
#
# Each builds a fresh registry around handler functions whose runtime
# __annotations__ carry the union type (the addon does NOT use
# `from __future__ import annotations`, so its annotations are real objects;
# we set them explicitly here for the same reason).
# ---------------------------------------------------------------------------


def _set_union_annotation(func, union):
    func.__annotations__ = {"params": union}
    return func


@pytest.fixture
def patch_registry(monkeypatch):
    """Provide a callable that swaps _registry for a caller-built dict.

    Saves/restores the real registry around the test so import-time tool
    registration from the package is not clobbered.
    """
    saved = dict(_registry)
    _registry.clear()

    def _install(entries: dict[str, dict]) -> None:
        _registry.clear()
        _registry.update(entries)

    yield _install

    _registry.clear()
    _registry.update(saved)


def _single_meta(name: str, *, destructive: bool) -> dict:
    """Registry meta for a single-action tool (no union param)."""

    def handler() -> dict:  # no params -> not a multi-action tool
        return {}

    return {
        "name": name,
        "description": f"{name} description",
        "original": handler,
        "write": True,
        "destructive": destructive,
    }


def _multi_meta(name: str, union, *, destructive: bool = False) -> dict:
    """Registry meta for a multi-action tool.

    ``destructive`` here is the WHOLE-TOOL flag (almost always False for
    multi-action tools -- their destructiveness is per-action via the
    ``_destructive`` ClassVar on member models). It is included because
    ``register_tools`` reads ``meta["destructive"]`` unconditionally.
    """

    def handler(params):  # type: ignore[no-untyped-def]
        return {}

    _set_union_annotation(handler, union)
    # Multi-action tools need a module-level _BASE_DESCRIPTION for
    # _make_mcp_tool to rebuild the description. handler is defined in THIS
    # module, so the module already needs the constant -- see below.
    return {
        "name": name,
        "description": f"{name} description",
        "original": handler,
        "write": True,
        "destructive": destructive,
    }


# _make_mcp_tool looks up _BASE_DESCRIPTION in the module where the handler
# was DEFINED (via inspect.getmodule). All our multi-action handlers are
# defined in this test module, so the constant must live here.
_BASE_DESCRIPTION = "Manage test things"


# ===========================================================================
# _get_destructive_actions
# ===========================================================================


class TestGetDestructiveActions:
    """Tests for _get_destructive_actions()."""

    def test_single_action_tool_returns_empty(self):
        """A tool with no union param has no destructive actions."""

        def handler() -> dict:
            return {}

        assert _get_destructive_actions(handler) == set()

    def test_multi_action_with_destructive_returns_those_literals(self):
        """Only actions whose model is marked _destructive are returned."""

        def handler(params):  # type: ignore[no-untyped-def]
            return {}

        _set_union_annotation(handler, MixedUnion)
        assert _get_destructive_actions(handler) == {"wipe"}

    def test_multi_action_with_two_destructive(self):
        def handler(params):  # type: ignore[no-untyped-def]
            return {}

        _set_union_annotation(handler, TwoDestructiveUnion)
        assert _get_destructive_actions(handler) == {"wipe", "drop"}

    def test_multi_action_all_safe_returns_empty(self):
        """A union with no _destructive markers yields the empty set."""

        def handler(params):  # type: ignore[no-untyped-def]
            return {}

        _set_union_annotation(handler, AllSafeUnion)
        assert _get_destructive_actions(handler) == set()


# ===========================================================================
# register_tools -- whole-tool destructive gating
# ===========================================================================


class TestRegisterToolsWholeTool:
    """Whole-tool destructive gate via register_tools()."""

    def test_destructive_tool_hidden_when_not_opted_in(self, patch_registry):
        patch_registry({"delete_decks": _single_meta("delete_decks", destructive=True)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, enabled_destructive_tools=[])
        assert "delete_decks" not in mcp.names()

    def test_destructive_tool_hidden_with_none_opt_in(self, patch_registry):
        """enabled_destructive_tools=None behaves like an empty allow-list."""
        patch_registry({"delete_decks": _single_meta("delete_decks", destructive=True)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread)  # no allow-list passed
        assert "delete_decks" not in mcp.names()

    def test_destructive_tool_revealed_when_opted_in(self, patch_registry):
        patch_registry({"delete_decks": _single_meta("delete_decks", destructive=True)})
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread, enabled_destructive_tools=["delete_decks"]
        )
        assert "delete_decks" in mcp.names()

    def test_non_destructive_tool_always_present(self, patch_registry):
        patch_registry({"sync": _single_meta("sync", destructive=False)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, enabled_destructive_tools=[])
        assert "sync" in mcp.names()

    def test_opted_in_but_also_disabled_is_hidden(self, patch_registry):
        """disabled_tools wins even over an opted-in destructive tool."""
        patch_registry({"delete_decks": _single_meta("delete_decks", destructive=True)})
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            disabled_tools=["delete_decks"],
            enabled_destructive_tools=["delete_decks"],
        )
        assert "delete_decks" not in mcp.names()

    def test_whole_tool_opt_in_is_exact_match_only(self, patch_registry):
        """Opting in 'other_tool' does not reveal 'delete_decks'."""
        patch_registry({"delete_decks": _single_meta("delete_decks", destructive=True)})
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread, enabled_destructive_tools=["other_tool"]
        )
        assert "delete_decks" not in mcp.names()


# ===========================================================================
# register_tools -- per-action destructive gating
# ===========================================================================


class TestRegisterToolsPerAction:
    """Per-action destructive gate within a multi-action tool."""

    def test_destructive_action_removed_when_not_opted_in(self, patch_registry):
        patch_registry({"deck_management": _multi_meta("deck_management", MixedUnion)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, enabled_destructive_tools=[])

        entry = mcp.by_name("deck_management")
        assert entry is not None, "tool should still register (safe actions remain)"
        actions = _schema_actions(entry)
        assert "wipe" not in actions
        assert {"safe_a", "safe_b"} <= actions

    def test_destructive_action_present_when_action_opted_in(self, patch_registry):
        patch_registry({"deck_management": _multi_meta("deck_management", MixedUnion)})
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            enabled_destructive_tools=["deck_management:wipe"],
        )
        entry = mcp.by_name("deck_management")
        assert entry is not None
        actions = _schema_actions(entry)
        assert "wipe" in actions
        assert {"safe_a", "safe_b"} <= actions

    def test_whole_tool_opt_in_does_not_reveal_destructive_action(self, patch_registry):
        """Exact-match: 'deck_management' (whole tool) != 'deck_management:wipe'."""
        patch_registry({"deck_management": _multi_meta("deck_management", MixedUnion)})
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            enabled_destructive_tools=["deck_management"],
        )
        entry = mcp.by_name("deck_management")
        assert entry is not None
        actions = _schema_actions(entry)
        assert "wipe" not in actions

    def test_non_destructive_actions_always_present(self, patch_registry):
        """Safe actions appear regardless of opt-in state."""
        patch_registry({"deck_management": _multi_meta("deck_management", MixedUnion)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread)  # nothing opted in
        entry = mcp.by_name("deck_management")
        actions = _schema_actions(entry)
        assert "safe_a" in actions
        assert "safe_b" in actions

    def test_opting_in_one_of_two_destructive_actions(self, patch_registry):
        patch_registry(
            {"deck_management": _multi_meta("deck_management", TwoDestructiveUnion)}
        )
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            enabled_destructive_tools=["deck_management:wipe"],
        )
        entry = mcp.by_name("deck_management")
        actions = _schema_actions(entry)
        assert "wipe" in actions   # opted in
        assert "drop" not in actions  # not opted in
        assert "safe_a" in actions


# ===========================================================================
# register_tools -- precedence / composition with disabled_tools
# ===========================================================================


class TestRegisterToolsPrecedence:
    """Precedence between destructive gating and disabled_tools."""

    def test_opted_in_action_also_disabled_is_hidden(self, patch_registry):
        """disabled_tools removes an action even if it was opted in."""
        patch_registry({"deck_management": _multi_meta("deck_management", MixedUnion)})
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            disabled_tools=["deck_management:wipe"],
            enabled_destructive_tools=["deck_management:wipe"],
        )
        entry = mcp.by_name("deck_management")
        assert entry is not None
        actions = _schema_actions(entry)
        assert "wipe" not in actions

    def test_all_actions_hidden_skips_whole_tool(self, patch_registry):
        """If every action is gated/disabled, the tool is not registered.

        Here the two safe actions are disabled and the destructive one is not
        opted in -> nothing remains -> the tool is skipped entirely.
        """
        patch_registry({"deck_management": _multi_meta("deck_management", MixedUnion)})
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            disabled_tools=["deck_management:safe_a", "deck_management:safe_b"],
            enabled_destructive_tools=[],  # wipe stays hidden
        )
        assert "deck_management" not in mcp.names()

    def test_destructive_action_hidden_does_not_require_disabled_entry(
        self, patch_registry
    ):
        """A destructive action is hidden purely by the opt-in gate.

        No disabled_tools entry is needed -- the gate alone removes it, while
        the safe actions register normally.
        """
        patch_registry({"deck_management": _multi_meta("deck_management", MixedUnion)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread)
        entry = mcp.by_name("deck_management")
        actions = _schema_actions(entry)
        assert actions == {"safe_a", "safe_b"}


# ===========================================================================
# validate_enabled_destructive_tools
# ===========================================================================


class TestValidateEnabledDestructiveTools:
    """Tests for validate_enabled_destructive_tools()."""

    @pytest.fixture
    def destructive_registry(self, patch_registry):
        """Registry with a destructive single tool, a safe tool, and a
        multi-action tool that has one destructive + safe actions."""
        patch_registry(
            {
                "delete_decks": _single_meta("delete_decks", destructive=True),
                "sync": _single_meta("sync", destructive=False),
                "deck_management": _multi_meta("deck_management", MixedUnion),
            }
        )

    def test_empty_list_no_warnings(self, destructive_registry):
        assert validate_enabled_destructive_tools([]) == []

    def test_nonexistent_tool_warns(self, destructive_registry):
        warnings = validate_enabled_destructive_tools(["nope"])
        assert len(warnings) == 1
        assert "'nope'" in warnings[0]
        assert "enabled_destructive_tools" in warnings[0]
        assert "typo" in warnings[0]

    def test_nonexistent_action_warns(self, destructive_registry):
        warnings = validate_enabled_destructive_tools(["deck_management:ghost"])
        assert len(warnings) == 1
        assert "'ghost'" in warnings[0] or "deck_management:ghost" in warnings[0]
        assert "not found" in warnings[0]

    def test_real_but_not_destructive_whole_tool_is_noop_warning(
        self, destructive_registry
    ):
        """sync is a real tool but not destructive -> opting it in is a no-op."""
        warnings = validate_enabled_destructive_tools(["sync"])
        assert len(warnings) == 1
        assert "no-op" in warnings[0]
        assert "'sync'" in warnings[0]

    def test_real_but_not_destructive_action_is_noop_warning(
        self, destructive_registry
    ):
        """safe_a is a real action but not destructive -> no-op."""
        warnings = validate_enabled_destructive_tools(["deck_management:safe_a"])
        assert len(warnings) == 1
        assert "no-op" in warnings[0]
        assert "deck_management:safe_a" in warnings[0]

    def test_correct_destructive_whole_tool_no_warning(self, destructive_registry):
        assert validate_enabled_destructive_tools(["delete_decks"]) == []

    def test_correct_destructive_action_no_warning(self, destructive_registry):
        """deck_management:wipe is a real destructive action -> no warning,
        and crucially NOT flagged as nonexistent even though it is hidden by
        default (validation reads the unfiltered union)."""
        assert validate_enabled_destructive_tools(["deck_management:wipe"]) == []

    def test_hidden_by_default_destructive_action_not_flagged_nonexistent(
        self, destructive_registry
    ):
        """Regression guard for the 'reads unfiltered union' requirement.

        'wipe' never reaches tools/list unless opted in, yet validation must
        still recognize it as a known action (no 'not found' warning)."""
        warnings = validate_enabled_destructive_tools(["deck_management:wipe"])
        assert not any("not found" in w for w in warnings)

    def test_mixed_entries_accumulate_warnings(self, destructive_registry):
        warnings = validate_enabled_destructive_tools(
            [
                "delete_decks",            # valid destructive -> no warning
                "deck_management:wipe",    # valid destructive action -> no warning
                "sync",                    # no-op (not destructive)
                "nope",                    # typo
                "deck_management:safe_a",  # no-op action
            ]
        )
        # Expect exactly: sync no-op, nope typo, safe_a no-op = 3 warnings.
        assert len(warnings) == 3


# ===========================================================================
# Tool.__init__ destructive guard (ValueError)
# ===========================================================================


class TestDestructiveWriteGuard:
    """Constructing @Tool(destructive=True) without write=True must fail."""

    def test_destructive_without_write_raises(self):
        with pytest.raises(ValueError, match="destructive=True requires write=True"):
            Tool("bad_tool", "desc", destructive=True)

    def test_destructive_with_write_ok(self, patch_registry):
        # patch_registry gives us a clean registry so registration succeeds.
        patch_registry({})

        @Tool("ok_destructive", "desc", write=True, destructive=True)
        def ok_destructive() -> dict:
            return {}

        assert _registry["ok_destructive"]["destructive"] is True
        assert _registry["ok_destructive"]["write"] is True

    def test_default_non_destructive_ok(self, patch_registry):
        patch_registry({})

        @Tool("plain", "desc")
        def plain() -> dict:
            return {}

        assert _registry["plain"]["destructive"] is False


# ===========================================================================
# Regression: disabled_tools behavior unchanged by the config_key refactor
# ===========================================================================


class TestDisabledToolsRegression:
    """The config_key parameter must not alter default disabled_tools output.

    _validate_disabled_entries gained `config_key: str = "disabled_tools"`.
    These tests pin that the default-path messages are byte-for-byte what the
    disabled_tools suite expects, and that validate_disabled_tools (which does
    not pass config_key) still emits the "disabled_tools" prefix.
    """

    @pytest.fixture
    def reg(self, patch_registry):
        patch_registry(
            {
                "sync": _single_meta("sync", destructive=False),
                "multi_tool": _multi_meta("multi_tool", AllSafeUnion),
            }
        )

    def test_default_config_key_is_disabled_tools(self, reg):
        warnings = _validate_disabled_entries({"nonexistent"}, {})
        assert len(warnings) == 1
        assert warnings[0].startswith("disabled_tools: 'nonexistent'")
        assert "typo" in warnings[0]

    def test_explicit_config_key_changes_prefix_only(self, reg):
        default = _validate_disabled_entries({"nonexistent"}, {})
        custom = _validate_disabled_entries(
            {"nonexistent"}, {}, config_key="enabled_destructive_tools"
        )
        # Same shape, only the prefix differs.
        assert default[0].replace("disabled_tools", "enabled_destructive_tools") == custom[0]

    def test_validate_disabled_tools_unknown_action_message(self, reg):
        warnings = validate_disabled_tools(["multi_tool:ghost"])
        assert len(warnings) == 1
        assert warnings[0].startswith("disabled_tools:")
        assert "not found" in warnings[0]
        assert "safe_a" in warnings[0]
        assert "safe_b" in warnings[0]

    def test_validate_disabled_tools_action_on_simple_tool(self, reg):
        warnings = validate_disabled_tools(["sync:whatever"])
        assert len(warnings) == 1
        assert warnings[0].startswith("disabled_tools:")
        assert "not a multi-action tool" in warnings[0]

    def test_validate_disabled_tools_valid_entries_no_warnings(self, reg):
        assert validate_disabled_tools(["sync", "multi_tool:safe_a"]) == []
