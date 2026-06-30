"""Lazy-loader for native dependencies - downloads the correct wheel from PyPI.

Two native deps are handled here:

* ``pydantic_core`` — never shipped in the bundle (Anki never provides it), so it
  is always downloaded on first run, pinned to the version vendored pydantic
  requires. See ``ensure_pydantic_core``.
* ``rpds`` (``rpds-py``) — the only compiled dependency in the
  mcp -> jsonschema -> referencing chain. Every Anki version that ships a
  compatible Python also ships ``rpds`` (it's a transitive dep of Anki's own
  jsonschema), so the common path is a plain ``import rpds`` with no download.
  We deliberately do NOT vendor ``rpds`` because the bundle can only carry one
  platform's binary, which crashed on Windows / Linux-aarch64 / macOS-Intel
  (issue #54). The download is a fallback for the rare case Anki doesn't provide
  it. See ``ensure_rpds``.

Each ensure-function is split into a pure-logic core (``_ensure_*_with_callbacks``)
and a thin Qt wrapper. The seam exists so headless callers (unit tests, CI
bootstrap) can drive the loader without a Qt event loop, while the Anki runtime
still gets a progress dialog. Both cores share ``_download_and_extract_wheel``
for the common download/extract/cache/sys.path flow.

Wheel selection (``_find_wheel_url``) defers to ``packaging.tags.sys_tags()`` —
the same interpreter-tag machinery pip uses — instead of ad-hoc substring
matching. This gives correct architecture/ABI/platform resolution for free,
including universal2 Apple-Silicon (issue #52) and free-threaded ``cp313t`` vs
standard ``cp313`` interpreters (issue #54). ``packaging`` is imported lazily
inside ``_find_wheel_url`` (not at module level) so the module stays importable
on source/Nix installs that don't provide it — same rationale as the other
download-only native deps.
"""

import os
import sys
import json
import logging
import time
import urllib.request
import zipfile
import shutil
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "_cache"

# Windows native-extension file lock (sharing violation). Established Windows
# tooling (MSBuild, Go, npm) retries this transient class but NOT access-denied
# (5) or missing (2/ENOENT), which don't clear on their own.
_WINERROR_SHARING_VIOLATION = 32
_WINERROR_ACCESS_DENIED = 5

# Lock-only bounded retry schedule (seconds): ~50ms, 100ms, 200ms, 400ms.
# Capped well under ~1s total across ~4 attempts.
_LOCK_RETRY_DELAYS = (0.05, 0.10, 0.20, 0.40)

# Pinned fallback version for the rpds download path.
#
# Why 0.30.0 specifically:
#   * Full cp313 + cp314 wheel coverage across win_amd64/win_arm64, macOS
#     arm64+x86_64, and manylinux x86_64+aarch64 — so the fallback works on
#     every platform/Python combo a current or near-future Anki can ship.
#   * API-compatible with the vendored referencing 0.37.0, which only uses the
#     stable HashTrieMap / HashTrieSet / List surface.
#   * 0.25.1 was rejected: it has no cp314 wheels and would break on a future
#     Anki/Python-3.14.
# This download only runs if `import rpds` fails (Anki normally provides it), so
# in practice this version is rarely materialized.
_RPDS_VERSION = "0.30.0"


def _get_required_pydantic_core_version() -> str:
    """Read the exact pydantic-core version required by vendored pydantic."""
    version_file = Path(__file__).parent / "vendor" / "shared" / "pydantic" / "version.py"
    if not version_file.exists():
        raise RuntimeError("Vendored pydantic/version.py not found")

    for line in version_file.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("_COMPATIBLE_PYDANTIC_CORE_VERSION") and "=" in stripped:
            return stripped.split("=", 1)[1].strip().strip("'\"")

    raise RuntimeError("Could not find _COMPATIBLE_PYDANTIC_CORE_VERSION in pydantic/version.py")


