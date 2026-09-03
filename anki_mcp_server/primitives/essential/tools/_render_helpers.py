import re
from typing import Any

# Anki's question-side cloze render (rslib/src/cloze.rs) embeds the deleted
# text verbatim in a data-cloze="..." attribute on the <span class="cloze">
# wrapping the deletion. Rendering alone does NOT hide the answer - the
# attribute must be stripped before the question is handed to an AI client.
_CLOZE_ANSWER_ATTR = re.compile(r'\s+data-cloze="[^"]*"')


def render_question(card: Any) -> str:
    """Rendered question without the note type CSS, with the cloze answer stripped."""
    return _CLOZE_ANSWER_ATTR.sub("", card.render_output().question_text)


def render_question_with_style(card: Any) -> str:
    """Rendered question including the note type CSS, with the cloze answer stripped."""
    return _CLOZE_ANSWER_ATTR.sub("", card.question())


def render_answer(card: Any) -> str:
    """Rendered answer. No stripping needed - kept here so both tools render
    through one module."""
    return card.answer()
