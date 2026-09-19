"""Cleanup-only hook for the native-dependency cache on add-on removal.

Anki removes an add-on (on update or uninstall) by deleting its whole folder.
The native-dependency cache used to live inside that folder; ``dependency_
loader.py`` now prefers a location outside it (see its ``_resolve_cache_root``)
specifically so a locked ``.pyd`` on Windows can't gut the delete. This module
is the other half of that fix: since the out-of-folder cache is no longer
inside the folder Anki deletes, it has to be cleaned up separately when the
add-on is removed.

This module depends on ``dependency_loader`` (one direction only) for the
cache paths; it never reaches back into it.

Windows specifics: a loaded native extension can be RENAMED/MOVED on the same
volume while in use, but not deleted. So on Windows we ``os.rename`` the cache
into the temp dir instead of deleting it — freeing the add-on's data
directory immediately without touching the locked file itself. We deliberately
never use ``shutil.move``: on a failed same-volume rename it falls back to
copy+delete, which can copy the (still-open) ``.pyd`` successfully and then
fail to delete the source, leaving both copies behind instead of one.

The registered hook (``on_addons_dialog_will_delete_addons``) must never raise:
Anki drops a hook callback that raises and does not re-invoke it, so an
uncaught exception here would look like nothing happened, and worse, a
re-raise would abort the user's delete/update entirely.

A second hook, ``on_addon_manager_will_install_addon``, closes the remaining
gap: Anki's own add-on UPDATE path (``AddonManager.install`` -> ``_install``
-> ``backupUserFiles`` -> ``deleteAddon``) never fires
``addons_dialog_will_delete_addons``, so an install with a legacy in-folder
cache still present was still exposed to the original Windows update failure
this module exists to fix. That hook relocates the legacy cache whenever its
directory exists (not gated on any "fallback mode" check -- see
``relocate_legacy_cache_before_update``'s docstring for why the
``_legacy_cache_in_use()`` guard used elsewhere doesn't apply here) and can
run on a BACKGROUND THREAD (AnkiWeb updates run
``taskman.run_in_background``), so it must stay Qt/UI-free, and a re-raise
there aborts Anki's WHOLE BATCH UPDATE of every add-on -- not just ours -- so
it is wrapped just as defensively as the delete hook.
"""

import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from . import dependency_loader
from .file_log import release_log_handle

logger = logging.getLogger(__name__)


def _unique_temp_destination(temp_dir: Path, addon_folder_name: str, ts: int) -> Path:
    """Pick a not-yet-existing path under ``temp_dir`` to rename a cache dir into."""
    base_name = f"ankimcp-cache-{addon_folder_name}-{ts}"
    candidate = temp_dir / base_name
    n = 1
    while candidate.exists():
        candidate = temp_dir / f"{base_name}-{n}"
        n += 1
    return candidate


def _remove_or_relocate(
    path: Path,
    *,
    is_windows: bool,
    temp_dir: Path,
    addon_folder_name: str,
) -> None:
    """Delete ``path`` outright (POSIX), or move it out of the way (Windows).

    Best-effort throughout. On Windows, if the rename itself fails (e.g. the
    temp dir is on another volume, raising WinError 17), the failure is
    swallowed and ``path`` is left in place rather than partially handled.
    """
    if not path.exists():
        return

    if not is_windows:
        shutil.rmtree(path, ignore_errors=True)
        return

    dest = _unique_temp_destination(temp_dir, addon_folder_name, int(time.time()))
    try:
        os.rename(path, dest)
    except OSError as exc:
        logger.warning(
            "Could not move %s to %s during add-on removal cleanup "
            "(leaving it in place): %r",
            path,
            dest,
            exc,
        )


def cleanup_addon_cache(
    addon_folder_name: str,
    *,
    out_of_folder_dir: Optional[Path],
    legacy_cache_dir: Path,
    is_windows: bool,
    temp_dir: Path,
) -> None:
    """Remove (or relocate, on Windows) this add-on's native-dependency cache.

    Pure function over explicit paths/flags — no global state read here — so
    it's directly unit-testable. ``out_of_folder_dir`` is only acted on when it
    exists (fallback-mode installs never created it); ``legacy_cache_dir`` is
    only acted on when it exists — an out-of-folder install never populated it,
    and ``dependency_loader._cleanup_legacy_cache_dir_if_out_of_folder`` removes
    it only once an ensure-call has actually succeeded from the out-of-folder
    root, so it can legitimately still be here after a failed first attempt
    (e.g. offline) or while it is pinned on ``sys.path``.
    """
    for target in (out_of_folder_dir, legacy_cache_dir):
        if target is not None:
            _remove_or_relocate(
                target,
                is_windows=is_windows,
                temp_dir=temp_dir,
                addon_folder_name=addon_folder_name,
            )

    if out_of_folder_dir is not None:
        ankimcp_root = out_of_folder_dir.parent
        try:
            if ankimcp_root.is_dir() and not any(ankimcp_root.iterdir()):
                os.rmdir(ankimcp_root)
        except OSError:
            pass


