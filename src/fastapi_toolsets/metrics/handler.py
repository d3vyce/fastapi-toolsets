"""Prometheus metrics endpoint for FastAPI applications."""

import inspect
import os

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    generate_latest,
    multiprocess,
)

from ..logger import get_logger
from .registry import MetricsRegistry

logger = get_logger()


def _is_multiprocess() -> bool:
    """Check if prometheus multi-process mode is enabled."""
    return "PROMETHEUS_MULTIPROC_DIR" in os.environ


def init_metrics(
    app: FastAPI,
    registry: MetricsRegistry,
    *,
    path: str = "/metrics",
) -> FastAPI:
    """Register a Prometheus ``/metrics`` endpoint on a FastAPI app.

    Args:
        app: FastAPI application instance.
        registry: A :class:`MetricsRegistry` containing providers and collectors.
        path: URL path for the metrics endpoint (default ``/metrics``).

    Returns:
        The same FastAPI instance (for chaining).

    Example:
        ```python
        from fastapi import FastAPI
        from fastapi_toolsets.metrics import MetricsRegistry, init_metrics

        metrics = MetricsRegistry()
        app = FastAPI()
        init_metrics(app, registry=metrics)
        ```
    """
    for provider in registry.get_providers():
        logger.debug("Initialising metric provider '%s'", provider.name)
        registry._instances[provider.name] = provider.func()

    # Partition collectors and cache env check at startup — both are stable for the app lifetime.
    async_collectors = [
        c for c in registry.get_collectors() if inspect.iscoroutinefunction(c.func)
    ]
    sync_collectors = [
        c for c in registry.get_collectors() if not inspect.iscoroutinefunction(c.func)
    ]
    multiprocess_mode = _is_multiprocess()

    @app.get(path, include_in_schema=False)
    async def metrics_endpoint() -> Response:
        for collector in sync_collectors:
            collector.func()
        for collector in async_collectors:
            await collector.func()

        if multiprocess_mode:
            prom_registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(prom_registry)
            output = generate_latest(prom_registry)
        else:
            output = generate_latest()

        return Response(content=output, media_type=CONTENT_TYPE_LATEST)

    return app
