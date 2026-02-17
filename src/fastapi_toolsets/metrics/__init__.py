"""Prometheus metrics integration for FastAPI applications."""

from .handler import init_metrics
from .registry import Metric, MetricsRegistry

__all__ = [
    "Metric",
    "MetricsRegistry",
    "init_metrics",
]
