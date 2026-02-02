"""CLI configuration."""

import importlib
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import typer


@dataclass
class CliConfig:
    """CLI configuration loaded from pyproject.toml."""

    fixtures: str | None = None
    db_context: str | None = None

    def get_fixtures_registry(self):
        """Import and return the fixtures registry."""
        from ..fixtures import FixtureRegistry

        if not self.fixtures:
            raise typer.BadParameter(
                "No fixtures registry configured. "
                "Add 'fixtures' to [tool.fastapi-toolsets] in pyproject.toml."
            )

        registry = _import_from_string(self.fixtures)

        if not isinstance(registry, FixtureRegistry):
            raise typer.BadParameter(
                f"'fixtures' must be a FixtureRegistry instance, got {type(registry).__name__}"
            )

        return registry

    def get_db_context(self):
        """Import and return the db_context function."""
        if not self.db_context:
            raise typer.BadParameter(
                "No db_context configured. "
                "Add 'db_context' to [tool.fastapi-toolsets] in pyproject.toml."
            )
        return _import_from_string(self.db_context)


def _import_from_string(import_path: str):
    """Import an object from a string path like 'module.submodule:attribute'."""
    if ":" not in import_path:
        raise typer.BadParameter(
            f"Invalid import path '{import_path}'. Expected format: 'module:attribute'"
        )

    module_path, attr_name = import_path.rsplit(":", 1)

    # Add cwd to sys.path for local imports
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise typer.BadParameter(f"Cannot import module '{module_path}': {e}")

    if not hasattr(module, attr_name):
        raise typer.BadParameter(
            f"Module '{module_path}' has no attribute '{attr_name}'"
        )

    return getattr(module, attr_name)


def load_config() -> CliConfig:
    """Load CLI configuration from pyproject.toml."""
    pyproject_path = Path.cwd() / "pyproject.toml"

    if not pyproject_path.exists():
        return CliConfig()

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        tool_config = data.get("tool", {}).get("fastapi-toolsets", {})
        return CliConfig(
            fixtures=tool_config.get("fixtures"),
            db_context=tool_config.get("db_context"),
        )
    except Exception:
        return CliConfig()
