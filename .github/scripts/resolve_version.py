#!/usr/bin/env python3
"""Resolve the addon's version from ``__version__``, the single source of truth.

Shared by every workflow that needs to know "what version is this repo at":
the release job, the separate registry-publish job, and the scheduled registry
drift check. Keeping the derivation in one file means those three can never
disagree about the answer -- a disagreement is exactly the class of bug that
let ``server.json`` sit at a stale 0.19.0 while releases reached 0.27.0.

Usage::

    resolve_version.py                      # print the version
    resolve_version.py --expect-tag v1.2.3  # ...and fail unless the tag matches

The version is written to ``$GITHUB_OUTPUT`` as ``version=<value>`` when that
variable is set, so callers can consume it as a step output.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

# Relative to the repository root, which is the working directory GitHub
# Actions gives every `run` step.
INIT_PATH = pathlib.Path("anki_mcp_server/__init__.py")

VERSION_RE = re.compile(r'^__version__ = "([^"]+)"', re.M)


def read_version(path: pathlib.Path) -> str:
    """Extract ``__version__`` from the addon's package init.

    Args:
        path: Path to ``anki_mcp_server/__init__.py``.

    Returns:
        The version string.

    Raises:
        SystemExit: If the file is unreadable or holds no ``__version__``.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        sys.exit(f"::error::Could not read {path}: {exc}")

    match = VERSION_RE.search(source)
    if match is None:
        sys.exit(
            f'::error::Could not find `__version__ = "..."` in {path} -- every '
            "release version is derived from it, so nothing can continue"
        )
    return match.group(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect-tag",
        default=None,
        help=(
            "Require the resolved version to equal this tag minus its leading "
            "'v'. Used by the tag-triggered release jobs."
        ),
    )
    parser.add_argument(
        "--init-path",
        type=pathlib.Path,
        default=INIT_PATH,
        help=f"Path to the package init to read (default: {INIT_PATH}).",
    )
    args = parser.parse_args(argv)

    version = read_version(args.init_path)

    if args.expect_tag is not None and args.expect_tag != f"v{version}":
        sys.exit(
            f"::error::Tag {args.expect_tag} does not match __version__ "
            f"{version} -- bump {args.init_path} or retag"
        )

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"version={version}\n")

    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
