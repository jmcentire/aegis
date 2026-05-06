// Shared case interpreter for golden vectors AND the differential
// fuzzer. Lives outside `tests/golden/` so Biome's
// `noExportsInTest` rule doesn't fire (test files cannot export).
//
// The shape mirrors py/tests/test_golden_vectors.py. Both implementations
// must produce byte-identical canonical JSON for any given input — the
// differential fuzzer enforces this on 1000+ random samples.

import {
  AegisConfigError,
  BudgetExceededError,
  FallbackFailedError,
  clearAegisObserver,
  setAegisObserver,
  withResourceBudget,
} from '../../src/index.ts';
import type { AegisOutcome, ResourceClass } from '../../src/index.ts';

const SLACK_MS = 200;

export type FnSpec =
  | { kind: 'resolve'; value: unknown; delayMs?: number }
  | { kind: 'reject'; errorName: string; errorMessage: string; delayMs?: number }
  | { kind: 'hang_until_abort' };

export type CaseInput = {
  budget: { timeoutMs: number; signalRespecting: boolean; pgStatementTimeoutMs?: number };
  tags: { component: string; op: string; tenantId?: string; correlationId?: string };
  primaryResourceClass: ResourceClass;
  primary: FnSpec;
  fallback?: { resourceClass: ResourceClass; fn: FnSpec };
};

export type CaseOutput = {
  outcome:
    | 'ok'
    | 'fallback'
    | 'budget_exceeded'
    | 'fallback_failed'
    | 'primary_error'
    | 'config_error';
  value?: unknown;
  errorName?: string;
  originalErrorName?: string;
  fallbackErrorName?: string;
  withinBudget: boolean;
  tags: { component: string; op: string; tenantId?: string; correlationId?: string };
};

export type GoldenCase = {
  id: string;
  description: string;
  input: CaseInput;
  expected: CaseOutput;
};

function buildFn(spec: FnSpec): (signal: AbortSignal) => Promise<unknown> {
  switch (spec.kind) {
    case 'resolve': {
      const delayMs = spec.delayMs ?? 0;
      return async (signal) => {
        if (delayMs === 0) return spec.value;
        return await new Promise((resolveDelay, rejectDelay) => {
          const t = setTimeout(() => resolveDelay(spec.value), delayMs);
          signal.addEventListener('abort', () => {
            clearTimeout(t);
            const err = new Error('aborted');
            err.name = 'AbortError';
            rejectDelay(err);
          });
        });
      };
    }
    case 'reject': {
      const delayMs = spec.delayMs ?? 0;
      return async (signal) => {
        if (delayMs === 0) {
          const err = new Error(spec.errorMessage);
          err.name = spec.errorName;
          throw err;
        }
        return await new Promise((_, rejectDelay) => {
          const t = setTimeout(() => {
            const err = new Error(spec.errorMessage);
            err.name = spec.errorName;
            rejectDelay(err);
          }, delayMs);
          signal.addEventListener('abort', () => {
            clearTimeout(t);
            const err = new Error('aborted');
            err.name = 'AbortError';
            rejectDelay(err);
          });
        });
      };
    }
    case 'hang_until_abort': {
      return (signal) =>
        new Promise((_, rejectHang) => {
          signal.addEventListener('abort', () => {
            const err = new Error('aborted');
            err.name = 'AbortError';
            rejectHang(err);
          });
        });
    }
  }
}

export async function runCase(input: CaseInput): Promise<CaseOutput> {
  const t0 = performance.now();
  const primaryFn = buildFn(input.primary);
  const fallbackFn = input.fallback ? buildFn(input.fallback.fn) : undefined;

  // Track the observed outcome so we can distinguish ok vs fallback at
  // the success branch (both return a value to the caller).
  let lastOutcome: AegisOutcome | undefined;
  setAegisObserver((evt) => {
    lastOutcome = evt.outcome;
  });

  try {
    const value = await withResourceBudget(
      {
        budget: input.budget,
        tags: input.tags,
        primaryResourceClass: input.primaryResourceClass,
        ...(input.fallback && fallbackFn
          ? {
              fallback: {
                fn: fallbackFn as (signal: AbortSignal) => Promise<unknown>,
                resourceClass: input.fallback.resourceClass,
              },
            }
          : {}),
      },
      primaryFn as (signal: AbortSignal) => Promise<unknown>,
    );
    const elapsed = performance.now() - t0;
    return {
      outcome: lastOutcome === 'fallback' ? 'fallback' : 'ok',
      value,
      withinBudget: elapsed < input.budget.timeoutMs + SLACK_MS,
      tags: input.tags,
    };
  } catch (err) {
    const elapsed = performance.now() - t0;
    if (err instanceof AegisConfigError) {
      return {
        outcome: 'config_error',
        errorName: 'AegisConfigError',
        withinBudget: true,
        tags: input.tags,
      };
    }
    if (err instanceof BudgetExceededError) {
      return {
        outcome: 'budget_exceeded',
        errorName: 'BudgetExceededError',
        withinBudget: elapsed < input.budget.timeoutMs + SLACK_MS,
        tags: input.tags,
      };
    }
    if (err instanceof FallbackFailedError) {
      return {
        outcome: 'fallback_failed',
        errorName: 'FallbackFailedError',
        originalErrorName: err.originalCause.name,
        fallbackErrorName: err.fallbackCause.name,
        withinBudget: elapsed < input.budget.timeoutMs + SLACK_MS,
        tags: input.tags,
      };
    }
    // primary error path
    const e = err as Error;
    return {
      outcome: 'primary_error',
      errorName: e.name,
      withinBudget: elapsed < input.budget.timeoutMs + SLACK_MS,
      tags: input.tags,
    };
  } finally {
    clearAegisObserver();
  }
}

// Canonicalize a result for byte-identical comparison across languages.
//
// `withinBudget` is INTENTIONALLY OMITTED — it's a per-case timing
// sanity assertion that can race between Node and Python on a loaded
// box (e.g., fallback_failed paths run two budgets sequentially, so a
// 200ms slack can flip true/false on the edge). The cross-lang
// contract is the OUTCOME shape; per-case timing is checked
// separately by each impl.
export function canonicalize(out: CaseOutput): string {
  const sortedTags: Record<string, string> = {};
  for (const k of Object.keys(out.tags).sort()) {
    const v = (out.tags as Record<string, string | undefined>)[k];
    if (v !== undefined) sortedTags[k] = v;
  }
  const obj: Record<string, unknown> = {
    outcome: out.outcome,
    tags: sortedTags,
  };
  if (out.value !== undefined) obj.value = out.value;
  if (out.errorName !== undefined) obj.errorName = out.errorName;
  if (out.originalErrorName !== undefined) obj.originalErrorName = out.originalErrorName;
  if (out.fallbackErrorName !== undefined) obj.fallbackErrorName = out.fallbackErrorName;
  const orderedKeys = [
    'outcome',
    'value',
    'errorName',
    'originalErrorName',
    'fallbackErrorName',
    'tags',
  ];
  const ordered: Record<string, unknown> = {};
  for (const k of orderedKeys) {
    if (k in obj) ordered[k] = obj[k];
  }
  return JSON.stringify(ordered);
}
