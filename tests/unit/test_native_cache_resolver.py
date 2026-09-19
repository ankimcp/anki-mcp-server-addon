"""Unit tests for ``dependency_loader``'s out-of-folder cache root resolver.

The addon's native-dependency cache (pydantic_core / rpds) used to live inside
the addon's own folder, which is unsafe on Windows: Anki deletes the whole
folder on update/uninstall, and a loaded ``.pyd`` can't be deleted mid-delete
(only renamed/moved), so the delete dies halfway and guts the addon. The fix
moves the cache to ``<mw.pm.base>/ankimcp/<addon_folder_name>/cache``, outside
the addon folder, falling back to the legacy in-folder location whenever the
preferred one isn't resolvable.

These tests load ``dependency_loader.py`` as a standalone module (same
technique ``test_dependency_loader.py`` uses) so they run headless without
booting Anki/Qt, and drive ``mw`` availability via the stubbed ``aqt`` module
that ``conftest.py`` installs into ``sys.modules``.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_loader_path = _REPO_ROOT / "anki_mcp_server" / "dependency_loader.py"
_spec = importlib.util.spec_from_file_location("_dep_loader_resolver_under_test", _loader_path)
_dep_loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dep_loader)

_ADDON_FOLDER_NAME = _dep_loader._addon_folder_name()


class _FakePm:
    def __init__(self, base):
        self.base = base


class _FakeMw:
    def __init__(self, base):
        self.pm = _FakePm(base)


@pytest.fixture()
def fake_cache_dir(monkeypatch, tmp_path):
    """Point the legacy fallback constant at a tmp dir for every test, so a
    test that falls back never touches the real repo's ``_cache/`` directory."""
    legacy = tmp_path / "legacy_cache"
    monkeypatch.setattr(_dep_loader, "CACHE_DIR", legacy)
    return legacy


def test_resolver_uses_preferred_root_when_mw_available(
    monkeypatch, tmp_path, fake_cache_dir, install_mw
):
    base = tmp_path / "anki_base"
    install_mw(_FakeMw(str(base)))

    root = _dep_loader._resolve_cache_root()

    assert root == base / "ankimcp" / _ADDON_FOLDER_NAME / "cache"
    assert root.is_dir()
    assert _ADDON_FOLDER_NAME in root.parts


def test_resolver_falls_back_when_mw_is_none(fake_cache_dir, install_mw):
    install_mw(None)

    root = _dep_loader._resolve_cache_root()

    assert root == fake_cache_dir


