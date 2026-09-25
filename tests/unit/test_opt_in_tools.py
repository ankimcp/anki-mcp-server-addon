"""Unit tests for the opt-in-tools mechanism in tool_decorator.py (issue #74).

Mirrors ``test_destructive_tools.py`` -- opt_in=True / ``_opt_in: ClassVar[bool]
= True`` gates a tool/action behind the ``enabled_opt_in_tools`` config
allow-list the same way destructive=True gates behind
``enabled_destructive_tools``. The two mechanisms share their registration and
validation machinery (``_get_marked_actions``, ``_validate_enabled_gate_tools``
in tool_decorator.py), so this file exercises the same shapes through the
opt_in-specific entry points (``_get_opt_in_actions``,
``validate_enabled_opt_in_tools``, and ``register_tools``'s
``enabled_opt_in_tools`` parameter).

Key difference from destructive: opt_in=True does NOT require write=True (no
Tool.__init__ guard), since opt-in tools aren't necessarily dangerous -- see
TestOptInWriteIndependence below.

IMPORTANT fixture note: same as test_destructive_tools.py -- fake meta dicts
that flow through register_tools or validate_enabled_opt_in_tools MUST include
both "destructive" and "opt_in" keys.
"""
from __future__ import annotations

from typing import Annotated, ClassVar, Literal, Union

import pytest
from pydantic import BaseModel, Field

from anki_mcp_server.tool_decorator import (
    Tool,
    _get_opt_in_actions,
    _registry,
    register_tools,
    validate_enabled_opt_in_tools,
)


# ---------------------------------------------------------------------------
# Minimal test models (mirror test_destructive_tools.py's pattern)
# ---------------------------------------------------------------------------


class SafeAParams(BaseModel):
    _tool_description: ClassVar[str] = "safe_a: A safe action."
    action: Literal["safe_a"]
    value: int = 0


class SafeBParams(BaseModel):
    _tool_description: ClassVar[str] = "safe_b: Another safe action."
    action: Literal["safe_b"]
    name: str = ""


class OptInParams(BaseModel):
    _tool_description: ClassVar[str] = "preview: Experimental action."
    _opt_in: ClassVar[bool] = True
    action: Literal["preview"]


class OptInTwoParams(BaseModel):
    _tool_description: ClassVar[str] = "beta: Another experimental action."
    _opt_in: ClassVar[bool] = True
    action: Literal["beta"]


MixedUnion = Annotated[
    Union[SafeAParams, SafeBParams, OptInParams],
    Field(discriminator="action"),
]

AllSafeUnion = Annotated[
    Union[SafeAParams, SafeBParams],
    Field(discriminator="action"),
]

TwoOptInUnion = Annotated[
    Union[SafeAParams, OptInParams, OptInTwoParams],
    Field(discriminator="action"),
]


# ---------------------------------------------------------------------------
# Test doubles (same shape as test_destructive_tools.py's _MockMCP)
# ---------------------------------------------------------------------------


class _MockMCP:
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
    from typing import get_args

    ann = entry["annotations"].get("params")
    assert ann is not None, "expected a 'params' annotation on the wrapper"
    union_args = get_args(ann)
    inner = union_args[0]
    members = get_args(inner)
    if not members:
        members = (inner,)
    actions: set[str] = set()
    for m in members:
        field = m.model_fields.get("action")
        if field and hasattr(field.annotation, "__args__"):
            actions.add(field.annotation.__args__[0])
    return actions


def _set_union_annotation(func, union):
    func.__annotations__ = {"params": union}
    return func


@pytest.fixture
def patch_registry(monkeypatch):
    saved = dict(_registry)
    _registry.clear()

    def _install(entries: dict[str, dict]) -> None:
        _registry.clear()
        _registry.update(entries)

    yield _install

    _registry.clear()
    _registry.update(saved)


def _single_meta(name: str, *, opt_in: bool) -> dict:
    def handler() -> dict:
        return {}

    return {
        "name": name,
        "description": f"{name} description",
        "original": handler,
        "write": True,
        "destructive": False,
        "opt_in": opt_in,
    }


def _multi_meta(name: str, union) -> dict:
    def handler(params):  # type: ignore[no-untyped-def]
        return {}

    _set_union_annotation(handler, union)
    return {
        "name": name,
        "description": f"{name} description",
        "original": handler,
        "write": True,
        "destructive": False,
        "opt_in": False,  # whole-tool flag; per-action gating is via _opt_in
    }


_BASE_DESCRIPTION = "Manage test things"


# ===========================================================================
# _get_opt_in_actions
# ===========================================================================


