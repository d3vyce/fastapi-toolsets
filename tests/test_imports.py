"""Tests for optional dependency import guards."""

import importlib
import sys
from unittest.mock import patch

import pytest

from fastapi_toolsets._imports import require_extra


class TestRequireExtra:
    """Tests for the require_extra helper."""

    def test_raises_import_error(self):
        """require_extra raises ImportError."""
        with pytest.raises(ImportError):
            require_extra(package="some_pkg", extra="some_extra")

    def test_error_message_contains_package_name(self):
        """Error message mentions the missing package."""
        with pytest.raises(ImportError, match="'prometheus_client'"):
            require_extra(package="prometheus_client", extra="metrics")

    def test_error_message_contains_install_instruction(self):
        """Error message contains the pip install command."""
        with pytest.raises(
            ImportError, match=r"pip install fastapi-toolsets\[metrics\]"
        ):
            require_extra(package="prometheus_client", extra="metrics")


def _reload_without_package(module_path: str, blocked_packages: list[str]):
    """Reload a module while blocking specific package imports.

    Removes the target module and its parents from sys.modules so they
    get re-imported, and patches builtins.__import__ to raise ImportError
    for *blocked_packages*.
    """
    # Remove cached modules so they get re-imported
    to_remove = [
        key
        for key in sys.modules
        if key == module_path or key.startswith(module_path + ".")
    ]
    saved = {}
    for key in to_remove:
        saved[key] = sys.modules.pop(key)

    # Also remove parent package to force re-execution of __init__.py
    parts = module_path.rsplit(".", 1)
    if len(parts) == 2:
        parent = parts[0]
        parent_keys = [
            key for key in sys.modules if key == parent or key.startswith(parent + ".")
        ]
        for key in parent_keys:
            if key not in saved:
                saved[key] = sys.modules.pop(key)

    original_import = (
        __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__
    )

    def blocking_import(name, *args, **kwargs):
        for blocked in blocked_packages:
            if name == blocked or name.startswith(blocked + "."):
                raise ImportError(f"Mocked: No module named '{name}'")
        return original_import(name, *args, **kwargs)

    return saved, blocking_import


class TestMetricsImportGuard:
    """Tests for metrics module import guard when prometheus_client is missing."""

    def test_registry_imports_without_prometheus(self):
        """Metric and MetricsRegistry are importable without prometheus_client."""
        saved, blocking_import = _reload_without_package(
            "fastapi_toolsets.metrics", ["prometheus_client"]
        )
        try:
            with patch("builtins.__import__", side_effect=blocking_import):
                mod = importlib.import_module("fastapi_toolsets.metrics")
                # Registry types should be available (they're stdlib-only)
                assert hasattr(mod, "Metric")
                assert hasattr(mod, "MetricsRegistry")
        finally:
            # Restore original modules
            for key in list(sys.modules):
                if key.startswith("fastapi_toolsets.metrics"):
                    sys.modules.pop(key, None)
            sys.modules.update(saved)

    def test_init_metrics_stub_raises_without_prometheus(self):
        """init_metrics raises ImportError when prometheus_client is missing."""
        saved, blocking_import = _reload_without_package(
            "fastapi_toolsets.metrics", ["prometheus_client"]
        )
        try:
            with patch("builtins.__import__", side_effect=blocking_import):
                mod = importlib.import_module("fastapi_toolsets.metrics")
                with pytest.raises(ImportError, match="prometheus_client"):
                    mod.init_metrics(None, None)  # type: ignore[arg-type]
        finally:
            for key in list(sys.modules):
                if key.startswith("fastapi_toolsets.metrics"):
                    sys.modules.pop(key, None)
            sys.modules.update(saved)

    def test_init_metrics_works_with_prometheus(self):
        """init_metrics is the real function when prometheus_client is available."""
        from fastapi_toolsets.metrics import init_metrics

        # Should be the real function, not a stub
        assert init_metrics.__module__ == "fastapi_toolsets.metrics.handler"


class TestPytestImportGuard:
    """Tests for pytest module import guard when dependencies are missing."""

    def test_import_raises_without_pytest_package(self):
        """Importing fastapi_toolsets.pytest raises when pytest is missing."""
        saved, blocking_import = _reload_without_package(
            "fastapi_toolsets.pytest", ["pytest"]
        )
        try:
            with patch("builtins.__import__", side_effect=blocking_import):
                with pytest.raises(ImportError, match="pytest"):
                    importlib.import_module("fastapi_toolsets.pytest")
        finally:
            for key in list(sys.modules):
                if key.startswith("fastapi_toolsets.pytest"):
                    sys.modules.pop(key, None)
            sys.modules.update(saved)

    def test_import_raises_without_httpx(self):
        """Importing fastapi_toolsets.pytest raises when httpx is missing."""
        saved, blocking_import = _reload_without_package(
            "fastapi_toolsets.pytest", ["httpx"]
        )
        try:
            with patch("builtins.__import__", side_effect=blocking_import):
                with pytest.raises(ImportError, match="httpx"):
                    importlib.import_module("fastapi_toolsets.pytest")
        finally:
            for key in list(sys.modules):
                if key.startswith("fastapi_toolsets.pytest"):
                    sys.modules.pop(key, None)
            sys.modules.update(saved)

    def test_all_exports_available_with_deps(self):
        """All expected exports are available when deps are installed."""
        from fastapi_toolsets.pytest import (
            cleanup_tables,
            create_async_client,
            create_db_session,
            create_worker_database,
            register_fixtures,
            worker_database_url,
        )

        assert callable(register_fixtures)
        assert callable(create_async_client)
        assert callable(create_db_session)
        assert callable(create_worker_database)
        assert callable(worker_database_url)
        assert callable(cleanup_tables)


class TestCliImportGuard:
    """Tests for CLI module import guard when typer is missing."""

    @pytest.mark.parametrize(
        "expected_match",
        [
            "typer",
            r"pip install fastapi-toolsets\[cli\]",
        ],
    )
    def test_import_raises_without_typer(self, expected_match):
        """Importing cli.app raises when typer is missing, with an informative error message."""
        saved, blocking_import = _reload_without_package(
            "fastapi_toolsets.cli.app", ["typer"]
        )
        # Also remove cli.config since it imports typer too
        config_keys = [
            k for k in sys.modules if k.startswith("fastapi_toolsets.cli.config")
        ]
        for key in config_keys:
            if key not in saved:
                saved[key] = sys.modules.pop(key)

        try:
            with patch("builtins.__import__", side_effect=blocking_import):
                with pytest.raises(ImportError, match=expected_match):
                    importlib.import_module("fastapi_toolsets.cli.app")
        finally:
            for key in list(sys.modules):
                if key.startswith("fastapi_toolsets.cli.app") or key.startswith(
                    "fastapi_toolsets.cli.config"
                ):
                    sys.modules.pop(key, None)
            sys.modules.update(saved)

    def test_async_command_imports_without_typer(self):
        """async_command is importable without typer (stdlib only)."""
        from fastapi_toolsets.cli import async_command

        assert callable(async_command)