def _import_vendored_packaging():
    """Import the ``packaging`` pieces wheel selection needs, re-resolving the
    import fresh against ``sys.path`` (where the addon's VENDORED copy sits
    first) even if another Anki add-on already loaded an older, incompatible
    ``packaging`` into ``sys.modules``. This neutralises the ``sys.modules``
    cache shadowing that causes the bug; it does NOT defend against the residual
    (low-probability) case where another add-on has prepended an old
    ``packaging`` AHEAD of our vendor dir on ``sys.path`` — that is out of scope.

    Why this is necessary (the shadowing bug):

    * Anki runs every add-on in ONE shared Python process. The addon prepends
      its ``vendor/shared`` (clean ``packaging`` 26.2) to ``sys.path`` at
      startup — but ``sys.path`` order only decides WHICH copy is imported when
      a module is not already cached. The ``sys.modules`` cache wins outright.
    * If another add-on (e.g. AnkiConnect) imported an OLD ``packaging``
      (<= 20.9) first, ``from packaging.tags import sys_tags`` resolves to that
      cached copy. Worse, the submodule import of ``packaging.tags`` walks the
      cached package's ``__path__`` — NOT ``sys.path`` — so our vendor-path
      prepend does not help.
    * ``packaging.tags`` <= 20.9 does ``import distutils.util`` at module top.
      ``distutils`` was removed from the stdlib in Python 3.12, so on Anki
      25.07+ (Python 3.13) that import raises ``ModuleNotFoundError: No module
      named 'distutils'`` — surfacing as a confusing pydantic_core "download"
      failure even though our vendored ``packaging`` is perfectly fine.

    The fix: temporarily evict any cached ``packaging`` / ``packaging.*`` from
    ``sys.modules`` so the import below resolves fresh against ``sys.path``
    (where the vendored copy sits first), then restore the foreign copy exactly
    as it was so we remain a good citizen toward the add-on that loaded it. The
    ``finally`` guarantees the restore even if the fresh import raises.

    Thread-safety: this runs on the first-run download path, reached from
    ``ensure_pydantic_core()`` / ``ensure_rpds()``, which are called at ADD-ON
    IMPORT time (top-level in ``__init__.py``) on the main thread while Anki
    imports the add-on — before the ``profile_did_open`` hook is registered and
    before the background MCP server thread is spawned. There is no concurrent
    ``sys.modules`` mutation to race against, so the evict/import/restore
    sequence is safe without locking.
    """
    def _packaging_modules():
        return [
            name for name in sys.modules
            if name == "packaging" or name.startswith("packaging.")
        ]

    saved = {name: sys.modules.pop(name) for name in _packaging_modules()}
    try:
        from packaging.tags import sys_tags
        from packaging.utils import parse_wheel_filename, InvalidWheelFilename
        from packaging.version import InvalidVersion
        return sys_tags, parse_wheel_filename, InvalidWheelFilename, InvalidVersion
    finally:
        # Drop whatever the fresh import pulled in (our vendored copy), then put
        # the foreign copy back so the borrowing add-on sees no change.
        for name in _packaging_modules():
            del sys.modules[name]
        sys.modules.update(saved)


def _find_wheel_url(pypi_data: dict) -> str:
    """Find the best-matching wheel URL from PyPI JSON data for this interpreter.

    Selection mirrors pip: ``packaging.tags.sys_tags()`` yields every wheel tag
    the *current* interpreter can install, most-preferred-first (index 0 is the
    highest-priority tag, and priority decreases as the index grows). This single
    source of truth already encodes architecture (x86_64/arm64/aarch64), Python
    version, ABI (standard ``cp313`` vs free-threaded ``cp313t``), and the
    platform family (manylinux/musllinux/macosx/win, including universal2). We
    parse each candidate filename with ``packaging.utils.parse_wheel_filename``
    and pick the wheel that carries the tag with the smallest index in
    ``sys_tags()`` — i.e. the highest-priority match, exactly as pip would.

    This subsumes the former hand-rolled substring matching, including the
    universal2/Apple-Silicon special case (issue #52) and the ``cp313`` vs
    ``cp313t`` free-threaded confusion (issue #54).
    """
    # ``packaging`` is imported here (download path only, function scope), not at
    # module level, on purpose: it is only needed when selecting a wheel —
    # exactly like the other download-only native deps (``pydantic_core``/
    # ``rpds``), which are also imported lazily inside their ensure-functions.
    # Keeping it out of module scope means the addon stays importable on
    # source/Nix installs that provide the addon's deps from nixpkgs but NOT
    # ``packaging`` (those installs hit the ``import pydantic_core``/``import
    # rpds`` fast-path or the ``_USING_SYSTEM_PACKAGES`` short-circuit and never
    # reach wheel selection). Do NOT hoist this back to the top of the file.
    #
    # The import is delegated to ``_import_vendored_packaging`` so we get the
    # VENDORED ``packaging`` even when another add-on has poisoned ``sys.modules``
    # with an old, distutils-importing copy — see that helper's docstring.
    sys_tags, parse_wheel_filename, InvalidWheelFilename, InvalidVersion = (
        _import_vendored_packaging()
    )

    # Map each supported tag to its priority (lower index = higher priority).
    tag_priority = {tag: index for index, tag in enumerate(sys_tags())}

    best_url: str | None = None
    best_priority = len(tag_priority)  # sentinel: worse than any real match

    for url_info in pypi_data.get("urls", []):
        filename = url_info["filename"]
        if not filename.endswith(".whl"):
            continue

        try:
            _name, _version, _build, tags = parse_wheel_filename(filename)
        except (InvalidWheelFilename, InvalidVersion):
            continue

        for tag in tags:
            priority = tag_priority.get(tag)
            if priority is not None and priority < best_priority:
                best_priority = priority
                best_url = url_info["url"]

    if best_url is None:
        raise RuntimeError(
            f"No matching wheel found for {sys.implementation.name} "
            f"{sys.version_info.major}.{sys.version_info.minor} on this platform"
        )

    return best_url


