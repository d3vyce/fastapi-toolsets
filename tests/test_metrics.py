"""Tests for fastapi_toolsets.metrics module."""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge

from fastapi_toolsets.metrics import Metric, MetricsRegistry, init_metrics


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """Unregister test collectors from the global registry after each test."""
    yield
    collectors = list(REGISTRY._names_to_collectors.values())
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass


class TestMetric:
    """Tests for Metric dataclass."""

    def test_default_collect_is_false(self):
        """Default collect is False (provider mode)."""
        definition = Metric(name="test", func=lambda: None)
        assert definition.collect is False

    def test_collect_true(self):
        """Collect can be set to True (collector mode)."""
        definition = Metric(name="test", func=lambda: None, collect=True)
        assert definition.collect is True


class TestMetricsRegistry:
    """Tests for MetricsRegistry class."""

    def test_register_with_decorator(self):
        """Register metric with bare decorator."""
        registry = MetricsRegistry()

        @registry.register
        def my_counter():
            return Counter("test_counter", "A test counter")

        names = [m.name for m in registry.get_all()]
        assert "my_counter" in names

    def test_register_with_custom_name(self):
        """Register metric with custom name."""
        registry = MetricsRegistry()

        @registry.register(name="custom_name")
        def my_counter():
            return Counter("test_counter_2", "A test counter")

        definition = registry.get_all()[0]
        assert definition.name == "custom_name"

    def test_register_as_collector(self):
        """Register metric with collect=True."""
        registry = MetricsRegistry()

        @registry.register(collect=True)
        def collect_something():
            pass

        definition = registry.get_all()[0]
        assert definition.collect is True

    def test_register_preserves_function(self):
        """Decorator returns the original function unchanged."""
        registry = MetricsRegistry()

        def my_func():
            return "original"

        result = registry.register(my_func)
        assert result is my_func
        assert result() == "original"

    def test_register_parameterized_preserves_function(self):
        """Parameterized decorator returns the original function unchanged."""
        registry = MetricsRegistry()

        def my_func():
            return "original"

        result = registry.register(name="custom")(my_func)
        assert result is my_func
        assert result() == "original"

    def test_get_all(self):
        """Get all registered metrics."""
        registry = MetricsRegistry()

        @registry.register
        def metric_a():
            pass

        @registry.register
        def metric_b():
            pass

        names = {m.name for m in registry.get_all()}
        assert names == {"metric_a", "metric_b"}

    def test_get_providers(self):
        """Get only provider metrics (collect=False)."""
        registry = MetricsRegistry()

        @registry.register
        def provider():
            pass

        @registry.register(collect=True)
        def collector():
            pass

        providers = registry.get_providers()
        assert len(providers) == 1
        assert providers[0].name == "provider"

    def test_get_collectors(self):
        """Get only collector metrics (collect=True)."""
        registry = MetricsRegistry()

        @registry.register
        def provider():
            pass

        @registry.register(collect=True)
        def collector():
            pass

        collectors = registry.get_collectors()
        assert len(collectors) == 1
        assert collectors[0].name == "collector"

    def test_register_overwrites_same_name(self):
        """Registering with the same name overwrites the previous entry."""
        registry = MetricsRegistry()

        @registry.register(name="metric")
        def first():
            pass

        @registry.register(name="metric")
        def second():
            pass

        assert len(registry.get_all()) == 1
        assert registry.get_all()[0].func is second


