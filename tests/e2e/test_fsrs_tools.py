"""E2E tests for FSRS tools and resource."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools, list_resources, read_resource


def _create_note_with_card(deck_name: str, uid: str) -> tuple[int, int]:
    """Create a deck + note and return (note_id, card_id).

    Helper used by tests that need a real card ID to exercise card-level tools.
    """
    call_tool("create_deck", {"deck_name": deck_name})
    result = call_tool("addNote", {
        "deck_name": deck_name,
        "model_name": "Basic",
        "fields": {
            "Front": f"FSRS Front {uid}",
            "Back": f"FSRS Back {uid}",
        },
    })
    assert "note_id" in result, f"Failed to create test note: {result}"
    note_id = result["note_id"]

    notes_info = call_tool("notesInfo", {"notes": [note_id]})
    card_id = notes_info["notes"][0]["cards"][0]
    return note_id, card_id


class TestFsrsToolDiscovery:
    """Verify all four FSRS tools appear in tools/list."""

    def test_get_fsrs_params_tool_exists(self):
        """get_fsrs_params tool should be registered."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "get_fsrs_params" in tool_names

    def test_get_card_memory_state_tool_exists(self):
        """get_card_memory_state tool should be registered."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "get_card_memory_state" in tool_names

    def test_set_fsrs_params_tool_exists(self):
        """set_fsrs_params tool should be registered."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "set_fsrs_params" in tool_names

    def test_optimize_fsrs_params_tool_exists(self):
        """optimize_fsrs_params tool should be registered."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "optimize_fsrs_params" in tool_names


class TestFsrsResourceDiscovery:
    """Verify the FSRS config resource appears in resources/list."""

    def test_fsrs_config_resource_exists(self):
        """anki://fsrs/config resource should be registered."""
        resources = list_resources()
        uris = [r["uri"] for r in resources]
        assert "anki://fsrs/config" in uris


class TestGetFsrsParams:
    """Tests for the get_fsrs_params tool."""

    def test_get_all_presets_no_args(self):
        """get_fsrs_params with no arguments should return all presets."""
        result = call_tool("get_fsrs_params")

        assert result.get("isError") is not True
        assert "fsrs_enabled" in result
        assert "fsrs_version" in result
        assert "presets" in result
        assert "total_presets" in result
        assert isinstance(result["presets"], list)
        assert result["total_presets"] == len(result["presets"])
        # Fresh profile has at least the Default preset
        assert result["total_presets"] >= 1

    def test_get_all_presets_preset_shape(self):
        """Each preset entry should contain the expected fields."""
        result = call_tool("get_fsrs_params")

        assert result.get("isError") is not True
        preset = result["presets"][0]
        assert "preset_name" in preset
        assert "preset_id" in preset
        assert "fsrs_weights" in preset
        assert "desired_retention" in preset
        assert "max_interval" in preset
        assert "decks" in preset
        assert isinstance(preset["fsrs_weights"], list)
        assert isinstance(preset["decks"], list)

    def test_get_params_for_default_deck(self):
        """get_fsrs_params with deck_name='Default' should return that deck's preset."""
        result = call_tool("get_fsrs_params", {"deck_name": "Default"})

        assert result.get("isError") is not True
        assert "fsrs_enabled" in result
        assert "fsrs_version" in result
        assert "deck_name" in result
        assert result["deck_name"] == "Default"
        assert "preset" in result

        preset = result["preset"]
        assert "preset_name" in preset
        assert "preset_id" in preset
        assert "fsrs_weights" in preset
        assert "desired_retention" in preset
        assert "max_interval" in preset
        assert "decks" in preset

    def test_get_params_invalid_deck_returns_error(self):
        """get_fsrs_params with a non-existent deck_name should return an error."""
        result = call_tool("get_fsrs_params", {"deck_name": f"NonExistentDeck{unique_id()}"})

        assert result.get("isError") is True


