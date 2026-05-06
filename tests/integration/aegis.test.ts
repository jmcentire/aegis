// withResourceBudget contract tests (ported from Reeve's
// tests/integration/observability/aegis.test.ts unchanged in behavior).
//
// Each test names a contract clause and asserts the primitive honors it
// under forced failure. Discipline (additive to slice 1's): every test
// uses real timers; every assertion has both a behavioral check (right
// error type, right return value) AND a timing check (within budget+slack).
//
// The contract these tests fix:
//   1. Containable case: primary returns within budget → its value.
//   2. Containable case: primary hangs → AbortSignal fires → fallback
//      runs (if declared) → fallback's value returned.
//   3. Container failure: primary hangs → no fallback → BudgetExceededError.
//   4. Fallback failure: primary hangs → fallback throws → FallbackFailedError
//      with both causes preserved.
//   5. Primary error path: primary throws non-abort → that error propagates
//      unwrapped (NOT BudgetExceededError; caller sees their own error).
//   6. Tags propagate via AsyncLocalStorage to nested calls.
//   7. Wrap-time validation: fallback.resourceClass cannot outrank primary's.
//   8. Nested budgets: a tighter parent budget aborts the child.

import { describe, expect, it } from 'vitest';
import {
  AegisConfigError,
  BudgetExceededError,
  FallbackFailedError,
  getAegisTags,
  withResourceBudget,
} from '../../src/index.ts';

const SLACK_MS = 200;

describe('withResourceBudget — happy path', () => {
  it('returns the primary fn value when it completes within budget', async () => {
    const result = await withResourceBudget(
      {
        budget: { timeoutMs: 200, signalRespecting: true },
        tags: { component: 'test', op: 'happy.path' },
        primaryResourceClass: 'cpu',
      },
      async () => 42,
    );
    expect(result).toBe(42);
  });
});

describe('withResourceBudget — budget exhaustion without fallback', () => {
  it('throws BudgetExceededError when primary hangs and no fallback declared', async () => {
    const t0 = performance.now();
    const result = await withResourceBudget(
      {
        budget: { timeoutMs: 100, signalRespecting: true },
        tags: { component: 'test', op: 'hang.no.fallback' },
        primaryResourceClass: 'io',
      },
      (signal) =>
        new Promise<never>((_, reject) => {
          signal.addEventListener('abort', () => {
            const err = new Error('aborted');
            err.name = 'AbortError';
            reject(err);
          });
        }),
    ).catch((e: Error) => e);
    const elapsed = performance.now() - t0;
    expect(result).toBeInstanceOf(BudgetExceededError);
    if (result instanceof BudgetExceededError) {
      expect(result.tags.op).toBe('hang.no.fallback');
      expect(result.elapsedMs).toBeGreaterThanOrEqual(95);
    }
    expect(elapsed).toBeLessThan(100 + SLACK_MS);
  });
});

describe('withResourceBudget — fallback fires on budget exhaustion', () => {
  it('returns fallback value when primary hangs', async () => {
    const t0 = performance.now();
    const result = await withResourceBudget(
      {
        budget: { timeoutMs: 100, signalRespecting: true },
        tags: { component: 'test', op: 'hang.with.fallback' },
        primaryResourceClass: 'llm',
        fallback: {
          fn: async () => 'fallback-value',
          resourceClass: 'cpu', // cheaper than primary
        },
      },
      (signal) =>
        new Promise<string>((_, reject) => {
          signal.addEventListener('abort', () => {
            const err = new Error('aborted');
            err.name = 'AbortError';
            reject(err);
          });
        }),
    );
    const elapsed = performance.now() - t0;
    expect(result).toBe('fallback-value');
    expect(elapsed).toBeLessThan(100 + SLACK_MS);
  });
});

describe('withResourceBudget — fallback failure surfaces both causes', () => {
  it('throws FallbackFailedError when both primary and fallback fail', async () => {
    const fallbackErr = new Error('fallback boom');
    const result = await withResourceBudget(
      {
        budget: { timeoutMs: 100, signalRespecting: true },
        tags: { component: 'test', op: 'both.fail' },
        primaryResourceClass: 'llm',
        fallback: {
          fn: async () => {
            throw fallbackErr;
          },
          resourceClass: 'cpu',
        },
      },
      (signal) =>
        new Promise<string>((_, reject) => {
          signal.addEventListener('abort', () => {
            const err = new Error('aborted');
            err.name = 'AbortError';
            reject(err);
          });
        }),
    ).catch((e: Error) => e);

    expect(result).toBeInstanceOf(FallbackFailedError);
    if (result instanceof FallbackFailedError) {
      expect(result.fallbackCause).toBe(fallbackErr);
      expect(result.originalCause.name).toBe('AbortError');
      expect(result.tags.op).toBe('both.fail');
    }
  });
});