class TestIncludeRegistry:
    """Tests for MetricsRegistry.include_registry method."""

    def test_include_empty_registry(self):
        """Include an empty registry does nothing."""
        main = MetricsRegistry()
        other = MetricsRegistry()

        @main.register
        def metric_a():
            pass

        main.include_registry(other)
        assert len(main.get_all()) == 1

    def test_include_registry_adds_metrics(self):
        """Include registry adds all metrics from the other registry."""
        main = MetricsRegistry()
        other = MetricsRegistry()

        @main.register
        def metric_a():
            pass

        @other.register
        def metric_b():
            pass

        @other.register
        def metric_c():
            pass

        main.include_registry(other)
        names = {m.name for m in main.get_all()}
        assert names == {"metric_a", "metric_b", "metric_c"}

    def test_include_registry_preserves_collect_flag(self):
        """Include registry preserves the collect flag."""
        main = MetricsRegistry()
        other = MetricsRegistry()

        @other.register(collect=True)
        def collector():
            pass

        main.include_registry(other)
        assert main.get_all()[0].collect is True

    def test_include_registry_raises_on_duplicate(self):
        """Include registry raises ValueError on duplicate metric names."""
        main = MetricsRegistry()
        other = MetricsRegistry()

        @main.register(name="metric")
        def metric_main():
            pass

        @other.register(name="metric")
        def metric_other():
            pass

        with pytest.raises(ValueError, match="already exists"):
            main.include_registry(other)

    def test_include_multiple_registries(self):
        """Include multiple registries sequentially."""
        main = MetricsRegistry()
        sub1 = MetricsRegistry()
        sub2 = MetricsRegistry()

        @main.register
        def base():
            pass

        @sub1.register
        def sub1_metric():
            pass

        @sub2.register
        def sub2_metric():
            pass

        main.include_registry(sub1)
        main.include_registry(sub2)

        names = {m.name for m in main.get_all()}
        assert names == {"base", "sub1_metric", "sub2_metric"}


class TestInitMetrics:
    """Tests for init_metrics function."""

    def test_returns_app(self):
        """Returns the FastAPI app."""
        app = FastAPI()
        registry = MetricsRegistry()
        result = init_metrics(app, registry)
        assert result is app

    def test_metrics_endpoint_responds(self):
        """The /metrics endpoint returns 200."""
        app = FastAPI()
        registry = MetricsRegistry()
        init_metrics(app, registry)

        client = TestClient(app)
        response = client.get("/metrics")

        assert response.status_code == 200

    def test_metrics_endpoint_content_type(self):
        """The /metrics endpoint returns prometheus content type."""
        app = FastAPI()
        registry = MetricsRegistry()
        init_metrics(app, registry)

        client = TestClient(app)
        response = client.get("/metrics")

        assert "text/plain" in response.headers["content-type"]

    def test_custom_path(self):
        """Custom path is used for the metrics endpoint."""
        app = FastAPI()
        registry = MetricsRegistry()
        init_metrics(app, registry, path="/custom-metrics")

        client = TestClient(app)
        assert client.get("/custom-metrics").status_code == 200
        assert client.get("/metrics").status_code == 404

    def test_providers_called_at_init(self):
        """Provider functions are called once at init time."""
        app = FastAPI()
        registry = MetricsRegistry()
        mock = MagicMock()

        @registry.register
        def my_provider():
            mock()

        init_metrics(app, registry)

        mock.assert_called_once()

    def test_collectors_called_on_scrape(self):
        """Collector functions are called on each scrape."""
        app = FastAPI()
        registry = MetricsRegistry()
        mock = MagicMock()

        @registry.register(collect=True)
        def my_collector():
            mock()

        init_metrics(app, registry)

        client = TestClient(app)
        client.get("/metrics")
        client.get("/metrics")

        assert mock.call_count == 2

    def test_collectors_not_called_at_init(self):
        """Collector functions are not called at init time."""
        app = FastAPI()
        registry = MetricsRegistry()
        mock = MagicMock()

        @registry.register(collect=True)
        def my_collector():
            mock()

        init_metrics(app, registry)

        mock.assert_not_called()

    def test_async_collectors_called_on_scrape(self):
        """Async collector functions are awaited on each scrape."""
        app = FastAPI()
        registry = MetricsRegistry()
        mock = AsyncMock()

        @registry.register(collect=True)
        async def my_async_collector():
            await mock()

        init_metrics(app, registry)

        client = TestClient(app)
        client.get("/metrics")
        client.get("/metrics")

        assert mock.call_count == 2

    def test_mixed_sync_and_async_collectors(self):
        """Both sync and async collectors are called on scrape."""
        app = FastAPI()
        registry = MetricsRegistry()
        sync_mock = MagicMock()
        async_mock = AsyncMock()

        @registry.register(collect=True)
        def sync_collector():
            sync_mock()

        @registry.register(collect=True)
        async def async_collector():
            await async_mock()

        init_metrics(app, registry)

        client = TestClient(app)
        client.get("/metrics")

        sync_mock.assert_called_once()
        async_mock.assert_called_once()

    def test_registered_metrics_appear_in_output(self):
        """Metrics created by providers appear in /metrics output."""
        app = FastAPI()
        registry = MetricsRegistry()

        @registry.register
        def my_gauge():
            g = Gauge("test_gauge_value", "A test gauge")
            g.set(42)
            return g

        init_metrics(app, registry)

        client = TestClient(app)
        response = client.get("/metrics")

        assert b"test_gauge_value" in response.content
        assert b"42.0" in response.content

    def test_endpoint_not_in_openapi_schema(self):
        """The /metrics endpoint is not included in the OpenAPI schema."""
        app = FastAPI()
        registry = MetricsRegistry()
        init_metrics(app, registry)

        schema = app.openapi()
        assert "/metrics" not in schema.get("paths", {})


