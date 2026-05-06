"""Typed errors aegis raises (Python sibling).

Three concrete shapes — same as the TS sibling:
    - BudgetExceededError: budget timer fired before primary completed
      and no fallback was declared.
    - FallbackFailedError: budget timer fired AND the fallback also
      failed; both causes preserved for diagnostics.
    - AegisConfigError: developer error — invalid registration (e.g.,
      fallback resource class outranks primary's). Raised synchronously
      at wrap-time.

The shallow tree is intentional. Callers `isinstance` against these
to decide handling.
"""

from __future__ import annotations

from .types import Budget, Tags


class BudgetExceededError(Exception):
    """Raised when the budget elapsed and no fallback was declared."""

    def __init__(self, budget: Budget, elapsed_ms: float, tags: Tags) -> None:
        self.budget = budget
        self.elapsed_ms = elapsed_ms
        self.tags = tags
        super().__init__(
            f"aegis: budget exceeded after {round(elapsed_ms)}ms "
            f"(timeoutMs={budget.timeout_ms}, op={tags.component}.{tags.op})"
        )

    @property
    def name(self) -> str:
        return "BudgetExceededError"


class FallbackFailedError(Exception):
    """Raised when both primary (budget) and fallback failed."""

    def __init__(
        self, original_cause: BaseException, fallback_cause: BaseException, tags: Tags
    ) -> None:
        self.original_cause = original_cause
        self.fallback_cause = fallback_cause
        self.tags = tags
        super().__init__(
            f"aegis: fallback failed for {tags.component}.{tags.op}: "
            f"original={original_cause}; fallback={fallback_cause}"
        )

    @property
    def name(self) -> str:
        return "FallbackFailedError"


class AegisConfigError(Exception):
    """Developer error — invalid configuration. Raised at wrap-time, not call-time."""

    def __init__(self, message: str) -> None:
        super().__init__(f"aegis: {message}")

    @property
    def name(self) -> str:
        return "AegisConfigError"