class TestGetOptInActions:
    def test_single_action_tool_returns_empty(self):
        def handler() -> dict:
            return {}

        assert _get_opt_in_actions(handler) == set()

    def test_multi_action_with_opt_in_returns_those_literals(self):
        def handler(params):  # type: ignore[no-untyped-def]
            return {}

        _set_union_annotation(handler, MixedUnion)
        assert _get_opt_in_actions(handler) == {"preview"}

    def test_multi_action_with_two_opt_in(self):
        def handler(params):  # type: ignore[no-untyped-def]
            return {}

        _set_union_annotation(handler, TwoOptInUnion)
        assert _get_opt_in_actions(handler) == {"preview", "beta"}

    def test_multi_action_all_safe_returns_empty(self):
        def handler(params):  # type: ignore[no-untyped-def]
            return {}

        _set_union_annotation(handler, AllSafeUnion)
        assert _get_opt_in_actions(handler) == set()


# ===========================================================================
# register_tools -- whole-tool opt-in gating
# ===========================================================================


class TestRegisterToolsWholeTool:
    def test_opt_in_tool_hidden_when_not_opted_in(self, patch_registry):
        patch_registry({"gui_answer_card": _single_meta("gui_answer_card", opt_in=True)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, enabled_opt_in_tools=[])
        assert "gui_answer_card" not in mcp.names()

    def test_opt_in_tool_hidden_with_none_opt_in(self, patch_registry):
        patch_registry({"gui_answer_card": _single_meta("gui_answer_card", opt_in=True)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread)  # no allow-list passed
        assert "gui_answer_card" not in mcp.names()

    def test_opt_in_tool_revealed_when_opted_in(self, patch_registry):
        patch_registry({"gui_answer_card": _single_meta("gui_answer_card", opt_in=True)})
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread, enabled_opt_in_tools=["gui_answer_card"]
        )
        assert "gui_answer_card" in mcp.names()

    def test_non_opt_in_tool_always_present(self, patch_registry):
        patch_registry({"sync": _single_meta("sync", opt_in=False)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, enabled_opt_in_tools=[])
        assert "sync" in mcp.names()

    def test_opted_in_but_also_disabled_is_hidden(self, patch_registry):
        patch_registry({"gui_answer_card": _single_meta("gui_answer_card", opt_in=True)})
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            disabled_tools=["gui_answer_card"],
            enabled_opt_in_tools=["gui_answer_card"],
        )
        assert "gui_answer_card" not in mcp.names()

    def test_whole_tool_opt_in_is_exact_match_only(self, patch_registry):
        patch_registry({"gui_answer_card": _single_meta("gui_answer_card", opt_in=True)})
        mcp = _MockMCP()
        register_tools(
            mcp, _call_main_thread, enabled_opt_in_tools=["other_tool"]
        )
        assert "gui_answer_card" not in mcp.names()

    def test_opt_in_and_destructive_gates_are_independent(self, patch_registry):
        """A tool marked both destructive and opt_in needs BOTH opt-ins."""

        def handler() -> dict:
            return {}

        patch_registry(
            {
                "danger": {
                    "name": "danger",
                    "description": "danger description",
                    "original": handler,
                    "write": True,
                    "destructive": True,
                    "opt_in": True,
                }
            }
        )
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            enabled_destructive_tools=["danger"],
            enabled_opt_in_tools=[],
        )
        assert "danger" not in mcp.names(), "opt_in not satisfied -- still hidden"

        mcp2 = _MockMCP()
        register_tools(
            mcp2,
            _call_main_thread,
            enabled_destructive_tools=[],
            enabled_opt_in_tools=["danger"],
        )
        assert "danger" not in mcp2.names(), "destructive not satisfied -- still hidden"

        mcp3 = _MockMCP()
        register_tools(
            mcp3,
            _call_main_thread,
            enabled_destructive_tools=["danger"],
            enabled_opt_in_tools=["danger"],
        )
        assert "danger" in mcp3.names(), "both satisfied -- revealed"


# ===========================================================================
# register_tools -- per-action opt-in gating
# ===========================================================================


class TestRegisterToolsPerAction:
    def test_opt_in_action_removed_when_not_opted_in(self, patch_registry):
        patch_registry({"deck_management": _multi_meta("deck_management", MixedUnion)})
        mcp = _MockMCP()
        register_tools(mcp, _call_main_thread, enabled_opt_in_tools=[])

        entry = mcp.by_name("deck_management")
        assert entry is not None, "tool should still register (safe actions remain)"
        actions = _schema_actions(entry)
        assert "preview" not in actions
        assert {"safe_a", "safe_b"} <= actions

    def test_opt_in_action_present_when_action_opted_in(self, patch_registry):
        patch_registry({"deck_management": _multi_meta("deck_management", MixedUnion)})
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            enabled_opt_in_tools=["deck_management:preview"],
        )
        entry = mcp.by_name("deck_management")
        assert entry is not None
        actions = _schema_actions(entry)
        assert "preview" in actions
        assert {"safe_a", "safe_b"} <= actions

    def test_whole_tool_opt_in_does_not_reveal_opt_in_action(self, patch_registry):
        patch_registry({"deck_management": _multi_meta("deck_management", MixedUnion)})
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            enabled_opt_in_tools=["deck_management"],
        )
        entry = mcp.by_name("deck_management")
        assert entry is not None
        actions = _schema_actions(entry)
        assert "preview" not in actions

    def test_opting_in_one_of_two_opt_in_actions(self, patch_registry):
        patch_registry(
            {"deck_management": _multi_meta("deck_management", TwoOptInUnion)}
        )
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            enabled_opt_in_tools=["deck_management:preview"],
        )
        entry = mcp.by_name("deck_management")
        actions = _schema_actions(entry)
        assert "preview" in actions
        assert "beta" not in actions
        assert "safe_a" in actions