class TestMultiProcessMode:
    """Tests for multi-process Prometheus mode."""

    def test_multiprocess_with_env_var(self):
        """Multi-process mode works when PROMETHEUS_MULTIPROC_DIR is set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["PROMETHEUS_MULTIPROC_DIR"] = tmpdir
            try:
                # Use a separate registry to avoid conflicts with default
                prom_registry = CollectorRegistry()
                app = FastAPI()
                registry = MetricsRegistry()

                @registry.register
                def mp_counter():
                    return Counter(
                        "mp_test_counter",
                        "A multiprocess counter",
                        registry=prom_registry,
                    )

                init_metrics(app, registry)

                client = TestClient(app)
                response = client.get("/metrics")

                assert response.status_code == 200
            finally:
                del os.environ["PROMETHEUS_MULTIPROC_DIR"]

    def test_single_process_without_env_var(self):
        """Single-process mode when PROMETHEUS_MULTIPROC_DIR is not set."""
        os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)

        app = FastAPI()
        registry = MetricsRegistry()

        @registry.register
        def sp_gauge():
            g = Gauge("sp_test_gauge", "A single-process gauge")
            g.set(99)
            return g

        init_metrics(app, registry)

        client = TestClient(app)
        response = client.get("/metrics")

        assert response.status_code == 200
        assert b"sp_test_gauge" in response.content


class TestMetricsIntegration:
    """Integration tests for the metrics module."""

    def test_full_workflow(self):
        """Full workflow: registry, providers, collectors, endpoint."""
        app = FastAPI()
        registry = MetricsRegistry()
        call_count = {"value": 0}

        @registry.register
        def request_counter():
            return Counter(
                "integration_requests_total",
                "Total requests",
                ["method"],
            )

        @registry.register(collect=True)
        def collect_uptime():
            call_count["value"] += 1

        init_metrics(app, registry)

        client = TestClient(app)

        response = client.get("/metrics")
        assert response.status_code == 200
        assert b"integration_requests_total" in response.content
        assert call_count["value"] == 1

        response = client.get("/metrics")
        assert call_count["value"] == 2

    def test_multiple_registries_merged(self):
        """Multiple registries can be merged and used together."""
        app = FastAPI()
        main = MetricsRegistry()
        sub = MetricsRegistry()

        @main.register
        def main_gauge():
            g = Gauge("main_gauge_val", "Main gauge")
            g.set(1)
            return g

        @sub.register
        def sub_gauge():
            g = Gauge("sub_gauge_val", "Sub gauge")
            g.set(2)
            return g

        main.include_registry(sub)
        init_metrics(app, main)

        client = TestClient(app)
        response = client.get("/metrics")

        assert b"main_gauge_val" in response.content
        assert b"sub_gauge_val" in response.content
