// withResourceBudget: the resource-budget primitive. Wraps a blocking
// call with a declared budget, optionally routes to a fallback when the
// budget is exhausted, and emits a structured event to a host-supplied
// observer.
//
// Contract (see ../../pact.yaml for full text):
//   - Containable: cooperative async, fetch with AbortSignal, pg query
//     with statement_timeout. The primitive's timer fires, the wrapped
//     fn observes the signal, returns or rejects, the budget is honored.
//   - Uncontainable cooperatively: synchronous CPU burn. The primitive
//     sets the timer but the burn ignores it. Caller MUST set
//     `signalRespecting: false` when wrapping such code AND consider
//     worker_threads for actual containment.
//
// Multi-tenant addition: tags propagate via AsyncLocalStorage so nested
// calls inherit the tenant context.
//
// Observability: the host wires an AegisObserver via setAegisObserver().
// aegis does not import any logging library — that is consumer choice.

import { AsyncLocalStorage } from 'node:async_hooks';
import { AegisConfigError, BudgetExceededError, FallbackFailedError } from './errors.js';
import type { AegisEvent, AegisObserver, Tags, WrapArgs } from './types.js';
import { resourceClassRank } from './types.js';

const tagsContext = new AsyncLocalStorage<Tags>();

// Read the active aegis tags from the current async scope. Returns
// undefined if no aegis-wrapped fn is on the stack.
export function getAegisTags(): Tags | undefined {
  return tagsContext.getStore();
}

// Host-installed observer. Default is a no-op so the primitive remains
// usable in tests / sandboxes that don't wire one. Hosts (Reeve, Baton,
// etc.) call setAegisObserver() at boot to plug in their structured
// logger / tracer.
let observer: AegisObserver = () => {};

export function setAegisObserver(o: AegisObserver): void {
  observer = o;
}

// Reset to the no-op observer. Useful for tests.
export function clearAegisObserver(): void {
  observer = () => {};
}

function emit(event: AegisEvent): void {
  // Observer is best-effort. Swallow its exceptions — the wrapped fn's
  // result must not depend on whether instrumentation succeeded.
  try {
    observer(event);
  } catch {
    // intentional: observer failures are not load-bearing
  }
}

export async function withResourceBudget<T>(
  args: WrapArgs<T>,
  fn: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  // Wrap-time validation: fallback resourceClass must not outrank primary's.
  if (args.fallback) {
    const primaryRank = resourceClassRank(args.primaryResourceClass);
    const fallbackRank = resourceClassRank(args.fallback.resourceClass);
    if (fallbackRank > primaryRank) {
      throw new AegisConfigError(
        `fallback resourceClass=${args.fallback.resourceClass} (rank ${fallbackRank}) ` +
          `is more expensive than primary=${args.primaryResourceClass} (rank ${primaryRank}) ` +
          `for ${args.tags.component}.${args.tags.op}. Fallback paths cannot escalate resource use.`,
      );
    }
  }

  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), args.budget.timeoutMs);
  const t0 = performance.now();

  try {
    return await tagsContext.run(args.tags, async () => {
      try {
        const result = await fn(ac.signal);
        const elapsedOk = performance.now() - t0;
        emit({
          outcome: 'ok',
          tags: args.tags,
          elapsedMs: elapsedOk,
          primaryResourceClass: args.primaryResourceClass,
          timeoutMs: args.budget.timeoutMs,
        });
        return result;
      } catch (err) {
        const elapsed = performance.now() - t0;
        const isAbort =
          err instanceof Error && (err.name === 'AbortError' || err.name === 'TimeoutError');
        const exhausted = ac.signal.aborted || elapsed >= args.budget.timeoutMs;

        if (isAbort && exhausted) {
          // Budget exhausted. If fallback is registered, invoke it with a
          // FRESH signal (so it gets its own budget rather than inheriting
          // the exhausted one).
          if (args.fallback) {
            const fbAc = new AbortController();
            const fbTimer = setTimeout(() => fbAc.abort(), args.budget.timeoutMs);
            try {
              const fbResult = await args.fallback.fn(fbAc.signal);
              emit({
                outcome: 'fallback',
                tags: args.tags,
                elapsedMs: elapsed,
                primaryResourceClass: args.primaryResourceClass,
                fallbackResourceClass: args.fallback.resourceClass,
                timeoutMs: args.budget.timeoutMs,
              });
              return fbResult;
            } catch (fbErr) {
              const fbErrMsg = fbErr instanceof Error ? fbErr.message : String(fbErr);
              emit({
                outcome: 'fallback_failed',
                tags: args.tags,
                elapsedMs: elapsed,
                primaryResourceClass: args.primaryResourceClass,
                fallbackResourceClass: args.fallback.resourceClass,
                errorMessage: (err as Error).message,
                fallbackErrorMessage: fbErrMsg,
                timeoutMs: args.budget.timeoutMs,
              });
              throw new FallbackFailedError(err as Error, fbErr as Error, args.tags);
            } finally {
              clearTimeout(fbTimer);
            }
          }
          // No fallback. Surface the budget-exceeded error.
          emit({
            outcome: 'budget_exceeded',
            tags: args.tags,
            elapsedMs: elapsed,
            primaryResourceClass: args.primaryResourceClass,
            timeoutMs: args.budget.timeoutMs,
          });
          throw new BudgetExceededError(args.budget, elapsed, args.tags);
        }

        // The primary fn threw something OTHER than abort, OR aborted
        // before the budget was up (fn cancelled itself for its own
        // reasons). Don't swallow — it's the caller's error to handle.
        emit({
          outcome: 'primary_error',
          tags: args.tags,
          elapsedMs: elapsed,
          primaryResourceClass: args.primaryResourceClass,
          errorMessage: err instanceof Error ? err.message : String(err),
          timeoutMs: args.budget.timeoutMs,
        });
        throw err;
      }
    });
  } finally {
    clearTimeout(timer);
  }
}
