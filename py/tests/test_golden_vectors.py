"""Golden vector runner (Python sibling).

Reads `vectors/budget-cases.json` from the repo root and runs each
case through the Python impl. The TS sibling consumes the SAME file
and runs the SAME assertions — divergence between the two is a
contract bug.

The case interpreter (`run_case`) is exported and used by the
differential fuzzer too, so generated cases share the same execution
model as golden cases.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable

import pytest
from aegis import (
    AegisConfigError,
    Budget,
    BudgetExceededError,
    Fallback,
    FallbackFailedError,
    Tags,
    WrapArgs,
    clear_aegis_observer,
    set_aegis_observer,
    with_resource_budget,
)
from aegis.budget import AbortError

SLACK_MS = 200

VECTORS_PATH = Path(__file__).resolve().parents[2] / "vectors" / "budget-cases.json"


def _build_fn(spec: dict[str, Any]) -> Callable[[asyncio.Event], "Any"]:
    """Translate a JSON fn-spec to an awaitable. Same semantics as TS buildFn."""
    kind = spec["kind"]
    if kind == "resolve":
        delay_ms = spec.get("delayMs", 0)
        value = spec["value"]

        async def _resolve(event: asyncio.Event) -> Any:
            if delay_ms == 0:
                return value
            try:
                await asyncio.wait_for(event.wait(), timeout=delay_ms / 1000.0)
            except asyncio.TimeoutError:
                # Delay elapsed without abort; resolve normally.
                return value
            # Abort fired before delay. Raise abort.
            raise AbortError()

        return _resolve

    if kind == "reject":
        delay_ms = spec.get("delayMs", 0)
        err_name = spec["errorName"]
        err_msg = spec["errorMessage"]

        async def _reject(event: asyncio.Event) -> Any:
            if delay_ms > 0:
                try:
                    await asyncio.wait_for(event.wait(), timeout=delay_ms / 1000.0)
                except asyncio.TimeoutError:
                    pass
                else:
                    # Abort beat the delay -> abort path.
                    raise AbortError()
            # Build a class with the right name so type(e).__name__
            # matches across languages.
            cls = type(err_name, (Exception,), {})
            raise cls(err_msg)

        return _reject

    if kind == "hang_until_abort":

        async def _hang(event: asyncio.Event) -> Any:
            await event.wait()
            raise AbortError()

        return _hang

    raise ValueError(f"unknown fn spec kind: {kind}")


def _canonicalize(out: dict[str, Any]) -> str:
    """Canonical JSON for cross-language byte-identical comparison.

    Must match ts/tests/golden/budget-cases.test.ts `canonicalize`.
    `withinBudget` is intentionally OMITTED from the canonical: it's a
    per-case timing sanity assertion that can race across languages on
    a loaded box. The cross-language contract is the outcome shape.
    """
    sorted_tags: dict[str, str] = {}
    for k in sorted(out["tags"].keys()):
        v = out["tags"][k]
        if v is not None:
            sorted_tags[k] = v
    obj: dict[str, Any] = {
        "outcome": out["outcome"],
        "tags": sorted_tags,
    }
    if out.get("value") is not None:
        obj["value"] = out["value"]
    if out.get("errorName") is not None:
        obj["errorName"] = out["errorName"]
    if out.get("originalErrorName") is not None:
        obj["originalErrorName"] = out["originalErrorName"]
    if out.get("fallbackErrorName") is not None:
        obj["fallbackErrorName"] = out["fallbackErrorName"]
    ordered_keys = [
        "outcome",
        "value",
        "errorName",
        "originalErrorName",
        "fallbackErrorName",
        "tags",
    ]
    ordered = {k: obj[k] for k in ordered_keys if k in obj}
    # JSON.stringify in Node produces no whitespace by default:
    #   {"a":1,"b":2}
    # We match by passing separators=(",", ":") — minimal canonical form.
    return json.dumps(ordered, separators=(",", ":"), ensure_ascii=False)


async def run_case(input_data: dict[str, Any]) -> dict[str, Any]:
    """Execute one case and return the canonical result dict.

    Mirrors ts/tests/golden/budget-cases.test.ts `runCase`.
    """
    last_outcome: dict[str, str | None] = {"outcome": None}

    def _observer(evt: Any) -> None:
        last_outcome["outcome"] = evt.outcome

    set_aegis_observer(_observer)
    try:
        budget_data = input_data["budget"]
        tags_data = input_data["tags"]
        primary_spec = input_data["primary"]
        fallback_data = input_data.get("fallback")

        budget = Budget(
            timeout_ms=budget_data["timeoutMs"],
            signal_respecting=budget_data["signalRespecting"],
            pg_statement_timeout_ms=budget_data.get("pgStatementTimeoutMs"),
        )
        tags = Tags(
            component=tags_data["component"],
            op=tags_data["op"],
            tenant_id=tags_data.get("tenantId"),
            correlation_id=tags_data.get("correlationId"),
        )
        primary_fn = _build_fn(primary_spec)
        fallback: Fallback | None = None
        if fallback_data is not None:
            fallback = Fallback(
                fn=_build_fn(fallback_data["fn"]),
                resource_class=fallback_data["resourceClass"],
            )

        wrap_args = WrapArgs(
            budget=budget,
            tags=tags,
            primary_resource_class=input_data["primaryResourceClass"],
            fallback=fallback,
        )

        t0 = time.monotonic()
        try:
            value = await with_resource_budget(wrap_args, primary_fn)
            elapsed_ms = (time.monotonic() - t0) * 1000
            outcome = "fallback" if last_outcome["outcome"] == "fallback" else "ok"
            return {
                "outcome": outcome,
                "value": value,
                "withinBudget": elapsed_ms < budget.timeout_ms + SLACK_MS,
                "tags": _tags_to_dict(tags),
            }
        except AegisConfigError:
            return {
                "outcome": "config_error",
                "errorName": "AegisConfigError",
                "withinBudget": True,
                "tags": _tags_to_dict(tags),
            }
        except BudgetExceededError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return {
                "outcome": "budget_exceeded",
                "errorName": "BudgetExceededError",
                "withinBudget": elapsed_ms < budget.timeout_ms + SLACK_MS,
                "tags": _tags_to_dict(tags),
            }
        except FallbackFailedError as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return {
                "outcome": "fallback_failed",
                "errorName": "FallbackFailedError",
                "originalErrorName": _exc_name(e.original_cause),
                "fallbackErrorName": _exc_name(e.fallback_cause),
                "withinBudget": elapsed_ms < budget.timeout_ms + SLACK_MS,
                "tags": _tags_to_dict(tags),
            }
        except BaseException as e:  # primary error path
            elapsed_ms = (time.monotonic() - t0) * 1000
            return {
                "outcome": "primary_error",
                "errorName": _exc_name(e),
                "withinBudget": elapsed_ms < budget.timeout_ms + SLACK_MS,
                "tags": _tags_to_dict(tags),
            }
    finally:
        clear_aegis_observer()


def _exc_name(exc: BaseException) -> str:
    # Match the TS Error.name field. Our AbortError uses .name property.
    if isinstance(exc, AbortError):
        return "AbortError"
    return type(exc).__name__


def _tags_to_dict(t: Tags) -> dict[str, str]:
    d: dict[str, str] = {"component": t.component, "op": t.op}
    if t.tenant_id is not None:
        d["tenantId"] = t.tenant_id
    if t.correlation_id is not None:
        d["correlationId"] = t.correlation_id
    return d


def _load_vectors() -> list[dict[str, Any]]:
    return json.loads(VECTORS_PATH.read_text())["cases"]


@pytest.mark.parametrize("case", _load_vectors(), ids=lambda c: c["id"])
async def test_golden_case(case: dict[str, Any]) -> None:
    actual = await run_case(case["input"])
    expected = case["expected"]
    assert actual["outcome"] == expected["outcome"], f"case={case['id']}"
    if expected.get("errorName") is not None:
        assert actual.get("errorName") == expected["errorName"]
    if expected.get("originalErrorName") is not None:
        assert actual.get("originalErrorName") == expected["originalErrorName"]
    if expected.get("fallbackErrorName") is not None:
        assert actual.get("fallbackErrorName") == expected["fallbackErrorName"]
    if expected.get("value") is not None:
        assert actual.get("value") == expected["value"]
    assert actual["withinBudget"] is True
    assert actual["tags"]["component"] == expected["tags"]["component"]
    assert actual["tags"]["op"] == expected["tags"]["op"]
