// Typed errors aegis throws. Callers `instanceof` against these to decide
// whether the failure was budget-related, fallback-related, or just the
// primary fn raising its own error. We keep the error tree shallow on
// purpose — three concrete shapes is enough for every call site.

import type { Budget, Tags } from './types.js';

export class BudgetExceededError extends Error {
  override readonly name = 'BudgetExceededError';
  constructor(
    readonly budget: Budget,
    readonly elapsedMs: number,
    readonly tags: Tags,
  ) {
    super(
      `aegis: budget exceeded after ${Math.round(elapsedMs)}ms ` +
        `(timeoutMs=${budget.timeoutMs}, op=${tags.component}.${tags.op})`,
    );
  }
}

export class FallbackFailedError extends Error {
  override readonly name = 'FallbackFailedError';
  constructor(
    readonly originalCause: Error,
    readonly fallbackCause: Error,
    readonly tags: Tags,
  ) {
    super(
      `aegis: fallback failed for ${tags.component}.${tags.op}: ` +
        `original=${originalCause.message}; fallback=${fallbackCause.message}`,
    );
  }
}

// Thrown at wrap-time, NOT call-time, when the configuration is illegal
// (e.g., fallback resourceClass exceeds primary's). Callers see this as
// a developer error, not an operational one — fix the registration.
export class AegisConfigError extends Error {
  override readonly name = 'AegisConfigError';
  constructor(message: string) {
    super(`aegis: ${message}`);
  }
}
