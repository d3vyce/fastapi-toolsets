"""Tests for fastapi_toolsets.cli module."""

import sys

import pytest
from typer.testing import CliRunner

from fastapi_toolsets.cli.config import (
    get_config_value,
    get_custom_cli,
    get_db_context,
    get_fixtures_registry,
    import_from_string,
)
from fastapi_toolsets.cli.pyproject import find_pyproject, load_pyproject
from fastapi_toolsets.cli.utils import async_command
from fastapi_toolsets.fixtures import FixtureRegistry

runner = CliRunner()


class TestPyproject:
    """Tests for pyproject.toml discovery and loading."""

    def test_find_pyproject_in_current_dir(self, tmp_path, monkeypatch):
        """Finds pyproject.toml in current directory."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'test'\n")
        monkeypatch.chdir(tmp_path)

        result = find_pyproject()
        assert result == pyproject

    def test_find_pyproject_in_parent_dir(self, tmp_path, monkeypatch):
        """Finds pyproject.toml in parent directory."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'test'\n")
        subdir = tmp_path / "src" / "app"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)

        result = find_pyproject()
        assert result == pyproject

    def test_find_pyproject_not_found(self, tmp_path, monkeypatch):
        """Returns None when no pyproject.toml exists."""
        monkeypatch.chdir(tmp_path)
        result = find_pyproject()
        assert result is None

    def test_load_pyproject_returns_tool_config(self, tmp_path, monkeypatch):
        """load_pyproject returns the [tool.fastapi-toolsets] section."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.fastapi-toolsets]\nfixtures = "app.fixtures:registry"\n'
        )
        monkeypatch.chdir(tmp_path)

        result = load_pyproject()
        assert result == {"fixtures": "app.fixtures:registry"}

    def test_load_pyproject_empty_when_no_file(self, tmp_path, monkeypatch):
        """Returns empty dict when no pyproject.toml exists."""
        monkeypatch.chdir(tmp_path)
        result = load_pyproject()
        assert result == {}

    def test_load_pyproject_empty_when_no_tool_section(self, tmp_path, monkeypatch):
        """Returns empty dict when no [tool.fastapi-toolsets] section."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'test'\n")
        monkeypatch.chdir(tmp_path)

        result = load_pyproject()
        assert result == {}

    def test_load_pyproject_invalid_toml(self, tmp_path, monkeypatch):
        """Returns empty dict when pyproject.toml is invalid."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("invalid toml {{{")
        monkeypatch.chdir(tmp_path)

        result = load_pyproject()
        assert result == {}


class TestImportFromString:
    """Tests for import_from_string function."""

    def test_import_valid_path(self):
        """Import valid module:attribute path."""
        result = import_from_string("fastapi_toolsets.fixtures:FixtureRegistry")
        assert result is FixtureRegistry

    def test_import_without_colon_raises_error(self):
        """Import path without colon raises error."""
        with pytest.raises(Exception) as exc_info:
            import_from_string("fastapi_toolsets.fixtures.FixtureRegistry")
        assert "Expected format: 'module:attribute'" in str(exc_info.value)

    def test_import_nonexistent_module_raises_error(self):
        """Import nonexistent module raises error."""
        with pytest.raises(Exception) as exc_info:
            import_from_string("nonexistent.module:something")
        assert "Cannot import module" in str(exc_info.value)

    def test_import_nonexistent_attribute_raises_error(self):
        """Import nonexistent attribute raises error."""
        with pytest.raises(Exception) as exc_info:
            import_from_string("fastapi_toolsets.fixtures:NonexistentClass")
        assert "has no attribute" in str(exc_info.value)


class TestGetConfigValue:
    """Tests for get_config_value function."""

    def test_get_existing_value(self, tmp_path, monkeypatch):
        """Returns value when key exists."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[tool.fastapi-toolsets]\nfixtures = "app:registry"\n')
        monkeypatch.chdir(tmp_path)

        result = get_config_value("fixtures")
        assert result == "app:registry"

    def test_get_missing_value_returns_none(self, tmp_path, monkeypatch):
        """Returns None when key is missing and not required."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.fastapi-toolsets]\n")
        monkeypatch.chdir(tmp_path)

        result = get_config_value("fixtures")
        assert result is None

    def test_get_missing_value_required_raises_error(self, tmp_path, monkeypatch):
        """Raises error when key is missing and required."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.fastapi-toolsets]\n")
        monkeypatch.chdir(tmp_path)

        with pytest.raises(Exception) as exc_info:
            get_config_value("fixtures", required=True)
        assert "No 'fixtures' configured" in str(exc_info.value)


