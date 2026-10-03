"""E2E tests for MCP tool annotations on tools/list.

Every tool must carry a human-readable ``title`` (top level, mirrored in
``annotations.title``) plus ``readOnlyHint`` / ``openWorldHint``, and write
tools must also declare ``destructiveHint``. Declared via ``@Tool(title=...,
write=..., destructive_hint=..., open_world_hint=...)`` in tool_decorator.py.
"""
from __future__ import annotations

import pytest

from .helpers import list_tools


@pytest.fixture(scope="module")
def tools_by_name() -> dict[str, dict]:
    tools = list_tools()
    assert tools, "tools/list returned no tools"
    return {t["name"]: t for t in tools}


class TestAnnotationsOnEveryTool:
    def test_every_tool_has_title(self, tools_by_name):
        missing = [
            name for name, t in tools_by_name.items()
            if not isinstance(t.get("title"), str) or not t["title"].strip()
        ]
        assert not missing, f"tools without a top-level title: {missing}"

    def test_annotations_title_matches_top_level(self, tools_by_name):
        mismatched = [
            name for name, t in tools_by_name.items()
            if (t.get("annotations") or {}).get("title") != t.get("title")
        ]
        assert not mismatched, f"annotations.title != title for: {mismatched}"

    def test_read_only_and_open_world_hints_are_bools(self, tools_by_name):
        bad = [
            name for name, t in tools_by_name.items()
            if not isinstance((t.get("annotations") or {}).get("readOnlyHint"), bool)
            or not isinstance((t.get("annotations") or {}).get("openWorldHint"), bool)
        ]
        assert not bad, f"readOnlyHint/openWorldHint missing or non-bool for: {bad}"

    def test_destructive_hint_only_on_write_tools(self, tools_by_name):
        bad: list[str] = []
        for name, t in tools_by_name.items():
            ann = t.get("annotations") or {}
            if ann.get("readOnlyHint") is False:
                if not isinstance(ann.get("destructiveHint"), bool):
                    bad.append(f"{name}: write tool without bool destructiveHint")
            elif "destructiveHint" in ann:
                bad.append(f"{name}: read-only tool carries destructiveHint")
        assert not bad, "\n".join(bad)


class TestAnnotationSpotChecks:
    @staticmethod
    def _ann(tools_by_name: dict[str, dict], name: str) -> dict:
        assert name in tools_by_name, f"{name} not in tools/list"
        return tools_by_name[name]["annotations"]

    def test_find_notes_is_read_only(self, tools_by_name):
        assert self._ann(tools_by_name, "find_notes")["readOnlyHint"] is True

    def test_add_note_is_additive_write(self, tools_by_name):
        ann = self._ann(tools_by_name, "add_note")
        assert ann["readOnlyHint"] is False
        assert ann["destructiveHint"] is False

    def test_delete_notes_is_destructive_write(self, tools_by_name):
        ann = self._ann(tools_by_name, "delete_notes")
        assert ann["readOnlyHint"] is False
        assert ann["destructiveHint"] is True

    def test_sync_is_open_world(self, tools_by_name):
        assert self._ann(tools_by_name, "sync")["openWorldHint"] is True

    def test_store_media_file_is_open_world_additive(self, tools_by_name):
        ann = self._ann(tools_by_name, "store_media_file")
        assert ann["openWorldHint"] is True
        assert ann["destructiveHint"] is False

    def test_list_decks_is_closed_world(self, tools_by_name):
        assert self._ann(tools_by_name, "list_decks")["openWorldHint"] is False
