"""Regression tests for ``_find_wheel_url`` against a poisoned ``packaging``.

Anki runs every add-on in ONE shared Python process. The addon vendors a clean
``packaging`` 26.2 under ``vendor/shared`` and prepends it to ``sys.path`` at
startup. But ``sys.path`` order only decides which copy gets imported when a
module is NOT already in ``sys.modules``. If another add-on (e.g. AnkiConnect)
has already imported an OLD ``packaging`` (<= 20.9) into ``sys.modules`` before
AnkiMCP hits its first-run download path, then ``from packaging.tags import
sys_tags`` resolves to that cached old copy — the ``sys.modules`` cache wins,
and the submodule import walks the cached package's ``__path__`` rather than
``sys.path``, so our vendor-path prepend does NOT save us.

``packaging.tags`` <= 20.9 does ``import distutils.util`` at module top.
``distutils`` was removed from the stdlib in Python 3.12, so on Anki 25.07+
(Python 3.13) that import raises ``ModuleNotFoundError: No module named
'distutils'``. The net symptom is that pydantic_core's first-run "download"
fails with a ``distutils`` error and the addon won't load — even though our own
vendored ``packaging`` is perfectly fine and just never gets used.

``_find_wheel_url`` is the ONLY place the addon imports ``packaging``, and only
on the download path (before pydantic_core is cached), which is why the crash is
a first-run-only event. The fix (``_import_vendored_packaging``) temporarily
evicts any foreign ``packaging`` from ``sys.modules`` so the vendored copy is
imported fresh, then restores the foreign copy so we stay a good citizen toward
the add-on that loaded it.

These tests load ``dependency_loader.py`` as a standalone module (same technique
as ``test_dependency_loader.py``) so they run headless without booting Anki/Qt.
The vendored ``packaging`` is made importable by the unit ``conftest.py``, which
prepends ``vendor/shared`` to ``sys.path``.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Load dependency_loader.py as a standalone module (mirrors
# test_dependency_loader.py) so we exercise _find_wheel_url / the new
# _import_vendored_packaging helper without triggering anki_mcp_server/__init__.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_loader_path = _REPO_ROOT / "anki_mcp_server" / "dependency_loader.py"
_spec = importlib.util.spec_from_file_location(
    "_dep_loader_packaging_isolation", _loader_path
)
_dep_loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dep_loader)

_find_wheel_url = _dep_loader._find_wheel_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _packaging_module_names() -> list[str]:
    """Every ``packaging`` / ``packaging.*`` key currently in sys.modules."""
    return [
        name for name in sys.modules
        if name == "packaging" or name.startswith("packaging.")
    ]


def _exposes_packaging(path_entry: str) -> bool:
    """True if ``path_entry`` makes ``import packaging`` resolvable."""
    base = Path(path_entry)
    return (base / "packaging" / "__init__.py").exists() or (
        base / "packaging.py"
    ).exists()


def _canned_pypi_for_current_interpreter() -> tuple[dict, str]:
    """Build PyPI JSON data containing exactly one wheel that matches THIS
    interpreter, plus the URL that ``_find_wheel_url`` must return for it.

    The wheel filename is synthesised from the highest-priority tag the REAL
    vendored ``packaging.tags.sys_tags()`` reports, so the match is guaranteed
    regardless of the platform/Python the test happens to run on. This must be
    called while the vendored ``packaging`` is the resolvable copy (i.e. before
    a test poisons or strips it).
    """
    import packaging.tags

    top = next(iter(packaging.tags.sys_tags()))
    filename = (
        f"anki_mcp_isolation_pkg-1.0.0-"
        f"{top.interpreter}-{top.abi}-{top.platform}.whl"
    )
    url = f"https://files.example.com/{filename}"
    pypi_data = {
        "info": {"version": "1.0.0"},
        "urls": [
            {"filename": filename, "url": url},
            # An sdist so the function also exercises its skip path.
            {
                "filename": "anki_mcp_isolation_pkg-1.0.0.tar.gz",
                "url": "https://files.example.com/anki_mcp_isolation_pkg-1.0.0.tar.gz",
            },
        ],
    }
    return pypi_data, url


def _make_poisoned_packaging(tmp_path: Path) -> object:
    """Create a stand-in ``packaging`` whose ``tags`` submodule raises on import.

    Mimics ``packaging`` <= 20.9, whose ``packaging/tags.py`` does
    ``import distutils.util`` at module top (and ``distutils`` is gone from the
    stdlib on Python 3.12+). We raise the exact ``ModuleNotFoundError`` directly
    instead of literally importing ``distutils`` so the reproduction is
    deterministic even in environments that ship a ``distutils`` shim (e.g. via
    setuptools).

    The returned module has a ``__path__`` pointing at the on-disk fake package,
    so when it sits in ``sys.modules`` as ``"packaging"`` the submodule import of
    ``packaging.tags`` walks THAT path (the bug) rather than ``sys.path``.
    """
    pkg_dir = tmp_path / "foreign_site" / "packaging"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("__version__ = '20.9'\n")
    (pkg_dir / "tags.py").write_text(
        "raise ModuleNotFoundError(\"No module named 'distutils'\")\n"
    )
    spec = importlib.util.spec_from_file_location(
        "packaging",
        pkg_dir / "__init__.py",
        submodule_search_locations=[str(pkg_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolate_packaging():
    """Snapshot and fully restore ``sys.modules`` packaging subtree + ``sys.path``.

    Tests in this module deliberately poison / strip the import state, so we
    must restore it afterwards or we leak into every other unit test.
    """
    saved_path = list(sys.path)
    saved_mods = {name: sys.modules[name] for name in _packaging_module_names()}
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for name in _packaging_module_names():
            del sys.modules[name]
        sys.modules.update(saved_mods)


# ---------------------------------------------------------------------------
# 1 + 2. Regression (RED before the fix) and correct resolution (GREEN after)
# ---------------------------------------------------------------------------

def test_find_wheel_url_uses_vendored_packaging_despite_poisoned_sysmodules(
    isolate_packaging, tmp_path: Path
) -> None:
    """With a foreign ``packaging`` <= 20.9 already in ``sys.modules``,
    ``_find_wheel_url`` STILL resolves via the vendored ``packaging`` and returns
    the correct wheel URL.

    Before the fix this raises ``ModuleNotFoundError: No module named 'distutils'``
    because ``from packaging.tags import sys_tags`` resolves to the cached old
    copy whose ``tags`` submodule imports the now-removed ``distutils`` (RED).
    After the fix the vendored copy is imported fresh and selection succeeds
    (GREEN).
    """
    # Build canned data while the vendored packaging is still resolvable.
    pypi_data, expected_url = _canned_pypi_for_current_interpreter()

    foreign = _make_poisoned_packaging(tmp_path)
    for name in _packaging_module_names():
        del sys.modules[name]
    sys.modules["packaging"] = foreign

    result = _find_wheel_url(pypi_data)

    assert result == expected_url


# ---------------------------------------------------------------------------
# 3. Good citizen: the foreign packaging is restored after the call
# ---------------------------------------------------------------------------

def test_find_wheel_url_restores_foreign_packaging(
    isolate_packaging, tmp_path: Path
) -> None:
    """After ``_find_wheel_url`` runs, the foreign ``packaging`` object (and its
    submodules) are restored to ``sys.modules`` BY IDENTITY — we never leave the
    other add-on's import state mutated."""
    pypi_data, expected_url = _canned_pypi_for_current_interpreter()

    foreign = _make_poisoned_packaging(tmp_path)
    # A foreign submodule the helper NEVER imports (it touches packaging.tags,
    # packaging.utils, and packaging.version — but not packaging.specifiers), to
    # prove the restore covers the WHOLE packaging.* subtree, not just the names
    # the helper happens to import.
    foreign_specifiers_submodule = type(sys)("packaging.specifiers")

    for name in _packaging_module_names():
        del sys.modules[name]
    sys.modules["packaging"] = foreign
    sys.modules["packaging.specifiers"] = foreign_specifiers_submodule

    result = _find_wheel_url(pypi_data)
    assert result == expected_url

    # Same objects, by identity — not merely re-importable.
    assert sys.modules.get("packaging") is foreign
    assert sys.modules.get("packaging.specifiers") is foreign_specifiers_submodule


