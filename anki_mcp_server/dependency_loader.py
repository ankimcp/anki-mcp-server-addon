"""Lazy-loader for pydantic_core - downloads correct wheel from PyPI on first run."""

import sys
import sysconfig
import json
import urllib.request
import zipfile
import shutil
from pathlib import Path

# Qt imports for progress dialog
from aqt.qt import QProgressDialog, QMessageBox, QApplication

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


def ensure_pydantic_core() -> bool:
    """Ensure pydantic_core is available. Downloads from PyPI if needed.

    Returns True if pydantic_core is ready, False if failed.
    Shows progress dialog during download.
    """
    required_version = _get_required_pydantic_core_version()
    pypi_url = f"https://pypi.org/pypi/pydantic-core/{required_version}/json"

    cache_dir = CACHE_DIR / "pydantic_core_pkg"
    marker_file = cache_dir / ".complete"
    version_file = cache_dir / ".version"

    # Already cached? Check version matches.
    if marker_file.exists():
        cached_version = version_file.read_text().strip() if version_file.exists() else None
        if cached_version == required_version:
            cache_str = str(cache_dir)
            if cache_str not in sys.path:
                sys.path.insert(0, cache_str)
            return True
        # Version mismatch (addon was upgraded) — re-download
        shutil.rmtree(cache_dir, ignore_errors=True)

    # Need to download
    try:
        # Create progress dialog
        progress = QProgressDialog(
            f"Downloading pydantic_core {required_version} (first run only)...",
            "Cancel",
            0, 100
        )
        progress.setWindowTitle("AnkiMCP Server - Setup")
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()

        # Fetch PyPI metadata for the exact required version
        progress.setLabelText("Fetching package info...")
        QApplication.processEvents()

        with urllib.request.urlopen(pypi_url, timeout=30) as response:
            pypi_data = json.loads(response.read().decode())

        served_version = pypi_data["info"]["version"]
        if served_version != required_version:
            raise RuntimeError(f"PyPI returned version {served_version}, expected {required_version}")

        if progress.wasCanceled():
            return False

        # Find correct wheel
        wheel_url = _find_wheel_url(pypi_data)
        wheel_name = wheel_url.split("/")[-1]

        progress.setLabelText(f"Downloading {wheel_name}...")
        progress.setValue(10)
        QApplication.processEvents()

        # Download wheel
        cache_dir.mkdir(parents=True, exist_ok=True)
        wheel_path = cache_dir / wheel_name

        def download_progress(block_num, block_size, total_size):
            if progress.wasCanceled():
                raise InterruptedError("Download cancelled")
            if total_size > 0:
                downloaded = block_num * block_size
                percent = min(10 + int(downloaded * 70 / total_size), 80)
                mb_done = downloaded / (1024 * 1024)
                mb_total = total_size / (1024 * 1024)
                progress.setLabelText(f"Downloading... {mb_done:.1f}/{mb_total:.1f} MB")
                progress.setValue(percent)
                QApplication.processEvents()

        urllib.request.urlretrieve(wheel_url, wheel_path, reporthook=download_progress)

        if progress.wasCanceled():
            return False

        # Extract wheel
        progress.setLabelText("Extracting...")
        progress.setValue(85)
        QApplication.processEvents()

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

        progress.setValue(100)
        progress.setLabelText("Done!")
        QApplication.processEvents()
        progress.close()

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
        # Show error
        shutil.rmtree(cache_dir, ignore_errors=True)
        QMessageBox.critical(
            None,
            "AnkiMCP Server - Setup Failed",
            f"Failed to download pydantic_core:\n\n{e}\n\n"
            "Please check your internet connection and try again."
        )
        return False