def _fix_windows_pyd(cache_dir: Path, package_subdir: str) -> None:
    """Windows needs untagged .pyd files.

    Copies any platform-tagged extension module in ``cache_dir/package_subdir``
    to its untagged name so the import machinery finds it:

      pydantic_core/_pydantic_core.cp313-win_amd64.pyd -> _pydantic_core.pyd
      rpds/rpds.cp313-win_amd64.pyd                     -> rpds.pyd

    No-op off Windows or when the package dir is absent.
    """
    if not sys.platform == "win32":
        return

    package_dir = cache_dir / package_subdir
    if not package_dir.exists():
        return

    for pyd_file in package_dir.glob("*.pyd"):
        if ".cp" in pyd_file.name:
            simple_name = pyd_file.name.split(".cp")[0] + ".pyd"
            simple_path = pyd_file.parent / simple_name
            if not simple_path.exists():
                shutil.copy(pyd_file, simple_path)


def _native_extension_path(cache_dir: Path, package_subdir: str) -> Optional[Path]:
    """Locate the native extension file for a cached package.

    Returns the path to the compiled module (``_pydantic_core*.pyd`` /
    ``rpds*.pyd`` on Windows, ``*.so`` elsewhere) inside the cached package
    directory, or None if none is found (e.g. cache not populated yet).
    """
    package_dir = cache_dir / package_subdir
    if not package_dir.exists():
        return None

    suffix = ".pyd" if sys.platform == "win32" else ".so"
    candidates = sorted(package_dir.glob(f"*{suffix}"))
    return candidates[0] if candidates else None


def _classify_native_load_error(exc: OSError) -> str:
    """Classify a filesystem error opening a native extension.

    A bare ``ImportError`` from a failed ``.pyd``/``.so`` load carries NO numeric
    code (it's not an ``OSError`` subclass), which is why we pre-flight-open the
    file: an ``OSError`` from ``open()`` DOES carry ``winerror``/``errno`` to
    branch on.

    Returns one of: ``"locked"`` (transient — safe to retry), ``"access-denied"``
    (won't clear on its own), ``"missing"``, or ``"unknown"``.
    """
    # Windows: prefer the OS-specific winerror code when present.
    if sys.platform == "win32":
        winerror = getattr(exc, "winerror", None)
        if winerror == _WINERROR_SHARING_VIOLATION:
            return "locked"
        if winerror == _WINERROR_ACCESS_DENIED:
            return "access-denied"

    import errno

    if exc.errno == errno.ENOENT:
        return "missing"
    if exc.errno in (errno.EACCES, errno.EPERM):
        return "access-denied"
    if exc.errno in (errno.EBUSY, getattr(errno, "ETXTBSY", -1)):
        return "locked"
    return "unknown"


def _preflight_native_extension(
    cache_dir: Path, package_subdir: str, display_name: str
) -> str:
    """Open the cached native extension to surface lock/permission problems.

    A plain ``import`` masks these as an opaque ``ImportError`` with no errno.
    Opening the file first gives a real ``OSError`` with a code we can classify
    and log. Returns the classification (``"ok"``, ``"locked"``,
    ``"access-denied"``, ``"missing"``, or ``"unknown"``). ``"ok"`` means the
    file either opened cleanly or isn't present to pre-check (let the normal
    import path decide).
    """
    ext_path = _native_extension_path(cache_dir, package_subdir)
    if ext_path is None:
        # Nothing cached to probe — not an error; the caller's import handles it.
        return "ok"

    try:
        # Open for read; on Windows a sharing violation / locked file raises here.
        with open(ext_path, "rb") as fh:
            fh.read(1)
        return "ok"
    except OSError as exc:
        classification = _classify_native_load_error(exc)
        # Keep the winerror branch Windows-only; elsewhere just log errno.
        code_detail = (
            f"winerror={getattr(exc, 'winerror', None)}"
            if sys.platform == "win32"
            else f"errno={exc.errno}"
        )
        logger.warning(
            "Pre-flight open of %s native extension %s failed: %s [%s]: %r",
            display_name,
            ext_path.name,
            classification,
            code_detail,
            exc,
        )
        return classification


