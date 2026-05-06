# aegis — resource-budget primitive

Wrap every blocking call in your stack with a declared timeout, optional
fallback, and structured tags. aegis is the primitive that makes budgets
first-class — a blocking call without a budget is a bug.

aegis ships as **twin implementations**: TypeScript (root) and Python
(`py/`). Both share a behavioral contract locked in `pact.yaml` and
verified by golden vectors (`vectors/budget-cases.json`) plus a
differential fuzzer that runs 1000+ random inputs through both impls
and asserts byte-identical canonical output.

## Status

| Layer                      | TS                    | Python                 |
| -------------------------- | --------------------- | ---------------------- |
| Source                     | `src/`                | `py/src/aegis/`        |
| Contract tests             | 13 passing            | 10 passing             |
| Golden vectors             | 15 cases passing      | 15 cases passing       |
| Differential fuzzer        | 1000 cases generated  | 1000 cases compared    |
| Hypothesis invariants      | n/a                   | 3 properties × 200 ex. |
| Lint plugin                | scaffolded (V1 warn)  | scaffolded (V1 warn)   |

See `SPEC.md` for the design charter and `ADR-001-extraction.md` for
the architecture lock.

## TypeScript usage

### Install

aegis is consumed as a git dependency. The package's `prepare` script
runs `tsc → dist/` at install time, so `npm install` from a git URL
gets a fully built package with `.d.ts` files.

```json
{
  "dependencies": {
    "@stack/aegis": "git+https://github.com/jmcentire/aegis.git#v0.1.1"
  }
}
```

Pin a specific tag (recommended) or follow `main` if you want
unreleased fixes.

### Wrap a blocking call

```typescript
import { withResourceBudget } from '@stack/aegis';

const result = await withResourceBudget(
  {
    budget: { timeoutMs: 5000, signalRespecting: true },
    tags: {
      component: 'reeve',
      op: 'anthropic.messages.create',
      tenantId: ctx.tenantId,
    },
    primaryResourceClass: 'llm',
    fallback: {
      // Cheaper fallback: cache lookup. Fallback class MUST NOT outrank
      // primary's class — aegis enforces this at wrap-time.
      fn: async (signal) => readFromCache(signal),
      resourceClass: 'io',
    },
  },
  async (signal) => anthropic.messages.create(req, { signal }),
);
```

### Wire the observer

aegis emits a structured event on every wrap completion (success,
fallback, error). It does NOT import any logging library — that's
your choice:

```typescript
import { setAegisObserver } from '@stack/aegis';
import { logger } from './observability/logger.js';

setAegisObserver((evt) => {
  logger.info(
    {
      component: evt.tags.component,
      op: evt.tags.op,
      outcome: evt.outcome,
      elapsedMs: Math.round(evt.elapsedMs),
      tenantId: evt.tags.tenantId,
    },
    `aegis.${evt.outcome}`,
  );
});
```

Call `setAegisObserver()` once at boot. To unwire (e.g., in tests):
`clearAegisObserver()`.

### Read tags from inside the wrapped fn

Tags propagate via Node's `AsyncLocalStorage`:

```typescript
import { getAegisTags } from '@stack/aegis';

async function deeplyNested() {
  const tags = getAegisTags();
  console.log('tenant:', tags?.tenantId);
}
```

## Python usage

### Install

```bash
pip install git+ssh://git@github.com/jmcentire/aegis.git#egg=stack-aegis&subdirectory=py
```

### Wrap a blocking call

```python
import asyncio
from aegis import (
    Budget, Fallback, Tags, WrapArgs,
    with_resource_budget,
)

async def call_anthropic(event: asyncio.Event) -> dict:
    # Honor the cancellation event in any blocking await.
    return await client.messages.create(...)

async def cache_lookup(event: asyncio.Event) -> dict:
    return await read_from_cache(...)

result = await with_resource_budget(
    WrapArgs(
        budget=Budget(timeout_ms=5000, signal_respecting=True),
        tags=Tags(
            component="baton",
            op="anthropic.messages.create",
            tenant_id=ctx.tenant_id,
        ),
        primary_resource_class="llm",
        fallback=Fallback(fn=cache_lookup, resource_class="io"),
    ),
    call_anthropic,
)
```

### Wire the observer

```python
from aegis import set_aegis_observer
from my_app.logging import logger

def _aegis_observer(evt) -> None:
    logger.info(
        "aegis.%s",
        evt.outcome,
        extra={
            "component": evt.tags.component,
            "op": evt.tags.op,
            "elapsed_ms": round(evt.elapsed_ms),
            "tenant_id": evt.tags.tenant_id,
        },
    )

set_aegis_observer(_aegis_observer)
```

