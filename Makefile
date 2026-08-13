.PHONY: build unit preview-description e2e e2e-full e2e-up e2e-down e2e-test e2e-logs \
       e2e-logs-dump e2e-debug \
       e2e-filtered e2e-filtered-up e2e-filtered-down e2e-filtered-test e2e-filtered-logs \
       e2e-filtered-logs-dump

# ---------------------------------------------------------------------------
# MCP Inspector CLI (the E2E test client)
# ---------------------------------------------------------------------------

# Pinned so a build is reproducible and an upstream Inspector release cannot
# break CI the moment it lands. MUST stay in sync with INSPECTOR_DEFAULT_VERSION
# in tests/e2e/helpers.py -- the readiness probes below use this value and the
# suite itself uses that one, and a mismatch would test two different clients.
# The `e2e-inspector-latest` nightly workflow overrides it with `latest`.
#
#   make e2e INSPECTOR_VERSION=latest
INSPECTOR_VERSION ?= 2.1.0

# An environment variable set to the EMPTY string still counts as "defined" for
# `?=`, and the E2E workflow exports INSPECTOR_VERSION unconditionally (empty
# meaning "use the pin"). Normalise that here so the npx spec is never a bare
# trailing `@`.
ifeq ($(strip $(INSPECTOR_VERSION)),)
override INSPECTOR_VERSION := 2.1.0
endif

# Exported so pytest/helpers.py resolves the same Inspector as the readiness
# probes when the suite is driven through make.
export INSPECTOR_VERSION

INSPECTOR_PKG := @modelcontextprotocol/inspector@$(INSPECTOR_VERSION)

# ---------------------------------------------------------------------------
# Readiness probe
# ---------------------------------------------------------------------------

# Attempts and per-attempt bound for the "is the MCP server up yet?" loop.
READY_ATTEMPTS ?= 60
PROBE_TIMEOUT ?= 60

# The cold-cache `npx` in this loop is the first npx of the run and the
# likeliest place for the whole suite to stall. Unbounded, it hangs `make` until
# the CI job hits `timeout-minutes` and is CANCELLED -- which also means the
# release job never runs. So bound it.
#
# macOS ships no coreutils `timeout`; Homebrew's coreutils installs `gtimeout`.
# If neither exists, degrade to an unbounded probe (with a printed note) rather
# than breaking `make e2e` on a stock macOS box.
TIMEOUT_BIN := $(shell command -v timeout 2>/dev/null || command -v gtimeout 2>/dev/null)
ifeq ($(TIMEOUT_BIN),)
PROBE_RUNNER :=
PROBE_NOTE := echo "note: no 'timeout'/'gtimeout' on PATH -- readiness probes run UNBOUNDED (brew install coreutils to bound them)"
else
PROBE_RUNNER := $(TIMEOUT_BIN) $(PROBE_TIMEOUT)
PROBE_NOTE := true
endif

# $(1) = port, $(2) = human label. One logical shell line so it can be used
# directly as a recipe line via $(call ...).
define wait_for_mcp
echo "Waiting for $(2) MCP server on port $(1) (Inspector $(INSPECTOR_VERSION))..."; \
	$(PROBE_NOTE); \
	for i in $$(seq 1 $(READY_ATTEMPTS)); do \
		if $(PROBE_RUNNER) npx -y $(INSPECTOR_PKG) --cli http://localhost:$(1) --transport http --method tools/list >/dev/null 2>&1; then \
			echo "Server ready!"; \
			break; \
		fi; \
		echo "Attempt $$i/$(READY_ATTEMPTS)..."; \
		sleep 1; \
	done
endef

# Build the addon package
build:
	./package.sh

# Run unit tests (no Docker / Anki needed -- aqt is stubbed in tests/unit/conftest.py)
unit:
	pytest tests/unit/ -v

# ---------------------------------------------------------------------------
# Preview the AnkiWeb description
# ---------------------------------------------------------------------------

