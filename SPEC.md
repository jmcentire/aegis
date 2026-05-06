# aegis — Resource Budget Primitive

## Charter

Wrap every blocking call in the stack with declared resource budgets and
honest semantics. A blocking call without a budget is a bug; aegis is the
primitive that makes budgets first-class. The contract aegis can promise
is bounded by what the underlying runtime can enforce — see Reeve's
`docs/production-stability/containment-capability-matrix.md` for the
empirical basis.

aegis is the cross-stack home for what was originally drafted as Reeve's
slice-2 `withResourceBudget`. It moves out of Reeve because Apprentice,
Chronicler, Baton, and any other component that issues blocking calls
needs the same primitive — and a Reeve-internal module would force every
other component to depend on Reeve.

## Interface (proposed)

```typescript
import type { Logger } from '@stack/observability';

export type ResourceClass = 'cpu' | 'io' | 'llm' | 'pg' | 'mem-sample';

export type Budget = {
  // Wall-clock deadline. The only enforceable budget for all classes.
  timeoutMs: number;
  // For callers that respect AbortSignal. Cooperatively-checking
  // functions only — e.g., fetch, pg query with statement_timeout.
  signalRespecting: boolean;
  // For pg work specifically — sets statement_timeout on the connection
  // before the wrapped fn runs.
  pgStatementTimeoutMs?: number;
  // Memory pressure sampling interval. aegis cannot ENFORCE per-call
  // memory limits in Node; it CAN sample process.memoryUsage() and
  // emit pressure events to baton so the host can be drained.
  memSampleEveryMs?: number;
};

export type Tags = {
  component: string;       // e.g., 'reeve', 'apprentice', 'chronicler'
  op: string;              // e.g., 'receptionist.send_email_reply'
  tenantId?: string;
  correlationId?: string;
};

export type Fallback<T> = {
  // Type-system enforced: same shape as the wrapped fn.
  fn: (signal: AbortSignal) => Promise<T>;
  // Declared resource class of the fallback. aegis refuses registration
  // if the fallback's resource class is more expensive than the
  // primary's. This implements roadmap principle #6 (fallback
  // dependency graph proven).
  resourceClass: ResourceClass;
};

export interface Aegis {
  wrap<T>(
    fn: (signal: AbortSignal) => Promise<T>,
    args: {
      budget: Budget;
      fallback?: Fallback<T>;
      tags: Tags;
      primaryResourceClass: ResourceClass;
    },
  ): Promise<T>;

  // Aegis-managed registries (for plugin-style consumers).
  registerHandler<T>(args: {
    id: string;
    handler: (signal: AbortSignal) => Promise<T>;
    budget: Budget;
    primaryResourceClass: ResourceClass;
    fallback?: Fallback<T>;
  }): void;

  // Walked by lint rules and the cascade-map generator.
  listRegistrations(): readonly Registration[];
}
```

## What aegis does NOT promise

- **It cannot kill a synchronous CPU burn.** Node is cooperative. A
  function with a `while (true) {}` loop and no `await` will run to
  completion regardless of timeout. Use worker_threads for compute-bound
  code; aegis exposes `wrapWorkerThread` as a separate primitive.
- **It cannot enforce per-call memory limits.** It samples and emits
  pressure events. The mitigation is at the process level (kill the
  process; baton routes traffic) — aegis surfaces the signal early.
- **It does not retry.** Retry policy is explicit at the call site or
  composed via a separate retry primitive. Mixing concerns here makes
  cascade-map analysis impossible.

## Stack consumers (who needs this)

- **reeve** — every LLM call, every pg query, every plugin handler
  (the original slice-2 caller).
- **apprentice** — skill execution wraps in aegis (apprentice already
  has its own loose timeouts; aegis tightens them).
- **chronicler** — event correlation is a long-running join; needs a
  budget per correlation window.
- **baton** — health checks need budgets so a slow check can't block
  promotion decisions.
- **covenant** — contract validation calls (especially OpenAPI schema
  loads) need budgets.

## Open questions

1. Does aegis own its own observability, or does it write events
   through baton's control port? (Lean: writes through baton; baton
   is the singular event channel.)
2. How does aegis interact with `AsyncLocalStorage` for tag
   propagation? (Lean: tags stored in ALS, automatically available to
   the wrapped fn.)
3. Should aegis ship as both a runtime library and a CLI lint plugin
   that flags un-wrapped blocking calls? (Lean: yes; the lint plugin
   lives in `aegis/lint/`.)

## Initial implementation plan

1. Spec lock: this doc + a TypeScript interface file `index.d.ts`.
2. First implementation lives at `reeve/src/observability/aegis/` as
   a private module. Public interface stable.
3. When Apprentice or Chronicler needs it, extract to `~/Code/aegis/`
   as a published `@stack/aegis` package.
4. CI gate: lint rule that rejects un-wrapped pg queries / fetch calls
   in src/ outside aegis itself.

## Provenance

Spec'd 2026-05-05 from a simulacrum-driven NASA-bar review of Reeve's
production-stability roadmap. Source roadmap:
`reeve/docs/production-stability-roadmap.md`. Derived from empirical
findings in `reeve/docs/production-stability/containment-capability-matrix.md`.
