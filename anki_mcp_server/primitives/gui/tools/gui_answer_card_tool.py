from typing import Any

from ....tool_decorator import Tool
from ....handler_wrappers import HandlerError, get_col
from ...essential.tools._ease_names import ease_name


@Tool(
    "gui_answer_card",
    "Press an answer button (1=Again, 2=Hard, 3=Good, 4=Easy) on the card currently shown "
    "in Anki's own reviewer, advancing it exactly like a real button press. Only use this "
    "when the user has explicitly asked for hands-free rating while reviewing in Anki's "
    "window (a voice/hands-free session) -- by default the USER presses the answer buttons "
    "themselves. Preconditions: the answer must already be on screen (call gui_show_answer "
    "first) and the user must have confirmed the rating; never call this to rate a card on "
    "the user's behalf without confirmation, and never use rate_card for a card shown in "
    "the reviewer -- rate_card bypasses the reviewer and leaves it desynced, still showing "
    "the same card. This tool does not wait for Anki to finish advancing the reviewer -- it "
    "returns immediately with pending=True; call gui_current_card afterwards to see the next "
    "card (it reports advancing=true while Anki is still transitioning).",
    # Deliberately write=False even though this mutates the collection:
    # answer_card is already its own CollectionOp with its own undo entry, and
    # write=True's _write_lock would call mw.reset() right after this handler
    # returns -- while the reviewer is still mid-"transition" from the async
    # op _answerCard() just kicked off. That reset() synthesizes an
    # operation_did_execute with everything marked changed, which sets
    # reviewer._refresh_needed=QUEUES and rebuilds the queue underneath the
    # in-flight op. write is otherwise reserved for a future readOnlyHint
    # (see tool_decorator.py) -- don't flip this to True later without
    # re-checking that reasoning.
    write=False,
)
def gui_answer_card(ease: int) -> dict[str, Any]:
    from aqt import mw

    col = get_col()

    if not mw.reviewer or not mw.reviewer.card or mw.state != "review":
        return {
            "success": True,
            "inReview": False,
            "message": "Not in review mode - no card to answer",
            "hint": "Use gui_deck_review to start reviewing a deck first.",
        }

    if mw.reviewer.state != "answer":
        raise HandlerError(
            "The answer is not on screen yet",
            hint="Call gui_show_answer first, then confirm the rating with the user "
            "before calling gui_answer_card.",
        )

    card = mw.reviewer.card
    max_ease = col.sched.answerButtons(card)
    if ease < 1 or ease > max_ease:
        raise HandlerError(
            f"Invalid ease: {ease}. This card accepts 1-{max_ease}",
            hint=f"Use gui_current_card to see this card's buttons; ease must be between "
            f"1 and {max_ease} for the card currently on screen",
        )

    answered_card_id = card.id
    # Computed BEFORE _answerCard() mutates the card -- see _ease_names.py.
    ease_display_name = ease_name(col, card, ease)

    # reviewer._answerCard() reschedules and advances via Anki's own async
    # CollectionOp -- it returns before the op completes. Handlers on the main
    # thread must return fast (see _sync_runner.py), so this does not wait or
    # pump the Qt event loop for it. A nested run_on_main flush triggered from
    # inside that op can't run another handler here either way -- the
    # _draining guard in request_processor.py makes it a no-op -- but a
    # modal dialog (e.g. a timebox prompt) could still block, so this stays
    # fire-and-forget. gui_current_card's advancing flag is how a caller
    # observes completion instead.
    mw.reviewer._answerCard(ease)

    # _answerCard has a third early return this tool can't pre-check: the
    # reviewer_will_answer_card gui hook can veto (another add-on rewrites or
    # rejects the ease). It returns None either way, but self.state is set to
    # "transition" synchronously before the async CollectionOp -- if that
    # didn't happen, nothing was accepted.
    if mw.reviewer.state != "transition":
        raise HandlerError(
            "Anki did not accept the rating",
            hint="Another add-on's reviewer_will_answer_card hook rejected it. "
            "Call gui_current_card to confirm the card on screen is unchanged.",
        )

    return {
        "success": True,
        "answeredCardId": answered_card_id,
        "ease": ease,
        "easeName": ease_display_name,
        "pending": True,
        "message": "Rating recorded. Anki is advancing the reviewer.",
        "hint": "Call gui_current_card for the next card. If it returns the same cardId "
        "with advancing=true, call it again.",
    }
