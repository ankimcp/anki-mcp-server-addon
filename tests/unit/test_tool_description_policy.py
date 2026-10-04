"""Regression guard: tool descriptions state facts, not orders to the model.

The Anthropic Software Directory Policy requires tool descriptions to say what
a tool does and when it applies, and to match its actual behaviour. Cross-tool
workflow guidance lives in the server instructions (``SERVER_INSTRUCTIONS`` in
mcp_server.py) instead. This is a small literal blocklist of the imperative
markers that used to appear in our descriptions, not a linter.
"""
from __future__ import annotations

from typing import Any, Iterator

import pytest

_FORBIDDEN_MARKERS = (
    "IMPORTANT:",
    "DO NOT",
    "Do NOT",
    "Remember to",
    "Wait for user",
    "Only use",
    "never use",
    "Never submit",
    "never call",
    "Confirm with the user",
    "WORKFLOW:",
    "FIRST",
)


def _schema_descriptions(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        desc = node.get("description")
        if isinstance(desc, str):
            yield desc
        for value in node.values():
            yield from _schema_descriptions(value)
    elif isinstance(node, list):
        for value in node:
            yield from _schema_descriptions(value)


# A prompt is a user-invoked session template, so its step list keeps its
# "WORKFLOW:" heading (tests/e2e/test_review_session_prompt.py splits on it);
# every other marker applies to prompt text too.
_PROMPT_MARKERS = (
    *(m for m in _FORBIDDEN_MARKERS if m != "WORKFLOW:"),
    "CRITICAL:",
    "IMPORTANT",
    "Never ",
    "Always ",
    "First, sync",
)


@pytest.mark.parametrize("review_style", ["interactive", "quick", "voice", "gui"])
def test_no_imperative_markers_in_review_session_prompt(real_mcp, review_style):
    # real_mcp imports the real primitives package, which registers the prompts.
    from anki_mcp_server.prompt_decorator import _registry as prompt_registry

    text = prompt_registry["review_session"]["func"](review_style=review_style)
    found = [marker for marker in _PROMPT_MARKERS if marker in text]
    assert not found, f"review_style={review_style!r} contains {found}"


@pytest.mark.parametrize("marker", _FORBIDDEN_MARKERS)
def test_no_imperative_markers_in_tool_descriptions(tools_list, marker):
    offenders = []
    for tool in tools_list:
        texts = [tool.description or "", *_schema_descriptions(tool.inputSchema)]
        if any(marker in text for text in texts):
            offenders.append(tool.name)
    assert not offenders, f"{marker!r} found in: {sorted(offenders)}"
