// @stack/aegis — resource-budget primitive.
//
// Wrap every blocking call in your stack with a declared budget,
// optional fallback, and structured tags. See ../../SPEC.md and
// ../../pact.yaml for the full contract.
//
// Public surface only. Implementation details live in budget.ts; types
// in types.ts; errors in errors.ts.

export {
  clearAegisObserver,
  getAegisTags,
  setAegisObserver,
  withResourceBudget,
} from './budget.ts';
export { AegisConfigError, BudgetExceededError, FallbackFailedError } from './errors.ts';
export type {
  AegisEvent,
  AegisObserver,
  AegisOutcome,
  Budget,
  Fallback,
  ResourceClass,
  Tags,
  WrapArgs,
} from './types.ts';
export { resourceClassRank } from './types.ts';
