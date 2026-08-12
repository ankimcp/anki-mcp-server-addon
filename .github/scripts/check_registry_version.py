#!/usr/bin/env python3
"""Ask the MCP registry whether a specific version of this server is live.

This is the *outcome* check that replaced the publisher's exit-code/error-text
classifier. The publisher's own output is prose written by someone else: three
successive attempts to grade a release by pattern-matching it all failed open
(most recently, the registry's DNS-auth 401 body embeds the Go resolver's
``lookup <domain> on 34.118.224.10:53: no such host``, which any sane
"transport failure" allowlist reads as an outage). So nothing here looks at
what the publisher said. It asks the registry what it *has*.

Endpoint
--------
``GET /v0.1/servers/{url-encoded name}/versions/{version}``

Deliberately NOT the ``?search=`` list endpoint: that one is nginx-cached with
a 30s TTL and no purge-on-publish, and it paginates, so it would eventually
start reporting "not published" for a perfectly good release. The exact-version
endpoint returned no cache headers when probed and is answered from the
registry's own store.

The ``%2F`` encoding of the ``/`` in ``ai.ankimcp/anki-mcp-server-addon`` is
mandatory -- an unencoded slash 404s.

Outcomes (also written to ``$GITHUB_OUTPUT`` as ``outcome=``):

``published``
    HTTP 200, the body's name and version are the ones we asked about, and the
    registry's own metadata marks the record ``active``. Exit 0.
``missing``
    HTTP 404. The registry has no such version: whatever the publisher printed,
    it accepted nothing. Exit 1.
``unverified``
    Anything else -- transport error, 5xx, 429, unparseable body, unexpected
    shape. We do not know, and "we do not know" is never green. Exit 2.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REGISTRY_BASE = "https://registry.modelcontextprotocol.io/v0.1"

# Key under `_meta` that carries the registry's own record status. Confirmed
# against a live 200 body for ai.ankimcp/anki-mcp-server-addon@0.19.0, which
# returned `"_meta": {"io.modelcontextprotocol.registry/official":
# {"status": "active", ...}}`.
OFFICIAL_META_KEY = "io.modelcontextprotocol.registry/official"
ACTIVE_STATUS = "active"

SERVER_JSON = pathlib.Path("server.json")

# Per-request bound. Short on purpose: the retry loop below is what provides
# patience, and a request that has not answered in 20s is not about to.
REQUEST_TIMEOUT = 20

# Total budget for the retry loop, and the gap between attempts. The registry
# is not documented as read-your-writes, so a publish that landed a moment ago
# could in principle 404 on the very next read; retrying costs a minute and
# removes a whole class of false "the release published nothing" alarms.
DEFAULT_BUDGET = 60
RETRY_INTERVAL = 5

USER_AGENT = "ankimcp-release-check/1 (+https://github.com/ankimcp/anki-mcp-server-addon)"

PUBLISHED = "published"
MISSING = "missing"
UNVERIFIED = "unverified"

EXIT_CODES = {PUBLISHED: 0, MISSING: 1, UNVERIFIED: 2}


def read_server_name(path: pathlib.Path) -> str:
    """Read the registry server name out of ``server.json``.

    Read rather than hardcoded so the URL and the thing we publish can never
    name two different servers.

    Args:
        path: Path to ``server.json``.

    Returns:
        The ``name`` field (e.g. ``ai.ankimcp/anki-mcp-server-addon``).

    Raises:
        SystemExit: If the file is unreadable or has no usable ``name``.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"::error::Could not read the server name from {path}: {exc}")

    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name:
        sys.exit(f'::error::{path} has no usable "name" field')
    return name


def build_url(name: str, version: str) -> str:
    """Build the exact-version endpoint URL.

    Every path segment is percent-encoded with ``safe=""`` so the ``/`` inside
    the server name becomes ``%2F`` (an unencoded one 404s).
    """
    return (
        f"{REGISTRY_BASE}/servers/{urllib.parse.quote(name, safe='')}"
        f"/versions/{urllib.parse.quote(version, safe='')}"
    )


def probe(url: str) -> tuple[int | None, str | None, str]:
    """Perform one GET.

    Returns:
        ``(status, body, detail)``. ``status`` is None when the request never
        produced an HTTP response at all; ``detail`` is a one-line human
        description of what happened.
    """
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.status, response.read().decode("utf-8", "replace"), "ok"
    except urllib.error.HTTPError as exc:
        # An HTTPError still carries a real status, which is the thing we grade
        # on -- 404 in particular is a verdict, not a transport failure.
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover - body is best-effort context only
            pass
        return exc.code, body, f"HTTP {exc.code}"
    except Exception as exc:  # urllib.error.URLError, socket.timeout, ssl errors...
        return None, None, f"{type(exc).__name__}: {exc}"


