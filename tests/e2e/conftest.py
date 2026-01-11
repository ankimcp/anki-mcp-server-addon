"""E2E test configuration and fixtures."""
from __future__ import annotations

import os
import subprocess
import time

import pytest

# Configuration
SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:3141")
KEEP_RUNNING = os.environ.get("E2E_KEEP_RUNNING", "0") == "1"
MAX_WAIT_SECONDS = int(os.environ.get("E2E_MAX_WAIT", "60"))


@pytest.fixture(scope="session", autouse=True)
def wait_for_server():
    """Wait for MCP server to be ready before running tests."""
    from .helpers import run_inspector

    print(f"\nWaiting for MCP server at {SERVER_URL}...")

    for attempt in range(MAX_WAIT_SECONDS):
        try:
            result = run_inspector("tools/list")
            if "tools" in result:
                print(f"Server ready after {attempt + 1}s")
                return
        except Exception:
            pass
        time.sleep(1)

    pytest.fail(f"MCP server not ready after {MAX_WAIT_SECONDS}s")


@pytest.fixture(scope="session")
def server_url():
    """Return the MCP server URL."""
    return SERVER_URL