# Wraps the bare `ankiweb-description.html` fragment in a page shell and points
# its images at the local copies, so the listing can be eyeballed in a browser
# before anything is pushed. The script's docstring explains why the fragment
# can't just be opened directly.
preview-description:
	@python3 tools/preview_description.py

# ---------------------------------------------------------------------------
# Run ALL E2E tests (regular + filtered)
# ---------------------------------------------------------------------------
e2e: e2e-full e2e-filtered

# ---------------------------------------------------------------------------
# Regular container (all tools enabled) -- port 3141
# ---------------------------------------------------------------------------

# Full cycle: build, start, test, stop.
# On test failure the logs are dumped BEFORE the container is torn down --
# `docker compose down` destroys them, so a teardown-first ordering leaves CI
# with no record of what Anki was doing when the suite failed.
e2e-full: e2e-up
	@$(call wait_for_mcp,3141,regular)
	$(MAKE) e2e-test || ($(MAKE) e2e-logs-dump; $(MAKE) e2e-down; exit 1)
	$(MAKE) e2e-down

# Start headless Anki container
e2e-up: build
	cd .docker && docker compose up -d
	@echo "Waiting for Anki to start..."
	@sleep 5

# Stop headless Anki container
e2e-down:
	cd .docker && docker compose down

# Run E2E tests (assumes container is running)
e2e-test:
	pytest tests/e2e/ -v --ignore=tests/e2e/test_tool_filtering_e2e.py

# Show container logs (follows -- interactive use only, never in CI)
e2e-logs:
	cd .docker && docker compose logs -f

# Dump container logs once and exit. Separate from e2e-logs because that one
# follows: a `-f` invocation in CI would hang the job instead of failing it.
# Tailed so a chatty container cannot bury the pytest traceback above it, or
# push the CI runner toward its log-size cap.
e2e-logs-dump:
	@echo "===== container logs (regular, port 3141, last 2000 lines) ====="
	cd .docker && docker compose logs --no-color --timestamps --tail=2000 2>&1 || true

# Keep container running after tests (for debugging)
e2e-debug: e2e-up
	@echo "Container running. Run 'make e2e-test' to test, 'make e2e-down' to stop."
	@echo "VNC available at localhost:5900"

# ---------------------------------------------------------------------------
# Filtered container (disabled_tools config) -- port 3142
# ---------------------------------------------------------------------------

# Full cycle: build, start, test, stop.
# Same log-before-teardown ordering as e2e-full.
e2e-filtered: e2e-filtered-up
	@$(call wait_for_mcp,3142,filtered)
	$(MAKE) e2e-filtered-test || ($(MAKE) e2e-filtered-logs-dump; $(MAKE) e2e-filtered-down; exit 1)
	$(MAKE) e2e-filtered-down

# Start filtered container
e2e-filtered-up: build
	cd .docker && docker compose -f docker-compose.filtered.yml up -d
	@echo "Waiting for filtered Anki to start..."
	@sleep 5

# Stop filtered container
e2e-filtered-down:
	cd .docker && docker compose -f docker-compose.filtered.yml down

# Run filtered E2E tests (assumes container is running).
# test_model_fields_remove.py rides along because it needs the filtered
# container's `enabled_destructive_tools` config, not because it tests filtering.
e2e-filtered-test:
	MCP_SERVER_URL=http://localhost:3142 pytest tests/e2e/test_tool_filtering_e2e.py tests/e2e/test_model_fields_remove.py -v

# Show filtered container logs (follows -- interactive use only, never in CI)
e2e-filtered-logs:
	cd .docker && docker compose -f docker-compose.filtered.yml logs -f

# Dump filtered container logs once and exit (see e2e-logs-dump).
e2e-filtered-logs-dump:
	@echo "===== container logs (filtered, port 3142, last 2000 lines) ====="
	cd .docker && docker compose -f docker-compose.filtered.yml logs --no-color --timestamps --tail=2000 2>&1 || true
