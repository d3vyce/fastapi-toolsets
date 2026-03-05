"""Metrics registry with decorator-based registration."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

from ..logger import get_logger

logger = get_logger()


@dataclass
class Metric:
    """A metric definition with metadata."""

    name: str
    func: Callable[..., Any]
    collect: bool = field(default=False)


class MetricsRegistry:
    """Registry for managing Prometheus metric providers and collectors."""

    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}
        self._instances: dict[str, Any] = {}

    def register(
        self,
        func: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        collect: bool = False,
    ) -> Callable[..., Any]:
        """Register a metric provider or collector function.

        Can be used as a decorator with or without arguments.

        Args:
            func: The metric function to register.
            name: Metric name (defaults to function name).
            collect: If ``True``, the function is called on every scrape.
                If ``False`` (default), called once at init time.
        """

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            metric_name = name or cast(Any, fn).__name__
            self._metrics[metric_name] = Metric(
                name=metric_name,
                func=fn,
                collect=collect,
            )
            return fn

        if func is not None:
            return decorator(func)
        return decorator

    def get(self, name: str) -> Any:
        """Return the metric instance created by a provider.

        Args:
            name: The metric name (defaults to the provider function name).

        Raises:
            KeyError: If the metric name is unknown or ``init_metrics`` has not
                been called yet.
        """
        if name not in self._instances:
            if name in self._metrics:
                raise KeyError(
                    f"Metric '{name}' exists but has not been initialized yet. "
                    "Ensure init_metrics() has been called before accessing metric instances."
                )
            raise KeyError(f"Unknown metric '{name}'.")
        return self._instances[name]

    def include_registry(self, registry: "MetricsRegistry") -> None:
        """Include another :class:`MetricsRegistry` into this one.

        Args:
            registry: The registry to merge in.

        Raises:
            ValueError: If a metric name already exists in the current registry.
        """
        for metric_name, definition in registry._metrics.items():
            if metric_name in self._metrics:
                raise ValueError(
                    f"Metric '{metric_name}' already exists in the current registry"
                )
            self._metrics[metric_name] = definition

    def get_all(self) -> list[Metric]:
        """Get all registered metric definitions."""
        return list(self._metrics.values())

    def get_providers(self) -> list[Metric]:
        """Get metric providers (called once at init)."""
        return [m for m in self._metrics.values() if not m.collect]

    def get_collectors(self) -> list[Metric]:
        """Get collectors (called on each scrape)."""
        return [m for m in self._metrics.values() if m.collect]
