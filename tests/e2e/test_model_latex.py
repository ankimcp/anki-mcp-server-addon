"""Tests for the opt-in LaTeX preamble on model_styling / update_model_styling.

A note type carries a LaTeX header/footer (``latexPre`` / ``latexPost``) and an
SVG-rendering flag (``latexsvg``) that Anki wraps around ``[latex]`` and
``[$]...[/$]`` blocks. These are exposed read-side behind ``include_latex`` and
write-side behind the optional ``latex_pre`` / ``latex_post`` / ``latex_svg``
params.

The key guarantee under test is that the feature is strictly OPT-IN: a plain
``model_styling`` call must return exactly what it always returned, with no
latex keys added. The default-value assertions double as a check that the addon
uses Anki's real notetype keys ("latexPre"/"latexPost"/"latexsvg" -- note the
inconsistent casing), since a wrong key would silently read back as empty.

Models are DISPOSABLE and uniquely named so the shared "Basic" model is never
dirtied; originals are still captured and restored in a ``finally`` so a failed
assertion mid-test cannot leave a half-written preamble behind.

Strings passed as tool args deliberately avoid '=' characters -- the Inspector
CLI passes tool args as ``key=value`` pairs (this rules out Anki's stock
``\\special{papersize...}`` line, which is why the fixtures below are trimmed).
"""
from __future__ import annotations

from .conftest import unique_id
from .helpers import call_tool

LATEX_KEYS = ("latex_pre", "latex_post", "latex_svg")

# A single-line preamble with the TikZ package added -- the canonical reason a
# user ever touches these fields.
TIKZ_PRE = (
    "\\documentclass[12pt]{article} \\usepackage{tikz} \\begin{document}"
)
CUSTOM_POST = "\\end{document} % patched"

BASE_CSS = ".card { color: blue; }"


def _create_model() -> str:
    """Create a disposable model with stock LaTeX settings."""
    model_name = f"LatexModel{unique_id()}"
    result = call_tool("create_model", {
        "model_name": model_name,
        "in_order_fields": ["Front", "Back"],
        "card_templates": [
            {
                "Name": "Card 1",
                "Front": "{{Front}}",
                "Back": "{{FrontSide}}<hr>{{Back}}",
            },
        ],
        "css": BASE_CSS,
    })
    assert result.get("isError") is not True, f"create_model failed: {result}"
    return model_name


def _read_latex(model_name: str) -> dict:
    result = call_tool("model_styling", {
        "model_name": model_name,
        "include_latex": True,
    })
    assert result.get("isError") is not True, f"model_styling failed: {result}"
    return result


def _restore_latex(model_name: str, original: dict) -> None:
    """Put the model's LaTeX preamble back the way it was found."""
    try:
        call_tool("update_model_styling", {
            "model_name": model_name,
            "latex_pre": original["latex_pre"],
            "latex_post": original["latex_post"],
            "latex_svg": original["latex_svg"],
        })
    except Exception as e:  # pragma: no cover - cleanup must never mask a failure
        print(f"cleanup failed: could not restore latex for {model_name}: {e}")


class TestModelStylingLatexRead:
    """Read side: include_latex is strictly opt-in."""

    def test_default_response_has_no_latex_keys(self):
        """Without include_latex the response must be unchanged from before."""
        model_name = _create_model()

        result = call_tool("model_styling", {"model_name": model_name})

        assert result.get("isError") is not True, f"Unexpected error: {result}"
        for key in LATEX_KEYS:
            assert key not in result, (
                f"'{key}' leaked into the default model_styling response"
            )
        # The pre-existing payload is untouched.
        assert result["css"] == BASE_CSS
        assert result["css_info"]["length"] == len(BASE_CSS)

    def test_include_latex_returns_three_fields(self):
        """include_latex=true adds latex_pre, latex_post and latex_svg."""
        model_name = _create_model()

        result = _read_latex(model_name)

        for key in LATEX_KEYS:
            assert key in result, f"'{key}' missing from include_latex response"
        assert isinstance(result["latex_pre"], str)
        assert isinstance(result["latex_post"], str)
        assert isinstance(result["latex_svg"], bool)

        # Stock values from Anki's default notetype. If the addon read the wrong
        # dict key these would come back empty instead.
        assert "documentclass" in result["latex_pre"]
        assert "begin{document}" in result["latex_pre"]
        assert "end{document}" in result["latex_post"]
        assert result["latex_svg"] is False

        # include_latex must not disturb the rest of the response.
        assert result["css"] == BASE_CSS


