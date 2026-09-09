"""Unit tests for the shared ease-name mapping (_ease_names.py).

rate_card_tool.py and gui_answer_card_tool.py each used to hardcode their own
identical {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"} dict. This helper
deduplicates them and makes the mapping button-count-aware, matching Anki's
own aqt/reviewer.py Reviewer._answerButtonList() (2/3/4-button shapes) even
though the current V3 scheduler's answerButtons() always returns 4 in
practice (anki/scheduler/legacy.py SchedulerBase.answerButtons() is a
hardcoded `return 4`) -- see the comment in _ease_names.py for the evidence.

These are pure-logic tests: no Docker, no running Anki. Loaded the same way
test_model_helpers.py loads _model_helpers.py -- _ease_names.py lives under
primitives/essential/tools/, whose __init__.py runs pkgutil auto-discovery,
and conftest.py stubs anki_mcp_server.primitives to a non-package to suppress
that, so the normal dotted import path is unavailable. _ease_names.py itself
has no addon imports (just `typing.Any`), so loading the single file directly
needs no further stubbing.
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
ease_names = _ease_names_module.ease_names


class _FakeSched:
    """Stand-in for ``col.sched`` exposing just ``answerButtons``."""

    def __init__(self, button_count: int) -> None:
        self._button_count = button_count
        self.calls: list[object] = []

    def answerButtons(self, card: object) -> int:
        self.calls.append(card)
        return self._button_count


class _FakeCol:
    """Minimal fake collection: only ``.sched`` is needed by the helper."""

    def __init__(self, button_count: int) -> None:
        self.sched = _FakeSched(button_count)


_CARD = object()  # opaque sentinel; the helper never inspects the card itself


class TestFourButtonCards:
    """The common case: matches the old hardcoded dict exactly."""

    def test_ease_names_mapping(self):
        col = _FakeCol(button_count=4)
        assert ease_names(col, _CARD) == {
            1: "Again", 2: "Hard", 3: "Good", 4: "Easy",
        }

    @pytest.mark.parametrize(
        "ease, expected",
        [(1, "Again"), (2, "Hard"), (3, "Good"), (4, "Easy")],
    )
    def test_ease_name(self, ease, expected):
        col = _FakeCol(button_count=4)
        assert ease_name(col, _CARD, ease) == expected

    def test_ease_name_out_of_range_raises(self):
        col = _FakeCol(button_count=4)
        with pytest.raises(ValueError, match="ease 5"):
            ease_name(col, _CARD, 5)

    def test_ease_name_zero_raises(self):
        col = _FakeCol(button_count=4)
        with pytest.raises(ValueError, match="ease 0"):
            ease_name(col, _CARD, 0)


class TestThreeButtonCards:
    """Learning/relearn cards with no Hard option: 1=Again, 2=Good, 3=Easy."""

    def test_ease_names_mapping(self):
        col = _FakeCol(button_count=3)
        assert ease_names(col, _CARD) == {1: "Again", 2: "Good", 3: "Easy"}

    @pytest.mark.parametrize(
        "ease, expected",
        [(1, "Again"), (2, "Good"), (3, "Easy")],
    )
    def test_ease_name(self, ease, expected):
        col = _FakeCol(button_count=3)
        assert ease_name(col, _CARD, ease) == expected

    def test_ease_name_out_of_range_raises(self):
        """ease=4 ("Easy" in the 4-button scale) is NOT valid for a 3-button
        card -- this is exactly the mislabeling this helper exists to avoid."""
        col = _FakeCol(button_count=3)
        with pytest.raises(ValueError, match="ease 4"):
            ease_name(col, _CARD, 4)


class TestTwoButtonCards:
    """Cards with only Again/Good, per Reviewer._answerButtonList()."""

    def test_ease_names_mapping(self):
        col = _FakeCol(button_count=2)
        assert ease_names(col, _CARD) == {1: "Again", 2: "Good"}

    @pytest.mark.parametrize(
        "ease, expected",
        [(1, "Again"), (2, "Good")],
    )
    def test_ease_name(self, ease, expected):
        col = _FakeCol(button_count=2)
        assert ease_name(col, _CARD, ease) == expected

    def test_ease_name_out_of_range_raises(self):
        col = _FakeCol(button_count=2)
        with pytest.raises(ValueError, match="ease 3"):
            ease_name(col, _CARD, 3)


class TestUnusualButtonCount:
    """Any count not explicitly matched (e.g. 1) falls back to the 4-button
    shape -- matching Reviewer._answerButtonList()'s own else branch."""

    def test_falls_back_to_four_button_names(self):
        col = _FakeCol(button_count=1)
        assert ease_names(col, _CARD) == {
            1: "Again", 2: "Hard", 3: "Good", 4: "Easy",
        }


class TestAnswerButtonsCalledPerCard:
    """ease_names/ease_name must consult the card's OWN current button count,
    not a cached/global value -- calling col.sched.answerButtons(card) each
    time is what makes it safe to call before a mutating operation."""

    def test_answer_buttons_called_with_given_card(self):
        col = _FakeCol(button_count=4)
        card = object()
        ease_name(col, card, 1)
        assert col.sched.calls == [card]
