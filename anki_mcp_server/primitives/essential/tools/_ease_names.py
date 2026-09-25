# {ease: name} for Anki's 4 answer buttons. col.sched.answerButtons(card) is
# hardcoded to `return 4` in anki/scheduler/legacy.py's SchedulerBase (used by
# the mandatory V3 scheduler as of Anki 25.07), so this is the only shape the
# reviewer ever shows.
EASE_NAMES: dict[int, str] = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}


def ease_name(ease: int) -> str:
    """Return the display name for ``ease`` (e.g. "Good").

    Raises:
        ValueError: ``ease`` is not 1-4. Callers that already validate ease
            against col.sched.answerButtons(card) before calling this (e.g.
            gui_answer_card) will never hit this path.
    """
    try:
        return EASE_NAMES[ease]
    except KeyError:
        raise ValueError(
            f"ease {ease} is out of range for a card with {len(EASE_NAMES)} answer buttons"
        )
