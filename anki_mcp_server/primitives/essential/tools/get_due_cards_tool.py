from typing import Any
import logging

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col

logger = logging.getLogger(__name__)


def _has_images(fields: list[str]) -> bool:
    """Check if any field contains image tags."""
    return any('<img ' in field.lower() or '<img>' in field.lower() for field in fields)


def _has_audio(fields: list[str]) -> bool:
    """Check if any field contains audio references."""
    return any('[sound:' in field.lower() for field in fields)


@Tool(
    "get_due_cards",
    "Retrieve the next single card due for review from a specified deck in true scheduler order. IMPORTANT: Use sync tool FIRST before getting cards to ensure latest data. After getting the card, use present_card to show it to the user. Returns one card per call to ensure correct scheduler interleaving. The deck_name parameter is required - you must specify which deck to study. For voice-mode review, use skip_images=True and/or skip_audio=True to filter out cards with media. Cards with media are temporarily buried (removed from queue) and can be unburied later using the card_management tool. Response includes has_images and has_audio flags for each card.",
    write=True,
)
def get_due_cards(
    deck_name: str,
    skip_images: bool = False,
    skip_audio: bool = False
) -> dict[str, Any]:
    from anki.consts import QUEUE_TYPE_NEW, QUEUE_TYPE_LRN, QUEUE_TYPE_REV

    col = get_col()

    # Select the specified deck
    deck = col.decks.by_name(deck_name)
    if not deck:
        raise HandlerError(
            f"Deck '{deck_name}' not found",
            hint="Check spelling or use list_decks to see available decks",
            deck_name=deck_name
        )
    col.decks.select(deck["id"])

    # Map queue type integers to human-readable names
    queue_names = {
        QUEUE_TYPE_NEW: "new",
        QUEUE_TYPE_LRN: "learning",
        QUEUE_TYPE_REV: "review"
    }

    # Track skipped cards when filtering
    skipped = {"images": 0, "audio": 0}

    # Safety limit to prevent infinite loops
    MAX_ITERATIONS = 100
    iteration = 0

    # Loop: fetch 1 card, check if it should be skipped, bury if needed, repeat
    found_card = None
    queued = None

    while iteration < MAX_ITERATIONS:
        iteration += 1

        # Fetch the next top card from the queue
        try:
            queued = col.sched.get_queued_cards(fetch_limit=1)
        except Exception as e:
            raise HandlerError(
                f"Failed to retrieve queued cards: {str(e)}",
                hint="Ensure a profile is open and the deck has due cards"
            )

        # If no cards are due, break
        if not queued.cards:
            break

        qc = queued.cards[0]

        try:
            # Get full card and note objects
            card = col.get_card(qc.card.id)
            note = card.note()

            # Check if card should be skipped
            fields = list(note.fields)
            should_skip = False

            if skip_images and _has_images(fields):
                skipped["images"] += 1
                should_skip = True
            if skip_audio and _has_audio(fields):
                skipped["audio"] += 1
                should_skip = True

            if should_skip:
                # Bury this card to remove it from the queue
                col.sched.bury_cards([card.id], manual=True)
                # Loop back to fetch the next card
                continue

            # Found a card that passes filters
            # Extract front/back from note fields
            fields_dict = dict(note.items())
            front = fields_dict.get("Front", "")
            back = fields_dict.get("Back", "")

            # Fallback: if no Front/Back fields, use first two fields
            if not front and not back:
                field_values = list(fields_dict.values())
                front = field_values[0] if len(field_values) > 0 else ""
                back = field_values[1] if len(field_values) > 1 else ""

            # Get deck name
            deck = col.decks.get(card.did)
            deck_name_str = deck["name"] if deck else "Unknown"

            # Get model name
            model = note.note_type()
            model_name = model["name"] if model else "Unknown"

            # Get queue type name
            queue_type = queue_names.get(qc.queue, "unknown")

            # Always include has_images and has_audio flags
            has_images = _has_images(fields)
            has_audio = _has_audio(fields)

            found_card = {
                "cardId": card.id,
                "front": front,
                "back": back,
                "deckName": deck_name_str,
                "modelName": model_name,
                "queueType": queue_type,
                "due": card.due,
                "interval": card.ivl,
                "factor": card.factor,
                "has_images": has_images,
                "has_audio": has_audio,
            }
            break

        except Exception as e:
            logger.warning(f"Could not retrieve card {qc.card.id}: {e}")
            try:
                col.sched.bury_cards([qc.card.id], manual=True)
            except Exception:
                pass  # if even burying fails, safety limit will catch us
            continue

    # If queued is None, we never successfully called get_queued_cards
    if queued is None:
        raise HandlerError(
            "Failed to retrieve any queued cards",
            hint="Ensure a profile is open and the deck has due cards"
        )

    # Calculate total cards available across all queues
    total_due = queued.new_count + queued.learning_count + queued.review_count

    # Build the cards list
    due_cards = [found_card] if found_card else []

    # Build response
    response = {
        "cards": due_cards,
        "counts": {
            "new": queued.new_count,
            "learning": queued.learning_count,
            "review": queued.review_count
        },
        "total": total_due,
        "returned": len(due_cards),
    }

    # Add skipped counts when filtering is active
    if skip_images or skip_audio:
        response["skipped"] = skipped
        if len(due_cards) == 0:
            if sum(skipped.values()) > 0:
                if iteration >= MAX_ITERATIONS:
                    response["message"] = f"Reached safety limit ({MAX_ITERATIONS} cards checked). All checked cards contain media. Use card_management to unbury cards or try without filters."
                else:
                    response["message"] = "All cards contain media. Use card_management to unbury cards or try without filters."
            else:
                response["message"] = "No cards are due for review."
        else:
            response["message"] = f"Next card in scheduler order (total {total_due} due cards, buried {sum(skipped.values())} cards with media)"
    else:
        if len(due_cards) == 0:
            response["message"] = "No cards are due for review."
        else:
            response["message"] = f"Next card in scheduler order (total {total_due} due cards)"

    return response
