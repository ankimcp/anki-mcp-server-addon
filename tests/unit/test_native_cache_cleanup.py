"""Unit tests for the native-dependency cache cleanup / relocation hooks.

Covers all three ``native_cache_cleanup`` entry points:

* ``on_addons_dialog_will_delete_addons`` fires before Anki deletes an add-on's
  folder (update or uninstall, triggered from the Add-ons dialog). Since the
  addon's cache now lives OUTSIDE that folder (see ``dependency_loader.
  _resolve_cache_root``), it would otherwise be orphaned on every removal --
  this hook cleans it up.
* ``relocate_legacy_cache_before_update`` is the pure-logic Windows-only
  relocation primitive for the LEGACY in-folder cache, shared by the delete
  path above and the install hook below.
* ``on_addon_manager_will_install_addon`` fires before Anki installs or
  updates an add-on (including the AnkiWeb update path, which never fires
  ``addons_dialog_will_delete_addons``). It relocates the legacy cache (via
  ``relocate_legacy_cache_before_update``) and releases the file-log handle,
  in that order, so Anki's own install/update machinery doesn't get blocked
  by a locked native extension or an open log file.

On Windows the cache can contain a still-loaded native extension (``.pyd``),
which can be renamed/moved but not deleted while the process holds it open, so
the Windows branches relocate instead of deleting -- and must never use
``shutil.move`` (its cross-volume copy+delete fallback can leave both a copy
and the locked original behind).

Both hooks must never raise: Anki drops (and never retries) a hook callback
that raises, and a re-raise here would abort the user's delete/update -- for
the install hook specifically, a re-raise aborts Anki's whole batch update of
every add-on, not just this one.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import anki_mcp_server  # noqa: F401 -- triggers vendor path setup

from anki_mcp_server import dependency_loader
from anki_mcp_server.native_cache_cleanup import (
    cleanup_addon_cache,
    on_addon_manager_will_install_addon,
    on_addons_dialog_will_delete_addons,
    relocate_legacy_cache_before_update,
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


# ---------------------------------------------------------------------------
# relocate_legacy_cache_before_update -- pure logic, shared with the delete
# path's _remove_or_relocate primitive
# ---------------------------------------------------------------------------

def test_relocate_legacy_cache_windows_moves_into_temp_dir(tmp_path):
    legacy = _make_dir_with_file(tmp_path / "legacy_cache")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    relocate_legacy_cache_before_update(
        "myaddon", legacy_cache_dir=legacy, is_windows=True, temp_dir=temp_dir
    )

    assert not legacy.exists()
    relocated = list(temp_dir.iterdir())
    assert len(relocated) == 1
    assert relocated[0].name.startswith("ankimcp-cache-myaddon-")
    assert (relocated[0] / "marker.txt").exists()


def test_relocate_legacy_cache_posix_leaves_it_alone(tmp_path):
    legacy = _make_dir_with_file(tmp_path / "legacy_cache")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    relocate_legacy_cache_before_update(
        "myaddon", legacy_cache_dir=legacy, is_windows=False, temp_dir=temp_dir
    )

    # POSIX: Anki's own delete handles it -- this is a no-op.
    assert legacy.exists()
    assert (legacy / "marker.txt").exists()
    assert list(temp_dir.iterdir()) == []


def test_relocate_legacy_cache_no_legacy_cache_is_noop(tmp_path):
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    # Must not raise even though the legacy cache doesn't exist.
    relocate_legacy_cache_before_update(
        "myaddon",
        legacy_cache_dir=tmp_path / "does_not_exist",
        is_windows=True,
        temp_dir=temp_dir,
    )

    assert list(temp_dir.iterdir()) == []


def test_relocate_legacy_cache_never_calls_shutil_move(tmp_path, monkeypatch):
    import anki_mcp_server.native_cache_cleanup as ncc

    legacy = _make_dir_with_file(tmp_path / "legacy_cache")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    def _boom(*args, **kwargs):
        raise AssertionError("shutil.move must never be called")

    monkeypatch.setattr(ncc.shutil, "move", _boom)

    relocate_legacy_cache_before_update(
        "myaddon", legacy_cache_dir=legacy, is_windows=True, temp_dir=temp_dir
    )

    assert not legacy.exists()


def test_relocate_legacy_cache_rename_failure_is_swallowed(tmp_path, monkeypatch):
    import anki_mcp_server.native_cache_cleanup as ncc

    legacy = _make_dir_with_file(tmp_path / "legacy_cache")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    def _raise_oserror(*args, **kwargs):
        raise OSError(17, "cannot move across volumes")

    monkeypatch.setattr(ncc.os, "rename", _raise_oserror)

    # Must not raise.
    relocate_legacy_cache_before_update(
        "myaddon", legacy_cache_dir=legacy, is_windows=True, temp_dir=temp_dir
    )

    assert legacy.exists()
    assert (legacy / "marker.txt").exists()


# ---------------------------------------------------------------------------
# on_addon_manager_will_install_addon -- the registered
# gui_hooks.addon_manager_will_install_addon handler
# ---------------------------------------------------------------------------

def test_install_hook_noop_for_other_addon(tmp_path, monkeypatch):
    import anki_mcp_server.native_cache_cleanup as ncc

    log_calls = []
    relocate_calls = []
    monkeypatch.setattr(ncc, "release_log_handle", lambda *a, **k: log_calls.append(a))
    monkeypatch.setattr(
        ncc, "relocate_legacy_cache_before_update", lambda *a, **k: relocate_calls.append(a)
    )

    on_addon_manager_will_install_addon(manager=object(), module="some_other_addon")

    assert log_calls == []
    assert relocate_calls == []


def test_install_hook_releases_log_and_relocates_legacy_cache_windows(
    tmp_path, monkeypatch
):
    import anki_mcp_server.native_cache_cleanup as ncc

    our_id = dependency_loader._addon_folder_name()
    legacy = _make_dir_with_file(tmp_path / "legacy_cache")
    out_of_folder = _make_dir_with_file(tmp_path / "ankimcp" / our_id / "cache")
    ankimcp_root = tmp_path / "ankimcp"
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    monkeypatch.setattr(dependency_loader, "CACHE_DIR", legacy)
    # Patches the real stdlib tempfile module (module-global state) --
    # this test is therefore serial-only, not xdist-safe.
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_dir))
    # Patches the real stdlib sys module (module-global state) -- like the
    # tempfile.gettempdir patch above, this test is serial-only, not
    # xdist-safe.
    monkeypatch.setattr(sys, "platform", "win32")

    log_calls = []
    monkeypatch.setattr(ncc, "release_log_handle", lambda *a, **k: log_calls.append(a))

    on_addon_manager_will_install_addon(manager=object(), module=our_id)

    assert len(log_calls) == 1
    # Legacy cache relocated (Windows) ...
    assert not legacy.exists()
    relocated = [
        p for p in temp_dir.iterdir() if p.name.startswith(f"ankimcp-cache-{our_id}-")
    ]
    assert len(relocated) == 1
    # ... but the out-of-folder cache and its `ankimcp` root are NEVER touched.
    assert out_of_folder.exists()
    assert (out_of_folder / "marker.txt").exists()
    assert ankimcp_root.exists()


def test_install_hook_posix_leaves_legacy_cache_alone(tmp_path, monkeypatch):
    import anki_mcp_server.native_cache_cleanup as ncc

    our_id = dependency_loader._addon_folder_name()
    legacy = _make_dir_with_file(tmp_path / "legacy_cache")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    monkeypatch.setattr(dependency_loader, "CACHE_DIR", legacy)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_dir))
    monkeypatch.setattr(sys, "platform", "linux")

    log_calls = []
    monkeypatch.setattr(ncc, "release_log_handle", lambda *a, **k: log_calls.append(a))

    on_addon_manager_will_install_addon(manager=object(), module=our_id)

    assert len(log_calls) == 1
    assert legacy.exists()
    assert (legacy / "marker.txt").exists()


def test_install_hook_no_legacy_cache_only_releases_log(tmp_path, monkeypatch):
    import anki_mcp_server.native_cache_cleanup as ncc

    our_id = dependency_loader._addon_folder_name()
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    monkeypatch.setattr(dependency_loader, "CACHE_DIR", tmp_path / "does_not_exist")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_dir))

    log_calls = []
    monkeypatch.setattr(ncc, "release_log_handle", lambda *a, **k: log_calls.append(a))

    on_addon_manager_will_install_addon(manager=object(), module=our_id)

    assert len(log_calls) == 1
    assert list(temp_dir.iterdir()) == []


def test_install_hook_log_release_failure_is_swallowed_and_cache_step_still_runs(
    tmp_path, monkeypatch
):
    import anki_mcp_server.native_cache_cleanup as ncc

    our_id = dependency_loader._addon_folder_name()
    legacy = _make_dir_with_file(tmp_path / "legacy_cache")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    monkeypatch.setattr(dependency_loader, "CACHE_DIR", legacy)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_dir))
    monkeypatch.setattr(sys, "platform", "win32")

    def _boom(*a, **k):
        raise RuntimeError("log release blew up")

    monkeypatch.setattr(ncc, "release_log_handle", _boom)

    # Must not raise, and the cache relocation must still happen.
    on_addon_manager_will_install_addon(manager=object(), module=our_id)

    assert not legacy.exists()
    relocated = [
        p for p in temp_dir.iterdir() if p.name.startswith(f"ankimcp-cache-{our_id}-")
    ]
    assert len(relocated) == 1


def test_install_hook_relocate_failure_is_swallowed(tmp_path, monkeypatch):
    import anki_mcp_server.native_cache_cleanup as ncc

    our_id = dependency_loader._addon_folder_name()

    log_calls = []
    monkeypatch.setattr(ncc, "release_log_handle", lambda *a, **k: log_calls.append(a))

    def _boom(*a, **k):
        raise RuntimeError("relocation blew up")

    monkeypatch.setattr(ncc, "relocate_legacy_cache_before_update", _boom)

    # Must not raise, and the log handle must still be released even though
    # relocation (which now runs first) failed.
    on_addon_manager_will_install_addon(manager=object(), module=our_id)

    assert len(log_calls) == 1


def test_install_hook_never_raises_on_top_level_failure(monkeypatch):
    import anki_mcp_server.native_cache_cleanup as ncc

    def _boom(*args, **kwargs):
        raise RuntimeError("addon folder name lookup blew up")

    monkeypatch.setattr(dependency_loader, "_addon_folder_name", _boom)

    # Must not raise -- a re-raise here would abort Anki's whole batch update.
    on_addon_manager_will_install_addon(manager=object(), module="whatever")


def test_install_hook_relocates_before_releasing_log(tmp_path, monkeypatch):
    """Relocation must run BEFORE the log handle is released, so the
    handler's own INFO/WARNING lines about the relocation still reach the
    file before it closes."""
    import anki_mcp_server.native_cache_cleanup as ncc

    our_id = dependency_loader._addon_folder_name()

    order = []
    monkeypatch.setattr(
        ncc, "relocate_legacy_cache_before_update", lambda *a, **k: order.append("relocate")
    )
    monkeypatch.setattr(
        ncc, "release_log_handle", lambda *a, **k: order.append("release_log")
    )

    on_addon_manager_will_install_addon(manager=object(), module=our_id)

    assert order == ["relocate", "release_log"]


def test_install_hook_never_calls_shutil_move(tmp_path, monkeypatch):
    import anki_mcp_server.native_cache_cleanup as ncc

    our_id = dependency_loader._addon_folder_name()
    legacy = _make_dir_with_file(tmp_path / "legacy_cache")
    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()

    monkeypatch.setattr(dependency_loader, "CACHE_DIR", legacy)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_dir))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ncc, "release_log_handle", lambda *a, **k: None)

    def _boom(*args, **kwargs):
        raise AssertionError("shutil.move must never be called")

    monkeypatch.setattr(ncc.shutil, "move", _boom)

    on_addon_manager_will_install_addon(manager=object(), module=our_id)

    assert not legacy.exists()