def _import_with_lock_retry(
    import_fn: Callable[[], bool],
    *,
    cache_dir: Path,
    package_subdir: str,
    display_name: str,
) -> bool:
    """Pre-flight the cached native extension, then run the import probe — with a
    lock-only bounded retry.

    Why the pre-flight drives the retry (not the import itself): when a native
    ``.pyd``/``.so`` is locked, the *import* surfaces an opaque ``ImportError``
    with NO numeric code, so we can't tell a transient lock from a corrupt
    binary from the import alone. The pre-flight ``open()`` of the same file
    DOES raise an ``OSError`` carrying ``winerror``/``errno``, which we classify.

    Per attempt we:
      1. Pre-flight-open the extension (classifies lock vs access-denied vs
         missing, and logs the detail).
      2. If "locked" — a Windows sharing violation (WinError 32) or POSIX EBUSY —
         back off and retry. This is the one class established Windows tooling
         (MSBuild/Go/npm) retries because it clears on its own.
      3. Any other classification ("ok"/"access-denied"/"missing"/"unknown") —
         stop retrying and run the import probe once. Access-denied / missing do
         NOT clear on their own, so retrying them is pointless; "ok" means the
         file is loadable and we should just import it.

    ``import_fn`` returns True on success (right version present) and False
    otherwise (wrong version, or load failure caught internally). A False result
    or a raised ``ImportError`` both mean "fall through to re-download".

    Returns True if the import eventually succeeded, False otherwise.
    """
    for attempt, delay in enumerate((0.0, *_LOCK_RETRY_DELAYS)):
        classification = _preflight_native_extension(
            cache_dir, package_subdir, display_name
        )
        if classification == "locked":
            # Transient — back off and retry the pre-flight, while we still have
            # a backoff slot left.
            if attempt < len(_LOCK_RETRY_DELAYS):
                backoff = _LOCK_RETRY_DELAYS[attempt]
                logger.info(
                    "%s native extension is locked (attempt %d); backing off %dms...",
                    display_name,
                    attempt + 1,
                    int(backoff * 1000),
                )
                time.sleep(backoff)
                continue
            # Exhausted retries while still locked — give up on the cache.
            logger.warning(
                "%s native extension still locked after %d retries; "
                "falling through to re-download",
                display_name,
                len(_LOCK_RETRY_DELAYS),
            )
            return False

        # Not locked ("ok", "access-denied", "missing", "unknown") — no point
        # retrying. Run the import probe once.
        try:
            if attempt:
                logger.info(
                    "%s lock cleared after %d retr%s; importing",
                    display_name,
                    attempt,
                    "y" if attempt == 1 else "ies",
                )
            return import_fn()
        except ImportError as exc:
            # Opaque native-load failure with no errno — can't distinguish a
            # lock here, and the pre-flight already said it isn't locked, so
            # treat as unusable and fall through to re-download.
            logger.warning("%s import failed: %r", display_name, exc)
            return False

    return False


def _atomic_swap_dir(temp_dir: Path, final_dir: Path) -> None:
    """Atomically move ``temp_dir`` into place at ``final_dir``.

    ``os.replace`` on a directory fails on Windows (and on POSIX too) when the
    target already exists and is non-empty, so we can't just replace. Instead:

      1. If ``final_dir`` exists, move it aside to a unique ``.old-*`` sibling.
      2. ``os.replace`` (atomic rename) the temp dir into ``final_dir``.
      3. Best-effort delete the moved-aside old dir.

    If step 2 fails after the old dir was moved aside, we attempt to restore it
    so we never leave the cache missing. The brief window between steps 1 and 2
    is a rename-only gap (no copy), keeping it as small as possible.
    """
    old_dir: Optional[Path] = None
    if final_dir.exists():
        old_dir = final_dir.with_name(final_dir.name + f".old-{os.getpid()}-{int(time.time()*1000)}")
        # Clear any stale leftover at the chosen name (extremely unlikely).
        shutil.rmtree(old_dir, ignore_errors=True)
        os.replace(final_dir, old_dir)

    try:
        os.replace(temp_dir, final_dir)
    except OSError:
        # Swap failed — restore the previous cache if we moved it aside, so the
        # existing good cache is never lost.
        if old_dir is not None and old_dir.exists() and not final_dir.exists():
            try:
                os.replace(old_dir, final_dir)
            except OSError:
                pass
        raise

    if old_dir is not None:
        shutil.rmtree(old_dir, ignore_errors=True)


