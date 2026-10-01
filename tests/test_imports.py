"""Tests for the optional-dependency import guards."""

import builtins
import contextlib
import importlib
import sys
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from fastapi_toolsets._imports import require_extra


def _under(key: str, module_path: str) -> bool:
    return key == module_path or key.startswith(module_path + ".")


@contextlib.contextmanager
def _without_package(module_path: str, blocked: str) -> Iterator[None]:
    """Re-import *module_path* from scratch while imports of *blocked* fail.

    The evicted modules are put back in ``sys.modules`` on exit, so the rest
    of the suite keeps the real objects.
    """
    saved = {k: sys.modules.pop(k) for k in list(sys.modules) if _under(k, module_path)}
    original_import = builtins.__import__

    def blocking_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if _under(name, blocked):
            raise ImportError(f"Mocked: No module named '{name}'")
        return original_import(name, *args, **kwargs)

    try:
        with patch("builtins.__import__", side_effect=blocking_import):
            yield
    finally:
        for key in [k for k in sys.modules if _under(k, module_path)]:
            del sys.modules[key]
        sys.modules.update(saved)
        parent, _, child = module_path.rpartition(".")
        if module_path in saved:
            setattr(sys.modules[parent], child, saved[module_path])


class TestImportGuards:
    """Missing extras fail with an install hint; present ones export everything."""

    def test_require_extra_names_the_package_and_the_extra(self):
        with pytest.raises(
            ImportError,
            match=r"'prometheus_client' is required.*pip install fastapi-toolsets\[metrics\]",
        ):
            require_extra(package="prometheus_client", extra="metrics")

    @pytest.mark.parametrize(
        ("module", "blocked", "match"),
        [
            ("fastapi_toolsets.pytest", "pytest", r"'pytest' is required.*\[pytest\]"),
            ("fastapi_toolsets.pytest", "httpx", r"'httpx' is required.*\[pytest\]"),
            ("fastapi_toolsets.cli.app", "typer", r"'typer' is required.*\[cli\]"),
        ],
        ids=["pytest-without-pytest", "pytest-without-httpx", "cli-without-typer"],
    )
    def test_importing_a_guarded_module_names_the_missing_extra(
        self, module, blocked, match
    ):
        with _without_package(module, blocked):
            with pytest.raises(ImportError, match=match):
                importlib.import_module(module)

    def test_metrics_registry_survives_a_missing_prometheus_client(self):
        """Only ``init_metrics`` becomes a stub; the registry stays importable."""
        with _without_package("fastapi_toolsets.metrics", "prometheus_client"):
            mod = importlib.import_module("fastapi_toolsets.metrics")

            assert callable(mod.Metric) and callable(mod.MetricsRegistry)
            with pytest.raises(ImportError, match=r"prometheus_client.*\[metrics\]"):
                mod.init_metrics(None, None)  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]

        restored = importlib.import_module("fastapi_toolsets.metrics")
        assert restored.init_metrics.__module__ == "fastapi_toolsets.metrics.handler"

    @pytest.mark.parametrize(
        "module",
        ["fastapi_toolsets.pytest", "fastapi_toolsets.cli", "fastapi_toolsets.metrics"],
        ids=["pytest", "cli", "metrics"],
    )
    def test_exports_resolve_when_the_extra_is_installed(self, module):
        mod = importlib.import_module(module)

        assert mod.__all__
        assert all(callable(getattr(mod, name)) for name in mod.__all__)
