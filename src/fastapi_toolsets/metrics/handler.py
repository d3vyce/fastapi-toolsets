"""Prometheus metrics endpoint for FastAPI applications."""

import asyncio
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
        from fastapi import FastAPI
        from fastapi_toolsets.metrics import MetricsRegistry, init_metrics

        metrics = MetricsRegistry()
        app = FastAPI()
        init_metrics(app, registry=metrics)
    """
    for provider in registry.get_providers():
        logger.debug("Initialising metric provider '%s'", provider.name)
        provider.func()

    collectors = registry.get_collectors()

    @app.get(path, include_in_schema=False)
    async def metrics_endpoint() -> Response:
        for collector in collectors:
            if asyncio.iscoroutinefunction(collector.func):
                await collector.func()
            else:
                collector.func()

        if _is_multiprocess():
            prom_registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(prom_registry)
            output = generate_latest(prom_registry)
        else:
            output = generate_latest()

        return Response(content=output, media_type=CONTENT_TYPE_LATEST)

    return app