def _sweep_stale_siblings(cache_dir: Path) -> None:
    """Remove orphaned .tmp-* and .old-* sibling directories left by failed swaps.

    Called at the start of each download attempt. Best-effort: any error is
    silently ignored — the sweep must never block a download.
    """
    parent = cache_dir.parent
    base = cache_dir.name
    try:
        for sibling in parent.iterdir():
            if sibling.name.startswith(f"{base}.tmp-") or sibling.name.startswith(f"{base}.old-"):
                shutil.rmtree(sibling, ignore_errors=True)
    except Exception:
        pass


def _download_and_extract_wheel(
    *,
    display_name: str,
    pypi_url: str,
    expected_version: str,
    cache_dir: Path,
    package_subdir: str,
    on_status: Callable[[str], None],
    on_progress: Callable[[int], None],
    is_cancelled: Callable[[], bool],
    on_error: Callable[[str], None],
    yield_ui: Callable[[], None],
) -> bool:
    """Download a wheel from PyPI, extract it into ``cache_dir`` and put it on
    ``sys.path``. Shared by the pydantic_core and rpds download paths.

    ``cache_dir`` is the package's dedicated cache subdir (e.g.
    ``_cache/pydantic_core_pkg``). ``package_subdir`` is the importable package
    directory inside the wheel (e.g. ``"pydantic_core"`` or ``"rpds"``), used for
    the Windows .pyd fixup.

    Atomic temp-swap: the wheel is downloaded and extracted into a FRESH sibling
    TEMP directory (``<cache_dir>.tmp-*``), the Windows .pyd fixup runs there, and
    the ``.version`` + ``.complete`` markers are written there. Only once the temp
    dir is fully built is it ATOMICALLY swapped into ``cache_dir``. A re-download
    therefore never destroys an existing good cache and never leaves a
    half-overwritten one. On ANY failure we clean up only the temp dir and leave
    the existing cache untouched. On success, writes markers and returns True.
    """
    # Sweep any orphaned temp/old siblings from previous failed swaps.
    # These accumulate when a swap fails or the process is SIGKILL'd mid-rename.
    # Best-effort: errors are silently ignored so this never blocks a download.
    _sweep_stale_siblings(cache_dir)

    # Fresh per-attempt temp dir, sibling of the final cache dir so the swap is a
    # same-filesystem rename. Unique name avoids colliding with a concurrent run.
    temp_dir = cache_dir.with_name(
        cache_dir.name + f".tmp-{os.getpid()}-{int(time.time()*1000)}"
    )
    marker_file = temp_dir / ".complete"
    version_file = temp_dir / ".version"

    def _cleanup_temp() -> None:
        shutil.rmtree(temp_dir, ignore_errors=True)

    try:
        on_status(f"Downloading {display_name} {expected_version} (first run only)...")
        on_progress(0)
        yield_ui()

        # Fetch PyPI metadata for the exact required version
        on_status("Fetching package info...")
        yield_ui()

        with urllib.request.urlopen(pypi_url, timeout=30) as response:
            pypi_data = json.loads(response.read().decode())

        served_version = pypi_data["info"]["version"]
        if served_version != expected_version:
            raise RuntimeError(f"PyPI returned version {served_version}, expected {expected_version}")

        if is_cancelled():
            _cleanup_temp()
            return False

        # Find correct wheel
        wheel_url = _find_wheel_url(pypi_data)
        wheel_name = wheel_url.split("/")[-1]

        on_status(f"Downloading {wheel_name}...")
        on_progress(10)
        yield_ui()

        # Build into the fresh temp dir (start clean in case a stale temp exists).
        shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        wheel_path = temp_dir / wheel_name

        def download_progress(block_num, block_size, total_size):
            if is_cancelled():
                raise InterruptedError("Download cancelled")
            if total_size > 0:
                downloaded = block_num * block_size
                percent = min(10 + int(downloaded * 70 / total_size), 80)
                mb_done = downloaded / (1024 * 1024)
                mb_total = total_size / (1024 * 1024)
                on_status(f"Downloading... {mb_done:.1f}/{mb_total:.1f} MB")
                on_progress(percent)
                yield_ui()

        urllib.request.urlretrieve(wheel_url, wheel_path, reporthook=download_progress)

        if is_cancelled():
            _cleanup_temp()
            return False

        # Extract wheel into the temp dir
        on_status("Extracting...")
        on_progress(85)
        yield_ui()

        with zipfile.ZipFile(wheel_path, 'r') as zf:
            for member in zf.namelist():
                # Skip dist-info directory
                if ".dist-info" in member:
                    continue
                zf.extract(member, temp_dir)

        # Remove wheel file
        wheel_path.unlink()

        # Fix Windows .pyd naming (in the temp dir, before the swap)
        _fix_windows_pyd(temp_dir, package_subdir)

        # Mark complete with version (markers are the LAST thing written, so a
        # dir carrying them is known-good)
        version_file.write_text(expected_version)
        marker_file.touch()

        # Atomically swap the fully-built temp dir into place.
        # The old cache is moved aside first (to .old-*) and only deleted
        # after the swap succeeds; if the swap fails it is restored. A SIGKILL
        # in the rename window (after "move aside" but before "replace") leaves
        # only the .old-* sibling — recovered on next startup by the
        # .old-* sibling check in _ensure_*_with_callbacks.
        _atomic_swap_dir(temp_dir, cache_dir)

        on_progress(100)
        on_status("Done!")
        yield_ui()

        # Add to path
        cache_str = str(cache_dir)
        if cache_str not in sys.path:
            sys.path.insert(0, cache_str)
        logger.info(
            "%s %s downloaded and installed into cache via atomic swap",
            display_name,
            expected_version,
        )
        return True

    except InterruptedError:
        # User cancelled — only the temp dir is touched; the existing cache (if
        # any) is left intact.
        _cleanup_temp()
        return False

    except Exception as e:
        # Any failure: clean up ONLY the temp dir, leave the existing cache
        # untouched so a previously-good install still works.
        _cleanup_temp()
        logger.error("Failed to download %s: %r", display_name, e)
        on_error(
            f"Failed to download {display_name}:\n\n{e}\n\n"
            "Please check your internet connection and try again."
        )
        return False


