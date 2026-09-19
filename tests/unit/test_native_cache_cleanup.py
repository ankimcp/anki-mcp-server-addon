"""Unit tests for the add-on-removal native-dependency cache cleanup hook.

``native_cache_cleanup.on_addons_dialog_will_delete_addons`` fires before Anki
deletes an add-on's folder (update or uninstall). Since the addon's cache now
lives OUTSIDE that folder (see ``dependency_loader._resolve_cache_root``), it
would otherwise be orphaned on every removal -- this hook cleans it up.

On Windows the cache can contain a still-loaded native extension (``.pyd``),
which can be renamed/moved but not deleted while the process holds it open, so
the Windows branch relocates instead of deleting -- and must never use
``shutil.move`` (its cross-volume copy+delete fallback can leave both a copy
and the locked original behind).

The handler itself must never raise: Anki drops (and never retries) a hook
callback that raises, and a re-raise here would abort the user's delete.
"""
from __future__ import annotations

from pathlib import Path

import anki_mcp_server  # noqa: F401 -- triggers vendor path setup

from anki_mcp_server import dependency_loader
from anki_mcp_server.native_cache_cleanup import (
    cleanup_addon_cache,
    on_addons_dialog_will_delete_addons,
)


def _make_dir_with_file(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "marker.txt").write_text("x")
    return path


# ---------------------------------------------------------------------------
# cleanup_addon_cache -- pure logic over explicit paths
# ---------------------------------------------------------------------------

def test_posix_deletes_both_targets(tmp_path):
    out_of_folder = tmp_path / "ankimcp" / "myaddon"
    _make_dir_with_file(out_of_folder / "cache")
    legacy = _make_dir_with_file(tmp_path / "legacy_cache")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    cleanup_addon_cache(
        "myaddon",
        out_of_folder_dir=out_of_folder,
        legacy_cache_dir=legacy,
        is_windows=False,
        temp_dir=temp_dir,
    )

    assert not out_of_folder.exists()
    assert not legacy.exists()
    # POSIX branch deletes in place -- nothing should show up in temp_dir.
    assert list(temp_dir.iterdir()) == []


def test_windows_moves_both_targets_into_temp_dir(tmp_path):
    out_of_folder = tmp_path / "ankimcp" / "myaddon"
    _make_dir_with_file(out_of_folder / "cache")
    legacy = _make_dir_with_file(tmp_path / "legacy_cache")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    cleanup_addon_cache(
        "myaddon",
        out_of_folder_dir=out_of_folder,
        legacy_cache_dir=legacy,
        is_windows=True,
        temp_dir=temp_dir,
    )

    assert not out_of_folder.exists()
    assert not legacy.exists()
    relocated = list(temp_dir.iterdir())
    assert len(relocated) == 2
    for entry in relocated:
        assert entry.name.startswith("ankimcp-cache-myaddon-")
    # The data dir was moved whole, with its cache/ content intact, and the
    # legacy cache (a flat dir with the marker at its own top level) was
    # moved whole too -- exactly one of each shape, regardless of order.
    has_nested_marker = [
        entry for entry in relocated if (entry / "cache" / "marker.txt").exists()
    ]
    has_flat_marker = [
        entry for entry in relocated if (entry / "marker.txt").exists()
    ]
    assert len(has_nested_marker) == 1
    assert len(has_flat_marker) == 1


def test_windows_branch_never_calls_shutil_move(tmp_path, monkeypatch):
    import anki_mcp_server.native_cache_cleanup as ncc

    out_of_folder = tmp_path / "ankimcp" / "myaddon"
    _make_dir_with_file(out_of_folder / "cache")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    def _boom(*args, **kwargs):
        raise AssertionError("shutil.move must never be called on the Windows branch")

    monkeypatch.setattr(ncc.shutil, "move", _boom)

    cleanup_addon_cache(
        "myaddon",
        out_of_folder_dir=out_of_folder,
        legacy_cache_dir=tmp_path / "does_not_exist",
        is_windows=True,
        temp_dir=temp_dir,
    )

    assert not out_of_folder.exists()


def test_windows_rename_failure_is_swallowed_and_target_left_in_place(
    tmp_path, monkeypatch
):
    import anki_mcp_server.native_cache_cleanup as ncc

    out_of_folder = tmp_path / "ankimcp" / "myaddon"
    _make_dir_with_file(out_of_folder / "cache")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    def _raise_oserror(*args, **kwargs):
        raise OSError(17, "cannot move across volumes")

    monkeypatch.setattr(ncc.os, "rename", _raise_oserror)

    # Must not raise.
    cleanup_addon_cache(
        "myaddon",
        out_of_folder_dir=out_of_folder,
        legacy_cache_dir=tmp_path / "does_not_exist",
        is_windows=True,
        temp_dir=temp_dir,
    )

    # Target is left exactly where it was.
    assert out_of_folder.exists()
    assert (out_of_folder / "cache" / "marker.txt").exists()
    assert list(temp_dir.iterdir()) == []


