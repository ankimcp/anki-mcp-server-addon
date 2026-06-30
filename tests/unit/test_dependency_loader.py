"""Unit tests for anki_mcp_server.dependency_loader._find_wheel_url.

Wheel selection now defers to ``packaging.tags.sys_tags()`` — the same
interpreter-tag machinery pip uses — instead of ad-hoc substring matching.
``_find_wheel_url`` parses each candidate filename with
``packaging.utils.parse_wheel_filename`` and returns the wheel carrying the tag
with the highest priority (smallest index) in ``sys_tags()``.

These tests drive selection deterministically by monkeypatching
``packaging.tags.sys_tags`` to return a controlled, ordered list of
``packaging.tags.Tag`` objects representing a specific target interpreter, so
the assertions don't depend on the machine the tests happen to run on.

Regression coverage:

* Issue #52 — Anki's universal2 framework Python on Apple Silicon must pick the
  arm64 wheel, never the x86_64 one. ``sys_tags()`` reports macosx arm64 +
  universal2 tags for such an interpreter, so the arm64 wheel wins on priority.
* Issue #54 — a STANDARD ``cp313`` interpreter must NOT select a free-threaded
  ``cp313t`` wheel (loose ``"cp313" in filename`` matching used to). Tag-based
  selection distinguishes the ``cp313``/``cp313`` ABI from ``cp313t``/``cp313t``.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from packaging.tags import Tag

# ---------------------------------------------------------------------------
# Load dependency_loader.py as a standalone module.
#
# The conftest already loads it this way to bootstrap pydantic_core, but that
# instance is registered under the private name "_anki_mcp_loader".  We use the
# same technique here so tests can import _find_wheel_url without triggering
# anki_mcp_server/__init__.py (which requires a running Anki/Qt environment).
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_loader_path = _REPO_ROOT / "anki_mcp_server" / "dependency_loader.py"
_spec = importlib.util.spec_from_file_location("_dep_loader_under_test", _loader_path)
_dep_loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dep_loader)

_find_wheel_url = _dep_loader._find_wheel_url


# ---------------------------------------------------------------------------
# Synthetic PyPI data matching the real pydantic_core wheel naming scheme
# ---------------------------------------------------------------------------

_VERSION = "2.46.4"


def _make_pypi_data(filenames: list[str]) -> dict:
    """Build a minimal PyPI JSON dict from a list of filenames."""
    return {
        "info": {"version": _VERSION},
        "urls": [
            {"filename": fn, "url": f"https://files.example.com/{fn}"}
            for fn in filenames
        ],
    }


def _url(filename: str) -> str:
    return f"https://files.example.com/{filename}"


def _use_tags(monkeypatch: pytest.MonkeyPatch, tags: list[Tag]) -> None:
    """Make ``_find_wheel_url`` see ``tags`` (highest-priority-first) as the
    current interpreter's supported tags.

    ``_find_wheel_url`` obtains ``packaging`` via ``_import_vendored_packaging``,
    which evicts any cached ``packaging`` from ``sys.modules`` and re-imports the
    vendored copy fresh (to dodge a poisoned ``sys.modules`` — see
    ``test_dependency_loader_packaging_isolation.py``). Because of that fresh
    re-import, a monkeypatch on the ``packaging.tags`` module object would NOT be
    seen by the function. So we stub the helper itself to return our controlled
    ``sys_tags`` alongside the REAL ``parse_wheel_filename`` / exception types,
    keeping filename parsing exercised end to end.

    The real pieces are sourced from the SAME vendored ``packaging`` module the
    test's ``Tag`` / ``_TAGS_*`` objects come from (the module-level import), NOT
    a fresh re-import. ``packaging.tags.Tag.__eq__`` is gated on
    ``isinstance(other, Tag)``, so Tag instances minted by two different copies
    of ``packaging`` never compare equal — sourcing both sides from one module
    keeps the priority-dict lookup in ``_find_wheel_url`` working.
    """
    import packaging.tags  # noqa: F401  (ensures the subtree is imported)
    import packaging.utils
    import packaging.version

    monkeypatch.setattr(
        _dep_loader,
        "_import_vendored_packaging",
        lambda: (
            lambda: iter(tags),
            packaging.utils.parse_wheel_filename,
            packaging.utils.InvalidWheelFilename,
            packaging.version.InvalidVersion,
        ),
    )


# Wheel filenames from a realistic pydantic_core release (standard cp313 ABI).
_MACOS_X86 = f"pydantic_core-{_VERSION}-cp313-cp313-macosx_10_12_x86_64.whl"
_MACOS_ARM = f"pydantic_core-{_VERSION}-cp313-cp313-macosx_11_0_arm64.whl"
_MACOS_UNI = f"pydantic_core-{_VERSION}-cp313-cp313-macosx_10_12_universal2.whl"
_LINUX_X86 = f"pydantic_core-{_VERSION}-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
_LINUX_ARM = f"pydantic_core-{_VERSION}-cp313-cp313-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
_WIN_AMD64 = f"pydantic_core-{_VERSION}-cp313-cp313-win_amd64.whl"
_SDIST = f"pydantic_core-{_VERSION}.tar.gz"

# Free-threaded (PEP 703) cp313t wheels — the issue #54 trap. The interpreter,
# ABI, and (for win) the same platform differ only by the trailing "t".
_WIN_AMD64_FT = f"pydantic_core-{_VERSION}-cp313-cp313t-win_amd64.whl"
_MACOS_ARM_FT = f"pydantic_core-{_VERSION}-cp313-cp313t-macosx_11_0_arm64.whl"


# ---------------------------------------------------------------------------
# Interpreter tag sets (highest-priority-first), mirroring what
# packaging.tags.sys_tags() yields on each target. We keep them short — only
# the tags relevant to the candidate wheels need to be present and ordered.
# ---------------------------------------------------------------------------

# Standard cp313 on Windows x86_64.
_TAGS_WIN_AMD64 = [
    Tag("cp313", "cp313", "win_amd64"),
    Tag("cp313", "abi3", "win_amd64"),
    Tag("cp313", "none", "win_amd64"),
]

# Standard cp313 on Apple-Silicon macOS. A universal2 framework build (issue
# #52) reports BOTH arm64 and universal2 platform tags, with arm64 ranked
# higher — so the arm64 wheel must win over universal2 and x86_64.
_TAGS_MACOS_ARM = [
    Tag("cp313", "cp313", "macosx_11_0_arm64"),
    Tag("cp313", "cp313", "macosx_11_0_universal2"),
    Tag("cp313", "cp313", "macosx_10_12_universal2"),
]

# Standard cp313 on Intel macOS.
_TAGS_MACOS_X86 = [
    Tag("cp313", "cp313", "macosx_10_12_x86_64"),
    Tag("cp313", "cp313", "macosx_10_12_universal2"),
]

# Standard cp313 on Linux aarch64.
_TAGS_LINUX_ARM = [
    Tag("cp313", "cp313", "manylinux_2_17_aarch64"),
    Tag("cp313", "cp313", "manylinux2014_aarch64"),
]

# Standard cp313 on Linux x86_64.
_TAGS_LINUX_X86 = [
    Tag("cp313", "cp313", "manylinux_2_17_x86_64"),
    Tag("cp313", "cp313", "manylinux2014_x86_64"),
]

# Free-threaded cp313t on Windows x86_64. A free-threaded interpreter ONLY
# supports the cp313t ABI — the standard cp313 ABI is absent from its tags.
_TAGS_WIN_AMD64_FT = [
    Tag("cp313", "cp313t", "win_amd64"),
    Tag("cp313", "none", "win_amd64"),
]


# ---------------------------------------------------------------------------
# Platform / arch selection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tags, expected_filename", [
    (_TAGS_WIN_AMD64, _WIN_AMD64),
    (_TAGS_MACOS_X86, _MACOS_X86),
    (_TAGS_LINUX_X86, _LINUX_X86),
    (_TAGS_LINUX_ARM, _LINUX_ARM),
], ids=[
    "win-amd64",
    "intel-mac",
    "linux-x86_64",
    "linux-aarch64",
])
def test_find_wheel_url_picks_platform_arch(
    monkeypatch: pytest.MonkeyPatch,
    tags: list[Tag],
    expected_filename: str,
) -> None:
    """_find_wheel_url selects the wheel matching the interpreter's top tag."""
    _use_tags(monkeypatch, tags)

    result = _find_wheel_url(_make_pypi_data(
        [_MACOS_X86, _MACOS_ARM, _MACOS_UNI, _LINUX_X86, _LINUX_ARM, _WIN_AMD64, _SDIST]
    ))

    assert result == _url(expected_filename)


def test_find_wheel_url_universal2_arm64_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #52: a universal2 Apple-Silicon interpreter picks the arm64 wheel.

    With arm64, universal2 AND x86_64 wheels all present, the arm64 tag ranks
    highest in sys_tags(), so the arm64 wheel must win — never x86_64.
    """
    _use_tags(monkeypatch, _TAGS_MACOS_ARM)

    result = _find_wheel_url(_make_pypi_data([_MACOS_X86, _MACOS_ARM, _MACOS_UNI]))

    assert result == _url(_MACOS_ARM)


def test_find_wheel_url_universal2_when_no_native_arm64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An Apple-Silicon interpreter falls back to the universal2 wheel when no
    dedicated arm64 wheel exists (universal2 carries the arm64 slice)."""
    _use_tags(monkeypatch, _TAGS_MACOS_ARM)

    result = _find_wheel_url(_make_pypi_data([_MACOS_X86, _MACOS_UNI]))

    assert result == _url(_MACOS_UNI)


# ---------------------------------------------------------------------------
# Free-threaded (cp313t) vs standard (cp313) — issue #54
# ---------------------------------------------------------------------------

def test_standard_cp313_does_not_pick_freethreaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #54: a STANDARD cp313 interpreter must pick the cp313 wheel, NOT the
    cp313t (free-threaded) one, even when both win_amd64 wheels are present."""
    _use_tags(monkeypatch, _TAGS_WIN_AMD64)

    result = _find_wheel_url(_make_pypi_data([_WIN_AMD64_FT, _WIN_AMD64]))

    assert result == _url(_WIN_AMD64)


def test_freethreaded_cp313t_picks_freethreaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A free-threaded cp313t interpreter DOES select the cp313t wheel and must
    NOT fall back to the standard cp313 wheel (its ABI is incompatible)."""
    _use_tags(monkeypatch, _TAGS_WIN_AMD64_FT)

    result = _find_wheel_url(_make_pypi_data([_WIN_AMD64, _WIN_AMD64_FT]))

    assert result == _url(_WIN_AMD64_FT)


