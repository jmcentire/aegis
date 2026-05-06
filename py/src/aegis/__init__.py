"""aegis — resource-budget primitive (Python sibling).

Wrap every blocking call in your stack with a declared budget, optional
fallback, and structured tags. See ../../SPEC.md and ../../pact.yaml for
the full contract.

Public surface only. Implementation details live in budget.py; types in
types.py; errors in errors.py.
"""

from .budget import (
    clear_aegis_observer,
    get_aegis_tags,
    set_aegis_observer,
    with_resource_budget,
)
from .errors import AegisConfigError, BudgetExceededError, FallbackFailedError
from .types import (
    AegisEvent,
    AegisObserver,
    AegisOutcome,
    Budget,
    Fallback,
    ResourceClass,
    Tags,
    WrapArgs,
    resource_class_rank,
)

__all__ = [
    "AegisConfigError",
    "AegisEvent",
    "AegisObserver",
    "AegisOutcome",
    "Budget",
    "BudgetExceededError",
    "Fallback",
    "FallbackFailedError",
    "ResourceClass",
    "Tags",
    "WrapArgs",
    "clear_aegis_observer",
    "get_aegis_tags",
    "resource_class_rank",
    "set_aegis_observer",
    "with_resource_budget",
]