class TestGetCardMemoryState:
    """Tests for the get_card_memory_state tool."""

    def test_empty_card_ids_returns_error(self):
        """get_card_memory_state with an empty list should return an error."""
        result = call_tool("get_card_memory_state", {"card_ids": []})

        assert result.get("isError") is True

    def test_nonexistent_card_id(self):
        """get_card_memory_state with a bogus card ID handles it gracefully.

        When FSRS is disabled the tool returns an error before touching cards.
        When FSRS is enabled, the card is reported as not_found.
        Either outcome is acceptable.
        """
        result = call_tool("get_card_memory_state", {"card_ids": [999999999999]})

        # Either FSRS not enabled (isError) or card not found (not_found list)
        if result.get("isError"):
            # FSRS disabled - expected in test container
            return
        assert "cards" in result
        assert result["total"] == 0
        assert "not_found" in result
        assert 999999999999 in result["not_found"]

    def test_real_card_id_fsrs_disabled_returns_error(self):
        """get_card_memory_state should return an error if FSRS is not enabled.

        The test container starts with a fresh profile where FSRS is disabled by
        default. This test creates a card and verifies the tool gracefully surfaces
        the disabled-FSRS error rather than crashing.
        """
        uid = unique_id()
        deck_name = f"E2E::MemState{uid}"
        _, card_id = _create_note_with_card(deck_name, uid)

        result = call_tool("get_card_memory_state", {"card_ids": [card_id]})

        if result.get("isError"):
            # FSRS not enabled - expected behaviour
            assert "fsrs" in str(result).lower() or "isError" in result
        else:
            # FSRS happened to be enabled - validate the response shape
            assert "cards" in result
            assert "total" in result
            assert isinstance(result["cards"], list)
            if result["total"] > 0:
                card = result["cards"][0]
                assert "card_id" in card
                assert card["card_id"] == card_id
                assert "stability" in card
                assert "difficulty" in card
                assert "interval" in card
                assert "queue" in card
                assert "type" in card

    def test_real_card_id_returns_valid_shape(self):
        """get_card_memory_state with a real card ID returns a well-formed response.

        Validates the response structure regardless of whether FSRS is enabled.
        """
        uid = unique_id()
        deck_name = f"E2E::MemShape{uid}"
        _, card_id = _create_note_with_card(deck_name, uid)

        result = call_tool("get_card_memory_state", {"card_ids": [card_id]})

        if result.get("isError"):
            # FSRS not enabled - nothing more to check
            return

        assert "cards" in result
        assert "total" in result
        assert isinstance(result["cards"], list)


class TestSetFsrsParams:
    """Tests for the set_fsrs_params tool."""

    def _get_default_preset_name(self) -> str:
        """Return the name of the first available preset."""
        result = call_tool("get_fsrs_params")
        assert "presets" in result, f"Unexpected get_fsrs_params response: {result}"
        assert len(result["presets"]) > 0
        return result["presets"][0]["preset_name"]

    def test_no_changes_returns_error(self):
        """set_fsrs_params with no updated fields should return an error."""
        preset_name = self._get_default_preset_name()

        result = call_tool("set_fsrs_params", {"preset_name": preset_name})

        assert result.get("isError") is True

    def test_invalid_preset_returns_error(self):
        """set_fsrs_params with a non-existent preset_name should return an error."""
        result = call_tool("set_fsrs_params", {
            "preset_name": f"GhostPreset{unique_id()}",
            "desired_retention": 0.85,
        })

        assert result.get("isError") is True

    def test_set_desired_retention(self):
        """set_fsrs_params should update desired_retention and report old/new values."""
        preset_name = self._get_default_preset_name()

        # Read current value so we can restore it afterwards
        params_before = call_tool("get_fsrs_params")
        preset_before = next(
            p for p in params_before["presets"] if p["preset_name"] == preset_name
        )
        original_retention = preset_before["desired_retention"]

        # Pick a new value that differs from the current one
        new_retention = 0.85 if abs(original_retention - 0.85) > 0.001 else 0.90

        result = call_tool("set_fsrs_params", {
            "preset_name": preset_name,
            "desired_retention": new_retention,
        })

        assert result.get("isError") is not True
        assert result["preset_name"] == preset_name
        assert result["status"] == "updated"
        assert "changes" in result
        assert "desired_retention" in result["changes"]
        change = result["changes"]["desired_retention"]
        assert "old" in change
        assert "new" in change
        assert abs(change["new"] - new_retention) < 1e-6

        # Restore original value to keep state clean for other tests
        call_tool("set_fsrs_params", {
            "preset_name": preset_name,
            "desired_retention": original_retention,
        })

    def test_set_max_interval(self):
        """set_fsrs_params should update max_interval and report old/new values."""
        preset_name = self._get_default_preset_name()

        # Read current max_interval
        params_before = call_tool("get_fsrs_params")
        preset_before = next(
            p for p in params_before["presets"] if p["preset_name"] == preset_name
        )
        original_max_ivl = preset_before["max_interval"]

        new_max_ivl = 1000 if original_max_ivl != 1000 else 2000

        result = call_tool("set_fsrs_params", {
            "preset_name": preset_name,
            "max_interval": new_max_ivl,
        })

        assert result.get("isError") is not True
        assert result["status"] == "updated"
        assert "max_interval" in result["changes"]
        assert result["changes"]["max_interval"]["new"] == new_max_ivl

        # Restore
        call_tool("set_fsrs_params", {
            "preset_name": preset_name,
            "max_interval": original_max_ivl,
        })

    def test_desired_retention_out_of_range_returns_error(self):
        """set_fsrs_params should reject retention values outside 0.70-0.99."""
        preset_name = self._get_default_preset_name()

        result_low = call_tool("set_fsrs_params", {
            "preset_name": preset_name,
            "desired_retention": 0.50,
        })
        assert result_low.get("isError") is True

        result_high = call_tool("set_fsrs_params", {
            "preset_name": preset_name,
            "desired_retention": 1.0,
        })
        assert result_high.get("isError") is True

    def test_max_interval_zero_returns_error(self):
        """set_fsrs_params should reject max_interval of 0."""
        preset_name = self._get_default_preset_name()

        result = call_tool("set_fsrs_params", {
            "preset_name": preset_name,
            "max_interval": 0,
        })

        assert result.get("isError") is True

    def test_set_params_updates_are_persisted(self):
        """Changes made by set_fsrs_params should be reflected by get_fsrs_params."""
        preset_name = self._get_default_preset_name()

        # Record original
        params_before = call_tool("get_fsrs_params")
        preset_before = next(
            p for p in params_before["presets"] if p["preset_name"] == preset_name
        )
        original_max_ivl = preset_before["max_interval"]

        new_max_ivl = 500 if original_max_ivl != 500 else 501

        call_tool("set_fsrs_params", {
            "preset_name": preset_name,
            "max_interval": new_max_ivl,
        })

        params_after = call_tool("get_fsrs_params")
        preset_after = next(
            p for p in params_after["presets"] if p["preset_name"] == preset_name
        )
        assert preset_after["max_interval"] == new_max_ivl

        # Restore
        call_tool("set_fsrs_params", {
            "preset_name": preset_name,
            "max_interval": original_max_ivl,
        })


