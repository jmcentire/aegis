"""aegis types (Python sibling).

Mirrors ts/src/types.ts exactly. ResourceClass values, ranks, Budget /
Tags / Fallback / WrapArgs / AegisEvent / AegisObserver names — all
identical to the TS surface. Cross-language drift here is a contract
bug.

Implementation note on optional fields: TS uses `T | undefined` with
`exactOptionalPropertyTypes`. Python uses `T | None` (None is the
sentinel for "absent"). Round-tripping JSON to/from canonical form must
strip None / undefined symmetrically — see budget.py canonicalization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, TypeAlias

ResourceClass: TypeAlias = Literal["mem-sample", "cpu", "io", "pg", "llm"]

# Ordered cheapest -> most expensive. A fallback whose class outranks
# the primary's is rejected at wrap-time.
_RESOURCE_RANK: dict[str, int] = {
    "mem-sample": 0,
    "cpu": 1,
    "io": 2,
    "pg": 3,
    "llm": 4,
}


def resource_class_rank(c: ResourceClass) -> int:
    """Return integer rank of a ResourceClass.

    Lower = cheaper. Used to enforce the rule that a fallback's resource
    class must not strictly exceed the primary's.
    """
    return _RESOURCE_RANK[c]


@dataclass(frozen=True, slots=True)
class Budget:
    # Wall-clock deadline (milliseconds). The only enforceable budget
    # across all classes (per the slice-1 capability matrix; sync CPU
    # burn is uncontainable cooperatively).
    timeout_ms: int
    # True when the wrapped fn correctly responds to cancellation
    # (fetch, async pg query with statement_timeout, an awaitable that
    # checks the cancellation event). False for compute-bound code that
    # cannot honor cancellation — the primitive will set the timer but
    # cannot kill the call cooperatively. Use multiprocessing for that
    # path.
    signal_respecting: bool
    # For pg-bound work. The primitive sets `SET statement_timeout = N`
    # on the connection BEFORE running fn, so a hanging query is
    # server-side-cancelled within budget. fn is responsible for using
    # the same connection.
    pg_statement_timeout_ms: int | None = None


@dataclass(frozen=True, slots=True)
class Tags:
    # e.g. 'reeve.adapters.llm', 'apprentice.skills', 'baton.health'
    component: str
    # e.g. 'anthropic.messages.create', 'pg.event_log.append'
    op: str
    tenant_id: str | None = None
    correlation_id: str | None = None


# Fallback fn receives a cancellation event (asyncio.Event) that the
# fn should check / respond to. Mirrors AbortSignal in TS.
FallbackFn: TypeAlias = Callable[["Any"], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class Fallback:
    fn: FallbackFn
    resource_class: ResourceClass


@dataclass(frozen=True, slots=True)
class WrapArgs:
    budget: Budget
    tags: Tags
    primary_resource_class: ResourceClass
    fallback: Fallback | None = None


# Outcome strings — MUST match the TS sibling's AegisOutcome union.
AegisOutcome: TypeAlias = Literal[
    "ok",
    "fallback",
    "budget_exceeded",
    "fallback_failed",
    "primary_error",
]


@dataclass(frozen=True, slots=True)
class AegisEvent:
    outcome: AegisOutcome
    tags: Tags
    elapsed_ms: float
    primary_resource_class: ResourceClass
    timeout_ms: int
    fallback_resource_class: ResourceClass | None = None
    error_message: str | None = None
    fallback_error_message: str | None = None


# Observer callable — host wires its preferred logging / tracing here.
# aegis itself imports no logging library.
AegisObserver: TypeAlias = Callable[[AegisEvent], None]


# Sentinel: the dataclass system needs `field` even though we don't use
# it directly here — keeps Python's `from __future__ import` happy with
# slot inheritance in some interpreters.
_ = field
