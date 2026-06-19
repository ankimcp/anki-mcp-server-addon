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
"""

import sys
import sysconfig
import platform as _platform_mod
import json
import urllib.request
import zipfile
import shutil
from pathlib import Path
from typing import Callable

CACHE_DIR = Path(__file__).parent / "_cache"

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


def _get_python_tag() -> str:
    """Get Python version tag like cp312, cp313."""
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _find_wheel_url(pypi_data: dict) -> str:
    """Find matching wheel URL from PyPI JSON data."""
    py_tag = _get_python_tag()
    platform = sysconfig.get_platform()

    # Determine what we're looking for.
    # sysconfig.get_platform() can return "macosx-10.13-universal2" on Apple
    # Silicon when Anki uses a universal2 framework Python — neither "arm64"
    # nor "aarch64" appears in that string, which would wrongly select the
    # x86_64 wheel. platform.machine() always reflects the actual running
    # process architecture (arm64 on Apple Silicon, x86_64 under Rosetta).
    machine = _platform_mod.machine()
    is_arm = (
        "arm64" in platform
        or "aarch64" in platform
        or machine in ("arm64", "aarch64")
    )
    is_windows = platform.startswith("win")
    is_macos = "macos" in platform
    is_linux = "linux" in platform

    for url_info in pypi_data["urls"]:
        filename = url_info["filename"]

        # Must be a wheel
        if not filename.endswith(".whl"):
            continue

        # Must match Python version
        if py_tag not in filename:
            continue

        # Platform matching
        if is_windows:
            if is_arm and "win_arm64" in filename:
                return url_info["url"]
            elif not is_arm and "win_amd64" in filename:
                return url_info["url"]
        elif is_macos:
            if is_arm and "arm64" in filename and "macosx" in filename:
                return url_info["url"]
            elif not is_arm and "x86_64" in filename and "macosx" in filename:
                return url_info["url"]
        elif is_linux:
            if is_arm and "aarch64" in filename and "manylinux" in filename:
                return url_info["url"]
            elif not is_arm and "x86_64" in filename and "manylinux" in filename:
                return url_info["url"]

    raise RuntimeError(f"No matching wheel found for {py_tag} on {platform}")


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
    the Windows .pyd fixup. On success, writes ``.version`` + ``.complete``
    markers and returns True. On failure, wipes ``cache_dir`` and returns False.
    """
    marker_file = cache_dir / ".complete"
    version_file = cache_dir / ".version"

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
            return False

        # Find correct wheel
        wheel_url = _find_wheel_url(pypi_data)
        wheel_name = wheel_url.split("/")[-1]

        on_status(f"Downloading {wheel_name}...")
        on_progress(10)
        yield_ui()

        # Download wheel
        cache_dir.mkdir(parents=True, exist_ok=True)
        wheel_path = cache_dir / wheel_name

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
            return False

        # Extract wheel
        on_status("Extracting...")
        on_progress(85)
        yield_ui()

        with zipfile.ZipFile(wheel_path, 'r') as zf:
            for member in zf.namelist():
                # Skip dist-info directory
                if ".dist-info" in member:
                    continue
                zf.extract(member, cache_dir)

        # Remove wheel file
        wheel_path.unlink()

        # Fix Windows .pyd naming
        _fix_windows_pyd(cache_dir, package_subdir)

        # Mark complete with version
        version_file.write_text(expected_version)
        marker_file.touch()

        on_progress(100)
        on_status("Done!")
        yield_ui()

        # Add to path
        cache_str = str(cache_dir)
        if cache_str not in sys.path:
            sys.path.insert(0, cache_str)
        return True

    except InterruptedError:
        # User cancelled
        shutil.rmtree(cache_dir, ignore_errors=True)
        return False

    except Exception as e:
        shutil.rmtree(cache_dir, ignore_errors=True)
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
    try:
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
    except ImportError:
        pass

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

    # Reuse a warm cache from a previous fallback download if it matches the pin.
    if marker_file.exists():
        cached_version = version_file.read_text().strip() if version_file.exists() else None
        if cached_version == _RPDS_VERSION:
            cache_str = str(cache_dir)
            if cache_str not in sys.path:
                sys.path.insert(0, cache_str)
            try:
                import rpds  # noqa: F401
                return True
            except ImportError:
                # Cache is on sys.path but unusable — wipe and re-download.
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
