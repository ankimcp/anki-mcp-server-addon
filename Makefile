.PHONY: build e2e e2e-full e2e-up e2e-down e2e-test e2e-logs e2e-debug \
       e2e-filtered e2e-filtered-up e2e-filtered-down e2e-filtered-test e2e-filtered-logs

# Build the addon package
build:
	./package.sh

# ---------------------------------------------------------------------------
# Run ALL E2E tests (regular + filtered)
# ---------------------------------------------------------------------------
e2e: e2e-full e2e-filtered

# ---------------------------------------------------------------------------
# Regular container (all tools enabled) -- port 3141
# ---------------------------------------------------------------------------

# Full cycle: build, start, test, stop
e2e-full: e2e-up
	@echo "Waiting for MCP server..."
	@for i in $$(seq 1 60); do \
		if npx @modelcontextprotocol/inspector --cli http://localhost:3141 --transport http --method tools/list 2>/dev/null; then \
			echo "Server ready!"; \
			break; \
		fi; \
		echo "Attempt $$i/60..."; \
		sleep 1; \
	done
	$(MAKE) e2e-test || ($(MAKE) e2e-down && exit 1)
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

# Show container logs
e2e-logs:
	cd .docker && docker compose logs -f

# Keep container running after tests (for debugging)
e2e-debug: e2e-up
	@echo "Container running. Run 'make e2e-test' to test, 'make e2e-down' to stop."
	@echo "VNC available at localhost:5900"

# ---------------------------------------------------------------------------
# Filtered container (disabled_tools config) -- port 3142
# ---------------------------------------------------------------------------

# Full cycle: build, start, test, stop
e2e-filtered: e2e-filtered-up
	@echo "Waiting for filtered MCP server on port 3142..."
	@for i in $$(seq 1 60); do \
		if npx @modelcontextprotocol/inspector --cli http://localhost:3142 --transport http --method tools/list 2>/dev/null; then \
			echo "Server ready!"; \
			break; \
		fi; \
		echo "Attempt $$i/60..."; \
		sleep 1; \
	done
	$(MAKE) e2e-filtered-test || ($(MAKE) e2e-filtered-down && exit 1)
	$(MAKE) e2e-filtered-down

# Start filtered container
e2e-filtered-up: build
	cd .docker && docker compose -f docker-compose.filtered.yml up -d
	@echo "Waiting for filtered Anki to start..."
	@sleep 5

# Stop filtered container
e2e-filtered-down:
	cd .docker && docker compose -f docker-compose.filtered.yml down

# Run filtered E2E tests (assumes filtered container is running)
e2e-filtered-test:
	MCP_SERVER_URL=http://localhost:3142 pytest tests/e2e/test_tool_filtering_e2e.py -v

# Show filtered container logs
e2e-filtered-logs:
	cd .docker && docker compose -f docker-compose.filtered.yml logs -f
