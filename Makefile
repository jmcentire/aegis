# aegis — cross-language test orchestration.
#
# Convenience targets for the twin-impl workflow.

.PHONY: install test test-ts test-py test-all clean lint build

install:
	npm install
	cd py && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

build:
	npm run build

test-ts:
	npm test

test-py:
	cd py && .venv/bin/pytest

# Ordered: TS first (regenerates the fuzzer fixtures), then Python.
test-all: test-ts test-py

test: test-all

lint:
	npm run lint

clean:
	rm -rf node_modules dist .tsbuildinfo
	rm -rf py/.venv py/.pytest_cache py/*.egg-info py/build py/dist
	find py -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
