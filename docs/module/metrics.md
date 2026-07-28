# Metrics

Prometheus metrics integration with a decorator-based registry and multi-process support.

## Installation

=== "uv"
    ``` bash
    uv add "fastapi-toolsets[metrics]"
    ```

=== "pip"
    ``` bash
    pip install "fastapi-toolsets[metrics]"
    ```

## Overview

The `metrics` module provides a [`MetricsRegistry`](../reference/metrics.md#fastapi_toolsets.metrics.registry.MetricsRegistry) to declare Prometheus metrics with decorators, and an [`init_metrics`](../reference/metrics.md#fastapi_toolsets.metrics.handler.init_metrics) function to mount a `/metrics` endpoint on your FastAPI app.

## Setup

```python
from fastapi import FastAPI
from fastapi_toolsets.metrics import MetricsRegistry, init_metrics

app = FastAPI()
metrics = MetricsRegistry()

init_metrics(app=app, registry=metrics)
```

This mounts the `/metrics` endpoint that Prometheus can scrape.

## Declaring metrics

### Providers

Providers are called once at startup by `init_metrics`. The return value (the Prometheus metric object) is stored in the registry and can be retrieved later with [`registry.get(name)`](../reference/metrics.md#fastapi_toolsets.metrics.registry.MetricsRegistry.get).

Use providers when you want **deferred initialization**: the Prometheus metric is not registered with the global `CollectorRegistry` until `init_metrics` runs, not at import time. This is particularly useful for testing — importing the module in a test suite without calling `init_metrics` leaves no metrics registered, avoiding cross-test pollution.

It is also useful when metrics are defined across multiple modules and merged with `include_registry`: any code that needs a metric can call `metrics.get()` on the shared registry instead of importing the metric directly from its origin module.

If neither of these applies to you, declaring metrics at module level (e.g. `HTTP_REQUESTS = Counter(...)`) is simpler and equally valid.

```python
from prometheus_client import Counter, Histogram


@metrics.register
def http_requests():
    return Counter("http_requests_total", "Total HTTP requests", ["method", "status"])


@metrics.register
def request_duration():
    return Histogram("request_duration_seconds", "Request duration")
```

To use a provider's metric elsewhere (e.g. in a middleware), call `metrics.get()` inside the handler — **not** at module level, as providers are only initialized when `init_metrics` runs:

```python
async def metrics_middleware(request: Request, call_next):
    response = await call_next(request)
    metrics.get("http_requests").labels(
        method=request.method, status=response.status_code
    ).inc()
    return response
```

### Collectors

Collectors are called on every scrape. Use them for metrics that reflect current state (e.g. gauges).

!!! warning "Declare the metric at module level"
    Do **not** instantiate the Prometheus metric inside the collector function. Doing so recreates it on every scrape, raising `ValueError: Duplicated timeseries in CollectorRegistry`. Declare it once at module level instead:

```python
from prometheus_client import Gauge

_queue_depth = Gauge("queue_depth", "Current queue depth")


@metrics.register(collect=True)
def collect_queue_depth():
    _queue_depth.set(get_current_queue_depth())
```

## Merging registries

Split metrics definitions across modules and merge them:

```python
from myapp.metrics.http import http_metrics
from myapp.metrics.db import db_metrics

metrics = MetricsRegistry()
metrics.include_registry(registry=http_metrics)
metrics.include_registry(registry=db_metrics)
```

## Multi-process mode

Multi-process support is enabled automatically when the `PROMETHEUS_MULTIPROC_DIR` environment variable is set. No code changes are required.

!!! warning "Environment variable name"
    The correct variable is `PROMETHEUS_MULTIPROC_DIR` (not `PROMETHEUS_MULTIPROCESS_DIR`).

---

[:material-api: API Reference](../reference/metrics.md)
