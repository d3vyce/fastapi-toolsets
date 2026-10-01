"""Tests for the CLI: pyproject discovery, configured imports and fixture commands."""

import importlib
import sys
from pathlib import Path

import pytest
import typer
import typer.rich_utils
from typer.testing import CliRunner

from fastapi_toolsets.cli import app
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


_FIXTURES_CONFIG = (
    'fixtures = "app_fixtures:registry"\ndb_context = "app_db:get_session"\n'
)
_CUSTOM_CLI_CONFIG = 'custom_cli = "app_cli:cli"\n'
_ALL_FIXTURES = ["roles", "users", "staging_only"]

_FIXTURES_MODULE = """
from fastapi_toolsets.fixtures import Context, FixtureRegistry

registry = FixtureRegistry()


@registry.register(contexts=[Context.BASE])
def roles():
    return [{"id": 1, "name": "admin"}, {"id": 2, "name": "user"}]


@registry.register(depends_on=["roles"], contexts=[Context.TESTING])
def users():
    return [{"id": 1, "name": "alice", "role_id": 1}]


@registry.register(contexts=["staging"])
def staging_only():
    return [{"id": 3, "name": "staging-user"}]
"""

_EMPTY_FIXTURES_MODULE = """
from fastapi_toolsets.fixtures import FixtureRegistry

registry = FixtureRegistry()
"""

_NO_ROWS_FIXTURES_MODULE = """
from fastapi_toolsets.fixtures import FixtureRegistry

registry = FixtureRegistry()


@registry.register
def roles():
    return []
"""

_DB_MODULE = """
from contextlib import asynccontextmanager

calls = []


@asynccontextmanager
async def get_session():
    calls.append("enter")
    yield None
    calls.append("exit")
"""

_CUSTOM_CLI_MODULE = """
import typer

cli = typer.Typer(name="my-app", help="My custom CLI")


@cli.command()
def hello():
    print("Hello from custom CLI!")
"""


@pytest.fixture(autouse=True)
def _plain_terminal(monkeypatch):
    """Keep typer from styling its output when CI sets GITHUB_ACTIONS or FORCE_COLOR."""
    monkeypatch.setattr(typer.rich_utils, "FORCE_TERMINAL", False)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """An empty project directory as cwd; what gets imported from it is forgotten."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    root = str(tmp_path.resolve())
    yield tmp_path
    for name, module in list(sys.modules.items()):
        if str(getattr(module, "__file__", None) or "").startswith(root):
            del sys.modules[name]


def _write(project: Path, config: str = "", **modules: str) -> None:
    """Write pyproject.toml with *config* under the tool section, plus *modules*."""
    project.joinpath("pyproject.toml").write_text(f"[tool.fastapi-toolsets]\n{config}")
    for name, source in modules.items():
        project.joinpath(f"{name}.py").write_text(source)


def _cli() -> typer.Typer:
    """The CLI as built from the current directory's pyproject.toml."""
    return importlib.reload(app).cli


@pytest.mark.usefixtures("project")
class TestPyproject:
    """pyproject.toml discovery and the [tool.fastapi-toolsets] section."""

    @pytest.mark.parametrize("subdir", ["", "src/app"], ids=["cwd", "nested"])
    def test_find_pyproject_walks_up_from_cwd_or_a_start_path(
        self, project, monkeypatch, subdir
    ):
        pyproject = project / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'test'\n")
        start = project / subdir
        start.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(start)

        assert find_pyproject() == pyproject
        assert find_pyproject(start) == pyproject

    def test_find_pyproject_returns_none_without_one(self):
        assert find_pyproject() is None

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            (
                '[tool.fastapi-toolsets]\nfixtures = "app:registry"\n',
                {"fixtures": "app:registry"},
            ),
            ("[project]\nname = 'test'\n", {}),
            ("invalid toml {{{", {}),
            (None, {}),
        ],
        ids=["tool-section", "no-tool-section", "invalid-toml", "no-file"],
    )
    def test_load_pyproject_returns_the_tool_section_or_nothing(
        self, project, content, expected
    ):
        pyproject = project / "pyproject.toml"
        if content is not None:
            pyproject.write_text(content)
            assert load_pyproject(pyproject) == expected

        assert load_pyproject() == expected


