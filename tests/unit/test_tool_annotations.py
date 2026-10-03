"""Unit tests for MCP tool annotations (title, readOnlyHint, destructiveHint,
openWorldHint) declared through the @Tool decorator.

Covers the import-time definition guards in ``Tool.__init__``, the resolved
values stored in ``_registry``, and the wire-level ``tools/list`` shape a real
``FastMCP`` produces from them -- both for synthetic tools and for every real
tool in the addon (via conftest.py's ``tools_list`` fixture, which registers
the full auto-discovered primitive set).
"""
from __future__ import annotations

import asyncio

import pytest

import anki_mcp_server  # noqa: F401 -- triggers vendor path setup (see conftest.py)

from mcp.server.fastmcp import FastMCP  # noqa: E402 -- after vendor path setup
from anki_mcp_server.handler_registry import _handlers  # noqa: E402
from anki_mcp_server.tool_decorator import (  # noqa: E402
    Tool,
    _registry,
    register_tools,
)


@pytest.fixture
def clean_registry():
    """Isolate both the tool registry and the handler registry, since
    registering a @Tool writes to both and each rejects duplicate names."""
    saved_tools = dict(_registry)
    saved_handlers = dict(_handlers)
    _registry.clear()
    _handlers.clear()
    yield
    _registry.clear()
    _registry.update(saved_tools)
    _handlers.clear()
    _handlers.update(saved_handlers)


async def _call_main_thread(*args, **kwargs) -> dict:
    return {}


def _wire(tool) -> dict:
    """Serialize a listed Tool the way it goes over the wire."""
    return tool.model_dump(by_alias=True, mode="json", exclude_none=True)


# ===========================================================================
# Definition guards (ValueError at import time)
# ===========================================================================


class TestDefinitionGuards:
    def test_missing_title_raises(self):
        with pytest.raises(TypeError, match="title"):
            Tool("no_title", "desc")  # type: ignore[call-arg]

    @pytest.mark.parametrize("title", ["", "   ", "\t\n"])
    def test_empty_title_raises(self, title):
        with pytest.raises(ValueError, match="title must be a non-empty string"):
            Tool("empty_title", "desc", title=title)

    @pytest.mark.parametrize("hint", [True, False])
    def test_destructive_hint_on_read_only_tool_raises(self, hint):
        with pytest.raises(ValueError, match="destructive_hint requires write=True"):
            Tool("ro", "desc", title="RO", destructive_hint=hint)

    def test_destructive_gate_with_destructive_hint_false_raises(self):
        with pytest.raises(ValueError, match="contradicts destructive_hint=False"):
            Tool(
                "contradiction",
                "desc",
                title="Contradiction",
                write=True,
                destructive=True,
                destructive_hint=False,
            )

    def test_destructive_gate_with_destructive_hint_true_ok(self, clean_registry):
        @Tool("gated", "desc", title="Gated", write=True, destructive=True, destructive_hint=True)
        def gated() -> dict:
            return {}

        assert _registry["gated"]["destructive_hint"] is True


# ===========================================================================
# Resolved values stored in _registry
# ===========================================================================


class TestRegistryResolution:
    def test_write_tool_default_destructive_hint_is_true(self, clean_registry):
        @Tool("w", "desc", title="Write Tool", write=True)
        def w() -> dict:
            return {}

        meta = _registry["w"]
        assert meta["title"] == "Write Tool"
        assert meta["destructive_hint"] is True
        assert meta["open_world_hint"] is False

    def test_write_tool_explicit_additive(self, clean_registry):
        @Tool("add", "desc", title="Add", write=True, destructive_hint=False)
        def add() -> dict:
            return {}

        assert _registry["add"]["destructive_hint"] is False

    def test_read_tool_destructive_hint_is_none(self, clean_registry):
        @Tool("r", "desc", title="Read Tool")
        def r() -> dict:
            return {}

        meta = _registry["r"]
        assert meta["write"] is False
        assert meta["destructive_hint"] is None
        assert meta["open_world_hint"] is False

    def test_open_world_hint_opt_in(self, clean_registry):
        @Tool("ow", "desc", title="Open World", open_world_hint=True)
        def ow() -> dict:
            return {}

        assert _registry["ow"]["open_world_hint"] is True


# ===========================================================================
# Wire-level tools/list shape via a real FastMCP
# ===========================================================================


class TestRegisteredAnnotations:
    @pytest.fixture
    def listed(self, clean_registry) -> dict[str, dict]:
        @Tool("read_tool", "desc", title="Read Tool")
        def read_tool() -> dict:
            return {}

        @Tool("write_tool", "desc", title="Write Tool", write=True)
        def write_tool() -> dict:
            return {}

        @Tool(
            "additive_open_tool",
            "desc",
            title="Additive Open Tool",
            write=True,
            destructive_hint=False,
            open_world_hint=True,
        )
        def additive_open_tool() -> dict:
            return {}

        mcp = FastMCP("annotations-test")
        register_tools(mcp, _call_main_thread)
        return {t.name: _wire(t) for t in asyncio.run(mcp.list_tools())}

    def test_read_tool(self, listed):
        tool = listed["read_tool"]
        assert tool["title"] == "Read Tool"
        assert tool["annotations"] == {
            "title": "Read Tool",
            "readOnlyHint": True,
            "openWorldHint": False,
        }

    def test_write_tool(self, listed):
        assert listed["write_tool"]["annotations"] == {
            "title": "Write Tool",
            "readOnlyHint": False,
            "destructiveHint": True,
            "openWorldHint": False,
        }

    def test_additive_open_world_tool(self, listed):
        assert listed["additive_open_tool"]["annotations"] == {
            "title": "Additive Open Tool",
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": True,
        }


# ===========================================================================
# Every real tool carries a complete annotation set
# ===========================================================================


def test_every_real_tool_has_complete_annotations(tools_list):
    problems: list[str] = []
    for tool in tools_list:
        wire = _wire(tool)
        title = wire.get("title")
        ann = wire.get("annotations") or {}
        if not isinstance(title, str) or not title.strip():
            problems.append(f"{tool.name}: missing top-level title")
        if ann.get("title") != title:
            problems.append(f"{tool.name}: annotations.title != title")
        if not isinstance(ann.get("readOnlyHint"), bool):
            problems.append(f"{tool.name}: readOnlyHint not a bool")
        if not isinstance(ann.get("openWorldHint"), bool):
            problems.append(f"{tool.name}: openWorldHint not a bool")
        if ann.get("readOnlyHint") is False:
            if not isinstance(ann.get("destructiveHint"), bool):
                problems.append(f"{tool.name}: write tool without destructiveHint")
        elif "destructiveHint" in ann:
            problems.append(f"{tool.name}: read-only tool carries destructiveHint")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    ("name", "read_only", "destructive", "open_world"),
    [
        ("find_notes", True, None, False),
        ("list_decks", True, None, False),
        ("add_note", False, False, False),
        ("delete_notes", False, True, False),
        ("sync", False, True, True),
        ("store_media_file", False, False, True),
        ("gui_undo", False, True, False),
    ],
)
def test_real_tool_spot_checks(tools_list, name, read_only, destructive, open_world):
    tool = next(t for t in tools_list if t.name == name)
    ann = _wire(tool)["annotations"]
    assert ann["readOnlyHint"] is read_only
    assert ann.get("destructiveHint") is destructive
    assert ann["openWorldHint"] is open_world
