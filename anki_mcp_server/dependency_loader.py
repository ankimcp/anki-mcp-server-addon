"""Lazy-loader for pydantic_core - downloads correct wheel from PyPI on first run.

Split into a pure-logic core (``_ensure_pydantic_core_with_callbacks``) and a thin
Qt wrapper (``ensure_pydantic_core``). The seam exists so headless callers
(unit tests, CI bootstrap) can drive the loader without a Qt event loop, while
the Anki runtime still gets a progress dialog.
"""

import sys
import sysconfig
import json
import urllib.request
import zipfile
import shutil
from pathlib import Path
from typing import Callable

CACHE_DIR = Path(__file__).parent / "_cache"


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

    # Determine what we're looking for
    is_arm = "arm64" in platform or "aarch64" in platform
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


def _fix_windows_pyd(cache_dir: Path) -> None:
    """Windows needs untagged .pyd files."""
    if not sys.platform == "win32":
        return

    pydantic_core_dir = cache_dir / "pydantic_core"
    if not pydantic_core_dir.exists():
        return

    for pyd_file in pydantic_core_dir.glob("*.pyd"):
        if ".cp" in pyd_file.name:
            # _pydantic_core.cp313-win_amd64.pyd -> _pydantic_core.pyd
            simple_name = pyd_file.name.split(".cp")[0] + ".pyd"
            simple_path = pyd_file.parent / simple_name
            if not simple_path.exists():
                shutil.copy(pyd_file, simple_path)


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
    try:
        on_status(f"Downloading pydantic_core {required_version} (first run only)...")
        on_progress(0)
        yield_ui()

        # Fetch PyPI metadata for the exact required version
        on_status("Fetching package info...")
        yield_ui()

        with urllib.request.urlopen(pypi_url, timeout=30) as response:
            pypi_data = json.loads(response.read().decode())

        served_version = pypi_data["info"]["version"]
        if served_version != required_version:
            raise RuntimeError(f"PyPI returned version {served_version}, expected {required_version}")

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
        _fix_windows_pyd(cache_dir)

        # Mark complete with version
        version_file.write_text(required_version)
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
            f"Failed to download pydantic_core:\n\n{e}\n\n"
            "Please check your internet connection and try again."
        )
        return False


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

    from aqt.qt import QProgressDialog, QMessageBox, QApplication

    # Lazy-create the progress dialog on the first status/progress callback.
    # Fast paths (cache hit, version match) never invoke those callbacks, so no
    # dialog is created and there's no "dialog flash" on Anki startup for users
    # with a warm _cache/.
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
        _ensure_dialog("Downloading pydantic_core...").setValue(pct)

    def is_cancelled() -> bool:
        # Defensive: pure core only checks cancellation inside the download
        # branch, after on_status has already fired. The None guard guarantees
        # safety even if that invariant ever changes.
        return progress.wasCanceled() if progress is not None else False

    def on_error(msg: str) -> None:
        QMessageBox.critical(None, "AnkiMCP Server - Setup Failed", msg)

    try:
        return _ensure_pydantic_core_with_callbacks(
            on_status=on_status,
            on_progress=on_progress,
            is_cancelled=is_cancelled,
            on_error=on_error,
            yield_ui=QApplication.processEvents,
        )
    finally:
        if progress is not None:
            progress.close()
