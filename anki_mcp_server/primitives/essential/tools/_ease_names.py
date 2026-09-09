from typing import Any

# Button-count -> {ease: name} mappings. Verified against the local Anki
# install's aqt/reviewer.py Reviewer._answerButtonList(): it branches on
# col.sched.answerButtons(card) and returns exactly these three shapes (2/3/4
# buttons), dropping Hard and/or Easy for cards with fewer review options.
# The 2- and 3-button cases are kept even though the SAME install's
# anki/scheduler/legacy.py SchedulerBase.answerButtons() (used by the current
# V3 scheduler) is hardcoded `return 4` -- so col.sched.answerButtons(card) is
# effectively always 4 today, and _answerButtonList()'s 2/3-button branches
# are themselves dead code on this Anki version. answerButtons() is the
# documented contract this helper follows, not "always 4" -- if a future
# scheduler (or an add-on overriding it) ever returns 2 or 3, this already
# matches Anki's own reviewer labels instead of silently mislabeling.
_TWO_BUTTON_NAMES = {1: "Again", 2: "Good"}
_THREE_BUTTON_NAMES = {1: "Again", 2: "Good", 3: "Easy"}
_FOUR_BUTTON_NAMES = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}


def ease_names(col: Any, card: Any) -> dict[int, str]:
    """Return the {ease: name} mapping for the answer buttons Anki would show
    for ``card`` right now (``col.sched.answerButtons(card)``).

    Call this BEFORE any operation that mutates ``card`` (e.g.
    ``col.sched.answerCard``) -- the button count reflects the card's current
    queue/type, so evaluating it after the card has already been answered
    would describe the wrong state.
    """
    button_count = col.sched.answerButtons(card)
    if button_count == 2:
        return _TWO_BUTTON_NAMES
    if button_count == 3:
        return _THREE_BUTTON_NAMES
    return _FOUR_BUTTON_NAMES


def ease_name(col: Any, card: Any, ease: int) -> str:
    """Return the display name for ``ease`` on ``card`` (e.g. "Good").

    Raises:
        ValueError: ``ease`` is not a valid button position for this card's
            current button count. Callers that already validate ease against
            col.sched.answerButtons(card) before calling this (e.g.
            gui_answer_card) will never hit this path.
    """
    names = ease_names(col, card)
    try:
        return names[ease]
    except KeyError:
        raise ValueError(
            f"ease {ease} is out of range for a card with {len(names)} answer buttons"
        )
