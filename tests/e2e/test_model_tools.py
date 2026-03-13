"""Tests for model/note type tools."""
from __future__ import annotations

from .helpers import call_tool


class TestModelTools:
    """Tests for model/note type tools."""

    def test_model_names(self):
        """model_names should return list of note types."""
        result = call_tool("model_names")
        assert "modelNames" in result
        assert len(result["modelNames"]) > 0
