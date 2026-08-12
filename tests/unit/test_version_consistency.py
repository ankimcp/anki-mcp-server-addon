"""Unit tests guarding the version constants this repo duplicates across files.

Four separate constants live in more than one place, each protected today by
nothing stronger than a "MUST stay in sync" comment:

1. The addon version -- ``__version__`` (the single source of truth) vs the
   hardcoded ``version`` in ``flake.nix``.
2. ``server.json``'s ``version``, which must stay the CI sentinel rather than be
   hand-synced to a real release number.
3. The MCP Inspector pin -- two ``Makefile`` assignments plus
   ``INSPECTOR_DEFAULT_VERSION`` in ``tests/e2e/helpers.py``.
4. The headless-anki Docker image tag -- two compose files plus two workflows.

These are pure file-reading and string-comparison checks: no Anki, no vendored
packages, no network. That is why they live here rather than in ``tests/e2e``,
where they would only run behind a ~20-minute Docker cycle. The unit workflow
runs on every push to every branch, which is exactly when drift should surface.

The addon version is read by *reusing* ``.github/scripts/resolve_version.py``
rather than re-implementing its parser. That script is deliberately shared by
every workflow that needs the version "so they cannot disagree"; a private
regex here would quietly become a fifth source of truth.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repo layout
#
# Derived from this file's own location, never from the working directory --
# pytest may be invoked from anywhere (``pytest tests/unit``, ``make unit``, an
# IDE runner) and cwd is not a reliable anchor.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]

INIT_PY = "anki_mcp_server/__init__.py"
RESOLVE_VERSION_PY = ".github/scripts/resolve_version.py"
FLAKE_NIX = "flake.nix"
SERVER_JSON = "server.json"
MAKEFILE = "Makefile"
E2E_HELPERS = "tests/e2e/helpers.py"

DOCKER_IMAGE_FILES = (
    ".docker/docker-compose.yml",
    ".docker/docker-compose.filtered.yml",
    ".github/workflows/e2e.yml",
    ".github/workflows/release.yml",
)

# ``server.json``'s schema makes ``version`` required, so the file carries this
# placeholder and the ``registry`` release job overwrites it from
# ``__version__`` at publish time.
SERVER_JSON_SENTINEL = "0.0.0-set-by-ci"


# ---------------------------------------------------------------------------
# File-reading helpers
#
# Every one of these fails loudly when the file or the expected line is gone. A
# check that silently no-ops after a rename is worse than no check at all: it
# reports green while guarding nothing.
# ---------------------------------------------------------------------------
def _read(rel_path: str) -> str:
    """Read a repo-relative text file, failing the test if it is missing."""
    path = REPO_ROOT / rel_path
    if not path.is_file():
        pytest.fail(
            f"{rel_path} does not exist (looked in {path}). "
            f"This test pins a version constant that lives in that file -- if "
            f"the file was renamed or moved, update the path constants at the "
            f"top of tests/unit/test_version_consistency.py so the check keeps "
            f"guarding something."
        )
    return path.read_text(encoding="utf-8")


def _find_all(rel_path: str, pattern: re.Pattern[str], what: str) -> list[str]:
    """Return every capture of ``pattern`` in a file; fail if there are none."""
    matches = pattern.findall(_read(rel_path))
    if not matches:
        pytest.fail(
            f"Could not find {what} in {rel_path} (pattern: {pattern.pattern!r}). "
            f"Either the line was removed -- in which case this check is now "
            f"guarding nothing and should be deleted deliberately -- or it was "
            f"reformatted, in which case update the pattern in "
            f"tests/unit/test_version_consistency.py."
        )
    return matches


def _find_one(rel_path: str, pattern: re.Pattern[str], what: str) -> str:
    """Return the single capture of ``pattern`` in a file.

    More than one match means the pattern got looser than intended (or the
    constant was genuinely duplicated *within* one file), so both cases fail.
    """
    matches = _find_all(rel_path, pattern, what)
    if len(matches) > 1:
        pytest.fail(
            f"Expected exactly one {what} in {rel_path}, found {len(matches)}: "
            f"{matches}. If the constant is now legitimately declared more than "
            f"once in that file, this check must compare all of them rather "
            f"than pick one."
        )
    return matches[0]


def _addon_version() -> str:
    """Read ``__version__`` via the shared ``resolve_version.py``.

    Loaded by file path with ``importlib``: the script lives under the
    dot-prefixed ``.github/`` directory, so it is not importable as a module.
    """
    script = REPO_ROOT / RESOLVE_VERSION_PY
    if not script.is_file():
        pytest.fail(
            f"{RESOLVE_VERSION_PY} does not exist (looked in {script}). "
            f"It is the shared version resolver used by the release, registry "
            f"and drift-check workflows -- if it moved, every one of those is "
            f"broken too, and this test's path constant needs updating."
        )

    spec = importlib.util.spec_from_file_location("_resolve_version_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    init_path = REPO_ROOT / INIT_PY
    if not init_path.is_file():
        pytest.fail(
            f"{INIT_PY} does not exist (looked in {init_path}) -- it holds "
            f"__version__, the single source of truth for the addon version."
        )
    # ``read_version`` raises SystemExit if the file holds no ``__version__``;
    # pytest reports that as a failure, with the script's own message.
    return module.read_version(init_path)


# ---------------------------------------------------------------------------
# Patterns
#
# Anchored to the start of a line so comments that merely *mention* a constant
# cannot match. The Makefile in particular names INSPECTOR_VERSION on six lines
# (comments, an ifeq guard, an export, and the npx package spec); only the two
# assignments carry a literal to compare.
# ---------------------------------------------------------------------------
FLAKE_VERSION_RE = re.compile(r'^\s*version = "([^"]+)";', re.M)
MAKEFILE_INSPECTOR_DEFAULT_RE = re.compile(r"^INSPECTOR_VERSION\s*\?=\s*(\S+)\s*$", re.M)
MAKEFILE_INSPECTOR_FALLBACK_RE = re.compile(
    r"^override\s+INSPECTOR_VERSION\s*:=\s*(\S+)\s*$", re.M
)
HELPERS_INSPECTOR_RE = re.compile(r'^INSPECTOR_DEFAULT_VERSION\s*=\s*"([^"]+)"', re.M)
# Excludes quotes so a quoted YAML image ref does not capture trailing punctuation
# and report a false drift between otherwise-identical tags.
HEADLESS_ANKI_IMAGE_RE = re.compile(r"ghcr\.io/ankimcp/headless-anki:([^\s\"']+)")


class TestAddonVersion:
    """``flake.nix`` must track ``__version__``, the single source of truth."""

    def test_flake_nix_matches_dunder_version(self) -> None:
        """The Nix package version equals the addon's ``__version__``."""
        addon_version = _addon_version()
        flake_version = _find_one(
            FLAKE_NIX, FLAKE_VERSION_RE, 'a `version = "...";` attribute'
        )

        assert flake_version == addon_version, (
            f"Addon version drift:\n"
            f"  {INIT_PY} -> __version__ = {addon_version!r}   (SOURCE OF TRUTH)\n"
            f"  {FLAKE_NIX} -> version = {flake_version!r}\n"
            f"\n"
            f"Fix: edit the `version = \"...\";` line in {FLAKE_NIX} to "
            f"{addon_version!r}. Never the other way around -- __version__ is "
            f"what the release tag is checked against and what the MCP registry "
            f"is published with. flake.nix is a consumer of that value, and a "
            f"stale one makes `nix build .#addon` produce a mislabelled package."
        )


