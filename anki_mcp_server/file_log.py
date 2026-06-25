"""Optional rotating file logging + a diagnostics snapshot — STDLIB ONLY.

This module is the foundation of the diagnostics/resilience feature set. Its
whole job is to survive failures in the vendored/third-party layer (pydantic,
mcp, starlette, ...), so it MUST NOT import any vendored or third-party package.
It uses only the Python standard library and is initialized as early as possible
in ``__init__.py`` — before vendor-path setup and before ``ensure_pydantic_core()``
— so failures during dependency loading land in the log.

Responsibilities:
    * Configure a ``RotatingFileHandler`` writing to ``user_files/ankimcp.log``.
    * Redact secrets (API key, OAuth tokens, Bearer tokens) before they hit disk.
    * Build a one-shot diagnostics snapshot block (versions + live-module
      provenance) reused by the startup log AND the "Copy diagnostics" button —
      a single source of truth.

Logger naming:
    All addon modules log under the top-level ``anki_mcp_server`` logger (or the
    AnkiWeb addon-id package name at runtime). We attach the file handler to that
    root-of-addon logger so every child ``logging.getLogger(__name__)`` in the
    addon propagates into the file. The handler is added without touching the
    root logger, so we never capture unrelated Anki/third-party logging.
"""

from __future__ import annotations

import logging
import logging.handlers
import platform
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

# The top-level package logger name. ``__name__`` here is e.g.
# ``anki_mcp_server.file_log`` (or ``124672614.file_log`` on AnkiWeb), so the
# first path segment is the addon's root logger that every child propagates to.
_ADDON_LOGGER_NAME = __name__.split(".")[0]

# Marker attached to our handler so re-initialization is idempotent and we never
# stack duplicate handlers across profile reloads.
_HANDLER_TAG = "ankimcp_file_handler"

_LOG_FILENAME = "ankimcp.log"
_MAX_BYTES = 1 * 1024 * 1024  # ~1 MB per file
_BACKUP_COUNT = 3

