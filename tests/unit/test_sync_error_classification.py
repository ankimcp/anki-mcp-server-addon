"""Unit tests for anki_mcp_server.sync_state.classify_sync_error.

Best-effort mapping of backend sync error message text to stable category
strings. Substrings are based on Anki's ftl sync strings. Pure logic -- no
Anki required.
"""
from __future__ import annotations

import pytest

from anki_mcp_server.sync_state import classify_sync_error


class TestClassifySyncError:
    @pytest.mark.parametrize(
        "message, expected",
        [
            # sync-conflict
            (
                "Only one copy of Anki can sync to your account at once. "
                "Please try again in a few minutes.",
                "another_sync_running",
            ),
            # sync-sanity-check-failed
            (
                "Please use the Check Database function, then sync again.",
                "sanity_check_failed",
            ),
            # sync-clock-off
            (
                "Your computer's clock is not set to the correct time.",
                "clock_incorrect",
            ),
            # sync-upload-too-large
            (
                "Your collection file is too large to send to AnkiWeb.",
                "upload_too_large",
            ),
            # sync-client-too-old
            (
                "Your Anki version is too old. Please update to the latest version.",
                "client_too_old",
            ),
            # sync-resync-required
            (
                "Please sync again, and post on the support site if this keeps happening.",
                "resync_required",
            ),
            # sync-server-error
            (
                "The server encountered an error. Please try again later.",
                "server_error",
            ),
        ],
    )
    def test_known_categories(self, message: str, expected: str):
        assert classify_sync_error(message) == expected

    def test_case_insensitive(self):
        assert classify_sync_error("ONLY ONE COPY of Anki") == "another_sync_running"

    def test_auth_phrases(self):
        assert classify_sync_error("Authentication failed") == "auth_failed"
        assert classify_sync_error("invalid credentials provided") == "auth_failed"

    def test_unknown_message_returns_unknown(self):
        assert classify_sync_error("something totally unexpected happened") == "unknown"

    def test_empty_message_returns_unknown(self):
        assert classify_sync_error("") == "unknown"

    def test_specific_beats_generic_ordering(self):
        """'only one copy' must win even though the text also says 'try again'."""
        msg = "Only one copy of Anki can sync. Please try again later."
        assert classify_sync_error(msg) == "another_sync_running"

    def test_server_error_beats_resync_required(self):
        """A 'server error ... sync again' message is a server error first.

        Both 'server encountered' and 'sync again' are present; server_error is
        ordered ahead of resync_required so the specific cause wins.
        """
        msg = "The server encountered an error. Please sync again later."
        assert classify_sync_error(msg) == "server_error"

    def test_plain_resync_still_classifies_as_resync(self):
        """Without a server phrase, 'sync again' still means resync_required."""
        msg = "Please sync again, and post on the support site if this recurs."
        assert classify_sync_error(msg) == "resync_required"

    def test_access_denied_is_not_misclassified_as_auth(self):
        """'denied' was dropped from auth needles (too broad); a bare 'access
        denied' server message must not masquerade as an auth failure."""
        assert classify_sync_error("access denied by proxy") == "unknown"