class TestServerJsonSentinel:
    """``server.json`` must keep the sentinel, not a real version."""

    def test_version_is_the_ci_sentinel(self) -> None:
        """``server.json``'s version is still the placeholder CI overwrites.

        The failure mode guarded here is somebody "helpfully" hand-syncing this
        field to the current release number. That is exactly what happened
        once: it was set to a real version, drifted to a stale 0.19.0, and the
        MCP registry silently rejected every release from 0.20.0 to 0.27.0 as a
        duplicate version.
        """
        raw = _read(SERVER_JSON)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            pytest.fail(f"{SERVER_JSON} is not valid JSON: {exc}")

        if "version" not in data:
            pytest.fail(
                f"{SERVER_JSON} has no `version` key. The MCP server schema "
                f"makes it REQUIRED, so publishing will fail -- restore it as "
                f'"version": "{SERVER_JSON_SENTINEL}".'
            )

        assert data["version"] == SERVER_JSON_SENTINEL, (
            f"{SERVER_JSON} holds version {data['version']!r}, expected the "
            f"sentinel {SERVER_JSON_SENTINEL!r}.\n"
            f"\n"
            f"Do NOT hand-sync this field to the real version. The `registry` "
            f"release job overwrites it from __version__ in {INIT_PY} at "
            f"publish time; the literal in the file only needs to satisfy the "
            f"schema's required-field rule. A hand-written real version drifts "
            f"the moment the next release lands, and a stale one makes the "
            f"registry silently reject the publish as a duplicate -- which is "
            f"how releases 0.20.0 through 0.27.0 never reached the registry.\n"
            f"\n"
            f'Fix: set "version": "{SERVER_JSON_SENTINEL}" in {SERVER_JSON}.'
        )


