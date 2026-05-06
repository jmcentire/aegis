"""ruff-aegis — custom ruff rule (V1 warn-mode placeholder).

PURPOSE
=======
Flag blocking call sites in consumer Python code that are NOT wrapped
by `with_resource_budget`. Patterns to flag (V1 list):

    - `pg.Connection.execute(...)` — Postgres queries
    - `requests.get(...)`, `requests.post(...)`, `requests.put(...)`, etc.
    - `httpx.get(...)`, `httpx.AsyncClient().send(...)`, etc.
    - `Anthropic().messages.create(...)` — LLM calls
    - `smtplib.SMTP.send_message(...)` — SMTP sends
    - `boto3.client('s3').get_object(...)` — AWS S3

A call is "wrapped" when textually nested inside the awaitable passed
as the second arg of `await with_resource_budget(args, fn)`. V1
checks the direct case; V2 will trace one level of indirection.

STATUS
======
V1: warn-mode only (does NOT fail consumer CI).
V2: hard-fails consumer CI. Migrate when 3+ components consume
aegis and the rule's false-positive rate is acceptable.

RUFF PLUGIN ARCHITECTURE
========================
ruff's plugin model is currently closed-set: rules are written in
Rust as part of ruff's source. There is an active proposal for
user-authored rules in Python (tracked at
https://github.com/astral-sh/ruff/issues/283) but it has not landed.

Paths forward:
    1. Wait for ruff plugins.
    2. Ship as a standalone Python AST-walker CLI under
       ``bin/aegis-lint-py`` that consumers run as a pre-commit hook.
       This is the most viable V1.
    3. Submit a PR to ruff upstream proposing the rule in their
       built-in set (high friction; not worth it for a stack-internal
       primitive).

The extraction agent (Wave 1) does NOT pick a path — that's Wave 3.

WHAT THIS FILE IS
=================
A specification placeholder. The shape below is the API the rule
will eventually expose; the function body is a no-op so consumers
can `import lint.ruff_aegis` without breaking, and the lint task is
a no-op until V2.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AegisLintRuleConfig:
    """Configuration for the aegis ruff rule.

    Patterns are matched against the call expression's dotted path
    (e.g., ``'requests.get'`` matches ``requests.get(...)`` and
    ``r.get(...)`` after import-alias resolution).

    Attributes:
        blocking_patterns: dotted-path patterns to flag.
        wrapper_name: the function name that signals "wrapped".
        severity: V1 = 'warn'. V2 = 'error'.
    """

    blocking_patterns: tuple[str, ...] = field(
        default_factory=lambda: (
            "requests.get",
            "requests.post",
            "requests.put",
            "requests.delete",
            "requests.patch",
            "httpx.get",
            "httpx.post",
            "httpx.put",
            "httpx.delete",
            "httpx.AsyncClient.send",
            "Anthropic.messages.create",
            "anthropic.messages.create",
            "smtplib.SMTP.send_message",
            "boto3.client.get_object",
            "psycopg.Connection.execute",
            "psycopg.AsyncConnection.execute",
            "asyncpg.Connection.execute",
            "asyncpg.Connection.fetch",
            "pg.Connection.execute",
        )
    )
    wrapper_name: str = "with_resource_budget"
    severity: str = "warn"  # V1


DEFAULT_AEGIS_LINT_CONFIG = AegisLintRuleConfig()


def aegis_lint_rule(config: AegisLintRuleConfig | None = None) -> dict[str, object]:
    """V1 entry point — no-op until ruff hosts user-authored rules.

    Shape is preserved so V2 can drop in a real visitor without
    consumers changing their config.
    """
    _ = config or DEFAULT_AEGIS_LINT_CONFIG
    return {
        "name": "aegis/no-unwrapped-blocking-call",
        "check": lambda *_args, **_kwargs: None,
    }