def test_missing_targets_are_no_ops(tmp_path):
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    # Must not raise even though neither target exists.
    cleanup_addon_cache(
        "myaddon",
        out_of_folder_dir=tmp_path / "does_not_exist_1",
        legacy_cache_dir=tmp_path / "does_not_exist_2",
        is_windows=False,
        temp_dir=temp_dir,
    )


def test_removes_empty_ankimcp_root_after_cleanup(tmp_path):
    ankimcp_root = tmp_path / "ankimcp"
    out_of_folder = ankimcp_root / "myaddon"
    _make_dir_with_file(out_of_folder / "cache")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    cleanup_addon_cache(
        "myaddon",
        out_of_folder_dir=out_of_folder,
        legacy_cache_dir=tmp_path / "does_not_exist",
        is_windows=False,
        temp_dir=temp_dir,
    )

    assert not ankimcp_root.exists()


def test_leaves_ankimcp_root_when_other_addons_still_have_a_cache(tmp_path):
    ankimcp_root = tmp_path / "ankimcp"
    out_of_folder = ankimcp_root / "myaddon"
    _make_dir_with_file(out_of_folder / "cache")
    # Simulate another add-on's cache sharing the same `ankimcp` parent dir.
    other_addon = ankimcp_root / "otheraddon"
    _make_dir_with_file(other_addon / "cache")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    cleanup_addon_cache(
        "myaddon",
        out_of_folder_dir=out_of_folder,
        legacy_cache_dir=tmp_path / "does_not_exist",
        is_windows=False,
        temp_dir=temp_dir,
    )

    # myaddon's own data dir is gone, but the sibling ankimcp root survives
    # because otheraddon's cache still has content -- the not-empty guard.
    assert not out_of_folder.exists()
    assert ankimcp_root.exists()
    assert (other_addon / "cache" / "marker.txt").exists()


# ---------------------------------------------------------------------------
# on_addons_dialog_will_delete_addons -- the registered gui_hooks handler
# ---------------------------------------------------------------------------

def test_hook_does_nothing_when_our_id_is_absent(tmp_path, monkeypatch):
    import anki_mcp_server.native_cache_cleanup as ncc

    called = []
    monkeypatch.setattr(ncc, "cleanup_addon_cache", lambda *a, **k: called.append((a, k)))

    on_addons_dialog_will_delete_addons(dialog=object(), ids=["some_other_addon"])

    assert called == []


def test_hook_calls_cleanup_when_our_id_is_present(tmp_path, monkeypatch, install_mw):
    import sys
    import tempfile

    import anki_mcp_server.native_cache_cleanup as ncc

    # Derived independently from the module's own `__file__`, NOT via
    # `dependency_loader._addon_folder_name()` (both now happen to be the same
    # expression), so this still catches a regression like a symlinked add-on
    # dir making the two disagree.
    our_id = Path(dependency_loader.__file__).resolve().parent.name

    class _FakePm:
        def __init__(self, base):
            self.base = base

    class _FakeMw:
        def __init__(self, base):
            self.pm = _FakePm(base)

    base = tmp_path / "anki_base"
    install_mw(_FakeMw(str(base)))

    called = []
    monkeypatch.setattr(
        ncc,
        "cleanup_addon_cache",
        lambda addon_folder_name, **kwargs: called.append((addon_folder_name, kwargs)),
    )

    on_addons_dialog_will_delete_addons(dialog=object(), ids=[our_id, "unrelated"])

    assert len(called) == 1
    addon_folder_name, kwargs = called[0]
    assert addon_folder_name == our_id
    assert kwargs["out_of_folder_dir"] == base / "ankimcp" / our_id
    assert kwargs["legacy_cache_dir"] == dependency_loader.CACHE_DIR
    assert kwargs["is_windows"] == (sys.platform == "win32")
    assert kwargs["temp_dir"] == Path(tempfile.gettempdir())


def test_hook_never_raises_even_when_cleanup_blows_up(monkeypatch):
    import anki_mcp_server.native_cache_cleanup as ncc

    our_id = dependency_loader._addon_folder_name()

    def _boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ncc, "cleanup_addon_cache", _boom)

    # Must not raise -- Anki drops a raising hook callback and a re-raise would
    # abort the user's delete.
    on_addons_dialog_will_delete_addons(dialog=object(), ids=[our_id])
