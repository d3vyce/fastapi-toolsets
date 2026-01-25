"""Main CLI application."""

import importlib.util
import sys
from pathlib import Path
from typing import Annotated

import typer

from .commands import fixtures

app = typer.Typer(
    name="fastapi-utils",
    help="CLI utilities for FastAPI projects.",
    no_args_is_help=True,
)

# Register built-in commands
app.add_typer(fixtures.app, name="fixtures")


def register_command(command: typer.Typer, name: str) -> None:
    """Register a custom command group.

    Args:
        command: Typer app for the command group
        name: Name for the command group

    Example:
        # In your project's cli.py:
        import typer
        from fastapi_toolsets.cli import app, register_command

        my_commands = typer.Typer()

        @my_commands.command()
        def seed():
            '''Seed the database.'''
            ...

        register_command(my_commands, "db")
        # Now available as: fastapi-utils db seed
    """
    app.add_typer(command, name=name)


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to project config file (Python module with fixtures registry).",
            envvar="FASTAPI_TOOLSETS_CONFIG",
        ),
    ] = None,
) -> None:
    """FastAPI utilities CLI."""
    ctx.ensure_object(dict)

    if config:
        ctx.obj["config_path"] = config
        # Load the config module
        config_module = _load_module_from_path(config)
        ctx.obj["config_module"] = config_module


def _load_module_from_path(path: Path) -> object:
    """Load a Python module from a file path.

    Handles both absolute and relative imports by adding the config's
    parent directory to sys.path temporarily.
    """
    path = path.resolve()

    # Add the parent directory to sys.path to support relative imports
    parent_dir = str(path.parent.parent)  # Go up two levels (e.g., from app/cli_config.py to project root)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    # Also add immediate parent for direct module imports
    immediate_parent = str(path.parent)
    if immediate_parent not in sys.path:
        sys.path.insert(0, immediate_parent)

    spec = importlib.util.spec_from_file_location("config", path)
    if spec is None or spec.loader is None:
        raise typer.BadParameter(f"Cannot load module from {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["config"] = module
    spec.loader.exec_module(module)
    return module
