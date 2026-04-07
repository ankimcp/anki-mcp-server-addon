"""Credentials manager for AnkiMCP tunnel authentication.

Reads and writes OAuth credentials shared with the TypeScript CLI.
Both the CLI and this addon use the same file at ~/.ankimcp/credentials.json,
so logging in from either side works for both.

This module handles ONLY file I/O. No auth logic, no network calls.
Uses only stdlib — no vendored dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Required top-level keys in the credentials JSON.
# If any are missing, the file is treated as corrupt.
_REQUIRED_KEYS = frozenset({"access_token", "refresh_token", "expires_at", "user"})


@dataclass
class Credentials:
    """OAuth credentials matching the TypeScript CLI format.

    The JSON on disk looks like::

        {
            "access_token": "eyJ...",
            "refresh_token": "dGhp...",
            "expires_at": "2024-01-15T12:00:00.000Z",
            "user": {"id": "usr_123", "email": "a@b.com", "tier": "free"}
        }

    Attributes:
        access_token: JWT access token for API calls.
        refresh_token: Long-lived token used to obtain new access tokens.
        expires_at: ISO 8601 datetime string for access token expiry.
        user: User info dict with ``id``, ``email``, and ``tier`` keys.
    """

    access_token: str
    refresh_token: str
    expires_at: str  # ISO 8601 datetime string
    user: dict[str, Any]  # {"id": "...", "email": "...", "tier": "free"|"paid"}


class CredentialsManager:
    """Manage the shared credentials file at ``~/.ankimcp/credentials.json``.

    All methods are defensive — filesystem errors are caught and logged,
    never propagated. This ensures a broken credentials file can never
    crash Anki.

    The file format is intentionally identical to the TypeScript CLI so
    that users who logged in via CLI don't need to re-authenticate in Anki.
    """

    CREDENTIALS_DIR: Path = Path.home() / ".ankimcp"
    CREDENTIALS_PATH: Path = CREDENTIALS_DIR / "credentials.json"
    EXPIRY_BUFFER_SECONDS: int = 60

    # ------------------------------------------------------------------
    # load — read credentials from disk
    # ------------------------------------------------------------------
    def load(self) -> Credentials | None:
        """Read and parse the credentials file.

        Returns:
            A ``Credentials`` instance, or ``None`` if the file is missing,
            unreadable, corrupt JSON, or missing required fields.
        """
        try:
            raw = self.CREDENTIALS_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("Failed to read credentials file: %s", exc)
            return None

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Credentials file contains invalid JSON: %s", exc)
            return None

        if not isinstance(data, dict):
            logger.warning("Credentials file is not a JSON object")
            return None

        missing = _REQUIRED_KEYS - data.keys()
        if missing:
            logger.warning("Credentials file missing required keys: %s", missing)
            return None

        return Credentials(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
            user=data["user"],
        )

    # ------------------------------------------------------------------
    # save — write credentials to disk
    # ------------------------------------------------------------------
    def save(self, credentials: Credentials) -> None:
        """Write credentials to disk with restrictive permissions.

        Creates ``~/.ankimcp/`` with mode 0700 if it doesn't exist.
        The credentials file is written with mode 0600 (owner read/write only).

        Args:
            credentials: The credentials to persist.
        """
        try:
            self.CREDENTIALS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Failed to create credentials directory: %s", exc)
            return

        try:
            data = asdict(credentials)
            content = json.dumps(data, indent=2) + "\n"

            # Write to a temp file first, then rename for atomicity.
            # This avoids leaving a half-written file if we crash mid-write.
            tmp_path = self.CREDENTIALS_PATH.with_suffix(".tmp")
            tmp_path.write_text(content, encoding="utf-8")

            # Set restrictive permissions before renaming into place
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600

            tmp_path.replace(self.CREDENTIALS_PATH)
        except OSError as exc:
            logger.error("Failed to write credentials file: %s", exc)
            # Clean up temp file if it exists
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # delete — remove the credentials file
    # ------------------------------------------------------------------
    def delete(self) -> None:
        """Remove the credentials file.

        No error if the file doesn't exist.
        """
        try:
            self.CREDENTIALS_PATH.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to delete credentials file: %s", exc)

    # ------------------------------------------------------------------
    # is_token_expired — check if access token needs refresh
    # ------------------------------------------------------------------
    def is_token_expired(self, credentials: Credentials) -> bool:
        """Check whether the access token is expired or about to expire.

        Returns ``True`` if ``expires_at`` is within
        :attr:`EXPIRY_BUFFER_SECONDS` of the current time, or if the
        timestamp can't be parsed.

        Args:
            credentials: Credentials whose expiry to check.
        """
        try:
            expires_at = _parse_iso8601(credentials.expires_at)
        except ValueError:
            logger.warning(
                "Could not parse expires_at %r — treating as expired",
                credentials.expires_at,
            )
            return True

        now = datetime.now(timezone.utc)
        remaining = (expires_at - now).total_seconds()
        return remaining <= self.EXPIRY_BUFFER_SECONDS


# --------------------------------------------------------------------------
# ISO 8601 parsing helper
# --------------------------------------------------------------------------
def _parse_iso8601(value: str) -> datetime:
    """Parse an ISO 8601 datetime string to a timezone-aware datetime.

    Handles the formats the TypeScript CLI produces:
    - ``2024-01-15T12:00:00.000Z`` (milliseconds + Z suffix)
    - ``2024-01-15T12:00:00Z`` (no fractional seconds)
    - ``2024-01-15T12:00:00+00:00`` (explicit offset)

    Args:
        value: ISO 8601 datetime string.

    Returns:
        A timezone-aware ``datetime`` in UTC.

    Raises:
        ValueError: If the string can't be parsed.
    """
    # Python 3.11+ datetime.fromisoformat handles "Z" suffix, but we
    # also need to support 3.10. Replace trailing Z with +00:00.
    # Update: Anki 25.07 ships Python 3.13, but keep this robust.
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"

    dt = datetime.fromisoformat(cleaned)

    # If the string had no timezone info, assume UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt
