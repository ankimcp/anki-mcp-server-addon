"""Unit tests for the shared ease-name mapping (_ease_names.py).

rate_card_tool.py and gui_answer_card_tool.py each used to hardcode their own
identical {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"} dict. This helper
deduplicates them. It's a plain constant (EASE_NAMES) rather than a
col/card-aware lookup: anki/scheduler/legacy.py's
SchedulerBase.answerButtons() is a hardcoded `return 4`, and the V3 scheduler
that ships it has been mandatory since Anki 25.07, so a 2- or 3-button shape
is unreachable on any supported Anki version -- there is nothing for it to
look up.

These are pure-logic tests: no Docker, no running Anki. Loaded the same way
test_model_helpers.py loads _model_helpers.py -- _ease_names.py lives under
primitives/essential/tools/, whose __init__.py runs pkgutil auto-discovery,
and conftest.py stubs anki_mcp_server.primitives to a non-package to suppress
that, so the normal dotted import path is unavailable. _ease_names.py itself
has no addon imports, so loading the single file directly needs no further
stubbing.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HELPER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "anki_mcp_server" / "primitives" / "essential" / "tools" / "_ease_names.py"
)
_spec = importlib.util.spec_from_file_location(
    "anki_mcp_server.primitives.essential.tools._ease_names", _HELPER_PATH
)
_ease_names_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ease_names_module)
ease_name = _ease_names_module.ease_name
EASE_NAMES = _ease_names_module.EASE_NAMES


class TestEaseNames:
    def test_ease_names_mapping(self):
        assert EASE_NAMES == {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}

    @pytest.mark.parametrize(
        "ease, expected",
        [(1, "Again"), (2, "Hard"), (3, "Good"), (4, "Easy")],
    )
    def test_ease_name(self, ease, expected):
        assert ease_name(ease) == expected

    def test_ease_name_out_of_range_raises(self):
        with pytest.raises(ValueError, match="ease 5"):
            ease_name(5)

    def test_ease_name_zero_raises(self):
        with pytest.raises(ValueError, match="ease 0"):
            ease_name(0)
