// Golden vector runner. Reads the cross-language test cases from
// `vectors/budget-cases.json` and asserts the TS impl produces the
// expected outcome shape for each. The Python sibling
// (`py/tests/test_golden_vectors.py`) consumes the SAME file and runs
// the SAME assertions — divergence is a contract bug.
//
// The case interpreter (`runCase`) and canonicalizer live in
// `tests/_helpers/case-runner.ts` because Biome's `noExportsInTest`
// rule rejects exports from `*.test.ts` files; both this file and
// the differential fuzzer import from there.

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { type GoldenCase, runCase } from '../_helpers/case-runner.ts';

const __dirname = dirname(fileURLToPath(import.meta.url));
const VECTORS_PATH = resolve(__dirname, '../../../vectors/budget-cases.json');

const vectors = JSON.parse(readFileSync(VECTORS_PATH, 'utf8')) as { cases: GoldenCase[] };

describe('golden vectors — TS impl', () => {
  for (const c of vectors.cases) {
    it(`case=${c.id} (${c.description.slice(0, 60)})`, async () => {
      const actual = await runCase(c.input);
      // Outcome must match.
      expect(actual.outcome).toBe(c.expected.outcome);
      // Error class match where applicable.
      if (c.expected.errorName !== undefined) {
        expect(actual.errorName).toBe(c.expected.errorName);
      }
      if (c.expected.originalErrorName !== undefined) {
        expect(actual.originalErrorName).toBe(c.expected.originalErrorName);
      }
      if (c.expected.fallbackErrorName !== undefined) {
        expect(actual.fallbackErrorName).toBe(c.expected.fallbackErrorName);
      }
      // Value match for happy/fallback paths.
      if (c.expected.value !== undefined) {
        expect(actual.value).toEqual(c.expected.value);
      }
      // Timing bound holds on this box.
      expect(actual.withinBudget).toBe(true);
      // Tags echo.
      expect(actual.tags).toEqual(c.expected.tags);
    });
  }
});
