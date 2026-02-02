"""Tests for fastapi_toolsets.cli module."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from fastapi_toolsets.cli.config import CliConfig, _import_from_string, load_config
from fastapi_toolsets.fixtures import Context, FixtureRegistry

runner = CliRunner()


class TestCliConfig:
    """Tests for CliConfig dataclass."""

    def test_default_values(self):
        """Config has None defaults."""
        config = CliConfig()
        assert config.fixtures is None
        assert config.db_context is None

    def test_with_values(self):
        """Config stores provided values."""
        config = CliConfig(
            fixtures="app.fixtures:registry",
            db_context="app.db:get_session",
        )
        assert config.fixtures == "app.fixtures:registry"
        assert config.db_context == "app.db:get_session"

    def test_get_fixtures_registry_without_config(self):
        """get_fixtures_registry raises error when not configured."""
        config = CliConfig()
        with pytest.raises(Exception) as exc_info:
            config.get_fixtures_registry()
        assert "No fixtures registry configured" in str(exc_info.value)

    def test_get_db_context_without_config(self):
        """get_db_context raises error when not configured."""
        config = CliConfig()
        with pytest.raises(Exception) as exc_info:
            config.get_db_context()
        assert "No db_context configured" in str(exc_info.value)


class TestImportFromString:
    """Tests for _import_from_string function."""

    def test_import_valid_path(self):
        """Import valid module:attribute path."""
        result = _import_from_string("fastapi_toolsets.fixtures:FixtureRegistry")
        assert result is FixtureRegistry

    def test_import_without_colon_raises_error(self):
        """Import path without colon raises error."""
        with pytest.raises(Exception) as exc_info:
            _import_from_string("fastapi_toolsets.fixtures.FixtureRegistry")
        assert "Expected format: 'module:attribute'" in str(exc_info.value)

    def test_import_nonexistent_module_raises_error(self):
        """Import nonexistent module raises error."""
        with pytest.raises(Exception) as exc_info:
            _import_from_string("nonexistent.module:something")
        assert "Cannot import module" in str(exc_info.value)

    def test_import_nonexistent_attribute_raises_error(self):
        """Import nonexistent attribute raises error."""
        with pytest.raises(Exception) as exc_info:
            _import_from_string("fastapi_toolsets.fixtures:NonexistentClass")
        assert "has no attribute" in str(exc_info.value)


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_without_pyproject(self, tmp_path, monkeypatch):
        """Returns empty config when no pyproject.toml exists."""
        monkeypatch.chdir(tmp_path)
        config = load_config()
        assert config.fixtures is None
        assert config.db_context is None

    def test_load_without_tool_section(self, tmp_path, monkeypatch):
        """Returns empty config when no [tool.fastapi-toolsets] section."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'test'\n")
        monkeypatch.chdir(tmp_path)

        config = load_config()
        assert config.fixtures is None
        assert config.db_context is None

    def test_load_with_fixtures_config(self, tmp_path, monkeypatch):
        """Loads fixtures config from pyproject.toml."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.fastapi-toolsets]\nfixtures = "app.fixtures:registry"\n'
        )
        monkeypatch.chdir(tmp_path)

        config = load_config()
        assert config.fixtures == "app.fixtures:registry"
        assert config.db_context is None

    def test_load_with_full_config(self, tmp_path, monkeypatch):
        """Loads full config from pyproject.toml."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[tool.fastapi-toolsets]\n"
            'fixtures = "app.fixtures:registry"\n'
            'db_context = "app.db:get_session"\n'
        )
        monkeypatch.chdir(tmp_path)

        config = load_config()
        assert config.fixtures == "app.fixtures:registry"
        assert config.db_context == "app.db:get_session"

    def test_load_with_invalid_toml(self, tmp_path, monkeypatch):
        """Returns empty config when pyproject.toml is invalid."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("invalid toml {{{")
        monkeypatch.chdir(tmp_path)

        config = load_config()
        assert config.fixtures is None


class TestCliApp:
    """Tests for CLI application."""

    def test_cli_import(self):
        """CLI can be imported."""
        from fastapi_toolsets.cli import cli

        assert cli is not None

    def test_cli_help(self, tmp_path, monkeypatch):
        """CLI shows help without fixtures."""
        monkeypatch.chdir(tmp_path)

        # Need to reload the module to pick up new cwd
        import importlib

        from fastapi_toolsets.cli import app

        importlib.reload(app)

        result = runner.invoke(app.cli, ["--help"])
        assert result.exit_code == 0
        assert "CLI utilities for FastAPI projects" in result.output


class TestFixturesCli:
    """Tests for fixtures CLI commands."""

    @pytest.fixture
    def cli_env(self, tmp_path, monkeypatch):
        """Set up CLI environment with fixtures config."""
        # Create pyproject.toml
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[tool.fastapi-toolsets]\n"
            'fixtures = "fixtures:registry"\n'
            'db_context = "db:get_session"\n'
        )

        # Create fixtures module
        fixtures_file = tmp_path / "fixtures.py"
        fixtures_file.write_text(
            "from fastapi_toolsets.fixtures import FixtureRegistry, Context\n"
            "\n"
            "registry = FixtureRegistry()\n"
            "\n"
            "@registry.register(contexts=[Context.BASE])\n"
            "def roles():\n"
            '    return [{"id": 1, "name": "admin"}, {"id": 2, "name": "user"}]\n'
            "\n"
            '@registry.register(depends_on=["roles"], contexts=[Context.TESTING])\n'
            "def users():\n"
            '    return [{"id": 1, "name": "alice", "role_id": 1}]\n'
        )

        # Create db module
        db_file = tmp_path / "db.py"
        db_file.write_text(
            "from contextlib import asynccontextmanager\n"
            "\n"
            "@asynccontextmanager\n"
            "async def get_session():\n"
            "    yield None\n"
        )

        monkeypatch.chdir(tmp_path)

        # Add tmp_path to sys.path for imports
        if str(tmp_path) not in sys.path:
            sys.path.insert(0, str(tmp_path))

        # Reload the CLI module to pick up new config
        import importlib

        from fastapi_toolsets.cli import app, config

        importlib.reload(config)
        importlib.reload(app)

        yield tmp_path, app.cli

        # Cleanup
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))

    def test_fixtures_list(self, cli_env):
        """fixtures list shows registered fixtures."""
        tmp_path, cli = cli_env
        result = runner.invoke(cli, ["fixtures", "list"])

        assert result.exit_code == 0
        assert "roles" in result.output
        assert "users" in result.output
        assert "Total: 2 fixture(s)" in result.output

    def test_fixtures_list_with_context(self, cli_env):
        """fixtures list --context filters by context."""
        tmp_path, cli = cli_env
        result = runner.invoke(cli, ["fixtures", "list", "--context", "base"])

        assert result.exit_code == 0
        assert "roles" in result.output
        assert "users" not in result.output
        assert "Total: 1 fixture(s)" in result.output

    def test_fixtures_load_dry_run(self, cli_env):
        """fixtures load --dry-run shows what would be loaded."""
        tmp_path, cli = cli_env
        result = runner.invoke(cli, ["fixtures", "load", "base", "--dry-run"])

        assert result.exit_code == 0
        assert "Fixtures to load" in result.output
        assert "roles" in result.output
        assert "[Dry run - no changes made]" in result.output

    def test_fixtures_load_invalid_strategy(self, cli_env):
        """fixtures load with invalid strategy shows error."""
        tmp_path, cli = cli_env
        result = runner.invoke(
            cli, ["fixtures", "load", "base", "--strategy", "invalid"]
        )

        assert result.exit_code == 1
        assert "Invalid strategy" in result.output


class TestCliWithoutFixturesConfig:
    """Tests for CLI when fixtures is not configured."""

    def test_no_fixtures_command(self, tmp_path, monkeypatch):
        """fixtures command is not available when not configured."""
        # Create pyproject.toml without fixtures
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "test"\n')

        monkeypatch.chdir(tmp_path)

        # Reload the CLI module
        import importlib

        from fastapi_toolsets.cli import app, config

        importlib.reload(config)
        importlib.reload(app)

        result = runner.invoke(app.cli, ["--help"])

        assert result.exit_code == 0
        assert "fixtures" not in result.output
