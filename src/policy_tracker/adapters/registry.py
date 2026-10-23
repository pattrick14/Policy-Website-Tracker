"""Default adapter registry used by the CLI.

Steps 3–5 add one factory per site to ``DEFAULT_ADAPTER_FACTORIES``; each
factory returns an object satisfying ``policy_tracker.adapters.base.Adapter``.
A tracked feed with no registered adapter fails with ``no_adapter``.
"""

from __future__ import annotations

from collections.abc import Callable

from policy_tracker.adapters import ministry_of_power
from policy_tracker.adapters.base import Adapter, AdapterRegistry

DEFAULT_ADAPTER_FACTORIES: tuple[Callable[[], Adapter], ...] = (ministry_of_power.make_adapter,)


def default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    for factory in DEFAULT_ADAPTER_FACTORIES:
        registry.register(factory())
    return registry