def on_addons_dialog_will_delete_addons(dialog, ids: list) -> None:
    """``gui_hooks.addons_dialog_will_delete_addons`` handler — cleanup only.

    Fires before Anki deletes the add-on folder(s) named in ``ids`` (folder
    names, e.g. ``124672614``). Only acts when OUR folder name is among them.
    Wrapped end-to-end so no exception can escape: Anki drops a raising hook
    callback, and a re-raise would abort the delete for the user.
    """
    try:
        addon_folder_name = dependency_loader._addon_folder_name()
        if addon_folder_name not in ids:
            return

        cleanup_addon_cache(
            addon_folder_name,
            out_of_folder_dir=dependency_loader.out_of_folder_data_dir(),
            legacy_cache_dir=dependency_loader.CACHE_DIR,
            is_windows=(sys.platform == "win32"),
            temp_dir=Path(tempfile.gettempdir()),
        )
    except Exception:  # noqa: BLE001 — must never abort the add-on delete
        logger.warning("Native cache cleanup on add-on removal failed", exc_info=True)


def relocate_legacy_cache_before_update(
    addon_folder_name: str,
    *,
    legacy_cache_dir: Path,
    is_windows: bool,
    temp_dir: Path,
) -> None:
    """Relocate (Windows only) the LEGACY in-folder cache before Anki's
    update path deletes the add-on folder out from under it.

    Runs whenever ``legacy_cache_dir`` EXISTS on disk -- there is no
    "fallback mode" check here; the directory's mere presence is the signal
    that the in-folder cache is (or was) in use and needs to survive the
    folder replacement. Only does anything on Windows: a loaded native
    extension can be renamed but not deleted while in use, so the cache has
    to be moved out of the folder Anki is about to replace. On POSIX, Anki's
    own delete handles it and this is a no-op. Never touches the
    out-of-folder cache — that one lives outside the folder being replaced
    and must survive the update untouched.

    Deliberately does NOT apply ``dependency_loader._legacy_cache_in_use()``,
    the guard ``_cleanup_legacy_cache_dir`` uses before DELETING the legacy
    cache elsewhere. That guard exists to avoid deleting around a native
    extension the process still has open — a delete of a locked ``.pyd``/
    ``.so`` fails outright on Windows. This function RENAMES instead, and a
    rename of an open file is exactly what Windows permits (unlike a delete)
    — that permission gap is the whole reason this relocation runs before
    Anki's own delete rather than relying on it. Applying the in-use guard
    here would skip the one case this function exists to handle: relocating
    a cache whose native extension is still loaded.

    Reuses ``_remove_or_relocate``, the exact same relocation primitive
    ``cleanup_addon_cache`` uses for the delete path, so both paths behave
    identically for the legacy cache.
    """
    if not is_windows:
        return
    _remove_or_relocate(
        legacy_cache_dir,
        is_windows=is_windows,
        temp_dir=temp_dir,
        addon_folder_name=addon_folder_name,
    )


def on_addon_manager_will_install_addon(manager, module: str) -> None:
    """``gui_hooks.addon_manager_will_install_addon`` handler.

    Fires before Anki installs or updates an add-on, for both ``.ankiaddon``
    installs (main thread) and AnkiWeb updates (background thread, via
    ``taskman.run_in_background``). Only acts when ``module`` is OUR add-on
    folder name — installing/updating any other add-on is a no-op.

    When it is ours:

      1. Relocate the legacy in-folder cache FIRST (Windows only; see
         ``relocate_legacy_cache_before_update``) whenever that directory
         exists. Never touches the out-of-folder cache.
      2. Release the file-log handle LAST (see ``file_log.release_log_handle``),
         so Anki's ``backupUserFiles`` rename of ``user_files`` can never be
         blocked by our own open ``ankimcp.log``.

    Relocation runs first, deliberately, so the handler's own INFO/WARNING
    lines about the relocation still reach the file before the log handle is
    closed — reversing the order would silence exactly the log lines someone
    debugging a failed relocation would need.

    Each step is wrapped on its own so a failure in one (e.g. the relocation
    helper raising) never skips the other, and the whole body is wrapped again
    so nothing can escape: Anki drops a raising hook callback, and on this
    particular hook a re-raise aborts the update of every add-on in the batch,
    not just ours.
    """
    try:
        addon_folder_name = dependency_loader._addon_folder_name()
        if module != addon_folder_name:
            return

        try:
            relocate_legacy_cache_before_update(
                addon_folder_name,
                legacy_cache_dir=dependency_loader.CACHE_DIR,
                is_windows=(sys.platform == "win32"),
                temp_dir=Path(tempfile.gettempdir()),
            )
        except Exception:
            logger.warning(
                "Legacy cache relocation before add-on update failed",
                exc_info=True,
            )

        try:
            release_log_handle(
                "Releasing file-log handle before add-on update/install "
                f"of {addon_folder_name}."
            )
        except Exception:
            logger.warning(
                "Failed to release file-log handle before add-on update",
                exc_info=True,
            )
    except Exception:  # noqa: BLE001 — must never abort the add-on update
        logger.warning(
            "Native cache handling before add-on update failed", exc_info=True
        )
