"""Unit tests asserting every MCP tool's top-level input-schema parameters
carry a non-empty ``description``.

This is TDD written ahead of the implementation: today no tool signature uses
``Annotated[..., Field(description=...)]`` for its top-level parameters, so
Test 1 below is expected to FAIL (listing every missing ``tool.param``) until
that annotation is added across the tool modules. Test 2 pins down the exact
description text for a representative subset of tools/actions -- the spec the
implementer should copy verbatim.

Bootstrap approach
-------------------
``tests/unit/conftest.py`` stubs ``anki_mcp_server.primitives`` with a no-op
module (see its section 3) so that unrelated unit tests don't pay the cost --
or the risk -- of importing every tool module. This test needs the OPPOSITE:
the real, fully auto-discovered tool registry, because it must inspect the
actual ``tools/list`` schema a real MCP client would see.

So this module discards that stub before importing the real
``anki_mcp_server.primitives`` package tree, which triggers
``pkgutil.walk_packages``-based auto-discovery of every ``@Tool``-decorated
function (mirrors what ``mcp_server.py`` does at server startup, minus
uvicorn/Qt). It then builds a real ``FastMCP`` instance, calls
``tool_decorator.register_tools()`` against it (the same function
``primitives/tools.py:register_all_tools`` delegates to), and inspects the
schema returned by ``await mcp.list_tools()``. Once the real import is done,
the original no-op stub entries are put back into ``sys.modules`` so later
test modules (which may run after this one, depending on collection order)
still see the lightweight boundary conftest.py installed.

``aqt``/``anki`` are not imported at module scope by any tool file (verified
by grep), so this works against the ``aqt`` stub conftest.py already installs
-- no real Anki needed. ``pydantic_core`` and ``rpds`` are bootstrapped by
conftest.py the same way every other unit test gets them (cached under
``anki_mcp_server/_cache/`` after the first run).
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types

import pytest

import anki_mcp_server  # noqa: F401 -- triggers vendor path setup (see conftest.py)

from mcp.server.fastmcp import FastMCP  # noqa: E402 -- after vendor path setup
from anki_mcp_server.tool_decorator import _registry  # noqa: E402


# ---------------------------------------------------------------------------
# Bootstrap: swap the no-op primitives stub for the real package tree, then
# populate a real FastMCP with every real tool (destructive tools opted in
# via enabled_destructive_tools so their schemas are covered too).
#
# The stub entries removed here are restored (as the same no-op stub
# conftest.py installed) once the real import + registration below is done,
# so later test modules still see the lightweight boundary conftest.py
# intended -- this module's real import is a one-time detour, not a
# permanent swap of what the rest of the suite sees.
# ---------------------------------------------------------------------------

_STUBBED_MODULES: dict[str, types.ModuleType] = {
    name: sys.modules[name]
    for name in list(sys.modules)
    if name == "anki_mcp_server.primitives" or name.startswith("anki_mcp_server.primitives.")
}
for _name in _STUBBED_MODULES:
    del sys.modules[_name]

_primitives_tools = importlib.import_module("anki_mcp_server.primitives.tools")

from anki_mcp_server.tool_decorator import register_tools  # noqa: E402

for _name, _module in _STUBBED_MODULES.items():
    sys.modules[_name] = _module


async def _call_main_thread(*args, **kwargs) -> dict:
    """Tools are never invoked in this test -- only registered for listing."""
    return {}


def _all_whole_tool_gated(meta_key: str) -> list[str]:
    """Every registered tool whose WHOLE-TOOL gate flag (``meta_key``, one of
    ``"destructive"``/``"opt_in"``) is set -- derived from ``_registry`` so
    this test can't silently stop covering a tool's schema just because a new
    gated primitive was added and nobody remembered to hand-list it here.

    Per-action gated entries (e.g. model_fields:remove) are deliberately NOT
    included: opting in a single action of a multi-action tool wouldn't
    change anything this test observes, since the tool still exposes one
    top-level "params" property regardless of which actions are enabled.
    """
    return [name for name, meta in _registry.items() if meta[meta_key]]


def _build_real_mcp() -> FastMCP:
    mcp = FastMCP("test-server")
    _primitives_tools.register_all_tools(
        mcp,
        _call_main_thread,
        disabled_tools=None,
        # Opt in every whole-tool-destructive / whole-tool-opt-in primitive
        # (see CLAUDE.md "Tool Filtering") so their schemas are covered by
        # this test too -- see _all_whole_tool_gated's docstring for why
        # per-action entries are excluded.
        enabled_destructive_tools=_all_whole_tool_gated("destructive"),
        enabled_opt_in_tools=_all_whole_tool_gated("opt_in"),
    )
    return mcp


@pytest.fixture(scope="session")
def real_mcp() -> FastMCP:
    return _build_real_mcp()


@pytest.fixture(scope="session")
def tools_list(real_mcp):
    result = asyncio.run(real_mcp.list_tools())
    # Compare against the decorator's own registry so this test can't pass
    # vacuously (e.g. if auto-discovery silently registered zero tools) and
    # doesn't need a hand-maintained floor that drifts as tools are added.
    # The one whole-tool-destructive primitive (change_note_type) is opted in
    # via enabled_destructive_tools above, so every registered tool should be
    # listed -- counts should be equal, not just >=.
    assert len(result) == len(_registry), (
        f"Expected {len(_registry)} registered tools (from _registry), got "
        f"{len(result)} -- auto-discovery or destructive opt-in may be broken"
    )
    return result


# ---------------------------------------------------------------------------
# Test 1: every top-level input-schema property on every tool must carry a
# non-empty string "description".
# ---------------------------------------------------------------------------


def test_every_tool_param_has_description(tools_list):
    missing: list[str] = []

    for tool in tools_list:
        properties = (tool.inputSchema or {}).get("properties", {})
        for param_name, param_schema in properties.items():
            description = param_schema.get("description")
            if not isinstance(description, str) or not description.strip():
                missing.append(f"{tool.name}.{param_name}")

    assert not missing, (
        f"{len(missing)} tool parameter(s) missing a non-empty top-level "
        f"description:\n" + "\n".join(sorted(missing))
    )


# ---------------------------------------------------------------------------
# Test 2: exact description strings for a representative subset of tools.
#
# This table is the spec: short, one clause, no "Anki 101" (the caller
# already knows what a deck/note/tag is). A shape hint is included only where
# the shape genuinely isn't obvious from the name.
# ---------------------------------------------------------------------------

EXPECTED_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "add_notes": {
        "deck_name": "Deck to add all notes to",
        "model_name": "Note type shared by all notes in this batch",
        "notes": 'Notes to add; each {"fields": {...}, "tags": [...]}',
        "tags": "Tags for every note (JSON array)",
        "allow_duplicate": "Skip the duplicate check against the sort field",
    },
    "add_note": {
        "deck_name": "Deck to add the note to",
        "model_name": "Note type to use",
        "fields": 'Field values by name, e.g. {"Front": "question", "Back": "answer"}',
        "tags": "Tags to apply to the note (JSON array)",
        "allow_duplicate": "Skip the duplicate check against the sort field",
    },
    "notes_info": {
        "notes": "Note IDs to fetch",
        "include_fields": "Return only these fields (takes priority over exclude_fields)",
        "exclude_fields": "Omit these fields from the response",
        "excerpt_chars": "Truncate each field value to this many characters",
    },
    "find_notes": {
        "query": "Anki search query, e.g. \"deck:Spanish\" or \"is:due\"",
        "limit": "Maximum note IDs to return (max 500)",
        "offset": "Results to skip, for pagination",
        "include_first_field": "Also return each note's excerpted first field",
    },
    "create_deck": {
        "deck_name": 'Deck name, optionally "Parent::Child" (max 2 levels)',
    },
    "tag_management": {
        "params": "The tag operation to perform and its arguments",
    },
    "card_management": {
        "params": "The card operation to perform and its arguments",
    },
}


def _schema_for(tools_list, tool_name: str):
    for tool in tools_list:
        if tool.name == tool_name:
            return tool.inputSchema
    pytest.fail(f"Tool '{tool_name}' not found in tools/list result")


PARAM_CASES = [
    (tool_name, param_name, expected)
    for tool_name, params in EXPECTED_DESCRIPTIONS.items()
    for param_name, expected in params.items()
]


@pytest.mark.parametrize("tool_name,param_name,expected", PARAM_CASES)
def test_exact_param_description(tools_list, tool_name, param_name, expected):
    schema = _schema_for(tools_list, tool_name)
    properties = (schema or {}).get("properties", {})
    assert param_name in properties, (
        f"{tool_name} has no top-level parameter '{param_name}' "
        f"(available: {sorted(properties)})"
    )
    actual = properties[param_name].get("description")
    assert actual == expected, (
        f"{tool_name}.{param_name} description mismatch:\n"
        f"  expected: {expected!r}\n"
        f"  actual:   {actual!r}"
    )
