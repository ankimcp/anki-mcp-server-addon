"""Unit tests for sync direction logic in anki_mcp_server.sync_state.

Covers ``legal_directions_for`` (mapping ChangesRequired names to allowed
resolve directions) and ``is_legal_resolution`` (the resolve validation
predicate). Pure logic -- no Anki required.
"""
from __future__ import annotations

import pytest

from anki_mcp_server.sync_state import is_legal_resolution, legal_directions_for


class TestLegalDirectionsFor:
    @pytest.mark.parametrize(
        "required_name, expected",
        [
            ("NO_CHANGES", []),
            ("NORMAL_SYNC", []),
            ("FULL_SYNC", ["upload", "download"]),
            ("FULL_UPLOAD", ["upload"]),
            ("FULL_DOWNLOAD", ["download"]),
        ],
    )
    def test_known_names(self, required_name: str, expected: list[str]):
        assert legal_directions_for(required_name) == expected

    def test_full_sync_allows_both_directions(self):
        assert set(legal_directions_for("FULL_SYNC")) == {"upload", "download"}

    def test_forced_directions_are_single(self):
        assert legal_directions_for("FULL_UPLOAD") == ["upload"]
        assert legal_directions_for("FULL_DOWNLOAD") == ["download"]

    def test_unknown_name_returns_empty(self):
        assert legal_directions_for("UNKNOWN") == []
        assert legal_directions_for("") == []

    def test_returns_fresh_list_not_shared_reference(self):
        """Mutating the result must not corrupt the internal mapping."""
        a = legal_directions_for("FULL_SYNC")
        a.append("bogus")
        b = legal_directions_for("FULL_SYNC")
        assert b == ["upload", "download"]


class TestIsLegalResolution:
    def test_direction_in_list_is_legal(self):
        assert is_legal_resolution(["upload", "download"], "upload") is True
        assert is_legal_resolution(["download"], "download") is True

    def test_direction_not_in_list_is_illegal(self):
        assert is_legal_resolution(["upload"], "download") is False
        assert is_legal_resolution([], "upload") is False

    def test_none_direction_is_illegal(self):
        assert is_legal_resolution(["upload", "download"], None) is False

    def test_forced_upload_rejects_download(self):
        """A FULL_UPLOAD conflict must reject a download resolution."""
        legal = legal_directions_for("FULL_UPLOAD")
        assert is_legal_resolution(legal, "download") is False
        assert is_legal_resolution(legal, "upload") is True
