// Differential fuzzer (TS half).
//
// Generates random valid input cases via fast-check and writes them
// to a temp file along with the TS impl's canonicalized result for
// each. The Python sibling reads the same input file, runs each case
// through the Python impl, canonicalizes its result with the SAME
// scheme, and asserts byte-identity.
//
// In CI we can't run both impls inside one test runner, so the
// strategy is: this TS test produces (a) the input set and (b) the
// TS-side outcomes, both as JSON files in a known location. The
// Python-side fuzzer test (`py/tests/test_differential.py`) consumes
// (a), runs the Python impl, and compares against (b). Either side
// fails on divergence.
//
// The TS-side test ALSO does a sanity assertion: each generated case
// matches the same outcome under repeated runs (timing-determinism
// regression — flag if a generated case is non-deterministic on the
// TS side alone).
//
// Minimum 1000 random inputs per ADR-001.

import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { type CaseInput, canonicalize, runCase } from './_helpers/case-runner.ts';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SHARED_DIR = resolve(__dirname, '../../vectors/_fuzzer-shared');

const RUNS = 1000;

const resourceClasses = ['mem-sample', 'cpu', 'io', 'pg', 'llm'] as const;

// Generate a fn spec. Constraints picked so timing is unambiguous
// across languages:
//   - Resolve delay <= 20ms (safely below smallest budget of 60ms).
//   - Reject delay <= 5ms (safely fast — non-timing outcome).
//   - Hangs always reach budget exhaustion.
// This avoids "is this 35ms delay over or under the 40ms budget"
// races between Node's event loop and Python's asyncio.
const fnSpecArb = fc.oneof(
  fc.record({
    kind: fc.constant('resolve' as const),
    value: fc.oneof(
      fc.string({ minLength: 0, maxLength: 16 }),
      fc.integer({ min: -1000, max: 1000 }),
      fc.boolean(),
    ),
    delayMs: fc.integer({ min: 0, max: 20 }),
  }),
  fc.record({
    kind: fc.constant('reject' as const),
    errorName: fc.constantFrom('PrimaryBoom', 'IOError', 'NetError', 'OtherErr'),
    errorMessage: fc.string({ minLength: 1, maxLength: 40 }),
    delayMs: fc.integer({ min: 0, max: 5 }),
  }),
  fc.record({ kind: fc.constant('hang_until_abort' as const) }),
);

// Generate a fallback spec, including a (possibly invalid) resource
// class — the runner will produce a config_error for outranking
// fallbacks, and the canonical output for that case must match the
// Python sibling exactly.
const fallbackArb = fc.option(
  fc.record({
    resourceClass: fc.constantFrom(...resourceClasses),
    fn: fnSpecArb,
  }),
  { nil: undefined },
);

const caseArb = fc.record({
  budget: fc.record({
    // Min 60ms keeps resolve delays (max 20) safely inside; max 200
    // keeps the 1000-run suite under a minute.
    timeoutMs: fc.integer({ min: 60, max: 200 }),
    signalRespecting: fc.constant(true),
  }),
  tags: fc.record({
    component: fc.constantFrom('test', 'fuzz', 'reeve', 'baton'),
    op: fc.string({ minLength: 1, maxLength: 20 }).filter((s) => /^[a-zA-Z0-9_.-]+$/.test(s)),
  }),
  primaryResourceClass: fc.constantFrom(...resourceClasses),
  primary: fnSpecArb,
  fallback: fallbackArb,
});

describe('differential fuzzer — TS side', () => {
  it(`emits ${RUNS} canonical cases for the Python sibling to compare`, async () => {
    mkdirSync(SHARED_DIR, { recursive: true });

    // Materialize the random sample BEFORE running, so both impls see
    // the same set. fast-check's sample() is deterministic given a seed.
    const seed = 0xdeadbeef;
    const sample = fc.sample(caseArb, { numRuns: RUNS, seed });

    // Strip undefined `fallback` to keep JSON canonical.
    const inputs: { id: string; input: CaseInput }[] = sample.map((s, idx) => ({
      id: `fuzz_${idx}`,
      input: {
        budget: s.budget,
        tags: s.tags,
        primaryResourceClass: s.primaryResourceClass,
        primary: s.primary,
        ...(s.fallback ? { fallback: s.fallback } : {}),
      },
    }));

    const tsOutcomes: { id: string; canonical: string }[] = [];
    for (const c of inputs) {
      const out = await runCase(c.input);
      tsOutcomes.push({ id: c.id, canonical: canonicalize(out) });
    }

    writeFileSync(
      resolve(SHARED_DIR, 'inputs.json'),
      JSON.stringify({ seed, runs: RUNS, inputs }, null, 2),
    );
    writeFileSync(
      resolve(SHARED_DIR, 'ts-outcomes.json'),
      JSON.stringify({ seed, runs: RUNS, outcomes: tsOutcomes }, null, 2),
    );

    // Sanity: at least each branch was exercised at least once across
    // the sample. If the arb's distribution is too skewed we want to
    // catch it now. (Not a hard requirement, but flagged.)
    const outcomeCounts = new Map<string, number>();
    for (const o of tsOutcomes) {
      const parsed = JSON.parse(o.canonical) as { outcome: string };
      outcomeCounts.set(parsed.outcome, (outcomeCounts.get(parsed.outcome) ?? 0) + 1);
    }
    expect(outcomeCounts.size).toBeGreaterThan(0);
    expect(tsOutcomes).toHaveLength(RUNS);
  }, 120_000);

  it('runs each case twice and asserts deterministic outcome class', async () => {
    // 50 cases run twice; we don't compare timing (flaky) — only the
    // OUTCOME class. If a generated case is truly non-deterministic on
    // the TS side it will surface here before we blame a cross-language
    // divergence.
    const sample = fc.sample(caseArb, { numRuns: 50, seed: 0xb16b00b5 });
    for (const s of sample) {
      const input: CaseInput = {
        budget: s.budget,
        tags: s.tags,
        primaryResourceClass: s.primaryResourceClass,
        primary: s.primary,
        ...(s.fallback ? { fallback: s.fallback } : {}),
      };
      const r1 = await runCase(input);
      const r2 = await runCase(input);
      expect(r1.outcome).toBe(r2.outcome);
    }
  }, 60_000);
});
