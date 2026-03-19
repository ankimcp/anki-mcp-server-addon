"""Tests for concurrent multi-session access (issue #21).

Verifies that multiple MCP sessions can make tool calls simultaneously
without receiving each other's responses.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from .helpers import call_tool


class TestConcurrentSessions:
    """Tests for multi-session concurrency (issue #21).

    These tests verify that when multiple MCP sessions make tool calls
    simultaneously, each session receives the correct response.
    """

    def test_concurrent_list_decks(self, wait_for_server):
        """Multiple sessions calling list_decks simultaneously should all succeed."""
        num_sessions = 5

        with ThreadPoolExecutor(max_workers=num_sessions) as pool:
            futures = {
                pool.submit(call_tool, "list_decks", {}): i
                for i in range(num_sessions)
            }

            results = []
            for future in as_completed(futures):
                result = future.result()
                results.append(result)

        assert len(results) == num_sessions
        for result in results:
            assert "decks" in result, f"Got unexpected result: {result}"
            assert isinstance(result["decks"], list)
            assert len(result["decks"]) >= 1  # Default deck always exists

    def test_concurrent_distinct_tools(self, wait_for_server):
        """Different tools called concurrently should return correct responses.

        If responses get swapped (the original bug), assertions catch it
        because each tool returns a response with a distinct top-level key.
        """
        calls = [
            ("list_decks", {}, "decks"),
            ("model_names", {}, "modelNames"),
            ("find_notes", {"query": "deck:*"}, "noteIds"),
        ]

        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = {}
            for tool_name, args, expected_key in calls:
                future = pool.submit(call_tool, tool_name, args)
                futures[future] = (tool_name, expected_key)

            for future in as_completed(futures):
                tool_name, expected_key = futures[future]
                result = future.result()
                assert expected_key in result, (
                    f"{tool_name} expected key '{expected_key}' but got: "
                    f"{list(result.keys())}. "
                    f"Response may have been routed to wrong session."
                )

    def test_concurrent_write_and_read(self, wait_for_server):
        """Concurrent write + read should not interfere."""
        deck_name = f"ConcurrencyTest::{uuid.uuid4().hex[:8]}"

        with ThreadPoolExecutor(max_workers=2) as pool:
            future_write = pool.submit(
                call_tool, "create_deck", {"deck_name": deck_name}
            )
            future_read = pool.submit(call_tool, "list_decks", {})

            write_result = future_write.result()
            read_result = future_read.result()

        assert "deckId" in write_result, f"create_deck failed: {write_result}"
        assert write_result["deckId"] > 0
        assert "decks" in read_result
        assert isinstance(read_result["decks"], list)

    def test_rapid_sequential_from_multiple_sessions(self, wait_for_server):
        """Rapid-fire calls from multiple 'sessions' should all get correct responses.

        Simulates the issue reporter's scenario: orchestrator + agents making
        many calls in quick succession.
        """
        num_calls = 10

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(call_tool, "list_decks", {}): i
                for i in range(num_calls)
            }

            success_count = 0
            for future in as_completed(futures):
                result = future.result()
                assert "decks" in result, (
                    f"Call {futures[future]} got wrong response: {result}"
                )
                success_count += 1

        assert success_count == num_calls

    def test_heavy_write_with_concurrent_read(self, wait_for_server):
        """A heavy add_notes (100 notes) runs while a fast read executes concurrently.

        add_notes with 100 notes is the heaviest single operation — it does
        per-note validation, duplicate checking, and a batch backend call.
        This maximizes the chance of the read request arriving while the
        write is still processing on the main thread.

        Not guaranteed to trigger overlap every time (subprocess startup
        variance), but verifies both responses are correctly routed.
        """
        uid = uuid.uuid4().hex[:8]
        deck_name = f"ConcurrentHeavy::{uid}"

        # Create the deck first
        deck_result = call_tool("create_deck", {"deck_name": deck_name})
        assert "deckId" in deck_result

        # Build 100 notes with unique content
        notes = [
            {"fields": {"Front": f"Q{uid}_{i}", "Back": f"A{uid}_{i}"}}
            for i in range(100)
        ]

        with ThreadPoolExecutor(max_workers=2) as pool:
            # Heavy write: add 100 notes
            future_write = pool.submit(call_tool, "add_notes", {
                "deck_name": deck_name,
                "model_name": "Basic",
                "notes": notes,
            })
            # Fast read: list decks (should complete while add_notes is processing)
            future_read = pool.submit(call_tool, "list_decks", {})

            write_result = future_write.result()
            read_result = future_read.result()

        # Write: verify it's the add_notes response, not list_decks
        assert "created" in write_result, (
            f"add_notes got wrong response: {list(write_result.keys())}"
        )
        assert write_result["created"] == 100
        assert write_result["total_requested"] == 100

        # Read: verify it's the list_decks response, not add_notes
        assert "decks" in read_result, (
            f"list_decks got wrong response: {list(read_result.keys())}"
        )
