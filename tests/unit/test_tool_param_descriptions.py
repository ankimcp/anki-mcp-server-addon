"""Unit tests asserting every MCP tool's top-level input-schema parameters
carry a non-empty ``description``.

Test 1 asserts that every top-level property in every tool's ``inputSchema``
has a non-empty string ``description`` (supplied via
``Annotated[..., Field(description=...)]`` on the tool signature), and lists
every offending ``tool.param`` on failure. Test 2 pins the exact description
text for a representative subset of tools, so wording changes are deliberate.

The real, fully auto-discovered tool registry and the ``tools/list`` result
a real ``FastMCP`` produces from it come from the session-scoped
``real_mcp``/``tools_list`` fixtures in ``tests/unit/conftest.py`` (see its
section 6).
"""
from __future__ import annotations

import pytest


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