class TestGetFixturesRegistry:
    """Tests for get_fixtures_registry function."""

    def test_raises_when_not_configured(self, tmp_path, monkeypatch):
        """Raises error when fixtures not configured."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.fastapi-toolsets]\n")
        monkeypatch.chdir(tmp_path)

        with pytest.raises(Exception) as exc_info:
            get_fixtures_registry()
        assert "No 'fixtures' configured" in str(exc_info.value)

    def test_raises_when_not_registry_instance(self, tmp_path, monkeypatch):
        """Raises error when imported object is not a FixtureRegistry."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.fastapi-toolsets]\nfixtures = "my_fixtures:registry"\n'
        )

        fixtures_file = tmp_path / "my_fixtures.py"
        fixtures_file.write_text("registry = 'not a registry'\n")

        monkeypatch.chdir(tmp_path)
        if str(tmp_path) not in sys.path:
            sys.path.insert(0, str(tmp_path))

        try:
            with pytest.raises(Exception) as exc_info:
                get_fixtures_registry()
            assert "must be a FixtureRegistry instance" in str(exc_info.value)
        finally:
            if str(tmp_path) in sys.path:
                sys.path.remove(str(tmp_path))
            if "my_fixtures" in sys.modules:
                del sys.modules["my_fixtures"]


