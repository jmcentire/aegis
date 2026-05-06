"""Contract tests for with_resource_budget (Python sibling).

Mirrors ts/tests/integration/aegis.test.ts. Each test names a contract
clause and asserts the primitive honors it. Real timers; behavioral +
timing assertions per case.

Contract clauses (same numbering as TS):
    1. Primary returns within budget -> its value.
    2. Primary hangs -> abort -> fallback runs (if declared) -> its value.
    3. Primary hangs -> no fallback -> BudgetExceededError.
    4. Primary hangs -> fallback raises -> FallbackFailedError preserves
       both causes.
    5. Primary throws non-abort -> propagates unwrapped.
    6. Tags propagate via contextvars to nested awaits.
    7. Wrap-time validation: fallback resource class cannot outrank.
    8. Nested budgets: parent timer aborts child even with longer child budget.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from aegis import (
    AegisConfigError,
    Budget,
    BudgetExceededError,
    Fallback,
    FallbackFailedError,
    Tags,
    WrapArgs,
    get_aegis_tags,
    with_resource_budget,
)

SLACK_MS = 200


async def _hang_until_abort(event: asyncio.Event) -> None:
    # Wait for the budget event; raise an AbortError-like exception so
    # the wrapper categorizes us as an abort.
    await event.wait()
    from aegis.budget import AbortError

    raise AbortError()


async def test_happy_path_returns_primary_value() -> None:
    async def primary(_event: asyncio.Event) -> int:
        return 42

    result = await with_resource_budget(
        WrapArgs(
            budget=Budget(timeout_ms=200, signal_respecting=True),
            tags=Tags(component="test", op="happy.path"),
            primary_resource_class="cpu",
        ),
        primary,
    )
    assert result == 42


async def test_budget_exceeded_no_fallback() -> None:
    t0 = time.monotonic()
    with pytest.raises(BudgetExceededError) as excinfo:
        await with_resource_budget(
            WrapArgs(
                budget=Budget(timeout_ms=100, signal_respecting=True),
                tags=Tags(component="test", op="hang.no.fallback"),
                primary_resource_class="io",
            ),
            _hang_until_abort,
        )
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert excinfo.value.tags.op == "hang.no.fallback"
    assert excinfo.value.elapsed_ms >= 95
    assert elapsed_ms < 100 + SLACK_MS


async def test_fallback_fires_on_budget_exhaustion() -> None:
    async def fallback(_event: asyncio.Event) -> str:
        return "fallback-value"

    t0 = time.monotonic()
    result = await with_resource_budget(
        WrapArgs(
            budget=Budget(timeout_ms=100, signal_respecting=True),
            tags=Tags(component="test", op="hang.with.fallback"),
            primary_resource_class="llm",
            fallback=Fallback(fn=fallback, resource_class="cpu"),
        ),
        _hang_until_abort,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert result == "fallback-value"
    assert elapsed_ms < 100 + SLACK_MS


async def test_fallback_failure_surfaces_both_causes() -> None:
    fallback_err = RuntimeError("fallback boom")

    async def fallback(_event: asyncio.Event) -> str:
        raise fallback_err

    with pytest.raises(FallbackFailedError) as excinfo:
        await with_resource_budget(
            WrapArgs(
                budget=Budget(timeout_ms=100, signal_respecting=True),
                tags=Tags(component="test", op="both.fail"),
                primary_resource_class="llm",
                fallback=Fallback(fn=fallback, resource_class="cpu"),
            ),
            _hang_until_abort,
        )
    assert excinfo.value.fallback_cause is fallback_err
    # Original cause is the primary's abort.
    from aegis.budget import AbortError

    assert isinstance(excinfo.value.original_cause, AbortError)
    assert excinfo.value.tags.op == "both.fail"


async def test_primary_error_passes_through_unwrapped() -> None:
    primary_err = RuntimeError("primary failed for its own reasons")

    async def primary(_event: asyncio.Event) -> str:
        raise primary_err

    async def fallback(_event: asyncio.Event) -> str:
        return "should-not-be-called"

    with pytest.raises(RuntimeError) as excinfo:
        await with_resource_budget(
            WrapArgs(
                budget=Budget(timeout_ms=1000, signal_respecting=True),
                tags=Tags(component="test", op="primary.threw"),
                primary_resource_class="pg",
                fallback=Fallback(fn=fallback, resource_class="cpu"),
            ),
            primary,
        )
    assert excinfo.value is primary_err
    assert not isinstance(excinfo.value, BudgetExceededError)
    assert not isinstance(excinfo.value, FallbackFailedError)


async def test_tags_propagate_via_contextvars() -> None:
    observed: dict[str, str | None] = {}

    async def primary(_event: asyncio.Event) -> None:
        t = get_aegis_tags()
        if t is not None:
            observed["component"] = t.component
            observed["op"] = t.op
            observed["tenant_id"] = t.tenant_id

    await with_resource_budget(
        WrapArgs(
            budget=Budget(timeout_ms=200, signal_respecting=True),
            tags=Tags(component="test", op="als.read", tenant_id="tenant-abc"),
            primary_resource_class="cpu",
        ),
        primary,
    )
    assert observed["component"] == "test"
    assert observed["op"] == "als.read"
    assert observed["tenant_id"] == "tenant-abc"


async def test_tags_propagate_through_nested_awaits() -> None:
    async def inner() -> str | None:
        await asyncio.sleep(0.01)
        t = get_aegis_tags()
        return t.tenant_id if t is not None else None

    async def primary(_event: asyncio.Event) -> str | None:
        return await inner()

    observed = await with_resource_budget(
        WrapArgs(
            budget=Budget(timeout_ms=200, signal_respecting=True),
            tags=Tags(component="test", op="als.nested", tenant_id="tenant-xyz"),
            primary_resource_class="cpu",
        ),
        primary,
    )
    assert observed == "tenant-xyz"


async def test_rejects_fallback_outranking_primary() -> None:
    async def primary(_event: asyncio.Event) -> str:
        return "never"

    async def fallback(_event: asyncio.Event) -> str:
        return "never"

    with pytest.raises(AegisConfigError) as excinfo:
        await with_resource_budget(
            WrapArgs(
                budget=Budget(timeout_ms=100, signal_respecting=True),
                tags=Tags(component="test", op="illegal.fallback"),
                primary_resource_class="cpu",
                fallback=Fallback(fn=fallback, resource_class="llm"),
            ),
            primary,
        )
    assert "llm" in str(excinfo.value)
    assert "cpu" in str(excinfo.value)


async def test_accepts_equal_resource_class_fallback() -> None:
    async def primary(_event: asyncio.Event) -> str:
        return "primary"

    async def fallback(_event: asyncio.Event) -> str:
        return "fallback"

    result = await with_resource_budget(
        WrapArgs(
            budget=Budget(timeout_ms=200, signal_respecting=True),
            tags=Tags(component="test", op="equal.fallback"),
            primary_resource_class="pg",
            fallback=Fallback(fn=fallback, resource_class="pg"),
        ),
        primary,
    )
    assert result == "primary"


async def test_nested_budgets_parent_aborts_child() -> None:
    """Parent's 100ms budget bounds the child's 5000ms budget."""

    t0 = time.monotonic()

    async def child_primary(parent_event: asyncio.Event) -> str:
        async def inner_primary(_child_event: asyncio.Event) -> str:
            # Wait for the parent's abort event to fire; mirror the TS
            # test's signal-attaching pattern.
            await parent_event.wait()
            from aegis.budget import AbortError

            raise AbortError()

        return await with_resource_budget(
            WrapArgs(
                budget=Budget(timeout_ms=5000, signal_respecting=True),
                tags=Tags(component="test", op="child"),
                primary_resource_class="io",
            ),
            inner_primary,
        )

    with pytest.raises(Exception):
        await with_resource_budget(
            WrapArgs(
                budget=Budget(timeout_ms=100, signal_respecting=True),
                tags=Tags(component="test", op="parent"),
                primary_resource_class="io",
            ),
            child_primary,
        )
    elapsed_ms = (time.monotonic() - t0) * 1000
    # Total elapsed bounded by parent's 100ms + slack, NOT child's 5000.
    assert elapsed_ms < 100 + SLACK_MS + 100
