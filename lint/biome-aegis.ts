// biome-aegis — custom Biome lint rule (V1 warn-mode placeholder).
//
// PURPOSE
// =======
// Flag blocking call sites in consumer code that are NOT wrapped by
// `withResourceBudget`. The list of patterns to flag (as of V1):
//
//   - `<pgClient>.query(...)` — Postgres queries
//   - `fetch(...)` — HTTP fetch
//   - `Anthropic.messages.create(...)` — LLM calls
//   - `<smtp>.send(...)` — SMTP / email send
//   - `axios.<verb>(...)` — HTTP via axios
//   - `<resend>.emails.send(...)` — Resend client
//
// A call site is "wrapped" when it appears textually inside the second
// argument (the fn callback) of `withResourceBudget(args, fn)` — either
// directly, or transitively through helper fns whose callers do wrap.
// V1 only checks the direct case; V2 will trace through one level of
// indirection (with a deny-list for known wrapper helpers).
//
// STATUS
// ======
// V1: warn-mode only (V1 does NOT fail consumer CI).
// V2: hard-fails consumer CI. Migrate when 3+ components consume aegis
// and the rule has been tuned for false-positive rate.
//
// BIOME PLUGIN ARCHITECTURE
// =========================
// Biome 1.9 does not yet support arbitrary user-authored lint rules in
// TypeScript at this writing — Biome's built-in rule set is closed.
// The path forward is one of:
//
//   1. Wait for Biome's plugin system (in development; tracked at
//      https://github.com/biomejs/biome/discussions/2463).
//   2. In the meantime, ship this as an ESLint plugin under
//      `lint/eslint-plugin-aegis/` (each consumer would need ESLint
//      installed alongside Biome).
//   3. Or: write a simple AST-walker as a standalone CLI
//      (`bin/aegis-lint`) that consumers run as a pre-commit hook.
//
// The extraction agent (Wave 1) does NOT pick a path — that decision
// is part of Wave 3 (Reeve migration), where we know what consumer
// constraints look like in practice.
//
// WHAT THIS FILE IS
// =================
// A specification placeholder. The shape below is the API the rule
// will eventually expose. The body is a no-op so consumers can import
// it without breaking and the lint task is a no-op until V2.

export type AegisLintRuleConfig = {
  // Patterns to flag. Each is matched against the call expression's
  // dotted path (e.g., 'Anthropic.messages.create' matches
  // `anthropic.messages.create(...)`).
  readonly blockingPatterns: readonly string[];
  // Wrap detection: an unbroken chain of enclosing nodes from the
  // call site up to the function literal passed as the second arg of
  // `withResourceBudget(args, fn)`.
  readonly wrapperName: string; // 'withResourceBudget' by default
  // V1: 'warn'. V2: 'error'.
  readonly severity: 'warn' | 'error';
};

export const DEFAULT_AEGIS_LINT_CONFIG: AegisLintRuleConfig = {
  blockingPatterns: [
    'fetch',
    'pgClient.query',
    'pgPool.query',
    'client.query',
    'pool.query',
    'Anthropic.messages.create',
    'anthropic.messages.create',
    'axios.get',
    'axios.post',
    'axios.put',
    'axios.delete',
    'resend.emails.send',
    'smtp.send',
  ],
  wrapperName: 'withResourceBudget',
  severity: 'warn',
};

// V1 rule entry: no-op until plugin host is wired. The shape is
// preserved so the V2 build can drop in a real implementation
// without consumers changing their config.
export function aegisLintRule(_config: AegisLintRuleConfig = DEFAULT_AEGIS_LINT_CONFIG): {
  readonly name: 'aegis/no-unwrapped-blocking-call';
  readonly check: () => void;
} {
  return {
    name: 'aegis/no-unwrapped-blocking-call',
    check: () => {
      // V1: no-op. V2 will register an AST visitor against the host.
    },
  };
}
