"""with_resource_budget: the resource-budget primitive (Python sibling).

Wraps a blocking awaitable with a declared budget, optionally routes to
a fallback when the budget is exhausted, and emits a structured event
to a host-supplied observer.

Cross-language contract is locked in pact.yaml at the repo root. The TS
sibling uses AbortController + AbortSignal; we use asyncio.Event +
asyncio.wait_for. The fn receives the cancellation event; it is the
fn's responsibility to check `event.is_set()` (analog of
`signal.aborted`) or attach handlers.

To match the TS error-name string (so cross-lang outcomes are
byte-identical), we name the cancellation exception "AbortError" via
the `name` property — even though it's structurally a Python class.
"""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, TypeVar

from .errors import AegisConfigError, BudgetExceededError, FallbackFailedError
from .types import (
    AegisEvent,
    AegisObserver,
    Tags,
    WrapArgs,
    resource_class_rank,
)

T = TypeVar("T")

# ContextVar mirrors AsyncLocalStorage. Tags propagate across `await`
# boundaries because asyncio sets up a fresh `Context` per task that
# inherits the parent's vars; nested `with_resource_budget` calls
# overwrite within their own scope and restore on exit (we use
# `set` + `reset(token)`).
_TAGS_CTX: ContextVar[Tags | None] = ContextVar("aegis_tags", default=None)


def get_aegis_tags() -> Tags | None:
    """Return active aegis tags from the current async context, or None."""
    return _TAGS_CTX.get()


# Cancellation exception that the wrapped fn raises when the budget
# fires. We name it `AbortError` so cross-language outcomes match the
# TS sibling's `err.name === 'AbortError'` check.
class AbortError(Exception):
    """Raised inside the wrapped fn when the budget timer fires."""

    @property
    def name(self) -> str:
        return "AbortError"

    def __init__(self) -> None:
        super().__init__("aborted")


# Host-installed observer. Default no-op so the primitive is usable in
# tests / sandboxes without wiring anything. Hosts (Reeve, Baton, etc.)
# call set_aegis_observer() at boot.
_observer: AegisObserver = lambda _evt: None  # noqa: E731


def set_aegis_observer(observer: AegisObserver) -> None:
    global _observer
    _observer = observer


def clear_aegis_observer() -> None:
    global _observer
    _observer = lambda _evt: None  # noqa: E731


def _emit(event: AegisEvent) -> None:
    """Dispatch to the host observer, swallowing its exceptions."""
    try:
        _observer(event)
    except Exception:
        # Observer failures are not load-bearing — instrumentation
        # outage cannot affect the wrapped fn's outcome.
        pass


def _make_abort_event() -> asyncio.Event:
    """Create the cancellation event the wrapped fn observes."""
    return asyncio.Event()


def _is_abort_like(exc: BaseException) -> bool:
    """Return True iff exc represents a cancellation/abort.

    Includes our internal AbortError, asyncio.CancelledError, and any
    exception whose class name is "AbortError" / "TimeoutError" (so the
    fn can raise its own analog without us needing to know its module).
    """
    if isinstance(exc, AbortError):
        return True
    if isinstance(exc, asyncio.CancelledError):
        return True
    cls_name = type(exc).__name__
    return cls_name in ("AbortError", "TimeoutError")


