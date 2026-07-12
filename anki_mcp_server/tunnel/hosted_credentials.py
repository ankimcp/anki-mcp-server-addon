"""Read-only loader for hosted-mode tunnel credentials.

In hosted mode the addon runs unattended (e.g. inside a Kubernetes pod) and does
NOT perform the interactive OAuth device-flow login. Instead, a control-plane
service provisions a small, read-only credentials file mounted into the pod. This
module turns that file's path into an optional :class:`HostedCredentials`.

The on-disk format is intentionally minimal and is a DIFFERENT shape from the
regular-mode OAuth file handled by ``credentials.py``::

    {
        "v": 1,
        "token": "<opaque internal bearer token>",
        "tunnelUrl": "wss://<internal-tunnel-host>/connect",
        "user": {"id": "<keycloakId>"}
    }

This module is READ-ONLY: it never writes to disk. It is also defensive — a
missing, unreadable, or malformed file yields ``None`` (never an exception).
Uses only stdlib — no vendored dependencies.

Single responsibility: given a path, produce an optional ``HostedCredentials``.
It deliberately knows nothing about ``Config`` or the tunnel runtime — the
caller (a later chunk) supplies the path and decides how to fall back.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The only credentials-file schema version this loader understands. Any other
# value is refused outright — we do not best-effort parse unknown versions.
_SUPPORTED_VERSION = 1


@dataclass(frozen=True)
class HostedCredentials:
    """Parsed hosted-mode tunnel credentials.

    Attributes:
        token: Opaque internal bearer token. Passed through verbatim — never
            decoded, expiry-checked, or written back.
        tunnel_url: Tunnel WebSocket URL from the file, or ``None`` if absent.
            When ``None`` the caller falls back to its configured default.
        user_id: Display-only user identifier (e.g. Keycloak id), or ``None``.
    """

    token: str
    tunnel_url: str | None
    user_id: str | None


class HostedCredentialsLoader:
    """Load :class:`HostedCredentials` from a file path.

    Constructed with the credentials-file path (from config, in a later chunk)
    so it can be injected and called inside the tunnel reconnect loop. The file
    is read FRESH on every :meth:`load` call — no caching — because the control
    plane may provision or rotate it while the addon is running.
    """

    def __init__(self, path: str | Path):
        """Store the credentials-file path.

        Args:
            path: Filesystem path to the hosted credentials JSON file.
        """
        self._path = Path(path)

    def load(self) -> HostedCredentials | None:
        """Read and parse the hosted credentials file.

        Returns:
            A :class:`HostedCredentials` instance, or ``None`` if the file is
            missing, unreadable, invalid JSON, or fails the reader semantics
            (see :func:`_parse`).
        """
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # Normal state: the platform may provision the file later. Keep quiet
            # so it never looks like an error in logs.
            logger.debug("Hosted credentials file not found: %s", self._path)
            return None
        except (OSError, ValueError) as exc:
            # OSError covers unreadable files (PermissionError, IsADirectoryError,
            # etc.); ValueError covers UnicodeDecodeError from non-UTF-8 bytes
            # (e.g. a truncated/partial atomic write or binary garbage). Both are
            # treated as a malformed/unreadable file. Log the exception type only
            # — NEVER the file's bytes/text, which may carry a bearer token.
            logger.warning(
                "Failed to read hosted credentials file (%s): %s",
                type(exc).__name__,
                exc,
            )
            return None

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Hosted credentials file contains invalid JSON: %s", exc)
            return None

        if not isinstance(data, dict):
            logger.warning("Hosted credentials file is not a JSON object")
            return None

        return _parse(data)


def _parse(data: dict[str, Any]) -> HostedCredentials | None:
    """Turn a decoded JSON dict into :class:`HostedCredentials`.

    Pure function (no I/O) so it can be unit-tested directly. Enforces the exact
    reader semantics:

    * ``v`` — REQUIRED, must strict-equal integer ``1``. Anything else is
      refused (no best-effort parsing of unknown versions).
    * ``token`` — REQUIRED, non-empty string. Opaque; never decoded.
    * ``tunnelUrl`` — OPTIONAL string; empty/absent/non-string → ``None``.
    * ``user.id`` — OPTIONAL, display-only; a missing/malformed ``user`` block
      does NOT fail the load.

    Unknown extra keys are ignored (forward compatibility).

    Args:
        data: Decoded JSON object from the credentials file.

    Returns:
        A :class:`HostedCredentials`, or ``None`` if the hard requirements
        (``v`` and ``token``) are not met.
    """
    # bool is a subclass of int, so guard against `"v": true` slipping through.
    version = data.get("v")
    if not isinstance(version, int) or isinstance(version, bool) or version != _SUPPORTED_VERSION:
        logger.warning(
            "Hosted credentials file has unsupported version %r (expected %d)",
            version,
            _SUPPORTED_VERSION,
        )
        return None

    token = data.get("token")
    if not isinstance(token, str) or not token:
        logger.warning("Hosted credentials file missing a non-empty 'token' string")
        return None

    tunnel_url = _optional_str(data.get("tunnelUrl"))

    user = data.get("user")
    user_id = _optional_str(user.get("id")) if isinstance(user, dict) else None

    return HostedCredentials(token=token, tunnel_url=tunnel_url, user_id=user_id)


def _optional_str(value: Any) -> str | None:
    """Return ``value`` if it is a non-empty string, else ``None``."""
    if isinstance(value, str) and value:
        return value
    return None
