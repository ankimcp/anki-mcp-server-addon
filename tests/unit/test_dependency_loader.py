"""Unit tests for anki_mcp_server.dependency_loader._find_wheel_url.

Regression coverage for issue #52: Anki's universal2 framework Python returns
sysconfig.get_platform() == "macosx-10.13-universal2", which contains neither
"arm64" nor "aarch64".  On Apple Silicon this caused the x86_64 wheel to be
selected, producing "ImportError: incompatible architecture" at startup.

The fix consults platform.machine() (imported as _platform_mod), which always
reflects the actual running-process architecture regardless of the build type.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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


# Wheel filenames from a realistic pydantic_core release
_MACOS_X86 = f"pydantic_core-{_VERSION}-cp313-cp313-macosx_10_12_x86_64.whl"
_MACOS_ARM = f"pydantic_core-{_VERSION}-cp313-cp313-macosx_11_0_arm64.whl"
_MACOS_UNI = f"pydantic_core-{_VERSION}-cp313-cp313-macosx_10_12_universal2.whl"
_LINUX_X86 = f"pydantic_core-{_VERSION}-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
_LINUX_ARM = f"pydantic_core-{_VERSION}-cp313-cp313-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
_WIN_AMD64 = f"pydantic_core-{_VERSION}-cp313-cp313-win_amd64.whl"
_SDIST     = f"pydantic_core-{_VERSION}.tar.gz"

_ALL_WHEELS = [
    _MACOS_X86,
    _MACOS_ARM,
    _MACOS_UNI,
    _LINUX_X86,
    _LINUX_ARM,
    _WIN_AMD64,
    _SDIST,
]

_PYPI_DATA = _make_pypi_data(_ALL_WHEELS)


def _url(filename: str) -> str:
    return f"https://files.example.com/{filename}"


def _exposes_rpds(path_entry: str) -> bool:
    """True if ``path_entry`` makes ``import rpds`` resolvable.

    Covers both the package form (``<entry>/rpds/__init__.py`` — e.g. the
    addon's vendored ``vendor/shared`` or the warm fallback cache) and the
    single-module form (``<entry>/rpds.py``).
    """
    base = Path(path_entry)
    return (base / "rpds" / "__init__.py").exists() or (base / "rpds.py").exists()


# ---------------------------------------------------------------------------
# Parametrized test cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("get_platform_val, machine_val, expected_filename", [
    # --- Issue #52 regression ---
    # universal2 build: get_platform() says "universal2", machine() says "arm64"
    (
        "macosx-10.13-universal2",
        "arm64",
        _MACOS_ARM,
    ),
    # Native arm64 Mac (non-universal2 build)
    (
        "macosx-11.0-arm64",
        "arm64",
        _MACOS_ARM,
    ),
    # Intel Mac
    (
        "macosx-10.13-x86_64",
        "x86_64",
        _MACOS_X86,
    ),
    # Rosetta: get_platform() reports x86_64 (process runs as x86_64), machine()
    # also x86_64 — so we correctly pick the x86_64 wheel (no false arm positive)
    (
        "macosx-10.13-x86_64",
        "x86_64",
        _MACOS_X86,
    ),
    # Linux x86_64
    (
        "linux-x86_64",
        "x86_64",
        _LINUX_X86,
    ),
    # Linux aarch64
    (
        "linux-aarch64",
        "aarch64",
        _LINUX_ARM,
    ),
], ids=[
    "regression-universal2-arm64",
    "native-arm64-mac",
    "intel-mac",
    "rosetta-x86_64",
    "linux-x86_64",
    "linux-aarch64",
])
def test_find_wheel_url(
    monkeypatch: pytest.MonkeyPatch,
    get_platform_val: str,
    machine_val: str,
    expected_filename: str,
) -> None:
    """_find_wheel_url selects the wheel matching the actual running architecture."""
    monkeypatch.setattr(_dep_loader.sysconfig, "get_platform", lambda: get_platform_val)
    monkeypatch.setattr(_dep_loader._platform_mod, "machine", lambda: machine_val)
    monkeypatch.setattr(_dep_loader, "_get_python_tag", lambda: "cp313")

    result = _find_wheel_url(_PYPI_DATA)

    assert result == _url(expected_filename), (
        f"Expected wheel {expected_filename!r} for "
        f"get_platform={get_platform_val!r}, machine={machine_val!r}, "
        f"but got {result!r}"
    )


def test_find_wheel_url_excludes_non_wheels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sdists and unrelated wheels are skipped; only matching .whl is returned."""
    monkeypatch.setattr(_dep_loader.sysconfig, "get_platform", lambda: "macosx-10.13-x86_64")
    monkeypatch.setattr(_dep_loader._platform_mod, "machine", lambda: "x86_64")
    monkeypatch.setattr(_dep_loader, "_get_python_tag", lambda: "cp313")

    # Dataset contains ONLY the sdist, the linux wheel, and the x86_64 mac wheel
    pypi_data = _make_pypi_data([_SDIST, _LINUX_ARM, _MACOS_X86])
    result = _find_wheel_url(pypi_data)
    assert result == _url(_MACOS_X86)


def test_find_wheel_url_raises_when_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """RuntimeError is raised when no wheel matches the current platform."""
    monkeypatch.setattr(_dep_loader.sysconfig, "get_platform", lambda: "macosx-11.0-arm64")
    monkeypatch.setattr(_dep_loader._platform_mod, "machine", lambda: "arm64")
    monkeypatch.setattr(_dep_loader, "_get_python_tag", lambda: "cp313")

    # Dataset has only the x86_64 mac wheel — no arm64 candidate
    pypi_data = _make_pypi_data([_MACOS_X86, _SDIST])
    with pytest.raises(RuntimeError, match="No matching wheel found"):
        _find_wheel_url(pypi_data)


# ---------------------------------------------------------------------------
# rpds (issue #54)
#
# rpds is the only compiled dep in the mcp -> jsonschema -> referencing chain.
# It is no longer vendored: ensure_rpds() imports it (Anki provides it on every
# supported version) and only downloads a pinned wheel as a fallback. These
# tests cover (a) the no-download fast path and (b) rpds wheel selection, which
# reuses the same _find_wheel_url as pydantic_core.
# ---------------------------------------------------------------------------

_RPDS_VERSION = _dep_loader._RPDS_VERSION  # pinned fallback download version

# rpds-py wheel filenames follow the same scheme as pydantic_core.
_RPDS_MACOS_X86 = f"rpds_py-{_RPDS_VERSION}-cp313-cp313-macosx_10_12_x86_64.whl"
_RPDS_MACOS_ARM = f"rpds_py-{_RPDS_VERSION}-cp313-cp313-macosx_11_0_arm64.whl"
_RPDS_LINUX_X86 = f"rpds_py-{_RPDS_VERSION}-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
_RPDS_LINUX_ARM = f"rpds_py-{_RPDS_VERSION}-cp313-cp313-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
_RPDS_WIN_AMD64 = f"rpds_py-{_RPDS_VERSION}-cp313-cp313-win_amd64.whl"
_RPDS_WIN_ARM64 = f"rpds_py-{_RPDS_VERSION}-cp313-cp313-win_arm64.whl"
_RPDS_SDIST     = f"rpds_py-{_RPDS_VERSION}.tar.gz"

_RPDS_ALL_WHEELS = [
    _RPDS_MACOS_X86,
    _RPDS_MACOS_ARM,
    _RPDS_LINUX_X86,
    _RPDS_LINUX_ARM,
    _RPDS_WIN_AMD64,
    _RPDS_WIN_ARM64,
    _RPDS_SDIST,
]


@pytest.mark.parametrize("get_platform_val, machine_val, expected_filename", [
    ("macosx-10.13-universal2", "arm64", _RPDS_MACOS_ARM),   # issue #52-style universal2
    ("macosx-11.0-arm64", "arm64", _RPDS_MACOS_ARM),
    ("macosx-10.13-x86_64", "x86_64", _RPDS_MACOS_X86),
    ("linux-x86_64", "x86_64", _RPDS_LINUX_X86),
    ("linux-aarch64", "aarch64", _RPDS_LINUX_ARM),
    ("win-amd64", "AMD64", _RPDS_WIN_AMD64),
    ("win-arm64", "ARM64", _RPDS_WIN_ARM64),
], ids=[
    "universal2-arm64",
    "native-arm64-mac",
    "intel-mac",
    "linux-x86_64",
    "linux-aarch64",
    "win-amd64",
    "win-arm64",
])
def test_find_wheel_url_rpds(
    monkeypatch: pytest.MonkeyPatch,
    get_platform_val: str,
    machine_val: str,
    expected_filename: str,
) -> None:
    """_find_wheel_url selects the correct rpds wheel for each platform."""
    monkeypatch.setattr(_dep_loader.sysconfig, "get_platform", lambda: get_platform_val)
    monkeypatch.setattr(_dep_loader._platform_mod, "machine", lambda: machine_val)
    monkeypatch.setattr(_dep_loader, "_get_python_tag", lambda: "cp313")

    pypi_data = _make_pypi_data(_RPDS_ALL_WHEELS)
    result = _find_wheel_url(pypi_data)

    assert result == _url(expected_filename), (
        f"Expected wheel {expected_filename!r} for "
        f"get_platform={get_platform_val!r}, machine={machine_val!r}, "
        f"but got {result!r}"
    )


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