# ---------------------------------------------------------------------------
# Skipping / no-match behaviour
# ---------------------------------------------------------------------------

def test_find_wheel_url_skips_sdists_and_unparseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sdists (.tar.gz) and unparseable .whl filenames are skipped; the one
    matching wheel is still returned."""
    _use_tags(monkeypatch, _TAGS_MACOS_X86)

    pypi_data = _make_pypi_data([
        _SDIST,                       # not a wheel
        "totally-not-a-wheel.whl",    # .whl but unparseable -> InvalidWheelFilename
        _LINUX_ARM,                   # wheel, but wrong platform
        _MACOS_X86,                   # the match
    ])
    result = _find_wheel_url(pypi_data)
    assert result == _url(_MACOS_X86)


def test_find_wheel_url_raises_when_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RuntimeError is raised when no wheel matches the interpreter's tags."""
    _use_tags(monkeypatch, _TAGS_MACOS_ARM)

    # Only an x86_64 mac wheel and an sdist — no arm64/universal2 candidate.
    pypi_data = _make_pypi_data([_MACOS_X86, _SDIST])
    with pytest.raises(RuntimeError, match="No matching wheel found"):
        _find_wheel_url(pypi_data)


# ---------------------------------------------------------------------------
# rpds ensure-path (issue #54 vendoring decision)
#
# rpds is the only compiled dep in the mcp -> jsonschema -> referencing chain.
# It is no longer vendored: ensure_rpds() imports it (Anki provides it on every
# supported version) and only downloads a pinned wheel as a fallback. These
# tests cover the no-download fast path and the warm-cache reuse path — they
# exercise _ensure_rpds_with_callbacks, not _find_wheel_url.
# ---------------------------------------------------------------------------

_RPDS_VERSION = _dep_loader._RPDS_VERSION  # pinned fallback download version


def _exposes_rpds(path_entry: str) -> bool:
    """True if ``path_entry`` makes ``import rpds`` resolvable.

    Covers both the package form (``<entry>/rpds/__init__.py`` — e.g. the
    addon's vendored ``vendor/shared`` or the warm fallback cache) and the
    single-module form (``<entry>/rpds.py``).
    """
    base = Path(path_entry)
    return (base / "rpds" / "__init__.py").exists() or (base / "rpds.py").exists()


