"""Tests for model/note type tools."""
from __future__ import annotations

from .helpers import call_tool


class TestModelTools:
    """Tests for model/note type tools."""

    def test_model_names(self):
        """modelNames should return list of note types."""
        result = call_tool("modelNames")
        assert "modelNames" in result
        assert len(result["modelNames"]) > 0
