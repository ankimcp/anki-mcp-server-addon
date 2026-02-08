"""E2E tests for rate_card when get_due_cards skips media cards.

Regression tests for Issue #10: "not at top of queue" error when rating cards
after get_due_cards with skip_images/skip_audio.

The Fix:
When get_due_cards skips media cards, it now buries them to remove them from
the scheduler queue. This ensures rate_card works on the returned card since
it's now actually at the top of the queue.

All tests should PASS.
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool


class TestSkipMediaRateCard:
    """Tests for rate_card after get_due_cards with media skip filters.

    Regression tests verifying that rate_card works correctly when get_due_cards
    skips and buries media cards.
    """

    def test_rate_card_after_skip_images(self):
        """REGRESSION TEST: Verifies rate_card works after skipping images.

        Steps:
        1. Create deck with image card (will be first in queue) and text card
        2. Call get_due_cards(skip_images=True) - buries image card, returns text card
        3. Call rate_card on returned text card - should succeed
        """
        uid = unique_id()
        deck_name = f"E2E::SkipImgRate{uid}"

        # Create deck
        call_tool("create_deck", {"deck_name": deck_name})

        # Add image card FIRST (will be at top of scheduler queue)
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"<img src='test.jpg'>Image Question {uid}",
                "Back": f"Image Answer {uid}"
            }
        })

        # Add text card SECOND
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Text Question {uid}",
                "Back": f"Text Answer {uid}"
            }
        })

        # Get due cards with skip_images=True
        # Should skip image card and return text card
        get_result = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "skip_images": True
        })

        # Verify we got the text card
        assert get_result.get("isError") is not True
        assert len(get_result["cards"]) == 1
        card = get_result["cards"][0]
        assert "Text Question" in card["front"]
        card_id = card["cardId"]

        # Should succeed since image card was buried
        rate_result = call_tool("rate_card", {
            "card_id": card_id,
            "rating": 3
        })

        # Should succeed without error
        assert rate_result.get("isError") is not True, (
            f"rate_card failed after skip_images: {rate_result.get('error')}"
        )

    def test_rate_card_after_skip_audio(self):
        """REGRESSION TEST: Verifies rate_card works after skipping audio.

        Same as skip_images test, but with audio cards.
        """
        uid = unique_id()
        deck_name = f"E2E::SkipAudioRate{uid}"

        # Create deck
        call_tool("create_deck", {"deck_name": deck_name})

        # Add audio card FIRST (will be at top of scheduler queue)
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"[sound:test.mp3]Audio Question {uid}",
                "Back": f"Audio Answer {uid}"
            }
        })

        # Add text card SECOND
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Text Question {uid}",
                "Back": f"Text Answer {uid}"
            }
        })

        # Get due cards with skip_audio=True
        get_result = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "skip_audio": True
        })

        # Verify we got the text card
        assert get_result.get("isError") is not True
        assert len(get_result["cards"]) == 1
        card = get_result["cards"][0]
        assert "Text Question" in card["front"]
        card_id = card["cardId"]

        # Should succeed since audio card was buried
        rate_result = call_tool("rate_card", {
            "card_id": card_id,
            "rating": 3
        })

        # Should succeed without error
        assert rate_result.get("isError") is not True, (
            f"rate_card failed after skip_audio: {rate_result.get('error')}"
        )

    def test_rate_card_without_skip_works(self):
        """Control test: rate_card works normally without skip flags.

        Verifies rate_card works fine when get_due_cards doesn't skip any cards.
        """
        uid = unique_id()
        deck_name = f"E2E::NoSkipRate{uid}"

        # Create deck
        call_tool("create_deck", {"deck_name": deck_name})

        # Add a simple text card
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Question {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Get due cards WITHOUT skip flags
        get_result = call_tool("get_due_cards", {
            "deck_name": deck_name
        })

        # Verify we got the card
        assert get_result.get("isError") is not True
        assert len(get_result["cards"]) == 1
        card_id = get_result["cards"][0]["cardId"]

        # This should work fine (control test)
        rate_result = call_tool("rate_card", {
            "card_id": card_id,
            "rating": 3
        })

        # Should succeed
        assert rate_result.get("isError") is not True

    def test_rate_card_after_skip_both(self):
        """REGRESSION TEST: Verifies rate_card works after skipping both media types.

        Tests the fix when both skip_images and skip_audio are used together.
        """
        uid = unique_id()
        deck_name = f"E2E::SkipBothRate{uid}"

        # Create deck
        call_tool("create_deck", {"deck_name": deck_name})

        # Add image card FIRST
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"<img src='img.jpg'>Image {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Add audio card SECOND
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"[sound:audio.mp3]Audio {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Add text card THIRD
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Text Question {uid}",
                "Back": f"Text Answer {uid}"
            }
        })

        # Get due cards with both skip flags
        get_result = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "skip_images": True,
            "skip_audio": True
        })

        # Verify we got the text card (skipped 2 media cards)
        assert get_result.get("isError") is not True
        assert len(get_result["cards"]) == 1
        card = get_result["cards"][0]
        assert "Text Question" in card["front"]
        card_id = card["cardId"]

        # Should succeed since both media cards were buried
        rate_result = call_tool("rate_card", {
            "card_id": card_id,
            "rating": 3
        })

        # Should succeed without error
        assert rate_result.get("isError") is not True, (
            f"rate_card failed after skip_both: {rate_result.get('error')}"
        )


class TestSkipMediaFullWorkflow:
    """Full workflow tests for skip media + rate + unbury functionality.

    These tests reproduce the exact workflow that users perform:
    1. Skip media cards (get buried)
    2. Rate text cards
    3. Unbury media cards when done with text-only review
    """

    def test_full_review_session_with_skip_and_unbury(self):
        """FULL WORKFLOW: Skip images → rate cards → unbury → images are back.

        This reproduces the exact workflow from LouisO's use case:
        - User wants to review only text cards (no images in voice mode)
        - Uses skip_images to filter them out
        - Reviews and rates text cards successfully
        - When done, unburys to restore image cards
        """
        uid = unique_id()
        deck_name = f"E2E::FullWorkflow{uid}"

        # Create deck
        call_tool("create_deck", {"deck_name": deck_name})

        # Add 2 image cards FIRST (will be at top of queue)
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"<img src='img1.jpg'>Image Question 1 {uid}",
                "Back": f"Image Answer 1 {uid}"
            }
        })
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"<img src='img2.jpg'>Image Question 2 {uid}",
                "Back": f"Image Answer 2 {uid}"
            }
        })

        # Add 2 text cards
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Text Question 1 {uid}",
                "Back": f"Text Answer 1 {uid}"
            }
        })
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Text Question 2 {uid}",
                "Back": f"Text Answer 2 {uid}"
            }
        })

        # Step 1: Get first text card (skip_images buries the 2 image cards)
        get_result1 = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "skip_images": True
        })
        assert get_result1.get("isError") is not True
        assert len(get_result1["cards"]) == 1
        card1 = get_result1["cards"][0]
        assert "Text Question" in card1["front"]
        card1_id = card1["cardId"]

        # Step 2: Rate first text card - should succeed!
        rate_result1 = call_tool("rate_card", {
            "card_id": card1_id,
            "rating": 4
        })
        assert rate_result1.get("isError") is not True, (
            f"rate_card failed on first text card: {rate_result1.get('error')}"
        )

        # Step 3: Get second text card (skip_images still active)
        get_result2 = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "skip_images": True
        })
        assert get_result2.get("isError") is not True
        assert len(get_result2["cards"]) == 1
        card2 = get_result2["cards"][0]
        assert "Text Question" in card2["front"]
        card2_id = card2["cardId"]

        # Step 4: Rate second text card - should succeed!
        rate_result2 = call_tool("rate_card", {
            "card_id": card2_id,
            "rating": 4
        })
        assert rate_result2.get("isError") is not True, (
            f"rate_card failed on second text card: {rate_result2.get('error')}"
        )

        # Step 5: No more text cards - get_due_cards should return empty
        get_result3 = call_tool("get_due_cards", {
            "deck_name": deck_name,
            "skip_images": True
        })
        assert get_result3.get("isError") is not True
        assert len(get_result3["cards"]) == 0, (
            "Expected no more text cards, but got some"
        )

        # Step 6: Unbury to restore image cards
        unbury_result = call_tool("card_management", {
            "params": {
                "action": "unbury",
                "deck_name": deck_name
            }
        })
        assert unbury_result.get("isError") is not True

        # Step 7: Image cards are back! (no skip flag now)
        get_result4 = call_tool("get_due_cards", {
            "deck_name": deck_name
        })
        assert get_result4.get("isError") is not True
        assert len(get_result4["cards"]) == 1  # One image card at a time
        card_image = get_result4["cards"][0]
        assert "Image Question" in card_image["front"], (
            "Expected image card after unbury, but got something else"
        )

    def test_get_due_cards_has_media_flags(self):
        """get_due_cards should return has_images and has_audio flags.

        These flags help clients decide whether to skip a card without
        parsing HTML themselves.
        """
        uid = unique_id()
        deck_name = f"E2E::MediaFlags{uid}"

        # Create deck
        call_tool("create_deck", {"deck_name": deck_name})

        # Add an image card
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"<img src='test.jpg'>Image {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Add a text card
        call_tool("addNote", {
            "deck_name": deck_name,
            "model_name": "Basic",
            "fields": {
                "Front": f"Text Only {uid}",
                "Back": f"Answer {uid}"
            }
        })

        # Get first card (should be image card)
        get_result1 = call_tool("get_due_cards", {
            "deck_name": deck_name
        })
        assert get_result1.get("isError") is not True
        assert len(get_result1["cards"]) == 1
        card1 = get_result1["cards"][0]

        # Verify media flags exist
        assert "has_images" in card1, "Card should have has_images field"
        assert "has_audio" in card1, "Card should have has_audio field"
        assert isinstance(card1["has_images"], bool), "has_images should be boolean"
        assert isinstance(card1["has_audio"], bool), "has_audio should be boolean"

        # If this is the image card, verify flags are correct
        if "Image" in card1["front"]:
            assert card1["has_images"] is True, "Image card should have has_images=True"
            assert card1["has_audio"] is False, "Image card without audio should have has_audio=False"
