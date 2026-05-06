# ADR-001: aegis extraction architecture

**Status:** Accepted (2026-05-06; Claude/Codex collaboration)
**Supersedes:** None
**Source spec:** `~/Code/aegis/SPEC.md`

## Context

aegis is the resource-budget primitive: `withResourceBudget({ ... }, fn)`
wraps every blocking call in the stack with a declared timeout, optional
fallback, structured tags, and observability hooks. First implementation
lives at `reeve/src/observability/aegis/` with 13 contract tests
passing.

The extraction question: what shape should aegis take as a standalone
component?

## Decision

**Twin implementations: TypeScript at `~/Code/aegis/ts/` and Python at
`~/Code/aegis/py/`. Shared `pact.yaml` contract spec + golden test
vectors at the repo root.**

### Why twin

- Hot-path primitive. A subprocess-IPC adapter or HTTP service adds
  per-call latency that breaks the budget guarantee aegis is supposed
  to enforce.
- TS world (Reeve, Apprentice, future TS components) and Python world
  (Baton, Ledger, Sentinel, Chronicler) BOTH need aegis. Privileging
  one and adapting the other creates second-class citizens.
- Behavior is small and deterministic. Twin maintenance cost is real
  but bounded (no plugin system, no pluggable backends, just a wrapper
  primitive).

### Why golden vectors AND a differential fuzzer

Cross-language behavioral parity is load-bearing. Two test layers,
both required (sim-vetted):

1. **Golden vectors** at `vectors/budget-cases.json` — fixed
   input-output pairs both impls must pass identically. Named cases:
   timeout fires within budget+slack; fallback runs on budget
   exhaustion; primary error passes through unwrapped; fallback
   resourceClass cannot exceed primary.
2. **Differential fuzzer** at `vectors/differential.test.{ts,py}` —
   property-based testing generates random valid inputs; runs BOTH
   impls; asserts outputs byte-identical. Golden vectors are a FLOOR,
   not a ceiling — they prove specific cases match but not all cases.

Edge cases vectors AND fuzzer must cover (sim-flagged):
- **Sub-millisecond concurrent arrivals** — Node microtask queue vs
  asyncio event loop schedule differently; impls must converge under
  microsecond interleaving.
- **Window-expiry boundaries** — request 0.5ms before vs 0.5ms after.
- **Max representable timestamps** — 2038 boundaries; Python
  `datetime` vs JS `Date` epoch ordering.
- **Wall-clock vs monotonic ordering** — `performance.now()` (TS)
  and `time.monotonic()` (Python) must agree on "which request first"
  when wall-clock and monotonic disagree.
- **Both primary AND fallback budget-exhaust simultaneously** — error
  tree shape must match.

### Repo layout

```
~/Code/aegis/
├── SPEC.md              # already exists
├── ADR-001-extraction.md  # this file
├── pact.yaml            # contract spec consumed by both impls
├── vectors/
│   └── budget-cases.json
├── ts/
│   ├── package.json     # @stack/aegis
│   ├── tsconfig.json
│   ├── src/
│   │   ├── types.ts     # ported from reeve/src/observability/aegis/
│   │   ├── errors.ts
│   │   ├── budget.ts
│   │   └── index.ts
│   └── tests/
│       ├── unit/aegis-types.test.ts
│       ├── integration/aegis.test.ts
│       └── golden/budget-cases.test.ts  # consumes vectors/
├── py/
│   ├── pyproject.toml   # stack-aegis (PyPI name TBD)
│   ├── src/aegis/
│   │   ├── types.py
│   │   ├── errors.py
│   │   ├── budget.py
│   │   └── __init__.py
│   └── tests/
│       ├── test_types.py
│       ├── test_budget.py
│       └── test_golden_vectors.py
└── lint/
    ├── biome-aegis.ts   # custom lint rule for TS consumers
    └── ruff-aegis.py    # equivalent for Python consumers
```

### Publishing

- TS: `git+ssh://github.com/jmcentire/aegis.git#main` referenced in
  consumer `package.json`. Subpath `ts/` resolved via `workspaces`
  field. Defer npm registry publish until aegis is consumed by 3+
  components.
- Python: `pip install git+ssh://github.com/jmcentire/aegis.git#egg=aegis&subdirectory=py`.
  Defer PyPI until similar threshold.

### Lint plugin

Both impls ship a lint rule that flags un-wrapped blocking calls in
consumer code:
- TS: a Biome custom plugin (or eslint plugin if Biome can't host
  custom rules at the time of build) that detects `client.query()`,
  `fetch()`, `Anthropic.messages.create()` outside a
  `withResourceBudget` wrapper.
- Python: a ruff plugin detecting equivalent patterns
  (`pg.Connection.execute()`, `requests.get()`, etc.).

V1 lint plugins flag (warn); V2 hard-fails consumer CI.

## Consequences

**Positive**
- Hot-path consumers in both languages get aegis without
  cross-language adapter latency.
- Behavior parity enforced via golden vectors; drift surfaces in CI.
- Reeve migration is a clean import-path swap; existing tests stay.
- Future stack components (apprentice, etc.) consume the same primitive.

**Negative**
- Twin maintenance is twice the surface area for any contract change.
- Two test suites + golden vectors = three places to update.
- Lint plugin authoring per-language doubles the discovery effort.

## Migration plan (Reeve, blocked on this ADR shipping)

1. Init `~/Code/aegis/` per layout above.
2. Copy + adapt `reeve/src/observability/aegis/` into `ts/src/`.
3. Port Reeve's tests to `ts/tests/`. They should pass unchanged.
4. Author `vectors/budget-cases.json` from the test cases.
5. Author Python sibling at `py/src/aegis/`. Pass golden vectors.
6. Add Biome custom-lint rule (V1 warn-mode).
7. Reeve PR: replace
   `import {...} from '../observability/aegis/index.js'` with
   `import {...} from '@stack/aegis/ts'` (or installed name); delete
   private module; verify suite stays green.

## Open questions for next ADR

- Memory budget enforcement: aegis's slice-2 capability matrix says
  Node can't enforce per-call memory limits. Does the Python side have
  the same limitation, or can `resource.RLIMIT_AS` give us a real
  budget? Defer to ADR-002 once Python sibling's first prototype lands.
- AsyncLocalStorage in Python: TS has `AsyncLocalStorage` for tag
  propagation; Python equivalent is `contextvars`. The semantics differ
  subtly across `async/await` boundaries vs threads. Document in
  ADR-002.
