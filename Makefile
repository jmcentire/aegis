# aegis — cross-language test orchestration.
#
# Convenience targets for the twin-impl workflow. CI uses the same
# steps; when CI lands (currently local-only per ADR), wire these into
# whichever runner.

.PHONY: install test test-ts test-py test-all clean lint

install:
	cd ts && npm install
	cd py && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

test-ts:
	cd ts && npm test

test-py:
	cd py && .venv/bin/pytest

# Ordered: TS first (regenerates the fuzzer fixtures), then Python.
test-all: test-ts test-py

test: test-all

lint:
	cd ts && npm run lint

clean:
	rm -rf ts/node_modules ts/dist ts/.tsbuildinfo
	rm -rf py/.venv py/.pytest_cache py/*.egg-info py/build py/dist
	find py -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
