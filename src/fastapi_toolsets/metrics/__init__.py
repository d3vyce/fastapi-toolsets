"""Prometheus metrics integration for FastAPI applications."""

from typing import Any

from .registry import Metric, MetricsRegistry

try:
    from .handler import init_metrics
except ImportError:

    def init_metrics(*_args: Any, **_kwargs: Any) -> None:
        from .._imports import require_extra

        require_extra(package="prometheus_client", extra="metrics")


__all__ = [
    "Metric",
    "MetricsRegistry",
    "init_metrics",
]
