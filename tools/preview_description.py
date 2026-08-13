#!/usr/bin/env python3
"""Render the AnkiWeb description as a page you can actually open in a browser.

Why this script exists
----------------------
``ankiweb-description.html`` is deliberately a bare HTML *fragment*. AnkiWeb
drops it into its own page shell, so it must NOT carry a doctype, a ``<head>``
or a charset of its own. That makes it unpleasant to preview by hand:

* Opened straight from disk it has no charset, so the browser guesses the
  encoding and every em dash renders as mojibake.
* The hero image points at ``raw.githubusercontent.com`` and only resolves once
  the GIF has been pushed to ``main`` -- so locally it just 404s.

This script fixes both: it splices the fragment into a minimal page shell
(``docs/ankiweb-preview.template.html``, which supplies the charset and some
listing-ish styling) and repoints every image at the local copy under
``docs/images/``. What you see is what the listing will show.

Usage::

    make preview-description        # or
    python3 tools/preview_description.py

The output is gitignored; the description fragment is never modified.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DESCRIPTION = ROOT / "ankiweb-description.html"
TEMPLATE = ROOT / "docs" / "ankiweb-preview.template.html"
OUTPUT = ROOT / "ankiweb-description.preview.html"

# Where the local copies of the published images live, relative to ROOT (which
# is also where OUTPUT sits, so the browser resolves these correctly).
IMAGE_DIR = "docs/images"

# The placeholder in the template that the fragment replaces.
MARKER = "<!-- DESCRIPTION -->"

# An image src, capturing just its filename. Matching on the filename alone is
# deliberate: the published URL's host, repo and branch are none of this
# script's business, and hardcoding them here would rot the moment any of them
# changes.
IMAGE_SRC = re.compile(r'src="[^"]*/([^"/]+\.(?:gif|png|jpe?g|svg|webp))"', re.IGNORECASE)

ANY_SRC = re.compile(r'src="([^"]+)"')

HTML_TAG = re.compile(r"<[^>]*>")


def use_local_images(page: str) -> str:
    """Repoint every image at its local copy under ``docs/images/``."""
    return IMAGE_SRC.sub(lambda match: f'src="{IMAGE_DIR}/{match.group(1)}"', page)


def warn_about_missing_images(page: str) -> None:
    """Warn -- but don't fail -- about local images that aren't on disk.

    A preview is still worth opening with a broken image in it; you just want
    to know the gap is yours and not the browser's.
    """
    for src in ANY_SRC.findall(page):
        if "://" in src or src.startswith("data:"):
            continue  # Still remote -- nothing to look for on disk.
        if not (ROOT / src).exists():
            print(f"warning: preview references a missing image: {src}")


def count_words(fragment: str) -> int:
    """Count the words a reader sees, i.e. with the markup stripped out."""
    return len(html.unescape(HTML_TAG.sub("", fragment)).split())


def show_in_browser(path: Path) -> None:
    """Open the preview, or say how to, if the platform has no ``open``."""
    opener = shutil.which("open")
    if opener:
        subprocess.run([opener, str(path)], check=False)
    else:
        print(f"Open {path} in a browser.")


def main() -> None:
    fragment = DESCRIPTION.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")
    if MARKER not in template:
        raise SystemExit(f"{TEMPLATE} no longer contains its {MARKER} marker")

    page = use_local_images(template.replace(MARKER, fragment))
    OUTPUT.write_text(page, encoding="utf-8")

    warn_about_missing_images(page)
    print(f"Wrote {OUTPUT} ({count_words(fragment)} words)")
    show_in_browser(OUTPUT)


if __name__ == "__main__":
    main()
