.PHONY: build e2e-up e2e-down e2e-test e2e e2e-logs

# Build the addon package
build:
	./package.sh

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
	pytest tests/e2e/ -v

# Full E2E cycle: build, start, test, stop
e2e: e2e-up
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

# Show container logs
e2e-logs:
	cd .docker && docker compose logs -f

# Keep container running after tests (for debugging)
e2e-debug: e2e-up
	@echo "Container running. Run 'make e2e-test' to test, 'make e2e-down' to stop."
	@echo "VNC available at localhost:5900"
