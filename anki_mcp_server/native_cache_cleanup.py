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