class TestModelStylingLatexWrite:
    """Write side: only the passed latex fields are written."""

    def test_write_latex_pre_round_trips(self):
        """A written latex_pre must persist and read back exactly."""
        model_name = _create_model()
        original = _read_latex(model_name)

        try:
            result = call_tool("update_model_styling", {
                "model_name": model_name,
                "latex_pre": TIKZ_PRE,
            })

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["updated_latex_fields"] == ["latex_pre"]

            after = _read_latex(model_name)
            assert after["latex_pre"] == TIKZ_PRE
            # Fields that were not passed must be left alone.
            assert after["latex_post"] == original["latex_post"]
            assert after["latex_svg"] == original["latex_svg"]
            # A latex-only write must not touch the CSS.
            assert after["css"] == BASE_CSS
        finally:
            _restore_latex(model_name, original)

    def test_write_all_three_fields(self):
        """latex_pre, latex_post and latex_svg can be written together."""
        model_name = _create_model()
        original = _read_latex(model_name)

        try:
            result = call_tool("update_model_styling", {
                "model_name": model_name,
                "latex_pre": TIKZ_PRE,
                "latex_post": CUSTOM_POST,
                "latex_svg": True,
            })

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert set(result["updated_latex_fields"]) == set(LATEX_KEYS)

            after = _read_latex(model_name)
            assert after["latex_pre"] == TIKZ_PRE
            assert after["latex_post"] == CUSTOM_POST
            assert after["latex_svg"] is True
        finally:
            _restore_latex(model_name, original)

    def test_latex_combines_with_css_replace(self):
        """A latex write can ride along with a full CSS replace."""
        model_name = _create_model()
        original = _read_latex(model_name)
        new_css = ".card { color: green; }"

        try:
            result = call_tool("update_model_styling", {
                "model_name": model_name,
                "css": new_css,
                "latex_pre": TIKZ_PRE,
            })

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["css_length"] == len(new_css)
            assert result["updated_latex_fields"] == ["latex_pre"]

            after = _read_latex(model_name)
            assert after["css"] == new_css
            assert after["latex_pre"] == TIKZ_PRE
        finally:
            _restore_latex(model_name, original)

    def test_latex_combines_with_css_patch(self):
        """A latex write can ride along with a CSS patch."""
        model_name = _create_model()
        original = _read_latex(model_name)

        try:
            result = call_tool("update_model_styling", {
                "model_name": model_name,
                "old_str": "color: blue",
                "new_str": "color: red",
                "latex_post": CUSTOM_POST,
            })

            assert result.get("isError") is not True, f"Unexpected error: {result}"
            assert result["patched"] is True
            assert result["updated_latex_fields"] == ["latex_post"]

            after = _read_latex(model_name)
            assert after["css"] == ".card { color: red; }"
            assert after["latex_post"] == CUSTOM_POST
            assert after["latex_pre"] == original["latex_pre"]
        finally:
            _restore_latex(model_name, original)

    def test_failed_css_patch_does_not_write_latex(self):
        """A rejected CSS patch must abort the whole call, latex fields included."""
        model_name = _create_model()
        original = _read_latex(model_name)

        try:
            result = call_tool("update_model_styling", {
                "model_name": model_name,
                "old_str": "color: magenta",
                "new_str": "color: red",
                "latex_pre": TIKZ_PRE,
            })

            assert result.get("isError") is True, f"Expected an error, got: {result}"

            after = _read_latex(model_name)
            assert after["css"] == BASE_CSS
            assert after["latex_pre"] == original["latex_pre"]
        finally:
            _restore_latex(model_name, original)