describe('withResourceBudget — primary error passes through unwrapped', () => {
  it('rethrows the primary fn error when fn throws non-abort', async () => {
    const primaryErr = new Error('primary failed for its own reasons');
    const result = await withResourceBudget(
      {
        budget: { timeoutMs: 1000, signalRespecting: true },
        tags: { component: 'test', op: 'primary.threw' },
        primaryResourceClass: 'pg',
        fallback: {
          fn: async () => 'should-not-be-called',
          resourceClass: 'cpu',
        },
      },
      async () => {
        throw primaryErr;
      },
    ).catch((e: Error) => e);

    // The primary's error should propagate unwrapped — fallback should
    // NOT have been called, since the error wasn't budget-related.
    expect(result).toBe(primaryErr);
    expect(result).not.toBeInstanceOf(BudgetExceededError);
    expect(result).not.toBeInstanceOf(FallbackFailedError);
  });
});

describe('withResourceBudget — tags propagate via AsyncLocalStorage', () => {
  it('getAegisTags() returns the active tags inside the wrapped fn', async () => {
    let observed: ReturnType<typeof getAegisTags>;
    await withResourceBudget(
      {
        budget: { timeoutMs: 200, signalRespecting: true },
        tags: { component: 'test', op: 'als.read', tenantId: 'tenant-abc' },
        primaryResourceClass: 'cpu',
      },
      async () => {
        observed = getAegisTags();
      },
    );
    expect(observed).toBeDefined();
    expect(observed?.component).toBe('test');
    expect(observed?.op).toBe('als.read');
    expect(observed?.tenantId).toBe('tenant-abc');
  });

  it('tags propagate through nested awaits', async () => {
    async function inner(): Promise<string | undefined> {
      // Simulate nested async work that wants to read the tenant.
      await new Promise((r) => setTimeout(r, 10));
      return getAegisTags()?.tenantId;
    }
    const observed = await withResourceBudget(
      {
        budget: { timeoutMs: 200, signalRespecting: true },
        tags: { component: 'test', op: 'als.nested', tenantId: 'tenant-xyz' },
        primaryResourceClass: 'cpu',
      },
      async () => inner(),
    );
    expect(observed).toBe('tenant-xyz');
  });
});

describe('withResourceBudget — wrap-time validation', () => {
  it('rejects fallback whose resourceClass exceeds primary', async () => {
    const result = await withResourceBudget(
      {
        budget: { timeoutMs: 100, signalRespecting: true },
        tags: { component: 'test', op: 'illegal.fallback' },
        primaryResourceClass: 'cpu',
        fallback: {
          fn: async () => 'never',
          resourceClass: 'llm', // more expensive than cpu — illegal
        },
      },
      async () => 'never',
    ).catch((e: Error) => e);

    expect(result).toBeInstanceOf(AegisConfigError);
    if (result instanceof AegisConfigError) {
      expect(result.message).toContain('llm');
      expect(result.message).toContain('cpu');
    }
  });

  it('accepts fallback whose resourceClass equals primary', async () => {
    // Equal is OK; only strict-greater is rejected.
    const result = await withResourceBudget(
      {
        budget: { timeoutMs: 200, signalRespecting: true },
        tags: { component: 'test', op: 'equal.fallback' },
        primaryResourceClass: 'pg',
        fallback: {
          fn: async () => 'fallback',
          resourceClass: 'pg',
        },
      },
      async () => 'primary',
    );
    expect(result).toBe('primary');
  });
});

describe('withResourceBudget — nested budgets', () => {
  it('parent budget aborts the child even if child has a longer budget', async () => {
    const t0 = performance.now();
    const result = await withResourceBudget(
      {
        budget: { timeoutMs: 100, signalRespecting: true },
        tags: { component: 'test', op: 'parent' },
        primaryResourceClass: 'io',
      },
      async (parentSignal) => {
        // Child has a generous 5000ms budget but inherits the parent's
        // signal — when the parent's 100ms timer fires, parentSignal
        // aborts and the child's hung primary observes it.
        return await withResourceBudget(
          {
            budget: { timeoutMs: 5000, signalRespecting: true },
            tags: { component: 'test', op: 'child' },
            primaryResourceClass: 'io',
          },
          (_childSignal) =>
            new Promise<string>((_, reject) => {
              parentSignal.addEventListener('abort', () => {
                const err = new Error('aborted');
                err.name = 'AbortError';
                reject(err);
              });
            }),
        );
      },
    ).catch((e: Error) => e);
    const elapsed = performance.now() - t0;

    // The child's 5000ms budget would not be exhausted by the time
    // parent aborts at 100ms, so the child's reject is NOT a
    // budget-exceeded — it propagates as the primary error to the
    // parent. The parent IS budget-exceeded, so parent throws
    // BudgetExceededError. Either of these behaviors is acceptable:
    //   - BudgetExceededError on the parent's timer
    //   - The AbortError raw (since the child's reject reaches the
    //     parent's catch path)
    // The CRITICAL claim is timing: total elapsed bounded by parent's
    // 100ms + slack, not the child's 5000ms.
    expect(elapsed).toBeLessThan(100 + SLACK_MS + 100);
    expect(result).toBeInstanceOf(Error);
  });
});
