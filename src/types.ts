// aegis types. Public surface declared here; the primitive lives in
// budget.ts. Discipline:
//   - ResourceClass is ordered. A fallback whose resource class outranks
//     the primary's is rejected at registration (cf. roadmap principle 6:
//     "fallback dependency graph proven").
//   - Tags are required. The primitive REFUSES un-tagged calls — telemetry
//     without tags is signal-without-context.
//
// Per ~/Code/aegis/pact.yaml, this surface is shared with the Python
// sibling at py/src/aegis/. Drift here means cross-language tests fail.

// Ordered cheapest -> most expensive. Used to validate that a fallback
// path cannot escalate beyond the primary's resource class.
export type ResourceClass = 'mem-sample' | 'cpu' | 'io' | 'pg' | 'llm';

const RESOURCE_RANK: Readonly<Record<ResourceClass, number>> = {
  'mem-sample': 0,
  cpu: 1,
  io: 2,
  pg: 3,
  llm: 4,
} as const;

export function resourceClassRank(c: ResourceClass): number {
  return RESOURCE_RANK[c];
}

export type Budget = {
  // Wall-clock deadline. The only enforceable budget across all classes
  // (per the slice-1 capability matrix; sync CPU burn is uncontainable
  // cooperatively).
  readonly timeoutMs: number;
  // True when the wrapped fn correctly responds to AbortSignal (fetch,
  // pg query with statement_timeout, an awaitable that checks signal).
  // False for compute-bound code that cannot honor the signal — the
  // primitive will set the timer but document that it cannot kill the
  // call cooperatively. Use worker_threads for that path.
  readonly signalRespecting: boolean;
  // For pg-bound work. The primitive sets `SET statement_timeout = N`
  // on the connection BEFORE running fn, so a hanging query is
  // server-side-cancelled within budget. fn is responsible for using
  // the same connection.
  readonly pgStatementTimeoutMs?: number;
};

export type Tags = {
  // e.g. 'reeve.adapters.llm', 'apprentice.skills', 'baton.health'
  readonly component: string;
  // e.g. 'anthropic.messages.create', 'pg.event_log.append'
  readonly op: string;
  readonly tenantId?: string;
  readonly correlationId?: string;
};

export type Fallback<T> = {
  readonly fn: (signal: AbortSignal) => Promise<T>;
  // Validated against the primary's resource class at wrap time. A
  // fallback whose class outranks the primary's is rejected — the
  // fallback path must not be more expensive than the path it replaces.
  readonly resourceClass: ResourceClass;
};

export type WrapArgs<T> = {
  readonly budget: Budget;
  readonly tags: Tags;
  readonly primaryResourceClass: ResourceClass;
  readonly fallback?: Fallback<T>;
};

// Outcome reported via the observer hook. Stable string union — the
// Python sibling MUST use the same names for cross-impl golden vectors
// to compare.
export type AegisOutcome =
  | 'ok'
  | 'fallback'
  | 'budget_exceeded'
  | 'fallback_failed'
  | 'primary_error';

// Structured event a host observer receives. The host wires this to
// its preferred logging / tracing stack. aegis itself does not import
// any logging library; that is consumer choice.
export type AegisEvent = {
  readonly outcome: AegisOutcome;
  readonly tags: Tags;
  readonly elapsedMs: number;
  readonly primaryResourceClass: ResourceClass;
  readonly fallbackResourceClass?: ResourceClass;
  readonly errorMessage?: string;
  readonly fallbackErrorMessage?: string;
  readonly timeoutMs: number;
};

// Host-supplied observer. Called once per wrap completion (success,
// fallback, error). aegis swallows observer exceptions — the observer
// is best-effort instrumentation, not load-bearing.
export type AegisObserver = (event: AegisEvent) => void;
