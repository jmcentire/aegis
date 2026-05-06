"""Differential fuzzer (Python half).

Strategy:
    1. The TS differential fuzzer (ts/tests/differential.test.ts) is run
       first. It uses fast-check to generate 1000 deterministic random
       cases (seed=0xdeadbeef), runs each through the TS impl, and
       writes:
         - vectors/_fuzzer-shared/inputs.json   (the input set)
         - vectors/_fuzzer-shared/ts-outcomes.json (TS canonical outputs)
    2. This Python test reads inputs.json, runs each case through the
       Python impl, canonicalizes the result with the SAME scheme, and
       asserts byte-identity with ts-outcomes.json.
    3. Independently, this test also runs hypothesis-generated cases
       through the Python impl to verify intrinsic invariants:
         - if primary resolves, outcome is ok
         - if primary hangs and no fallback, outcome is budget_exceeded
         - within-budget timing assertions hold
       This catches Python-only regressions before blaming a cross-lang
       divergence.

Run order: in CI, run `npm test` (TS) BEFORE pytest (Python). The
fixtures are committed to vectors/_fuzzer-shared/ as a regenerable
snapshot — if missing, this test xfails with a hint.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from test_golden_vectors import _canonicalize, run_case

SHARED_DIR = Path(__file__).resolve().parents[2] / "vectors" / "_fuzzer-shared"
INPUTS_PATH = SHARED_DIR / "inputs.json"
TS_OUTCOMES_PATH = SHARED_DIR / "ts-outcomes.json"


def _load_shared() -> tuple[list[dict[str, Any]], dict[str, str]] | None:
    if not INPUTS_PATH.exists() or not TS_OUTCOMES_PATH.exists():
        return None
    inputs_blob = json.loads(INPUTS_PATH.read_text())
    ts_blob = json.loads(TS_OUTCOMES_PATH.read_text())
    ts_by_id = {row["id"]: row["canonical"] for row in ts_blob["outcomes"]}
    return inputs_blob["inputs"], ts_by_id


async def test_differential_against_ts_outcomes() -> None:
    """Compare Python canonical outputs against TS ones for 1000 cases.

    Skipped (with explicit reason) if the TS fuzzer hasn't been run
    yet — the shared fixtures are produced by ts/tests/differential.test.ts
    and committed to vectors/_fuzzer-shared/.
    """
    loaded = _load_shared()
    if loaded is None:
        pytest.skip(
            "vectors/_fuzzer-shared/{inputs.json,ts-outcomes.json} missing; "
            "run `cd ts && npm test` first to generate fixtures."
        )
    inputs, ts_by_id = loaded

    divergences: list[tuple[str, str, str]] = []
    for case in inputs:
        case_id = case["id"]
        py_result = await run_case(case["input"])
        py_canonical = _canonicalize(py_result)
        ts_canonical = ts_by_id.get(case_id)
        if ts_canonical is None:
            divergences.append((case_id, "<missing in TS outcomes>", py_canonical))
            continue
        if ts_canonical != py_canonical:
            divergences.append((case_id, ts_canonical, py_canonical))

    assert len(inputs) >= 1000, f"expected >=1000 cases, got {len(inputs)}"

    if divergences:
        # Show the first few divergences for triage; full list
        # available in the assertion message.
        head = divergences[:5]
        head_msg = "\n".join(
            f"  case={cid}\n    ts={ts}\n    py={py}" for cid, ts, py in head
        )
        pytest.fail(
            f"differential fuzzer found {len(divergences)} divergence(s) out of "
            f"{len(inputs)} cases:\n{head_msg}"
        )


# -----------------------------------------------------------------
# Independent Python-only invariant tests via hypothesis. These catch
# Python-side bugs before they get blamed on cross-lang divergence.
# -----------------------------------------------------------------


_resource_classes = ["mem-sample", "cpu", "io", "pg", "llm"]


@settings(
    max_examples=100,
    deadline=2000,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    timeout_ms=st.integers(min_value=60, max_value=200),
    delay_ms=st.integers(min_value=0, max_value=20),
    rc=st.sampled_from(_resource_classes),
    op_name=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="._-"),
        min_size=1,
        max_size=20,
    ),
    val=st.one_of(st.integers(min_value=-1000, max_value=1000), st.booleans()),
)
async def test_invariant_resolve_delay_within_budget_is_ok(
    timeout_ms: int, delay_ms: int, rc: str, op_name: str, val: Any
) -> None:
    """If primary resolves within budget, outcome is ok and value is preserved."""
    case_input = {
        "budget": {"timeoutMs": timeout_ms, "signalRespecting": True},
        "tags": {"component": "fuzz", "op": op_name},
        "primaryResourceClass": rc,
        "primary": {"kind": "resolve", "value": val, "delayMs": delay_ms},
    }
    out = await run_case(case_input)
    assert out["outcome"] == "ok"
    assert out["value"] == val
    assert out["withinBudget"] is True


@settings(
    max_examples=50,
    deadline=2000,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    timeout_ms=st.integers(min_value=30, max_value=120),
    rc=st.sampled_from(_resource_classes),
    op_name=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="._-"),
        min_size=1,
        max_size=20,
    ),
)
async def test_invariant_hang_no_fallback_is_budget_exceeded(
    timeout_ms: int, rc: str, op_name: str
) -> None:
    """Primary hang + no fallback always reaches budget_exceeded."""
    case_input = {
        "budget": {"timeoutMs": timeout_ms, "signalRespecting": True},
        "tags": {"component": "fuzz", "op": op_name},
        "primaryResourceClass": rc,
        "primary": {"kind": "hang_until_abort"},
    }
    out = await run_case(case_input)
    assert out["outcome"] == "budget_exceeded"
    assert out["errorName"] == "BudgetExceededError"
    assert out["withinBudget"] is True


@settings(
    max_examples=50,
    deadline=2000,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    primary_rc=st.sampled_from(_resource_classes),
    fallback_rc=st.sampled_from(_resource_classes),
)
async def test_invariant_fallback_outranking_is_config_error(
    primary_rc: str, fallback_rc: str
) -> None:
    """A fallback whose rank > primary's rank must produce config_error.

    Equal or lower rank is permitted.
    """
    rank = {"mem-sample": 0, "cpu": 1, "io": 2, "pg": 3, "llm": 4}
    case_input = {
        "budget": {"timeoutMs": 100, "signalRespecting": True},
        "tags": {"component": "fuzz", "op": "rank.test"},
        "primaryResourceClass": primary_rc,
        "primary": {"kind": "resolve", "value": "p", "delayMs": 0},
        "fallback": {
            "resourceClass": fallback_rc,
            "fn": {"kind": "resolve", "value": "f", "delayMs": 0},
        },
    }
    out = await run_case(case_input)
    if rank[fallback_rc] > rank[primary_rc]:
        assert out["outcome"] == "config_error"
        assert out["errorName"] == "AegisConfigError"
    else:
        # Primary returns immediately — outcome ok regardless of fallback rank.
        assert out["outcome"] == "ok"
        assert out["value"] == "p"