# Shared libraries whose live provenance we report. We deliberately inspect
# sys.modules WITHOUT importing them, so the snapshot reflects only what is
# already loaded (revealing whose copy of a shared lib is live — ours vs another
# add-on's). ``google.protobuf`` is the importable module for the protobuf dep.
_PROVENANCE_MODULES = (
    "pydantic",
    "pydantic_core",
    "typing_extensions",
    "mcp",
    "starlette",
    "uvicorn",
    "google.protobuf",
    "certifi",
    "urllib3",
)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
class _RedactingFilter(logging.Filter):
    """Logging filter that masks secrets before a record is written.

    Two redaction strategies, applied to the fully-formatted message:

    * Exact-value masking: any registered literal secret (the configured
      ``http_api_key`` and OAuth access/refresh tokens) is replaced wherever it
      appears. Registered at runtime via ``register_secret`` because the values
      aren't known at import time.
    * Pattern masking: ``Authorization: Bearer <token>`` / bare ``Bearer <token>``
      style strings are masked structurally, so a token we never registered
      (e.g. one logged by a vendored library) still never reaches disk.

    The filter rewrites ``record.msg`` to the redacted, already-%-formatted
    string and clears ``record.args`` so the handler's formatter doesn't try to
    re-interpolate it.
    """

    _REPLACEMENT = "***REDACTED***"

    # Bearer tokens: "Bearer <token>" possibly preceded by "Authorization:".
    # Token charset covers JWTs and opaque tokens (base64url + dots/dashes).
    # The {16,} floor avoids false-positives on prose like "Bearer token
    # authentication" — real OAuth / JWT / API-key tokens always exceed 16
    # chars, while ordinary English words after "Bearer " are almost always
    # shorter. Bias toward over-redaction: lower this floor rather than
    # raise it if a legitimate token ever fails to match.
    _BEARER_RE = re.compile(
        r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{16,}",
    )

    def __init__(self) -> None:
        super().__init__()
        # Exact secret values to mask. Stored longest-first at mask time so a
        # token that is a substring of another doesn't leak via partial replace.
        self._secrets: set[str] = set()

    def register_secret(self, value: Optional[str]) -> None:
        """Register a literal secret value to mask in future log records.

        No-op for empty or very short values. The minimum-length guard (≥4
        chars) is intentional and protective: a 1- or 2-char registered
        secret would redact every occurrence of that character everywhere in
        the log, making it unreadable. Short keys (< 16 chars) are already
        flagged as weak by ``validate_http_api_key`` in ``http_auth.py``
        (``_MIN_API_KEY_LENGTH = 16``), so the source-level advisory covers
        the gap — the redactor does not need to handle them.
        """
        if value and len(value) >= 4:
            self._secrets.add(value)

    def _mask(self, text: str) -> str:
        # Exact-value secrets first (longest first to avoid partial overlaps).
        for secret in sorted(self._secrets, key=len, reverse=True):
            if secret in text:
                text = text.replace(secret, self._REPLACEMENT)
        # Structural Bearer-token masking.
        text = self._BEARER_RE.sub(lambda m: m.group(1) + self._REPLACEMENT, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # If interpolation itself fails, fall back to the raw template so
            # we never crash the logging path.
            message = str(record.msg)
        record.msg = self._mask(message)
        record.args = None

        # Redact exception tracebacks. The Formatter checks exc_text first;
        # if set, it uses it verbatim and never re-formats exc_info. So:
        # 1. If exc_info is present and exc_text is not yet populated, format
        #    it ourselves, mask, store on exc_text, then clear exc_info.
        # 2. If exc_text is already populated (formatted by an earlier handler
        #    or our previous pass), mask it in place.
        # 3. Clear exc_info in both cases so the Formatter never re-formats
        #    from the raw exception objects.
        if record.exc_info:
            import traceback
            # Format using exc_info (3-tuple) — stdlib traceback, no third-party.
            try:
                formatted = "".join(traceback.format_exception(*record.exc_info))
            except Exception:
                formatted = repr(record.exc_info[1])
            record.exc_text = self._mask(formatted)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = self._mask(record.exc_text)

        # stack_info is a plain string (already formatted by the logger).
        if record.stack_info:
            record.stack_info = self._mask(record.stack_info)

        return True


# Module-level singletons. The filter is shared so callers can register secrets
# even before/independently of handler setup.
_redacting_filter = _RedactingFilter()
_initialized = False


def register_secret(value: Optional[str]) -> None:
    """Register a literal secret value to mask in all future log records."""
    _redacting_filter.register_secret(value)


def get_logger() -> logging.Logger:
    """Return the addon's root logger (child loggers propagate into it)."""
    return logging.getLogger(_ADDON_LOGGER_NAME)


def _existing_handler(logger: logging.Logger) -> Optional[logging.Handler]:
    for handler in logger.handlers:
        if getattr(handler, _HANDLER_TAG, False):
            return handler
    return None


def init_file_logging(enabled: bool, user_files_dir) -> None:
    """Configure (or tear down) rotating file logging for the addon.

    Idempotent: calling this multiple times is safe — the setup path exits
    early if a handler is already attached, and the teardown path is a no-op
    if no handler is present. Any failure is swallowed (printed to stderr
    only) because logging must never crash the addon.

    Note on runtime toggling: the ``log_to_file`` config flag is currently
    read once at addon import time and takes effect on restart. The teardown
    branch (``enabled=False``) is kept for symmetry and forward-compatibility
    — it correctly removes and closes any previously attached handler — but it
    is not wired to a live config-save signal in the current implementation.

    Args:
        enabled: When False, removes our handler if present and returns. When
            True, ensures a RotatingFileHandler is attached to the addon
            logger. Calling with ``True`` when a handler is already attached
            is a no-op.
        user_files_dir: Path-like directory for ``user_files``. The log file is
            written as ``<user_files_dir>/ankimcp.log``.
    """
    global _initialized

    logger = get_logger()

    if not enabled:
        existing = _existing_handler(logger)
        if existing is not None:
            logger.removeHandler(existing)
            try:
                existing.close()
            except Exception:
                pass
        _initialized = False
        return

    # Already wired up — nothing to do.
    if _existing_handler(logger) is not None:
        _initialized = True
        return

    try:
        directory = Path(user_files_dir)
        directory.mkdir(parents=True, exist_ok=True)
        log_path = directory / _LOG_FILENAME

        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        setattr(handler, _HANDLER_TAG, True)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        # Redaction runs on the handler so it applies regardless of which child
        # logger emitted the record.
        handler.addFilter(_redacting_filter)

        logger.addHandler(handler)
        # Ensure our records reach the handler. Don't disturb the root logger.
        if logger.level == logging.NOTSET or logger.level > logging.INFO:
            logger.setLevel(logging.INFO)
        _initialized = True
    except Exception as exc:  # pragma: no cover - defensive
        # Logging setup itself failed — never propagate.
        print(f"AnkiMCP Server: failed to initialize file logging: {exc!r}", file=sys.stderr)


def is_enabled() -> bool:
    """Whether file logging is currently active (handler attached)."""
    return _existing_handler(get_logger()) is not None


# ---------------------------------------------------------------------------
# Diagnostics snapshot — single source of truth for startup log + UI button
# ---------------------------------------------------------------------------
def _safe(fn) -> str:
    """Run a zero-arg producer defensively, returning its value or an error tag."""
    try:
        return str(fn())
    except Exception as exc:  # pragma: no cover - defensive
        return f"<error: {exc!r}>"


# Maps import names to their distribution names for the file-scoped metadata
# lookup below. Only the cases where they genuinely differ need entries.
_DIST_NAME_OVERRIDES: dict[str, str] = {
    "google.protobuf": "protobuf",
}


def _version_from_file_for(import_name: str, mod_path: "Path") -> "Optional[str]":
    """Look up a distribution version by file-scoped RECORD ownership.

    Ties the reported version to the physically loaded file rather than to
    the first dist-info found on ``sys.path``. This avoids phantom-version
    bugs where Anki's copy and our vendored copy both exist on ``sys.path``
    and the global ``importlib.metadata.version()`` resolves whichever dist-
    info sorts first — independent of which module object is live.

    Resolution is two-tiered, both scoped to the file's own directory tree:

    Tier 1 — RECORD ownership (most accurate): walk up from the loaded
    file's directory (≤4 levels). At each level call
    ``importlib.metadata.distributions(path=[candidate_dir])`` with an
    EXPLICIT path — never the global no-arg form. For every distribution
    found, resolve each RECORD entry via ``f.locate().resolve()`` and compare
    to the resolved ``mod_path``. The distribution whose RECORD contains the
    live file is the authoritative owner.

    Tier 2 — name-based (editable installs / missing RECORD): same candidate
    dirs, each distribution filtered by normalised name (PEP 503: lowercase,
    dashes == underscores). Uses ``_DIST_NAME_OVERRIDES`` for the known
    import-name → dist-name mismatches (e.g. ``google.protobuf`` → ``protobuf``).

    Returns the version string on success, or ``None`` (caller falls back to
    "unknown-version"). Never raises.
    """
    try:
        import importlib.metadata

        # Resolve once so comparisons survive symlinks and macOS
        # case-insensitive filesystems.
        resolved = mod_path.resolve()

        # Candidate directories: the module's own directory and up to 4
        # parents. dist-info for a single-file module (typing_extensions.py)
        # sits alongside it; for a package (google/protobuf/__init__.py) the
        # protobuf dist-info is a couple of levels up.
        candidates: list[Path] = []
        d = resolved.parent
        for _ in range(5):
            candidates.append(d)
            parent = d.parent
            if parent == d:
                break
            d = parent

        # Tier 1: RECORD ownership — distribution-name-agnostic.
        for candidate in candidates:
            try:
                for dist in importlib.metadata.distributions(path=[str(candidate)]):
                    files = dist.files
                    if files is None:
                        continue
                    for f in files:
                        try:
                            if f.locate().resolve() == resolved:
                                return dist.version
                        except Exception:
                            continue
            except Exception:
                continue

        # Tier 2: name-based match, still scoped to candidate dirs.
        dist_name = _DIST_NAME_OVERRIDES.get(import_name, import_name.split(".")[0])
        norm_target = dist_name.lower().replace("-", "_")
        for candidate in candidates:
            try:
                for dist in importlib.metadata.distributions(path=[str(candidate)]):
                    try:
                        dn = dist.metadata.get("Name", "") or ""
                        if dn.lower().replace("-", "_") == norm_target:
                            return dist.version
                    except Exception:
                        continue
            except Exception:
                continue

    except Exception:
        pass
    return None


def _module_provenance(name: str) -> str:
    """Describe a module's live provenance WITHOUT importing it.

    Inspects ``sys.modules`` only. Reports version and ``__file__`` of the
    already-loaded copy, or ``not loaded`` if absent.

    Version resolution (in order, stopping at the first hit):

    1. ``module.__version__`` — fast path; most packages expose this.
    2. File-scoped RECORD match — walks up from the loaded file's directory
       calling ``importlib.metadata.distributions(path=[candidate_dir])``.
       Checks each distribution's RECORD to find which one owns the exact
       live file, so the version is tied to the physical file on disk — not
       to whichever dist-info comes first on ``sys.path``. Avoids the phantom-
       version bug where a vendored copy and an Anki-provided copy both exist.
    3. ``"unknown-version"`` — honest fallback when the module is vendored
       without dist-info or the filesystem is in an unexpected layout.
    """
    module = sys.modules.get(name)
    if module is None:
        return "not loaded"

    version: Optional[str] = getattr(module, "__version__", None) or None
    file = getattr(module, "__file__", None)
    file_str = file if file else "unknown-path"

    if not version:
        if file:
            try:
                version = _version_from_file_for(name, Path(file))
            except Exception:
                pass
        # If __file__ is absent (e.g. built-in / zip-imported) there is no
        # physical anchor to scope the lookup — leave version as None.

    version_str = version if version else "unknown-version"
    return f"{version_str}  ({file_str})"


def build_diagnostics_snapshot(
    *,
    addon_version: str,
    transports: Optional[Iterable[str]] = None,
    extra_modules: Iterable[str] = (),
) -> str:
    """Build the diagnostics snapshot block.

    Single implementation shared by the startup log and the "Copy diagnostics"
    settings button. Every field is gathered defensively so one failure never
    aborts the whole snapshot.

    Args:
        addon_version: The add-on ``__version__``.
        transports: Optional human-readable transport-state lines (e.g.
            ``["HTTP: enabled", "Tunnel: connected"]``). Omitted at early
            startup when transport state isn't known yet.
        extra_modules: Extra module names to include in the provenance section
            beyond the built-in shared-library set.

    Returns:
        A multi-line plain-text block suitable for a log file or a forum paste.
    """
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("AnkiMCP Server diagnostics")
    lines.append("=" * 60)
    lines.append(f"Add-on version : {addon_version}")
    lines.append(f"Python         : {_safe(lambda: platform.python_version())}")
    lines.append(f"Platform       : {_safe(lambda: platform.platform())}")
    lines.append(f"Machine        : {_safe(lambda: platform.machine())}")

    # Anki / Qt versions — imported defensively; absent in headless contexts.
    def _anki_version() -> str:
        from anki.buildinfo import version as anki_version  # type: ignore

        return anki_version

    def _qt_version() -> str:
        from aqt.qt import qVersion  # type: ignore

        return qVersion()

    lines.append(f"Anki           : {_safe(_anki_version)}")
    lines.append(f"Qt             : {_safe(_qt_version)}")

    if transports is not None:
        lines.append("")
        lines.append("Transports:")
        for line in transports:
            lines.append(f"  {line}")

    lines.append("")
    lines.append("Loaded module provenance (sys.modules; not force-imported):")
    for name in (*_PROVENANCE_MODULES, *extra_modules):
        lines.append(f"  {name:<18}: {_module_provenance(name)}")
    lines.append("=" * 60)
    return "\n".join(lines)


def log_diagnostics_snapshot(
    addon_version: str,
    *,
    label: str = "startup",
    transports: Optional[Iterable[str]] = None,
) -> None:
    """Write the diagnostics snapshot to the file log (no-op if disabled).

    Args:
        addon_version: The add-on ``__version__``.
        label: A short tag distinguishing snapshots (e.g. ``"startup"`` vs
            ``"post-dependency-load"``).
        transports: Optional transport-state lines (see ``build_diagnostics_snapshot``).
    """
    if not is_enabled():
        return
    try:
        snapshot = build_diagnostics_snapshot(
            addon_version=addon_version, transports=transports
        )
        get_logger().info("Diagnostics snapshot (%s):\n%s", label, snapshot)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"AnkiMCP Server: failed to log diagnostics snapshot: {exc!r}", file=sys.stderr)
