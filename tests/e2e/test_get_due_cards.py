"""Tests for get_due_cards tool."""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool, list_tools


class TestGetDueCards:
    """Tests for get_due_cards tool."""

    def test_get_due_cards_tool_exists(self):
        """get_due_cards tool should be registered."""
        tools = list_tools()
        tool_names = [t["name"] for t in tools]
        assert "get_due_cards" in tool_names

    def test_get_due_cards_requires_deck_name(self):
        """get_due_cards should error without deck_name parameter."""
        result = call_tool("get_due_cards", {})
        assert result.get("isError") is True

    def test_get_due_cards_invalid_deck(self):
        """get_due_cards should error for non-existent deck."""
        result = call_tool("get_due_cards", {
            "deck_name": f"NonExist{unique_id()}"
        })
        assert result.get("isError") is True

    def test_get_due_cards_empty_deck(self):
        """get_due_cards on empty deck should return empty cards with counts."""
        uid = unique_id()
        deck_name = f"E2E::EmptyDue{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        result = call_tool("get_due_cards", {"deck_name": deck_name})

        # Should not error
        assert result.get("isError") is not True

        # Should have required fields
        assert "cards" in result
        assert "counts" in result
        assert "total" in result
        assert "returned" in result
        assert "message" in result

        # Empty deck should have no cards
        assert result["cards"] == []
        assert result["total"] == 0
        assert result["returned"] == 0

        # Counts should be present (even if zero)
        assert "new" in result["counts"]
        assert "learning" in result["counts"]
        assert "review" in result["counts"]

    def test_get_due_cards_returns_new_card(self):
        """get_due_cards should return newly created card."""
        uid = unique_id()
        deck_name = f"E2E::Due{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add a note (creates a card)
        note_result = call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Test Question {uid}",
                "Back": f"Test Answer {uid}"
            }
        })
        assert "note_id" in note_result

        # Get due cards
        result = call_tool("get_due_cards", {"deck_name": deck_name})

        # Should have one card
        assert len(result["cards"]) == 1
        assert result["returned"] == 1
        assert result["total"] >= 1

        # Verify card structure
        card = result["cards"][0]
        assert "cardId" in card
        assert "front" in card
        assert "back" in card
        assert "deckName" in card
        assert "modelName" in card
        assert "queueType" in card
        assert "due" in card
        assert "interval" in card
        assert "factor" in card

        # Verify content
        assert f"Test Question {uid}" in card["front"]
        assert f"Test Answer {uid}" in card["back"]
        assert card["deckName"] == deck_name
        assert card["modelName"] == "Basic"
        # New cards should be in "new" queue
        assert card["queueType"] == "new"

    def test_get_due_cards_response_structure(self):
        """get_due_cards response should have all expected fields."""
        uid = unique_id()
        deck_name = f"E2E::Struct{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add a card to ensure non-empty response
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {"Front": "Q", "Back": "A"}
        })

        result = call_tool("get_due_cards", {"deck_name": deck_name})

        # Top-level fields
        assert "cards" in result
        assert "counts" in result
        assert "total" in result
        assert "returned" in result
        assert "message" in result

        # Verify types
        assert isinstance(result["cards"], list)
        assert isinstance(result["counts"], dict)
        assert isinstance(result["total"], int)
        assert isinstance(result["returned"], int)
        assert isinstance(result["message"], str)

        # Counts structure
        assert "new" in result["counts"]
        assert "learning" in result["counts"]
        assert "review" in result["counts"]
        assert isinstance(result["counts"]["new"], int)
        assert isinstance(result["counts"]["learning"], int)
        assert isinstance(result["counts"]["review"], int)

        # If cards exist, verify card structure
        if result["cards"]:
            card = result["cards"][0]
            assert isinstance(card["cardId"], int)
            assert isinstance(card["front"], str)
            assert isinstance(card["back"], str)
            assert isinstance(card["deckName"], str)
            assert isinstance(card["modelName"], str)
            assert isinstance(card["queueType"], str)
            assert isinstance(card["due"], int)
            assert isinstance(card["interval"], int)
            assert isinstance(card["factor"], int)

    def test_get_due_cards_skip_images_filters_image_cards(self):
        """get_due_cards with skip_images should filter out cards with images."""
        uid = unique_id()
        deck_name = f"E2E::FilterImg{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add note WITH an image
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"<img src='test.jpg'>Question With Image {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Add note WITHOUT an image
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Text Only Question {uid}",
                "Back": f"Text Answer {uid}"
            }
        })

        # Call with skip_images=True
        result = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "skip_images": True
        })

        # Should not error
        assert result.get("isError") is not True

        # Should return the text-only card
        assert len(result["cards"]) == 1
        card = result["cards"][0]
        assert "Text Only Question" in card["front"]
        assert "<img" not in card["front"]

        # Should have skipped field with image count
        assert "skipped" in result
        assert result["skipped"]["images"] == 1  # We created exactly 1 image card

    def test_get_due_cards_skip_audio_filters_audio_cards(self):
        """get_due_cards with skip_audio should filter out cards with audio."""
        uid = unique_id()
        deck_name = f"E2E::FilterAudio{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add note WITH audio
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"[sound:test.mp3]Question With Audio {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Add note WITHOUT audio
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Text Only Question {uid}",
                "Back": f"Text Answer {uid}"
            }
        })

        # Call with skip_audio=True
        result = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "skip_audio": True
        })

        # Should not error
        assert result.get("isError") is not True

        # Should return the text-only card
        assert len(result["cards"]) == 1
        card = result["cards"][0]
        assert "Text Only Question" in card["front"]
        assert "[sound:" not in card["front"]

        # Should have skipped field with audio count
        assert "skipped" in result
        assert result["skipped"]["audio"] == 1  # We created exactly 1 audio card

    def test_get_due_cards_skip_both_filters(self):
        """get_due_cards with both skip parameters should filter both types."""
        uid = unique_id()
        deck_name = f"E2E::FilterBoth{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add note with image
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"<img src='test.jpg'>Image Question {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Add note with audio
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"[sound:test.mp3]Audio Question {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Add text-only note
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Text Only Question {uid}",
                "Back": f"Text Answer {uid}"
            }
        })

        # Call with both filters
        result = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "skip_images": True,
            "skip_audio": True
        })

        # Should not error
        assert result.get("isError") is not True

        # Should return the text-only card
        assert len(result["cards"]) == 1
        card = result["cards"][0]
        assert "Text Only Question" in card["front"]

        # Should have skipped counts for both
        assert "skipped" in result
        assert result["skipped"]["images"] >= 1
        assert result["skipped"]["audio"] >= 1

    def test_get_due_cards_skip_images_filters_back_field(self):
        """get_due_cards should filter cards with images in Back field."""
        uid = unique_id()
        deck_name = f"E2E::FilterBack{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add note with image in BACK field only
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Clean front {uid}",
                "Back": f"<img src='test.jpg'>Answer with image {uid}"
            }
        })

        # Add text-only note
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Text question {uid}",
                "Back": f"Text answer {uid}"
            }
        })

        result = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "skip_images": True
        })

        assert result.get("isError") is not True
        assert len(result["cards"]) == 1
        assert "Text question" in result["cards"][0]["front"]
        assert result["skipped"]["images"] == 1

    def test_get_due_cards_skip_audio_case_insensitive(self):
        """get_due_cards should filter audio regardless of case."""
        uid = unique_id()
        deck_name = f"E2E::FilterAudioCase{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add note with UPPERCASE audio tag
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"[SOUND:test.mp3]Uppercase audio {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Add text-only note
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Text question {uid}",
                "Back": f"Text answer {uid}"
            }
        })

        result = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "skip_audio": True
        })

        assert result.get("isError") is not True
        assert len(result["cards"]) == 1
        assert "Text question" in result["cards"][0]["front"]
        assert result["skipped"]["audio"] == 1

    def test_get_due_cards_card_with_both_media_types(self):
        """Card with both image and audio should be filtered by either flag."""
        uid = unique_id()
        deck_name = f"E2E::FilterBothTypes{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add note with BOTH image and audio
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"<img src='test.jpg'>[sound:test.mp3]Both media {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Add text-only note
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Text question {uid}",
                "Back": f"Text answer {uid}"
            }
        })

        # Test with only skip_images - should still filter the mixed card
        result = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "skip_images": True
        })

        assert result.get("isError") is not True
        assert len(result["cards"]) == 1
        assert "Text question" in result["cards"][0]["front"]

    def test_get_due_cards_all_filtered_returns_empty(self):
        """get_due_cards should return empty when all cards are filtered."""
        uid = unique_id()
        deck_name = f"E2E::AllFiltered{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add ONLY image cards
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"<img src='test1.jpg'>Image Question 1 {uid}",
                "Back": f"Answer 1 {uid}"
            }
        })

        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"<img src='test2.jpg'>Image Question 2 {uid}",
                "Back": f"Answer 2 {uid}"
            }
        })

        # Call with skip_images=True (should filter all)
        result = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "skip_images": True
        })

        # Should not error
        assert result.get("isError") is not True

        # Should return empty cards
        assert result["cards"] == []
        assert result["returned"] == 0

        # Should have skipped count
        assert "skipped" in result
        assert result["skipped"]["images"] > 0

        # Should have message about all cards containing media
        assert "message" in result
        assert "All cards contain media" in result["message"]

    def test_get_due_cards_no_filter_no_skipped_field(self):
        """get_due_cards without filters should not include skipped field."""
        uid = unique_id()
        deck_name = f"E2E::NoFilter{uid}"
        call_tool("create_deck", {"deck_name": deck_name})

        # Add a card (any type)
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Call WITHOUT skip parameters (default)
        result = call_tool("get_due_cards", {
            "deck_name": deck_name
        })

        # Should not error
        assert result.get("isError") is not True

        # Should return a card
        assert len(result["cards"]) >= 1

        # Should NOT have skipped field
        assert "skipped" not in result