def test_resolver_falls_back_when_aqt_is_none_in_sys_modules(monkeypatch, fake_cache_dir):
    # `_get_mw()` looks up `sys.modules.get("aqt")` rather than importing --
    # an explicit `None` entry (the standard "import blocked" sentinel some
    # tooling uses) must resolve to `mw is None`, not raise, and this doesn't
    # depend on whether a real `aqt` happens to be installed in the
    # environment running this test.
    monkeypatch.setitem(sys.modules, "aqt", None)
    for name in list(sys.modules):
        if name.startswith("aqt."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    root = _dep_loader._resolve_cache_root()

    assert root == fake_cache_dir


def test_resolver_falls_back_when_aqt_absent_from_sys_modules(monkeypatch, fake_cache_dir):
    # `_get_mw()` never imports `aqt` -- if it simply isn't in `sys.modules`
    # at all (as in the standalone-module CI smoke path), the lookup must
    # resolve to `mw is None` rather than raising or importing it.
    monkeypatch.delitem(sys.modules, "aqt", raising=False)
    for name in list(sys.modules):
        if name.startswith("aqt."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    root = _dep_loader._resolve_cache_root()

    assert root == fake_cache_dir


def test_resolver_falls_back_when_pm_missing(fake_cache_dir, install_mw):
    install_mw(types.SimpleNamespace())  # mw with no `pm` attribute

    root = _dep_loader._resolve_cache_root()

    assert root == fake_cache_dir


def test_resolver_falls_back_when_base_empty(fake_cache_dir, install_mw):
    install_mw(_FakeMw(""))

    root = _dep_loader._resolve_cache_root()

    assert root == fake_cache_dir


def test_resolver_falls_back_when_preferred_dir_uncreatable(
    monkeypatch, tmp_path, fake_cache_dir, install_mw
):
    install_mw(_FakeMw(str(tmp_path / "anki_base")))
    monkeypatch.setattr(_dep_loader, "_is_writable_dir", lambda _path: False)

    root = _dep_loader._resolve_cache_root()

    assert root == fake_cache_dir


def test_resolver_falls_back_when_out_of_folder_data_dir_raises(
    fake_cache_dir, monkeypatch
):
    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(_dep_loader, "out_of_folder_data_dir", _boom)

    # Must never raise -- the resolver wraps everything defensively.
    root = _dep_loader._resolve_cache_root()

    assert root == fake_cache_dir


def test_resolver_never_raises_on_broken_mw(fake_cache_dir, install_mw):
    class _ExplodingPm:
        @property
        def base(self):
            raise RuntimeError("pm.base blew up")

    install_mw(types.SimpleNamespace(pm=_ExplodingPm()))

    root = _dep_loader._resolve_cache_root()  # must not raise

    assert root == fake_cache_dir


def test_out_of_folder_data_dir_returns_none_without_mw(install_mw):
    install_mw(None)

    assert _dep_loader.out_of_folder_data_dir() is None


def test_out_of_folder_data_dir_matches_preferred_root_parent(
    tmp_path, install_mw
):
    base = tmp_path / "anki_base"
    install_mw(_FakeMw(str(base)))

    data_dir = _dep_loader.out_of_folder_data_dir()

    assert data_dir == base / "ankimcp" / _ADDON_FOLDER_NAME


def test_resolver_has_no_destructive_side_effect_when_preferred_root_used(
    monkeypatch, tmp_path, fake_cache_dir, install_mw
):
    """Fix 1 regression: `_resolve_cache_root()` only DECIDES a root -- it
    must never delete the legacy cache itself, even when it resolves to the
    preferred out-of-folder root. Cleanup only happens after a caller's
    ensure-call has actually succeeded from that root (see
    `_cleanup_legacy_cache_dir_if_out_of_folder` in dependency_loader.py and
    the tests in test_dependency_loader.py exercising that seam)."""
    fake_cache_dir.mkdir(parents=True)
    (fake_cache_dir / "leftover.txt").write_text("stale")
    install_mw(_FakeMw(str(tmp_path / "anki_base")))

    root = _dep_loader._resolve_cache_root()

    assert root != fake_cache_dir
    assert fake_cache_dir.exists()
    assert (fake_cache_dir / "leftover.txt").exists()


def test_resolver_leaves_legacy_cache_alone_in_fallback_mode(fake_cache_dir, install_mw):
    fake_cache_dir.mkdir(parents=True)
    (fake_cache_dir / "leftover.txt").write_text("stale")
    install_mw(None)

    _dep_loader._resolve_cache_root()

    assert fake_cache_dir.exists()
    assert (fake_cache_dir / "leftover.txt").exists()


# ---------------------------------------------------------------------------
# Real (non-monkeypatched) `_is_writable_dir` probe -- fix 5
# ---------------------------------------------------------------------------

def test_resolver_real_probe_falls_back_when_cache_path_is_a_file(
    tmp_path, fake_cache_dir, install_mw
):
    """Exercises the REAL `_is_writable_dir` probe (not monkeypatched away):
    if `cache` already exists as a plain FILE instead of a directory,
    `Path.mkdir(parents=True, exist_ok=True)` raises and the resolver must
    fall back to the legacy cache."""
    base = tmp_path / "anki_base"
    addon_dir = base / "ankimcp" / _ADDON_FOLDER_NAME
    addon_dir.mkdir(parents=True)
    (addon_dir / "cache").write_text("not a directory")
    install_mw(_FakeMw(str(base)))

    root = _dep_loader._resolve_cache_root()

    assert root == fake_cache_dir


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="chmod-based read-only directories are a no-op on Windows and for root",
)
def test_resolver_real_probe_falls_back_when_parent_dir_read_only(
    tmp_path, fake_cache_dir, install_mw
):
    """Exercises the REAL `_is_writable_dir` probe against a genuinely
    read-only parent directory: `mkdir` must raise `PermissionError` and the
    resolver must fall back to the legacy cache, leaving the read-only dir
    empty (no stray probe file)."""
    base = tmp_path / "anki_base"
    ankimcp_dir = base / "ankimcp"
    ankimcp_dir.mkdir(parents=True)
    ankimcp_dir.chmod(0o500)  # r-x: can't create a subdir inside it

    try:
        install_mw(_FakeMw(str(base)))
        root = _dep_loader._resolve_cache_root()
    finally:
        ankimcp_dir.chmod(0o700)  # restore so tmp_path cleanup can remove it

    assert root == fake_cache_dir
    assert list(ankimcp_dir.iterdir()) == []


def test_resolver_real_probe_leaves_no_stray_file_on_success(
    tmp_path, fake_cache_dir, install_mw
):
    """The real `_is_writable_dir` probe writes then removes its own marker
    file; a successful resolve must leave the preferred cache dir clean."""
    base = tmp_path / "anki_base"
    install_mw(_FakeMw(str(base)))

    root = _dep_loader._resolve_cache_root()

    assert root.is_dir()
    assert list(root.iterdir()) == []


# ---------------------------------------------------------------------------
# `_legacy_cache_in_use()` / `_cleanup_legacy_cache_dir()` sys.path guard --
# fix 2
# ---------------------------------------------------------------------------

def test_cleanup_legacy_cache_dir_noop_when_on_sys_path(fake_cache_dir):
    """A native module loaded from (a subpath of) the legacy cache must
    block cleanup -- deleting around it would recreate the Windows gutting
    bug this whole cache relocation exists to avoid."""
    sub = fake_cache_dir / "pydantic_core_pkg"
    sub.mkdir(parents=True)
    (sub / "marker.txt").write_text("in use")

    original_sys_path = list(sys.path)
    sys.path.insert(0, str(sub))
    try:
        _dep_loader._cleanup_legacy_cache_dir()
        assert fake_cache_dir.exists()
    finally:
        sys.path[:] = original_sys_path


def test_legacy_cache_in_use_ignores_prefix_only_match(fake_cache_dir):
    """A sibling dir whose name merely STARTS WITH the legacy dir's name (e.g.
    `<CACHE_DIR>_x`) is a different directory entirely and must NOT be treated
    as the legacy cache being in use -- the check has to be a path-boundary
    match (`==` or `+ os.sep` prefix), not a raw string prefix."""
    sibling = Path(str(fake_cache_dir) + "_x")
    sibling.mkdir(parents=True)

    original_sys_path = list(sys.path)
    sys.path.insert(0, str(sibling))
    try:
        assert not _dep_loader._legacy_cache_in_use()
    finally:
        sys.path[:] = original_sys_path


def test_cleanup_legacy_cache_dir_removes_when_not_on_sys_path(fake_cache_dir):
    fake_cache_dir.mkdir(parents=True)
    (fake_cache_dir / "marker.txt").write_text("stale")

    assert not _dep_loader._legacy_cache_in_use()

    _dep_loader._cleanup_legacy_cache_dir()

    assert not fake_cache_dir.exists()


# ---------------------------------------------------------------------------
# `_cleanup_legacy_cache_dir_if_out_of_folder()` seam -- fix 1
# ---------------------------------------------------------------------------

def test_cleanup_seam_is_noop_when_root_is_the_legacy_dir(fake_cache_dir):
    fake_cache_dir.mkdir(parents=True)
    (fake_cache_dir / "marker.txt").write_text("x")

    _dep_loader._cleanup_legacy_cache_dir_if_out_of_folder(fake_cache_dir)

    assert fake_cache_dir.exists()


def test_cleanup_seam_removes_legacy_when_root_is_preferred(tmp_path, fake_cache_dir):
    fake_cache_dir.mkdir(parents=True)
    (fake_cache_dir / "marker.txt").write_text("x")

    _dep_loader._cleanup_legacy_cache_dir_if_out_of_folder(tmp_path / "preferred")

    assert not fake_cache_dir.exists()