class TestGetDbContext:
    """Tests for get_db_context function."""

    def test_raises_when_not_configured(self, tmp_path, monkeypatch):
        """Raises error when db_context not configured."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.fastapi-toolsets]\n")
        monkeypatch.chdir(tmp_path)

        with pytest.raises(Exception) as exc_info:
            get_db_context()
        assert "No 'db_context' configured" in str(exc_info.value)


class TestGetCustomCli:
    """Tests for get_custom_cli function."""

    def test_returns_none_when_not_configured(self, tmp_path, monkeypatch):
        """Returns None when custom_cli not configured."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.fastapi-toolsets]\n")
        monkeypatch.chdir(tmp_path)

        result = get_custom_cli()
        assert result is None

    def test_raises_when_not_typer_instance(self, tmp_path, monkeypatch):
        """Raises error when imported object is not a Typer instance."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[tool.fastapi-toolsets]\ncustom_cli = "my_cli:cli"\n')

        cli_file = tmp_path / "my_cli.py"
        cli_file.write_text("cli = 'not a typer'\n")

        monkeypatch.chdir(tmp_path)
        if str(tmp_path) not in sys.path:
            sys.path.insert(0, str(tmp_path))

        try:
            with pytest.raises(Exception) as exc_info:
                get_custom_cli()
            assert "must be a Typer instance" in str(exc_info.value)
        finally:
            if str(tmp_path) in sys.path:
                sys.path.remove(str(tmp_path))
            if "my_cli" in sys.modules:
                del sys.modules["my_cli"]


class TestCliApp:
    """Tests for CLI application."""

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

        from fastapi_toolsets.cli import app

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

        assert result.exit_code != 0


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

        from fastapi_toolsets.cli import app

        importlib.reload(app)

        result = runner.invoke(app.cli, ["--help"])

        assert result.exit_code == 0
        assert "fixtures" not in result.output


class TestCustomCliConfig:
    """Tests for custom CLI configuration."""

    def test_cli_with_custom_cli(self, tmp_path, monkeypatch):
        """CLI uses custom Typer instance when configured."""
        import typer

        # Create pyproject.toml with custom_cli config
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[tool.fastapi-toolsets]\ncustom_cli = "my_cli:cli"\n')

        # Create custom CLI module with its own Typer and commands
        cli_file = tmp_path / "my_cli.py"
        cli_file.write_text(
            "import typer\n"
            "\n"
            "cli = typer.Typer(name='my-app', help='My custom CLI')\n"
            "\n"
            "@cli.command()\n"
            "def hello():\n"
            '    print("Hello from custom CLI!")\n'
        )

        monkeypatch.chdir(tmp_path)

        # Add tmp_path to sys.path for imports
        if str(tmp_path) not in sys.path:
            sys.path.insert(0, str(tmp_path))

        # Remove my_cli from sys.modules if it was previously loaded
        if "my_cli" in sys.modules:
            del sys.modules["my_cli"]

        # Reload the CLI module to pick up new config
        import importlib

        from fastapi_toolsets.cli import app

        importlib.reload(app)

        try:
            # Verify custom CLI is used
            assert isinstance(app.cli, typer.Typer)

            result = runner.invoke(app.cli, ["--help"])
            assert result.exit_code == 0
            assert "My custom CLI" in result.output
            assert "hello" in result.output

            result = runner.invoke(app.cli, ["hello"])
            assert result.exit_code == 0
            assert "Hello from custom CLI!" in result.output
        finally:
            if str(tmp_path) in sys.path:
                sys.path.remove(str(tmp_path))
            if "my_cli" in sys.modules:
                del sys.modules["my_cli"]

    def test_custom_cli_with_fixtures(self, tmp_path, monkeypatch):
        """Custom CLI gets fixtures command added when configured."""
        # Create pyproject.toml with both custom_cli and fixtures
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            "[tool.fastapi-toolsets]\n"
            'custom_cli = "my_cli:cli"\n'
            'fixtures = "fixtures:registry"\n'
            'db_context = "db:get_session"\n'
        )

        # Create custom CLI module
        cli_file = tmp_path / "my_cli.py"
        cli_file.write_text(
            "import typer\n"
            "\n"
            "cli = typer.Typer(name='my-app', help='My custom CLI')\n"
            "\n"
            "@cli.command()\n"
            "def hello():\n"
            '    print("Hello!")\n'
        )

        # Create fixtures module
        fixtures_file = tmp_path / "fixtures.py"
        fixtures_file.write_text(
            "from fastapi_toolsets.fixtures import FixtureRegistry\n"
            "\n"
            "registry = FixtureRegistry()\n"
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

        if str(tmp_path) not in sys.path:
            sys.path.insert(0, str(tmp_path))

        for mod in ["my_cli", "fixtures", "db"]:
            if mod in sys.modules:
                del sys.modules[mod]

        import importlib

        from fastapi_toolsets.cli import app

        importlib.reload(app)

        try:
            result = runner.invoke(app.cli, ["--help"])
            assert result.exit_code == 0
            # Should have both custom command and fixtures
            assert "hello" in result.output
            assert "fixtures" in result.output
        finally:
            if str(tmp_path) in sys.path:
                sys.path.remove(str(tmp_path))
            for mod in ["my_cli", "fixtures", "db"]:
                if mod in sys.modules:
                    del sys.modules[mod]


class TestAsyncCommand:
    """Tests for async_command decorator."""

    def test_async_command_runs_coroutine(self):
        """async_command runs async function synchronously."""

        @async_command
        async def async_func(value: int) -> int:
            return value * 2

        result = async_func(21)
        assert result == 42

    def test_async_command_preserves_signature(self):
        """async_command preserves function signature."""

        @async_command
        async def async_func(name: str, count: int = 1) -> str:
            return f"{name} x {count}"

        result = async_func("test", count=3)
        assert result == "test x 3"

    def test_async_command_preserves_docstring(self):
        """async_command preserves function docstring."""

        @async_command
        async def async_func() -> None:
            """This is a docstring."""
            pass

        assert async_func.__doc__ == """This is a docstring."""

    def test_async_command_preserves_name(self):
        """async_command preserves function name."""

        @async_command
        async def my_async_function() -> None:
            pass

        assert my_async_function.__name__ == "my_async_function"