def evaluate(
    status: int | None, body: str | None, detail: str, name: str, version: str
) -> tuple[str, str]:
    """Turn one probe result into an outcome plus a one-line explanation."""
    if status is None:
        return UNVERIFIED, f"could not reach the registry ({detail})"

    if status == 404:
        return MISSING, f"the registry has no {name}@{version} (HTTP 404)"

    if status != 200:
        return UNVERIFIED, f"unexpected HTTP {status} from the registry"

    try:
        payload = json.loads(body or "")
    except json.JSONDecodeError:
        return UNVERIFIED, "the registry returned HTTP 200 with a body that is not JSON"

    if not isinstance(payload, dict):
        return UNVERIFIED, "the registry returned HTTP 200 with a non-object body"

    # Observed shape nests the record under "server"; fall back to the top level
    # so a schema reshuffle degrades into a mismatch report rather than a crash.
    record = payload.get("server")
    if not isinstance(record, dict):
        record = payload

    got_name = record.get("name")
    got_version = record.get("version")
    if got_name != name or got_version != version:
        return UNVERIFIED, (
            f"the registry answered for {got_name!r}@{got_version!r} but we asked "
            f"about {name!r}@{version!r}"
        )

    meta = payload.get("_meta")
    official = meta.get(OFFICIAL_META_KEY) if isinstance(meta, dict) else None
    status_field = official.get("status") if isinstance(official, dict) else None
    if status_field != ACTIVE_STATUS:
        return UNVERIFIED, (
            f"{name}@{version} exists but its registry status is "
            f"{status_field!r}, not {ACTIVE_STATUS!r}"
        )

    return PUBLISHED, f"{name}@{version} is live in the registry and active"


def check(name: str, version: str, budget: int) -> tuple[str, str, str]:
    """Probe until the version is confirmed published or the budget runs out.

    Only ``published`` ends the loop early. ``missing`` is retried too: a read
    taken immediately after a publish is the one case where a 404 might simply
    be too early, and a false "the release published nothing" is expensive.

    Returns:
        ``(outcome, detail, url)``.
    """
    url = build_url(name, version)
    print(f"Checking {url}")

    deadline = time.monotonic() + budget
    attempt = 0
    while True:
        attempt += 1
        status, body, detail = probe(url)
        outcome, explanation = evaluate(status, body, detail, name, version)
        print(f"  attempt {attempt}: {outcome} -- {explanation}")
        if outcome == PUBLISHED or time.monotonic() + RETRY_INTERVAL >= deadline:
            return outcome, explanation, url
        time.sleep(RETRY_INTERVAL)


def write_outputs(**values: str) -> None:
    """Append ``key=value`` pairs to ``$GITHUB_OUTPUT`` when running in Actions."""
    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        return
    with open(github_output, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            # Single-line values only; every value produced here is one line.
            handle.write(f"{key}={value.splitlines()[0] if value else ''}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Version to look for.")
    parser.add_argument(
        "--name",
        default=None,
        help="Registry server name (default: the 'name' field of server.json).",
    )
    parser.add_argument(
        "--server-json",
        type=pathlib.Path,
        default=SERVER_JSON,
        help=f"Path to server.json (default: {SERVER_JSON}).",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_BUDGET,
        help=f"Seconds to keep retrying before giving up (default: {DEFAULT_BUDGET}).",
    )
    parser.add_argument(
        "--hint-missing",
        default="the registry accepted nothing",
        help="Caller-specific advice appended to the 'missing' error.",
    )
    parser.add_argument(
        "--hint-unverified",
        default="could not verify; the registry may be down",
        help="Caller-specific advice appended to the 'unverified' error.",
    )
    args = parser.parse_args(argv)

    name = args.name or read_server_name(args.server_json)
    outcome, detail, url = check(name, args.version, args.budget)

    write_outputs(outcome=outcome, detail=detail, url=url, checked_version=args.version)

    if outcome == PUBLISHED:
        print(f"::notice::Registry verified: {detail}")
    elif outcome == MISSING:
        print(f"::error::{args.hint_missing} -- {detail}")
    else:
        print(f"::error::{args.hint_unverified} -- {detail}")

    return EXIT_CODES[outcome]


if __name__ == "__main__":
    raise SystemExit(main())