async def with_resource_budget(
    args: WrapArgs,
    fn: Callable[[asyncio.Event], Awaitable[T]],
) -> T:
    """Wrap a blocking awaitable with a declared budget.

    See pact.yaml `contract.outcomes` for the full state machine.
    """
    # Wrap-time validation: fallback resourceClass must not outrank primary's.
    if args.fallback is not None:
        primary_rank = resource_class_rank(args.primary_resource_class)
        fallback_rank = resource_class_rank(args.fallback.resource_class)
        if fallback_rank > primary_rank:
            raise AegisConfigError(
                f"fallback resourceClass={args.fallback.resource_class} "
                f"(rank {fallback_rank}) is more expensive than "
                f"primary={args.primary_resource_class} (rank {primary_rank}) "
                f"for {args.tags.component}.{args.tags.op}. "
                f"Fallback paths cannot escalate resource use."
            )

    abort_event = _make_abort_event()
    timeout_s = args.budget.timeout_ms / 1000.0
    t0 = time.monotonic()

    # Establish the tag context. We use a fresh token so on exit the
    # parent's tags (if any) are restored.
    tag_token = _TAGS_CTX.set(args.tags)

    # Schedule a timer that flips abort_event when the budget is up.
    loop = asyncio.get_running_loop()
    timer_handle: asyncio.Handle = loop.call_later(timeout_s, abort_event.set)

    try:
        try:
            # Run the primary fn under wait_for so we get a hard kill if
            # it ignores abort_event entirely. wait_for raises
            # asyncio.TimeoutError when the deadline is hit.
            result = await asyncio.wait_for(fn(abort_event), timeout=timeout_s)
            elapsed_ok = (time.monotonic() - t0) * 1000.0
            _emit(
                AegisEvent(
                    outcome="ok",
                    tags=args.tags,
                    elapsed_ms=elapsed_ok,
                    primary_resource_class=args.primary_resource_class,
                    timeout_ms=args.budget.timeout_ms,
                )
            )
            return result
        except BaseException as err:  # noqa: BLE001 — we re-raise after categorizing
            elapsed = (time.monotonic() - t0) * 1000.0
            is_abort = _is_abort_like(err)
            exhausted = abort_event.is_set() or elapsed >= args.budget.timeout_ms

            if is_abort and exhausted:
                # Budget exhausted. If a fallback is registered, run it
                # with a FRESH abort event (its own budget timer).
                if args.fallback is not None:
                    fb_event = _make_abort_event()
                    fb_timer = loop.call_later(timeout_s, fb_event.set)
                    try:
                        fb_result: Any = await asyncio.wait_for(
                            args.fallback.fn(fb_event), timeout=timeout_s
                        )
                        _emit(
                            AegisEvent(
                                outcome="fallback",
                                tags=args.tags,
                                elapsed_ms=elapsed,
                                primary_resource_class=args.primary_resource_class,
                                fallback_resource_class=args.fallback.resource_class,
                                timeout_ms=args.budget.timeout_ms,
                            )
                        )
                        return fb_result
                    except BaseException as fb_err:  # noqa: BLE001
                        _emit(
                            AegisEvent(
                                outcome="fallback_failed",
                                tags=args.tags,
                                elapsed_ms=elapsed,
                                primary_resource_class=args.primary_resource_class,
                                fallback_resource_class=args.fallback.resource_class,
                                error_message=str(err),
                                fallback_error_message=str(fb_err),
                                timeout_ms=args.budget.timeout_ms,
                            )
                        )
                        # Wrap both causes. Mirror the TS impl: the
                        # original cause is the primary's abort
                        # (AbortError); the fallback cause is whatever
                        # the fallback raised.
                        primary_cause = AbortError() if isinstance(err, asyncio.TimeoutError | asyncio.CancelledError) else err
                        fallback_cause: BaseException = fb_err
                        if isinstance(fb_err, asyncio.TimeoutError | asyncio.CancelledError):
                            fallback_cause = AbortError()
                        raise FallbackFailedError(
                            primary_cause, fallback_cause, args.tags
                        ) from fb_err
                    finally:
                        fb_timer.cancel()
                # No fallback — surface BudgetExceededError.
                _emit(
                    AegisEvent(
                        outcome="budget_exceeded",
                        tags=args.tags,
                        elapsed_ms=elapsed,
                        primary_resource_class=args.primary_resource_class,
                        timeout_ms=args.budget.timeout_ms,
                    )
                )
                raise BudgetExceededError(args.budget, elapsed, args.tags) from err

            # Primary threw something OTHER than abort, OR aborted
            # before budget. Don't swallow.
            _emit(
                AegisEvent(
                    outcome="primary_error",
                    tags=args.tags,
                    elapsed_ms=elapsed,
                    primary_resource_class=args.primary_resource_class,
                    error_message=str(err),
                    timeout_ms=args.budget.timeout_ms,
                )
            )
            raise
    finally:
        timer_handle.cancel()
        _TAGS_CTX.reset(tag_token)
