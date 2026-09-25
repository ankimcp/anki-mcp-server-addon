"""Unit tests for tool_decorator.py's _write_lock -- the write=True wrapper
that refreshes Anki's UI via mw.reset() after a handler runs.

Covers the refresh_ui split (CLAUDE.md "Adding New Primitives" / @Tool docs):
write=True marks a tool as collection-mutating, but refresh_ui (default True)
independently gates whether _write_lock actually calls mw.reset() -- a tool
like gui_answer_card sets refresh_ui=False because it already refreshes the
reviewer itself.

Uses the ``install_mw`` fixture from conftest.py (monkeypatches the stubbed
``aqt`` module's ``mw`` attribute), same pattern as test_sync_tooltip.py.
"""
from __future__ import annotations

import types

import pytest

from anki_mcp_server.tool_decorator import _write_lock


def _fake_mw(*, reset_side_effect=None):
    """A minimal fake mw: mw.col.db is truthy (collection open), mw.reset()
    is a plain call recorder unless given a side effect to raise."""
    col = types.SimpleNamespace(db=object())
    calls: list[None] = []

    def reset():
        calls.append(None)
        if reset_side_effect is not None:
            raise reset_side_effect

    mw = types.SimpleNamespace(col=col, reset=reset)
    mw._reset_calls = calls  # type: ignore[attr-defined]
    return mw


class TestRefreshUiGate:
    """mw.reset() is called iff write (i.e. _write_lock is applied at all)
    AND refresh_ui=True."""

    def test_refresh_ui_true_calls_reset(self, install_mw):
        mw = install_mw(_fake_mw())

        def handler():
            return "result"

        wrapped = _write_lock(handler, refresh_ui=True)
        result = wrapped()

        assert result == "result"
        assert len(mw._reset_calls) == 1

    def test_refresh_ui_false_does_not_call_reset(self, install_mw):
        mw = install_mw(_fake_mw())

        def handler():
            return "result"

        wrapped = _write_lock(handler, refresh_ui=False)
        result = wrapped()

        assert result == "result"
        assert len(mw._reset_calls) == 0

    def test_default_refresh_ui_is_true(self, install_mw):
        """_write_lock's own default (no refresh_ui kwarg) matches the
        @Tool decorator's default of refresh_ui=True."""
        mw = install_mw(_fake_mw())

        def handler():
            return None

        wrapped = _write_lock(handler)
        wrapped()

        assert len(mw._reset_calls) == 1


class TestResetRunsOnHandlerException:
    """mw.reset() still runs (when refresh_ui=True) even if the handler
    raised -- a handler that raised may still have written (e.g.
    change_note_type verifies after the mutation)."""

    def test_reset_called_after_handler_raises(self, install_mw):
        mw = install_mw(_fake_mw())

        def handler():
            raise ValueError("boom")

        wrapped = _write_lock(handler, refresh_ui=True)
        with pytest.raises(ValueError, match="boom"):
            wrapped()

        assert len(mw._reset_calls) == 1

    def test_refresh_ui_false_skips_reset_even_on_exception(self, install_mw):
        mw = install_mw(_fake_mw())

        def handler():
            raise ValueError("boom")

        wrapped = _write_lock(handler, refresh_ui=False)
        with pytest.raises(ValueError, match="boom"):
            wrapped()

        assert len(mw._reset_calls) == 0


class TestResetFailureDoesNotMaskHandlerOutcome:
    """A failing mw.reset() must never mask the handler's own result or
    exception -- it's logged and swallowed (tool_decorator.py wraps it in
    its own try/except around the reset() call)."""

    def test_reset_failure_does_not_mask_success_result(self, install_mw):
        install_mw(_fake_mw(reset_side_effect=RuntimeError("reset blew up")))

        def handler():
            return "original result"

        wrapped = _write_lock(handler, refresh_ui=True)
        # Must return the handler's result, not raise the reset() failure.
        assert wrapped() == "original result"

    def test_reset_failure_does_not_mask_handler_exception(self, install_mw):
        install_mw(_fake_mw(reset_side_effect=RuntimeError("reset blew up")))

        def handler():
            raise ValueError("original error")

        wrapped = _write_lock(handler, refresh_ui=True)
        # The handler's own exception must propagate, not the reset() one.
        with pytest.raises(ValueError, match="original error"):
            wrapped()


class TestClosedCollectionGuard:
    """mw.reset() is skipped when the collection is closed-for-full-sync
    (mw.col non-None but mw.col.db is None) -- see handler_wrappers.py's
    _check_col_available for the same guard shape."""

    def test_reset_skipped_when_col_db_is_none(self, install_mw):
        col = types.SimpleNamespace(db=None)
        calls: list[None] = []
        mw = types.SimpleNamespace(col=col, reset=lambda: calls.append(None))
        install_mw(mw)

        def handler():
            return "result"

        wrapped = _write_lock(handler, refresh_ui=True)
        assert wrapped() == "result"
        assert calls == []