@pytest.mark.usefixtures("project")
class TestConfig:
    """Dotted imports and the values configured in pyproject.toml."""

    def test_import_from_string_resolves_the_attribute(self):
        imported = import_from_string("fastapi_toolsets.fixtures:FixtureRegistry")

        assert imported is FixtureRegistry

    @pytest.mark.parametrize(
        ("path", "message"),
        [
            (
                "fastapi_toolsets.fixtures.FixtureRegistry",
                "Expected format: 'module:attribute'",
            ),
            (
                "nonexistent.module:something",
                "Cannot import module 'nonexistent.module'",
            ),
            ("fastapi_toolsets.fixtures:Nope", "has no attribute 'Nope'"),
        ],
        ids=["no-colon", "unknown-module", "unknown-attribute"],
    )
    def test_import_from_string_errors(self, path, message):
        with pytest.raises(typer.BadParameter, match=message):
            import_from_string(path)

    def test_project_root_is_added_to_sys_path_once(self, project):
        _write(project, "", my_module="value = 'found'\n")
        root = str(project.resolve())
        assert root not in sys.path

        first = import_from_string("my_module:value")
        second = import_from_string("my_module:value")

        assert first == second == "found"
        assert sys.path[0] == root and sys.path.count(root) == 1

    def test_get_config_value(self, project):
        _write(project, 'fixtures = "app:registry"\n')

        assert get_config_value("fixtures") == "app:registry"
        assert get_config_value("db_context") is None
        assert get_custom_cli() is None
        with pytest.raises(typer.BadParameter, match="No 'db_context' configured"):
            get_config_value("db_context", required=True)

    @pytest.mark.parametrize(
        ("getter", "config", "message"),
        [
            (get_fixtures_registry, "", "No 'fixtures' configured"),
            (
                get_fixtures_registry,
                'fixtures = "fake:obj"\n',
                "must be a FixtureRegistry instance, got str",
            ),
            (get_db_context, "", "No 'db_context' configured"),
            (
                get_custom_cli,
                'custom_cli = "fake:obj"\n',
                "must be a Typer instance, got str",
            ),
        ],
        ids=[
            "fixtures-missing",
            "fixtures-wrong-type",
            "db-context-missing",
            "custom-cli-wrong-type",
        ],
    )
    def test_configured_imports_are_validated(self, project, getter, config, message):
        _write(project, config, fake="obj = 'not it'\n")

        with pytest.raises(typer.BadParameter, match=message):
            getter()

    def test_configured_imports_return_the_objects(self, project):
        _write(
            project,
            _FIXTURES_CONFIG + _CUSTOM_CLI_CONFIG,
            app_fixtures=_FIXTURES_MODULE,
            app_db=_DB_MODULE,
            app_cli=_CUSTOM_CLI_MODULE,
        )

        assert isinstance(get_fixtures_registry(), FixtureRegistry)
        assert get_db_context().__name__ == "get_session"
        assert isinstance(get_custom_cli(), typer.Typer)


@pytest.mark.usefixtures("project")
class TestApp:
    """The CLI is the default Typer or the configured one, with fixtures when set."""

    def test_default_cli_without_configuration(self):
        result = runner.invoke(_cli(), ["--help"])

        assert result.exit_code == 0
        assert "CLI utilities for FastAPI projects" in result.output
        assert "fixtures" not in result.output

    @pytest.mark.parametrize(
        "with_fixtures", [False, True], ids=["alone", "with-fixtures"]
    )
    def test_custom_cli_replaces_the_default(self, project, with_fixtures):
        config = _CUSTOM_CLI_CONFIG + (_FIXTURES_CONFIG if with_fixtures else "")
        _write(
            project,
            config,
            app_cli=_CUSTOM_CLI_MODULE,
            app_fixtures=_EMPTY_FIXTURES_MODULE,
            app_db=_DB_MODULE,
        )
        cli = _cli()

        shown = runner.invoke(cli, ["--help"])
        hello = runner.invoke(cli, ["hello"])

        assert shown.exit_code == 0 and "My custom CLI" in shown.output
        assert "hello" in shown.output
        assert ("fixtures" in shown.output) is with_fixtures
        assert hello.exit_code == 0 and "Hello from custom CLI!" in hello.output


