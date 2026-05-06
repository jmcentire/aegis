// Unit tests for aegis types. Pure type-shape and rank ordering — no I/O.

import { describe, expect, it } from 'vitest';
import { type ResourceClass, resourceClassRank } from '../../src/types.ts';

describe('resourceClassRank', () => {
  it('ranks mem-sample cheapest', () => {
    expect(resourceClassRank('mem-sample')).toBe(0);
  });

  it('ranks llm most expensive', () => {
    expect(resourceClassRank('llm')).toBe(4);
  });

  it('preserves cpu < io < pg < llm ordering', () => {
    const order: ResourceClass[] = ['mem-sample', 'cpu', 'io', 'pg', 'llm'];
    const ranks = order.map(resourceClassRank);
    for (let i = 1; i < ranks.length; i++) {
      const prev = ranks[i - 1];
      const cur = ranks[i];
      if (prev === undefined || cur === undefined) throw new Error('test bug');
      expect(cur).toBeGreaterThan(prev);
    }
  });
});