# ===========================================================================
# register_tools -- precedence / composition with disabled_tools
# ===========================================================================


class TestRegisterToolsPrecedence:
    def test_opted_in_action_also_disabled_is_hidden(self, patch_registry):
        patch_registry({"deck_management": _multi_meta("deck_management", MixedUnion)})
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            disabled_tools=["deck_management:preview"],
            enabled_opt_in_tools=["deck_management:preview"],
        )
        entry = mcp.by_name("deck_management")
        assert entry is not None
        actions = _schema_actions(entry)
        assert "preview" not in actions

    def test_all_actions_hidden_skips_whole_tool(self, patch_registry):
        patch_registry({"deck_management": _multi_meta("deck_management", MixedUnion)})
        mcp = _MockMCP()
        register_tools(
            mcp,
            _call_main_thread,
            disabled_tools=["deck_management:safe_a", "deck_management:safe_b"],
            enabled_opt_in_tools=[],  # preview stays hidden
        )
        assert "deck_management" not in mcp.names()


# ===========================================================================
# validate_enabled_opt_in_tools
# ===========================================================================


class TestValidateEnabledOptInTools:
    @pytest.fixture
    def opt_in_registry(self, patch_registry):
        patch_registry(
            {
                "gui_answer_card": _single_meta("gui_answer_card", opt_in=True),
                "sync": _single_meta("sync", opt_in=False),
                "deck_management": _multi_meta("deck_management", MixedUnion),
            }
        )

    def test_empty_list_no_warnings(self, opt_in_registry):
        assert validate_enabled_opt_in_tools([]) == []

    def test_nonexistent_tool_warns(self, opt_in_registry):
        warnings = validate_enabled_opt_in_tools(["nope"])
        assert len(warnings) == 1
        assert "'nope'" in warnings[0]
        assert "enabled_opt_in_tools" in warnings[0]
        assert "typo" in warnings[0]

    def test_nonexistent_action_warns(self, opt_in_registry):
        warnings = validate_enabled_opt_in_tools(["deck_management:ghost"])
        assert len(warnings) == 1
        assert "not found" in warnings[0]

    def test_real_but_not_opt_in_whole_tool_is_noop_warning(self, opt_in_registry):
        warnings = validate_enabled_opt_in_tools(["sync"])
        assert len(warnings) == 1
        assert "no-op" in warnings[0]
        assert "'sync'" in warnings[0]

    def test_real_but_not_opt_in_action_is_noop_warning(self, opt_in_registry):
        warnings = validate_enabled_opt_in_tools(["deck_management:safe_a"])
        assert len(warnings) == 1
        assert "no-op" in warnings[0]
        assert "deck_management:safe_a" in warnings[0]

    def test_correct_opt_in_whole_tool_no_warning(self, opt_in_registry):
        assert validate_enabled_opt_in_tools(["gui_answer_card"]) == []

    def test_correct_opt_in_action_no_warning(self, opt_in_registry):
        assert validate_enabled_opt_in_tools(["deck_management:preview"]) == []

    def test_hidden_by_default_opt_in_action_not_flagged_nonexistent(
        self, opt_in_registry
    ):
        warnings = validate_enabled_opt_in_tools(["deck_management:preview"])
        assert not any("not found" in w for w in warnings)


# ===========================================================================
# opt_in does NOT require write=True (unlike destructive)
# ===========================================================================


class TestOptInWriteIndependence:
    def test_opt_in_without_write_is_allowed(self, patch_registry):
        patch_registry({})

        @Tool("read_only_opt_in", "desc", write=False, opt_in=True)
        def read_only_opt_in() -> dict:
            return {}

        assert _registry["read_only_opt_in"]["opt_in"] is True
        assert _registry["read_only_opt_in"]["write"] is False

    def test_default_non_opt_in_ok(self, patch_registry):
        patch_registry({})

        @Tool("plain_opt_in_check", "desc")
        def plain_opt_in_check() -> dict:
            return {}

        assert _registry["plain_opt_in_check"]["opt_in"] is False