def test_ensure_rpds_no_download_when_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    """ensure_rpds() returns True with NO network when rpds is already importable.

    This is the common case: Anki provides rpds as a transitive dep of its own
    jsonschema, so the pure core should short-circuit on `import rpds` before
    touching the download path. We inject a dummy rpds into sys.modules and make
    every network/download entry point raise if called, then assert none fire.
    """
    # Inject a stand-in rpds module so `import rpds` succeeds without the real one.
    monkeypatch.setitem(sys.modules, "rpds", type(sys)("rpds"))

    def _boom(*args, **kwargs):
        raise AssertionError("ensure_rpds attempted a download/network call")

    # Guard every path that would imply a download attempt.
    monkeypatch.setattr(_dep_loader, "_download_and_extract_wheel", _boom)
    monkeypatch.setattr(_dep_loader, "_find_wheel_url", _boom)
    monkeypatch.setattr(_dep_loader.urllib.request, "urlopen", _boom)
    monkeypatch.setattr(_dep_loader.urllib.request, "urlretrieve", _boom)

    assert _dep_loader._ensure_rpds_with_callbacks() is True


def test_ensure_rpds_uses_warm_cache_no_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ensure_rpds() reuses a warm fallback cache with NO network.

    Simulates the state after a previous successful fallback download: the
    rpds_pkg cache dir holds an importable rpds package plus matching .complete
    and .version (== _RPDS_VERSION) markers. The pure core must put the cache on
    sys.path and import rpds from there WITHOUT touching the download path.
    """
    # Point CACHE_DIR at a tmp dir and build the warm cache the loader expects:
    #   <CACHE_DIR>/rpds_pkg/{.complete, .version, rpds/__init__.py}
    cache_root = tmp_path / "_cache"
    cache_dir = cache_root / "rpds_pkg"
    rpds_pkg = cache_dir / "rpds"
    rpds_pkg.mkdir(parents=True)
    # Minimal stand-in exposing the surface vendored referencing uses.
    (rpds_pkg / "__init__.py").write_text(
        "class HashTrieMap: ...\n"
        "class HashTrieSet: ...\n"
        "class List: ...\n"
    )
    (cache_dir / ".version").write_text(_RPDS_VERSION)
    (cache_dir / ".complete").touch()

    monkeypatch.setattr(_dep_loader, "CACHE_DIR", cache_root)

    def _boom(*args, **kwargs):
        raise AssertionError("ensure_rpds attempted a download/network call")

    monkeypatch.setattr(_dep_loader, "_download_and_extract_wheel", _boom)
    monkeypatch.setattr(_dep_loader, "_find_wheel_url", _boom)
    monkeypatch.setattr(_dep_loader.urllib.request, "urlopen", _boom)
    monkeypatch.setattr(_dep_loader.urllib.request, "urlretrieve", _boom)

    cache_str = str(cache_dir)

    # Snapshot state we are about to mutate so we can fully restore it in the
    # finally block — test ordering must not leak rpds into other tests.
    original_sys_path = list(sys.path)
    original_rpds_modules = {
        k: v for k, v in sys.modules.items() if k == "rpds" or k.startswith("rpds.")
    }

    try:
        # The fast-path `import rpds` in the loader must deterministically MISS so
        # the warm-cache branch is what satisfies the import. Two things can leave
        # rpds importable and short-circuit that branch:
        #
        #   1. A cached `rpds` (or native `rpds.rpds` submodule) in sys.modules.
        #      We must clear the WHOLE subtree — clearing only the "rpds" parent
        #      while leaving "rpds.rpds" cached makes the vendored
        #      rpds/__init__.py re-run `from .rpds import *` against the stale
        #      submodule and then hit `__doc__ = rpds.__doc__` with the name
        #      unbound → NameError (not ImportError), which the loader's
        #      `except ImportError` would not catch.
        #   2. Any sys.path entry that exposes an importable rpds (e.g. the
        #      addon's vendored `vendor/shared`, which conftest puts on sys.path,
        #      or the warm cache dir itself). We drop all of them so the initial
        #      probe genuinely fails and the warm-cache branch re-adds the cache.
        for name in list(sys.modules):
            if name == "rpds" or name.startswith("rpds."):
                del sys.modules[name]
        sys.path[:] = [p for p in sys.path if not _exposes_rpds(p)]

        assert _dep_loader._ensure_rpds_with_callbacks() is True

        # Warm-cache branch must have prepended the cache dir and made rpds
        # importable from there.
        assert cache_str in sys.path
        import rpds  # noqa: F401

        assert Path(rpds.__file__).resolve() == (rpds_pkg / "__init__.py").resolve()
    finally:
        # Fully restore sys.path and the rpds sys.modules subtree so other tests
        # are unaffected, regardless of where the body failed.
        sys.path[:] = original_sys_path
        for name in list(sys.modules):
            if name == "rpds" or name.startswith("rpds."):
                del sys.modules[name]
        sys.modules.update(original_rpds_modules)