def _ensure_pydantic_core_with_callbacks(
    on_status: Callable[[str], None] = lambda msg: None,
    on_progress: Callable[[int], None] = lambda pct: None,
    is_cancelled: Callable[[], bool] = lambda: False,
    on_error: Callable[[str], None] = lambda msg: None,
    yield_ui: Callable[[], None] = lambda: None,
) -> bool:
    """Ensure pydantic_core is available. Downloads from PyPI if needed.

    Pure-logic core with no Qt dependency. The Qt wrapper
    ``ensure_pydantic_core`` adapts these callbacks to QProgressDialog /
    QMessageBox. Default no-op callbacks make this directly callable from
    headless contexts (tests, CI).

    Returns True if pydantic_core is ready, False otherwise.
    """
    try:
        required_version = _get_required_pydantic_core_version()
    except Exception as e:
        on_error(
            f"Failed to determine required pydantic_core version:\n\n{e}\n\n"
            "If you installed from source, install pydantic_core with pip."
        )
        return False

    cache_dir = CACHE_DIR / "pydantic_core_pkg"
    marker_file = cache_dir / ".complete"
    version_file = cache_dir / ".version"

    pypi_url = f"https://pypi.org/pypi/pydantic-core/{required_version}/json"

    # Recover from a crash in _atomic_swap_dir's rename window: if cache_dir
    # doesn't exist but a .old-* sibling does, the SIGKILL hit between
    # "move final_dir aside" and "os.replace(temp_dir, final_dir)". The old
    # sibling IS the last good cache — put it back to avoid an unnecessary
    # re-download. The entire block is best-effort: on a first-ever install
    # the parent dir won't exist yet, and iterdir() would raise — we must
    # never let recovery logic block the normal download path.
    if not cache_dir.exists():
        try:
            parent = cache_dir.parent
            old_siblings = sorted(
                (p for p in parent.iterdir() if p.name.startswith(cache_dir.name + ".old-")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if old_siblings:
                os.replace(old_siblings[0], cache_dir)
                logger.info(
                    "pydantic_core cache recovered from orphaned .old-* sibling: %s",
                    old_siblings[0].name,
                )
        except Exception:
            pass

    # Validate cache version BEFORE prepending. A stale cache (from a previous
    # addon version that required a different pydantic_core) must NOT be put on
    # sys.path — otherwise the version-match probe below would accept it without
    # noticing it's the wrong build for our vendored pydantic.
    if marker_file.exists():
        cached_version = version_file.read_text().strip() if version_file.exists() else None
        if cached_version == required_version:
            cache_str = str(cache_dir)
            if cache_str not in sys.path:
                sys.path.insert(0, cache_str)
        else:
            # Stale cache — wipe so the download path rebuilds it.
            shutil.rmtree(cache_dir, ignore_errors=True)

    # Snapshot which pydantic_core entries are in sys.modules BEFORE our probe
    # so we can undo only the tracks our probe leaves behind. Entries already
    # present (loaded by another addon) are off-limits — leave them alone.
    pre_probe_modules = {
        name for name in sys.modules
        if name == "pydantic_core" or name.startswith("pydantic_core.")
    }

    # Fast path: already importable AND its __version__ matches what our
    # vendored pydantic expects. Version-string match is what pydantic itself
    # checks at the same boundary, and it works under symlinked installs (Nix)
    # where path-based ownership checks misfire. A system-provided pydantic_core
    # at a different version would ABI-mismatch our vendored pydantic, so reject
    # it and fall through to download.
    #
    # The import is wrapped in a lock-only bounded retry (C2): a Windows sharing
    # violation (WinError 32) is transient (e.g. AV scanner / another process
    # momentarily holding the .pyd) and clears on its own, so we retry with short
    # backoff. Access-denied / missing / corrupt are NOT retried — they don't
    # clear — and fall through to a re-download.
    def _probe_pydantic_core() -> bool:
        import pydantic_core
        if getattr(pydantic_core, "__version__", None) == required_version:
            return True
        # Probe loaded a wrong-version module and cached it in sys.modules.
        # If it was already there before our probe, leave it (another addon's
        # state — popping it would break them). Otherwise pop to undo our own
        # probe, so the next import (after we prepend _cache/ post-download)
        # re-resolves against the correct location.
        for name in list(sys.modules):
            if (
                (name == "pydantic_core" or name.startswith("pydantic_core."))
                and name not in pre_probe_modules
            ):
                sys.modules.pop(name, None)
        # Signal "not the right version" without raising — fall through to
        # download. Returning False here means the retry loop stops immediately.
        return False

    if _import_with_lock_retry(
        _probe_pydantic_core,
        cache_dir=cache_dir,
        package_subdir="pydantic_core",
        display_name="pydantic_core",
    ):
        return True

    # Need to download
    return _download_and_extract_wheel(
        display_name="pydantic_core",
        pypi_url=pypi_url,
        expected_version=required_version,
        cache_dir=cache_dir,
        package_subdir="pydantic_core",
        on_status=on_status,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
        on_error=on_error,
        yield_ui=yield_ui,
    )


def _ensure_rpds_with_callbacks(
    on_status: Callable[[str], None] = lambda msg: None,
    on_progress: Callable[[int], None] = lambda pct: None,
    is_cancelled: Callable[[], bool] = lambda: False,
    on_error: Callable[[str], None] = lambda msg: None,
    yield_ui: Callable[[], None] = lambda: None,
) -> bool:
    """Ensure ``rpds`` is importable. Downloads from PyPI only if it isn't.

    Unlike pydantic_core (which Anki never provides, so it always downloads),
    Anki normally ships ``rpds`` as a transitive dep of its own jsonschema. The
    common path here is therefore a plain ``import rpds`` with zero network and
    zero UI. The ``try import`` also naturally covers Nix/source installs, where
    ``rpds`` lives in the system environment.

    On ImportError we fall back to downloading the pinned ``_RPDS_VERSION`` wheel
    for the current platform. Pure-logic core (no Qt); ``ensure_rpds`` is the Qt
    wrapper.

    Returns True if rpds is ready, False otherwise.
    """
    # Fast path: Anki (or the system env) already provides rpds — no download.
    # `import rpds` eagerly loads the native `.rpds` submodule (the vendored/
    # installed rpds/__init__.py does `from .rpds import *`), so a wrong-platform
    # binary raises here and correctly falls through to the download path.
    try:
        import rpds  # noqa: F401
        return True
    except ImportError:
        pass

    cache_dir = CACHE_DIR / "rpds_pkg"
    marker_file = cache_dir / ".complete"
    version_file = cache_dir / ".version"

    # Recover from a crash in _atomic_swap_dir's rename window: if cache_dir
    # doesn't exist but a .old-* sibling does, the SIGKILL hit between
    # "move final_dir aside" and "os.replace(temp_dir, final_dir)". The old
    # sibling IS the last good cache — put it back to avoid an unnecessary
    # re-download. The entire block is best-effort: on a first-ever install
    # the parent dir won't exist yet, and iterdir() would raise — we must
    # never let recovery logic block the normal download path.
    if not cache_dir.exists():
        try:
            parent = cache_dir.parent
            old_siblings = sorted(
                (p for p in parent.iterdir() if p.name.startswith(cache_dir.name + ".old-")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if old_siblings:
                os.replace(old_siblings[0], cache_dir)
                logger.info(
                    "rpds cache recovered from orphaned .old-* sibling: %s",
                    old_siblings[0].name,
                )
        except Exception:
            pass

    # Reuse a warm cache from a previous fallback download if it matches the pin.
    if marker_file.exists():
        cached_version = version_file.read_text().strip() if version_file.exists() else None
        if cached_version == _RPDS_VERSION:
            cache_str = str(cache_dir)
            if cache_str not in sys.path:
                sys.path.insert(0, cache_str)

            def _probe_rpds() -> bool:
                import rpds  # noqa: F401
                return True

            # Pre-flight + lock-retry (C1/C2): a transient Windows lock is
            # retried; access-denied / missing / corrupt fall through to wipe +
            # re-download.
            if _import_with_lock_retry(
                _probe_rpds,
                cache_dir=cache_dir,
                package_subdir="rpds",
                display_name="rpds",
            ):
                return True
            # Cache is on sys.path but unusable (and not a transient lock) —
            # wipe and re-download.
            shutil.rmtree(cache_dir, ignore_errors=True)
        else:
            # Stale cache (different pin) — wipe so the download rebuilds it.
            shutil.rmtree(cache_dir, ignore_errors=True)

    pypi_url = f"https://pypi.org/pypi/rpds-py/{_RPDS_VERSION}/json"
    return _download_and_extract_wheel(
        display_name="rpds",
        pypi_url=pypi_url,
        expected_version=_RPDS_VERSION,
        cache_dir=cache_dir,
        package_subdir="rpds",
        on_status=on_status,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
        on_error=on_error,
        yield_ui=yield_ui,
    )


def ensure_pydantic_core() -> bool:
    """Ensure pydantic_core is available. Shows Qt progress dialog during download.

    Thin Qt wrapper around ``_ensure_pydantic_core_with_callbacks``. The
    _USING_SYSTEM_PACKAGES short-circuit lives here because it is only
    meaningful at production runtime (source / Nix installs); headless
    callers should always go through the download path.

    Returns True if pydantic_core is ready, False if failed.
    """
    from . import _USING_SYSTEM_PACKAGES

    # When running on a system-packages install (e.g. NixOS, source pip install),
    # vendor/ doesn't exist. Trust whatever pydantic_core the system provides —
    # the matching pydantic comes from the same environment.
    if _USING_SYSTEM_PACKAGES:
        try:
            import pydantic_core  # noqa: F401
            return True
        except ImportError:
            return False

    return _run_with_qt_progress(
        _ensure_pydantic_core_with_callbacks,
        progress_label="Downloading pydantic_core...",
    )


def ensure_rpds() -> bool:
    """Ensure ``rpds`` is available. Shows a Qt progress dialog only on download.

    Thin Qt wrapper around ``_ensure_rpds_with_callbacks``. The common path
    (Anki provides rpds) returns immediately from the pure core before any
    callback fires, so no dialog is created and no network happens. The
    _USING_SYSTEM_PACKAGES short-circuit mirrors ``ensure_pydantic_core`` — on
    source/Nix installs we trust the system-provided rpds (the same ``import
    rpds`` the pure core does, surfaced here for parity and an explicit return).

    Returns True if rpds is ready, False if failed.
    """
    from . import _USING_SYSTEM_PACKAGES

    if _USING_SYSTEM_PACKAGES:
        try:
            import rpds  # noqa: F401
            return True
        except ImportError:
            return False

    return _run_with_qt_progress(
        _ensure_rpds_with_callbacks,
        progress_label="Downloading rpds...",
    )


def _run_with_qt_progress(
    core: Callable[..., bool],
    *,
    progress_label: str,
) -> bool:
    """Run a ``_ensure_*_with_callbacks`` core, adapting its callbacks to Qt.

    Lazily creates a QProgressDialog only when the core first emits a status or
    progress callback. Fast paths (already importable, warm cache) return before
    any callback fires, so no dialog is created — no startup flash. ``core`` is
    one of the pure-logic ensure cores; ``progress_label`` is the fallback label
    shown if progress fires before status.
    """
    from aqt.qt import QProgressDialog, QMessageBox, QApplication

    progress: "QProgressDialog | None" = None

    def _ensure_dialog(initial_label: str) -> QProgressDialog:
        nonlocal progress
        if progress is None:
            progress = QProgressDialog(initial_label, "Cancel", 0, 100)
            progress.setWindowTitle("AnkiMCP Server - Setup")
            progress.setMinimumDuration(0)
            progress.show()
        return progress

    def on_status(msg: str) -> None:
        _ensure_dialog(msg).setLabelText(msg)

    def on_progress(pct: int) -> None:
        _ensure_dialog(progress_label).setValue(pct)

    def is_cancelled() -> bool:
        # Defensive: pure cores only check cancellation inside the download
        # branch, after on_status has already fired. The None guard guarantees
        # safety even if that invariant ever changes.
        return progress.wasCanceled() if progress is not None else False

    def on_error(msg: str) -> None:
        QMessageBox.critical(None, "AnkiMCP Server - Setup Failed", msg)

    try:
        return core(
            on_status=on_status,
            on_progress=on_progress,
            is_cancelled=is_cancelled,
            on_error=on_error,
            yield_ui=QApplication.processEvents,
        )
    finally:
        if progress is not None:
            progress.close()
