"""Tests for the metrics registry and the Prometheus ``/metrics`` endpoint."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY, Gauge

from fastapi_toolsets.metrics import Metric, MetricsRegistry, init_metrics


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """Unregister test collectors from the global registry after each test."""
    yield
    for collector in list(REGISTRY._names_to_collectors.values()):
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass


def _gauge_registry(name: str, value: float) -> MetricsRegistry:
    """Registry with one provider, ``gauge``, creating a gauge set to *value*."""
    registry = MetricsRegistry()

    @registry.register
    def gauge() -> Gauge:
        instance = Gauge(name, "A test gauge")
        instance.set(value)
        return instance

    return registry


class TestMetricsRegistry:
    """Registration, lookup and merging of metric definitions."""

    @pytest.mark.parametrize(
        ("kwargs", "name", "collect"),
        [
            ({}, "my_metric", False),
            ({"name": "custom"}, "custom", False),
            ({"collect": True}, "my_metric", True),
        ],
        ids=["bare", "named", "collector"],
    )
    def test_register_stores_a_metric_and_returns_the_function(
        self, kwargs, name, collect
    ):
        registry = MetricsRegistry()

        def my_metric() -> str:
            return "original"

        decorator = registry.register(**kwargs) if kwargs else registry.register

        assert decorator(my_metric) is my_metric
        assert registry.get_all() == [
            Metric(name=name, func=my_metric, collect=collect)
        ]

    def test_providers_and_collectors_are_partitioned_and_keyed_by_name(self):
        registry = MetricsRegistry()

        @registry.register
        def provider() -> None:
            pass

        @registry.register(collect=True)
        def collector() -> None:
            pass

        @registry.register(name="provider")
        def replacement() -> None:
            pass

        assert [m.name for m in registry.get_all()] == ["provider", "collector"]
        assert [m.func for m in registry.get_providers()] == [replacement]
        assert [m.func for m in registry.get_collectors()] == [collector]

    def test_get_returns_the_provider_instance_after_init(self):
        app = FastAPI()
        registry = _gauge_registry("get_test_gauge", 1)

        assert init_metrics(app, registry) is app
        assert isinstance(registry.get("gauge"), Gauge)

    @pytest.mark.parametrize(
        ("name", "message"),
        [("gauge", "not been initialized yet"), ("nonexistent", "Unknown metric")],
        ids=["not-initialized", "unknown"],
    )
    def test_get_raises_an_explanatory_key_error(self, name, message):
        registry = _gauge_registry("get_uninit_gauge", 1)

        with pytest.raises(KeyError, match=message):
            registry.get(name)

    def test_include_registry_merges_definitions_and_rejects_duplicates(self):
        main, sub, empty = MetricsRegistry(), MetricsRegistry(), MetricsRegistry()

        @main.register
        def base() -> None:
            pass

        @sub.register(collect=True)
        def collector() -> None:
            pass

        @sub.register
        def provider() -> None:
            pass

        main.include_registry(empty)
        main.include_registry(sub)

        assert [(m.name, m.collect) for m in main.get_all()] == [
            ("base", False),
            ("collector", True),
            ("provider", False),
        ]
        with pytest.raises(ValueError, match="already exists"):
            main.include_registry(sub)


class TestInitMetrics:
    """The scrape endpoint and the provider / collector lifecycle."""

    @pytest.mark.parametrize(
        "kwargs", [{}, {"path": "/custom-metrics"}], ids=["default-path", "custom-path"]
    )
    def test_endpoint_serves_prometheus_output_outside_the_schema(self, kwargs):
        app = FastAPI()
        init_metrics(app, _gauge_registry("test_gauge_value", 42), **kwargs)
        path = kwargs.get("path", "/metrics")
        client = TestClient(app)

        response = client.get(path)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert b"test_gauge_value 42.0" in response.content
        assert path not in app.openapi().get("paths", {})
        assert client.get("/metrics").status_code == (
            200 if path == "/metrics" else 404
        )

    def test_providers_run_once_at_init_and_collectors_on_every_scrape(self):
        app = FastAPI()
        registry = MetricsRegistry()
        provider_calls, sync_calls, async_calls = MagicMock(), MagicMock(), AsyncMock()

        @registry.register
        def provider() -> None:
            provider_calls()

        @registry.register(collect=True)
        def sync_collector() -> None:
            sync_calls()

        @registry.register(collect=True)
        async def async_collector() -> None:
            await async_calls()

        init_metrics(app, registry)
        provider_calls.assert_called_once()
        sync_calls.assert_not_called()
        async_calls.assert_not_called()

        client = TestClient(app)
        client.get("/metrics")
        client.get("/metrics")

        assert provider_calls.call_count == 1
        assert sync_calls.call_count == async_calls.call_count == 2

    @pytest.mark.parametrize(
        "multiprocess", [True, False], ids=["multiprocess", "single-process"]
    )
    def test_multiprocess_mode_follows_the_environment(
        self, monkeypatch, tmp_path, multiprocess
    ):
        """Multi-process mode serves the aggregated registry, not the in-process one."""
        if multiprocess:
            monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
        else:
            monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
        app = FastAPI()
        init_metrics(app, _gauge_registry("mode_gauge", 99))

        response = TestClient(app).get("/metrics")

        assert response.status_code == 200
        assert (b"mode_gauge 99.0" in response.content) is not multiprocess
