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

Providers are called once at startup and register metrics that are updated externally (e.g. counters, histograms):

```python
from prometheus_client import Counter, Histogram

@metrics.register
def http_requests():
    return Counter("http_requests_total", "Total HTTP requests", ["method", "status"])

@metrics.register
def request_duration():
    return Histogram("request_duration_seconds", "Request duration")
```

### Collectors

Collectors are called on every scrape. Use them for metrics that reflect current state (e.g. gauges):

```python
@metrics.register(collect=True)
def queue_depth():
    gauge = Gauge("queue_depth", "Current queue depth")
    gauge.set(get_current_queue_depth())
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
