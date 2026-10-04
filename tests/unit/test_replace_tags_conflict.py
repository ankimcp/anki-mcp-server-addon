"""Unit tests for replace_tags' pre-write conflict check (_replace_conflict.py).

replace_tags adds new_tag then calls ``col.tags.bulk_remove(old_tag)``, whose
rslib TagMatcher matches case-insensitively and also matches ``old_tag::...``
children. A new_tag that is a case variant or a child of old_tag would be
removed again, so the dispatcher rejects those inputs before writing.

Loaded by file path like test_ease_names.py: conftest.py stubs
anki_mcp_server.primitives to suppress auto-discovery, and the helper has no
addon imports.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HELPER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "anki_mcp_server" / "primitives" / "essential" / "tools"
    / "tag_management" / "actions" / "_replace_conflict.py"
)
_spec = importlib.util.spec_from_file_location(
    "anki_mcp_server.primitives.essential.tools.tag_management.actions._replace_conflict",
    _HELPER_PATH,
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
find_replace_conflict = _module.find_replace_conflict


@pytest.mark.parametrize(
    "old_tag, new_tag",
    [("vocab", "vocab"), ("Vocab", "vocab"), ("VOCAB", "Vocab"), (" vocab ", "vocab")],
)
def test_same_tag_ignoring_case(old_tag, new_tag):
    assert find_replace_conflict(old_tag, new_tag) == "same_tag"


@pytest.mark.parametrize(
    "old_tag, new_tag",
    [("vocab", "vocab::verbs"), ("Vocab", "vocab::verbs::irregular"), ("a::b", "A::B::c")],
)
def test_child_of_old_tag(old_tag, new_tag):
    assert find_replace_conflict(old_tag, new_tag) == "child_tag"


@pytest.mark.parametrize(
    "old_tag, new_tag",
    [
        ("old-tag", "new-tag"),
        ("vocab", "vocabulary"),
        ("vocab", "vocab-verbs"),
        ("vocab::verbs", "vocab"),
        ("vocab::verbs", "grammar::verbs"),
    ],
)
def test_no_conflict(old_tag, new_tag):
    assert find_replace_conflict(old_tag, new_tag) is None


def test_space_separated_tags_checked_individually():
    assert find_replace_conflict("grammar vocab", "vocab::verbs") == "child_tag"
    assert find_replace_conflict("grammar", "other Grammar") == "same_tag"
    assert find_replace_conflict("a b", "b::c") == "child_tag"
    assert find_replace_conflict("x y", "Y") == "same_tag"
