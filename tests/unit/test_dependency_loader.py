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