class TestInspectorPin:
    """The MCP Inspector pin is duplicated in three literals; all must agree."""

    def test_all_three_literals_agree(self) -> None:
        """Makefile default, Makefile empty-env fallback and helpers.py match.

        The Makefile value drives the readiness probes, ``helpers.py`` drives
        the suite itself, and the ``override`` line is the fallback for the
        workflow exporting ``INSPECTOR_VERSION`` as an empty string. A mismatch
        means CI probes one Inspector and tests with another.
        """
        makefile_default = _find_one(
            MAKEFILE,
            MAKEFILE_INSPECTOR_DEFAULT_RE,
            "an `INSPECTOR_VERSION ?= <version>` assignment",
        )
        makefile_fallback = _find_one(
            MAKEFILE,
            MAKEFILE_INSPECTOR_FALLBACK_RE,
            "an `override INSPECTOR_VERSION := <version>` assignment",
        )
        helpers_default = _find_one(
            E2E_HELPERS,
            HELPERS_INSPECTOR_RE,
            'an `INSPECTOR_DEFAULT_VERSION = "<version>"` assignment',
        )

        held = {
            f"{MAKEFILE} (INSPECTOR_VERSION ?=)": makefile_default,
            f"{MAKEFILE} (override INSPECTOR_VERSION :=)": makefile_fallback,
            f"{E2E_HELPERS} (INSPECTOR_DEFAULT_VERSION)": helpers_default,
        }

        assert len(set(held.values())) == 1, (
            f"MCP Inspector pin drift -- these three MUST hold the same "
            f"version:\n"
            + "".join(f"  {where} -> {value!r}\n" for where, value in held.items())
            + f"\n"
            f"There is no source of truth among them: pick the version you "
            f"actually intend to pin and set ALL THREE to it. The two Makefile "
            f"lines drive the readiness probes (the second is the fallback for "
            f"an INSPECTOR_VERSION exported as an empty string), while "
            f"{E2E_HELPERS} drives the test suite -- disagreement means the "
            f"probe and the suite talk to two different Inspector releases.\n"
            f"\n"
            f"To test another version without editing anything, override at "
            f"run time instead: `make e2e INSPECTOR_VERSION=latest`."
        )


class TestHeadlessAnkiImage:
    """The headless-anki image tag is duplicated in four files; all must agree."""

    def test_all_four_files_agree(self) -> None:
        """Both compose files and both workflows pin the same image tag.

        The workflows ``docker pull`` the image and the compose files ``run``
        it. If those disagree, CI warms the cache with one image and then pulls
        a second one implicitly -- and the suite silently tests an Anki build
        nobody chose.
        """
        held: dict[str, str] = {}
        for rel_path in DOCKER_IMAGE_FILES:
            tags = _find_all(
                rel_path,
                HEADLESS_ANKI_IMAGE_RE,
                "a `ghcr.io/ankimcp/headless-anki:<tag>` reference",
            )
            unique = sorted(set(tags))
            # Fold an internally-consistent file down to its single tag so the
            # failure message stays readable; a file that disagrees with itself
            # is reported as such.
            held[rel_path] = unique[0] if len(unique) == 1 else " AND ".join(unique)

        assert len(set(held.values())) == 1, (
            f"headless-anki image tag drift -- all four files MUST pin the "
            f"same tag:\n"
            + "".join(f"  {where} -> {value!r}\n" for where, value in held.items())
            + f"\n"
            f"There is no source of truth among them: decide which "
            f"headless-anki release the E2E suite should run against and set "
            f"ALL FOUR to `ghcr.io/ankimcp/headless-anki:<that tag>`. The "
            f".github/workflows/* entries pre-pull the image and the "
            f".docker/docker-compose*.yml entries are what actually start the "
            f"container, so a mismatch means CI tests an image it never "
            f"deliberately pinned."
        )