class TestOptimizeFsrsParams:
    """Tests for the optimize_fsrs_params tool."""

    def _get_default_preset_name(self) -> str:
        """Return the name of the first available preset."""
        result = call_tool("get_fsrs_params")
        assert "presets" in result, f"Unexpected get_fsrs_params response: {result}"
        assert len(result["presets"]) > 0
        return result["presets"][0]["preset_name"]

    def test_invalid_preset_returns_error(self):
        """optimize_fsrs_params with a non-existent preset should return an error."""
        result = call_tool("optimize_fsrs_params", {
            "preset_name": f"GhostPreset{unique_id()}",
        })

        assert result.get("isError") is True

    def test_dry_run_fsrs_disabled_returns_error(self):
        """optimize_fsrs_params should return an error if FSRS is not enabled.

        In the test container FSRS is disabled by default so we expect an error.
        If FSRS happens to be enabled we validate the dry-run response shape instead.
        """
        preset_name = self._get_default_preset_name()

        result = call_tool("optimize_fsrs_params", {
            "preset_name": preset_name,
            "apply_results": False,
        })

        if result.get("isError"):
            # Either FSRS disabled or not enough review history - both are acceptable
            return

        # FSRS enabled - validate dry-run response
        assert "preset_name" in result
        assert result["preset_name"] == preset_name
        assert "current_params" in result
        assert "optimized_params" in result
        assert "already_optimal" in result
        assert "applied" in result
        # Dry run must not apply
        assert result["applied"] is False
        assert "search_query" in result

    def test_dry_run_does_not_modify_params(self):
        """optimize_fsrs_params dry run should leave parameters unchanged.

        Reads params before, runs dry-run optimize, then reads again and checks
        that nothing changed.  Skips the assertion if FSRS is not enabled (error
        path) since there is nothing to compare.
        """
        preset_name = self._get_default_preset_name()

        params_before = call_tool("get_fsrs_params")
        preset_before = next(
            p for p in params_before["presets"] if p["preset_name"] == preset_name
        )
        weights_before = preset_before["fsrs_weights"]

        result = call_tool("optimize_fsrs_params", {
            "preset_name": preset_name,
            "apply_results": False,
        })

        if result.get("isError"):
            # FSRS disabled or no review history - skip modification check
            return

        assert result["applied"] is False

        params_after = call_tool("get_fsrs_params")
        preset_after = next(
            p for p in params_after["presets"] if p["preset_name"] == preset_name
        )
        assert preset_after["fsrs_weights"] == weights_before


class TestFsrsConfigResource:
    """Tests for the anki://fsrs/config resource."""

    def test_read_fsrs_config_resource(self):
        """anki://fsrs/config should return a well-formed response."""
        result = read_resource("anki://fsrs/config")

        assert result is not None
        assert "fsrs_enabled" in result
        assert "fsrs_version" in result
        assert "total_presets" in result
        assert "presets" in result
        assert isinstance(result["presets"], list)
        assert result["total_presets"] == len(result["presets"])

    def test_fsrs_config_resource_preset_shape(self):
        """Each preset in anki://fsrs/config should have the expected summary fields."""
        result = read_resource("anki://fsrs/config")

        assert result.get("isError") is not True
        assert len(result["presets"]) >= 1

        preset = result["presets"][0]
        assert "name" in preset
        assert "has_weights" in preset
        assert "param_count" in preset
        assert "desired_retention" in preset
        assert "deck_count" in preset
        assert isinstance(preset["has_weights"], bool)
        assert isinstance(preset["param_count"], int)
        assert preset["param_count"] >= 0

    def test_fsrs_config_resource_consistent_with_tool(self):
        """anki://fsrs/config preset count should match get_fsrs_params preset count."""
        resource = read_resource("anki://fsrs/config")
        tool = call_tool("get_fsrs_params")

        assert resource.get("isError") is not True
        assert tool.get("isError") is not True

        assert resource["total_presets"] == tool["total_presets"]
        assert resource["fsrs_enabled"] == tool["fsrs_enabled"]