class TestFixturesCommands:
    """``fixtures list`` and ``fixtures load``."""

    @pytest.fixture
    def cli(self, project):
        _write(
            project, _FIXTURES_CONFIG, app_fixtures=_FIXTURES_MODULE, app_db=_DB_MODULE
        )
        return _cli()

    @pytest.mark.parametrize(
        ("args", "listed"),
        [
            ([], _ALL_FIXTURES),
            (["--context", "base"], ["roles"]),
            (["-c", "testing"], ["roles", "users"]),
            (["--context", "staging"], ["roles", "staging_only"]),
        ],
        ids=["all", "base", "testing", "custom-context"],
    )
    def test_list_shows_the_context_fixtures_plus_base(self, cli, args, listed):
        result = runner.invoke(cli, ["fixtures", "list", *args])

        assert result.exit_code == 0
        assert [n for n in _ALL_FIXTURES if n in result.output] == listed
        assert f"Total: {len(listed)} fixture(s)" in result.output

    @pytest.mark.parametrize(
        ("args", "env", "strategy", "listed"),
        [
            (["base"], {}, "merge", ["roles: 2 dict(s)"]),
            ([], {}, "merge", ["roles: 2 dict(s)"]),
            (
                ["testing", "-s", "insert"],
                {},
                "insert",
                ["roles: 2 dict(s)", "users: 1 dict(s)"],
            ),
            (["staging"], {}, "merge", ["roles: 2 dict(s)", "staging_only: 1 dict(s)"]),
            (
                [],
                {"FIXTURES_CONTEXT": "staging"},
                "merge",
                ["roles: 2 dict(s)", "staging_only: 1 dict(s)"],
            ),
        ],
        ids=[
            "base",
            "default-context",
            "testing-insert",
            "custom-context",
            "context-from-env",
        ],
    )
    def test_load_dry_run_lists_what_would_be_loaded(
        self, cli, args, env, strategy, listed
    ):
        """Base fixtures always come along; the context may come from FIXTURES_CONTEXT."""
        result = runner.invoke(cli, ["fixtures", "load", *args, "--dry-run"], env=env)

        assert result.exit_code == 0
        assert f"Fixtures to load ({strategy} strategy):" in result.output
        lines = result.output.splitlines()
        assert [line.partition("  - ")[2] for line in lines if "  - " in line] == listed
        assert "[Dry run - no changes made]" in result.output

    def test_load_runs_the_fixtures_inside_the_db_context(self, project):
        _write(
            project,
            _FIXTURES_CONFIG,
            app_fixtures=_NO_ROWS_FIXTURES_MODULE,
            app_db=_DB_MODULE,
        )
        cli = _cli()

        result = runner.invoke(cli, ["fixtures", "load"])

        assert result.exit_code == 0
        assert "Loaded 0 record(s) successfully." in result.output
        assert sys.modules["app_db"].calls == ["enter", "exit"]

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (["list"], "No fixtures found."),
            (["list", "--context", "testing"], "No fixtures found."),
            (["load", "testing"], "No fixtures to load for the specified context(s)."),
        ],
        ids=["list", "list-context", "load"],
    )
    def test_empty_registry_has_nothing_to_list_or_load(self, project, args, message):
        _write(
            project,
            _FIXTURES_CONFIG,
            app_fixtures=_EMPTY_FIXTURES_MODULE,
            app_db=_DB_MODULE,
        )
        cli = _cli()

        result = runner.invoke(cli, ["fixtures", *args])

        assert result.exit_code == 0 and message in result.output

    def test_load_rejects_an_unknown_strategy(self, cli):
        result = runner.invoke(
            cli, ["fixtures", "load", "base", "--strategy", "invalid"]
        )

        assert result.exit_code == 2
        assert "Invalid value for '--strategy'" in result.output


class TestAsyncCommand:
    """async_command runs a coroutine function synchronously."""

    def test_runs_the_coroutine_and_keeps_the_metadata(self):
        @async_command
        async def multiply(value: int, *, times: int = 2) -> int:
            """Multiply it."""
            return value * times

        assert multiply(21) == 42 and multiply(2, times=5) == 10
        assert multiply.__name__ == "multiply" and multiply.__doc__ == "Multiply it."
