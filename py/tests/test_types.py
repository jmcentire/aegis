"""Unit tests for aegis types (Python sibling).

Pure type-shape and rank ordering — no I/O. Mirrors the TS unit
tests at ts/tests/unit/aegis-types.test.ts.
"""

from aegis import resource_class_rank


def test_ranks_mem_sample_cheapest() -> None:
    assert resource_class_rank("mem-sample") == 0


def test_ranks_llm_most_expensive() -> None:
    assert resource_class_rank("llm") == 4


def test_preserves_cpu_io_pg_llm_ordering() -> None:
    order = ["mem-sample", "cpu", "io", "pg", "llm"]
    ranks = [resource_class_rank(c) for c in order]  # type: ignore[arg-type]
    for i in range(1, len(ranks)):
        assert ranks[i] > ranks[i - 1]
