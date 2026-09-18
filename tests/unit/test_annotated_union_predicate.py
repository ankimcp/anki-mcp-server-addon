"""Unit tests for tool_decorator._is_annotated_union().

The predicate distinguishes a genuine multi-action discriminated union
(Annotated[Union[...], Field(discriminator="action")]) from an ordinary
optional parameter whose annotation happens to also be
Annotated[<Union>, Field(...)] -- e.g. Annotated[str | None,
Field(description="...")], which the upcoming parameter-description work
adds across tool signatures. Only the former should trigger the
multi-action registration path (dynamic description rebuild,
_BASE_DESCRIPTION lookup, per-action filtering).
"""

import asyncio
from typing import Annotated, Literal, Optional, Union

import pytest
from pydantic import BaseModel, Field

import anki_mcp_server  # noqa: F401 -- triggers vendor path setup (see conftest.py)

from mcp.server.fastmcp import FastMCP  # noqa: E402 -- after vendor path setup

from anki_mcp_server.tool_decorator import (  # noqa: E402
    Tool,
    _is_annotated_union,
    _registry,
    register_tools,
)
from anki_mcp_server.handler_registry import _handlers  # noqa: E402


class _ActionOne(BaseModel):
    action: Literal["one"] = "one"
    value: str = ""


class _ActionTwo(BaseModel):
    action: Literal["two"] = "two"
    value: str = ""


# A real discriminated union, shaped exactly like CardManagementParams /
# TagManagementParams / FilteredDeckParams / ModelFieldsParams.
_DiscriminatedUnion = Annotated[
    Union[_ActionOne, _ActionTwo],
    Field(discriminator="action"),
]


@pytest.mark.parametrize(
    "annotation,expected",
    [
        (Annotated[Optional[str], Field(description="Anki search query")], False),
        (Annotated[Optional[list[str]], Field(description="Tags to apply")], False),
        (Annotated[str | None, Field(description="Anki search query")], False),
        (_DiscriminatedUnion, True),
        (Optional[str], False),
    ],
    ids=[
        "annotated_optional_str_with_description",
        "annotated_optional_list_with_description",
        "annotated_optional_str_pep604_spelling",
        "real_discriminated_union",
        "bare_optional_str",
    ],
)
def test_is_annotated_union(annotation, expected):
    assert _is_annotated_union(annotation) is expected


# ---------------------------------------------------------------------------
# End-to-end: registering a tool with an Annotated[str | None, Field(...)]
# optional parameter must not be mistaken for a multi-action tool -- it must
# register cleanly through the real Tool + register_tools path and produce
# a schema carrying the given description.
# ---------------------------------------------------------------------------


def test_probe_tool_with_optional_annotated_param_registers():
    tool_name = "__test_probe_annotated_optional__"

    # Guard against leaking into other tests / a prior failed run.
    assert tool_name not in _registry

    @Tool(tool_name, "Probe tool for _is_annotated_union regression")
    def probe_tool(
        query: Annotated[Optional[str], Field(description="Anki search query")] = None,
    ) -> dict:
        return {"query": query}

    try:
        mcp = FastMCP("probe-server")

        async def _call_main_thread(name: str, kwargs: dict) -> dict:
            return {}

        register_tools(mcp, _call_main_thread, disabled_tools=None)

        tools_list = asyncio.run(mcp.list_tools())
        probe = next((t for t in tools_list if t.name == tool_name), None)
        assert probe is not None, "probe tool was not registered"

        properties = (probe.inputSchema or {}).get("properties", {})
        assert "query" in properties
        assert properties["query"].get("description") == "Anki search query"
    finally:
        _registry.pop(tool_name, None)
        _handlers.pop(tool_name, None)