### Read tags from inside the wrapped fn

```python
from aegis import get_aegis_tags

async def deeply_nested():
    tags = get_aegis_tags()
    print(f"tenant: {tags.tenant_id if tags else None}")
```

Tags propagate via Python's `contextvars`.

## Stack consumers

| Component   | Language   | Use case                                       |
| ----------- | ---------- | ---------------------------------------------- |
| reeve       | TypeScript | LLM, pg query, webhook, plugin handler wraps   |
| apprentice  | TypeScript | Skill execution timeouts                       |
| baton       | Python     | Health-check bounded execution                 |
| chronicler  | Python     | Per-correlation join window                    |
| covenant    | TypeScript | OpenAPI schema-load wraps                      |
| ledger      | Python     | Indexer-side query wraps                       |

When you wire aegis in a new consumer, also wire the lint plugin
(see `lint/`) so unwrapped blocking calls surface in your local
linter.

## What aegis does NOT promise

- **It cannot kill a synchronous CPU burn.** Both Node (cooperative
  event loop) and Python (GIL) require external mechanisms (worker
  threads / multiprocessing) to interrupt compute-bound code. aegis
  sets the timer and emits the event, but the burn runs to
  completion. Use `signalRespecting: false` to acknowledge this and
  consider out-of-process execution.
- **It does not enforce per-call memory limits.** Sampling only;
  aegis emits pressure events that a host (e.g., `baton`) can route
  on. Real per-call enforcement requires `RLIMIT_AS` (Linux) or
  process boundaries — covered by ADR-002.
- **It does not retry.** Retry is a separate concern; mixing it here
  makes cascade-map analysis impossible.

## Development

### TypeScript

```bash
cd ts
npm install
npm test          # vitest: contract + golden + fuzzer
npm run type-check
npm run lint
```

### Python

```bash
cd py
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest            # contract + golden + differential + hypothesis
```

### Running both

```bash
# From the repo root:
npm run test:all  # runs TS, then Python
```

The differential fuzzer requires the TS test suite to run FIRST
(it produces `vectors/_fuzzer-shared/{inputs.json,ts-outcomes.json}`
which the Python side consumes). If the shared fixtures are missing,
the Python differential test xfails with a hint.

### `pact validate`

aegis's `pact.yaml` is a behavioral contract spec, NOT a project
consumed by the `pact` CLI's contract-first decomposition pipeline.
Running `pact validate .` against this repo will report
"No decomposition tree found" — that's expected. The contract is
verified by the golden vectors and the differential fuzzer, not by
the pact CLI.

## Layout

```
aegis/
├── SPEC.md                   # design charter
├── ADR-001-extraction.md     # twin TS+Python decision
├── pact.yaml                 # behavioral contract spec (doc artifact)
├── README.md                 # this file
├── package.json              # @stack/aegis (TS package)
├── tsconfig.json             # TS compiler config (allowImportingTsExtensions for dev)
├── tsconfig.build.json       # emits dist/ via the prepare script
├── vitest.config.ts
├── biome.json
├── src/                      # TS impl
│   ├── types.ts
│   ├── errors.ts
│   ├── budget.ts
│   └── index.ts
├── tests/                    # vitest
│   ├── unit/
│   ├── integration/
│   ├── golden/               # consumes ../vectors/budget-cases.json
│   └── differential.test.ts
├── py/
│   ├── src/aegis/            # Python impl
│   │   ├── types.py
│   │   ├── errors.py
│   │   ├── budget.py
│   │   └── __init__.py
│   ├── tests/                # pytest
│   │   ├── test_types.py
│   │   ├── test_budget.py
│   │   ├── test_golden_vectors.py
│   │   └── test_differential.py
│   └── pyproject.toml        # stack-aegis
├── vectors/
│   ├── budget-cases.json     # cross-language golden vectors
│   └── _fuzzer-shared/       # generated by TS fuzzer; committed
│       ├── inputs.json       # 1000 deterministic random inputs
│       └── ts-outcomes.json  # TS canonical outputs for those inputs
└── lint/
    ├── biome-aegis.ts        # V1 warn-mode placeholder
    └── ruff-aegis.py         # V1 warn-mode placeholder
```

## Provenance

Spec'd and extracted 2026-05-05/06 from the simulacrum-driven
NASA-bar review of Reeve's production-stability roadmap. First
implementation lived at `~/Code/reeve/src/observability/aegis/` and
moves out of Reeve because Apprentice, Baton, Chronicler, and other
stack components need the same primitive.