# ---------------------------------------------------------------------------
# 4. Exception safety: foreign packaging is restored even if the fresh import
#    inside the helper raises.
# ---------------------------------------------------------------------------

def test_foreign_packaging_restored_when_vendored_import_fails(
    isolate_packaging, tmp_path: Path
) -> None:
    """If the helper's fresh ``from packaging.tags import ...`` itself raises
    (simulated by stripping every ``packaging``-exposing entry from ``sys.path``
    so no copy is importable), the foreign ``packaging`` is STILL restored to
    ``sys.modules`` via the helper's ``finally`` block, and the import error
    propagates.

    This guards the restore against the unhappy path — a half-evicted
    ``sys.modules`` would silently break the very add-on whose ``packaging`` we
    borrowed.
    """
    pypi_data, _expected_url = _canned_pypi_for_current_interpreter()

    foreign = _make_poisoned_packaging(tmp_path)
    for name in _packaging_module_names():
        del sys.modules[name]
    sys.modules["packaging"] = foreign

    # Make the fresh import impossible: remove every sys.path entry that exposes
    # a `packaging` (the vendored copy AND any site-packages copy). The foreign
    # copy lives only in sys.modules and is evicted by the helper before import,
    # so the import has nowhere to resolve from and raises.
    sys.path[:] = [p for p in sys.path if not _exposes_packaging(p)]

    with pytest.raises(ModuleNotFoundError):
        _find_wheel_url(pypi_data)

    # The finally path must have put the foreign copy back, by identity.
    assert sys.modules.get("packaging") is foreign


# ---------------------------------------------------------------------------
# 5. No-conflict regression: with a clean sys.modules (no foreign packaging),
#    selection still works and leaves no packaging residue behind.
# ---------------------------------------------------------------------------

def test_find_wheel_url_clean_sysmodules_no_foreign(
    isolate_packaging,
) -> None:
    """With NO foreign ``packaging`` in ``sys.modules``, ``_find_wheel_url``
    resolves via the vendored copy and returns the right URL, and the helper
    leaves ``sys.modules`` as it found it (no ``packaging`` keys lingering)."""
    pypi_data, expected_url = _canned_pypi_for_current_interpreter()

    # Start from a genuinely clean slate: no packaging anywhere in sys.modules.
    for name in _packaging_module_names():
        del sys.modules[name]
    assert _packaging_module_names() == []

    result = _find_wheel_url(pypi_data)
    assert result == expected_url

    # The helper saved an empty set and restored it: nothing left behind.
    assert _packaging_module_names() == []
