import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts'],
    testTimeout: 10000,
    // Differential fuzzer can take longer.
    hookTimeout: 10000,
  },
});
