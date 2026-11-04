"""Guard the deck update boundary without a running Anki instance."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def deck_tools(real_mcp):
    from anki_mcp_server.tool_decorator import _registry

    return {
        name: _registry[name]["original"]
        for name in ("get_deck_description", "update_deck_description")
    }


@pytest.fixture
def collection(install_mw):
    deck = {
        "id": 42, "name": "Languages::English", "dyn": 0,
        "desc": "Original", "md": True, "conf": 7,
        "extendNew": 12, "extendRev": 34,
        "newToday": [100, 3], "revToday": [100, 9],
    }
    col = MagicMock()
    col.decks.by_name.return_value = deck
    install_mw(SimpleNamespace(col=col))
    return col, deck


def test_update_preserves_unrelated_deck_data(deck_tools, collection):
    col, deck = collection
    before = deepcopy(deck)
    deck_tools["update_deck_description"](deck["name"], "Replacement", False)

    saved = col.decks.update_dict.call_args.args[0]
    assert saved == {**before, "desc": "Replacement", "md": False}


def test_read_does_not_persist_or_create_a_deck(deck_tools, collection):
    col, deck = collection
    deck_tools["get_deck_description"](deck["name"])

    col.decks.update_dict.assert_not_called()
    col.decks.id.assert_not_called()
