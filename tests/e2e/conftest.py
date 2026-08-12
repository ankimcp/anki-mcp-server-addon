"""E2E test configuration and fixtures."""
from __future__ import annotations

import os
import time
import uuid

import pytest

# Configuration
SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:3141")
KEEP_RUNNING = os.environ.get("E2E_KEEP_RUNNING", "0") == "1"

# A wall-clock budget, NOT an attempt count. It used to be the loop bound of
# `range(MAX_WAIT_SECONDS)`, which read like seconds but was 60 *attempts* --
# and one attempt can cost ~111s (helpers.py's 45s Inspector timeout, plus a
# silent-timeout retry, plus the post-kill drain), so the real worst case was
# closer to 110 minutes than to a minute.
MAX_WAIT_SECONDS = int(os.environ.get("E2E_MAX_WAIT", "60"))

# Gap between readiness probes. Only the gaps are skipped once the budget is
# spent; a probe already in flight still has to finish, so the true ceiling is
# the budget plus one probe.
POLL_INTERVAL_SECONDS = 1


def unique_id() -> str:
    """Generate unique suffix to avoid duplicate conflicts."""
    return str(uuid.uuid4())


@pytest.fixture(scope="session", autouse=True)
def wait_for_server():
    """Wait for MCP server to be ready before running tests."""
    from .helpers import run_inspector

    print(f"\nWaiting for MCP server at {SERVER_URL} (up to {MAX_WAIT_SECONDS}s)...")

    started = time.monotonic()
    deadline = started + MAX_WAIT_SECONDS
    attempts = 0

    while True:
        attempts += 1
        try:
            result = run_inspector("tools/list")
            if "tools" in result:
                print(
                    f"Server ready after {time.monotonic() - started:.1f}s "
                    f"({attempts} attempt(s))"
                )
                return
        except Exception:
            pass

        # Checked after the probe so at least one probe always runs, however
        # small the budget. The sleep is clamped to whatever is left instead of
        # being skipped when a whole interval no longer fits: skipping gave up a
        # full interval early (a 5s budget stopped probing at ~4s), while
        # clamping spends the budget exactly and still terminates -- the next
        # iteration's probe starts at or after the deadline and the loop breaks
        # right after it.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(POLL_INTERVAL_SECONDS, remaining))

    pytest.fail(
        f"MCP server not ready after {time.monotonic() - started:.1f}s "
        f"({attempts} attempt(s), budget {MAX_WAIT_SECONDS}s)"
    )


@pytest.fixture(scope="session")
def server_url():
    """Return the MCP server URL."""
    return SERVER_URL
