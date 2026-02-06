"""Main CLI application."""

import typer

from ..logger import configure_logging
from .config import get_custom_cli
from .pyproject import load_pyproject

# Use custom CLI if configured, otherwise create default one
_custom_cli = get_custom_cli()

if _custom_cli is not None:
    cli = _custom_cli
else:
    cli = typer.Typer(
        name="manager",
        help="CLI utilities for FastAPI projects.",
        no_args_is_help=True,
    )

_config = load_pyproject()
if _config.get("fixtures") and _config.get("db_context"):
    from .commands.fixtures import fixture_cli

    cli.add_typer(fixture_cli, name="fixtures")


@cli.callback()
def main(ctx: typer.Context) -> None:
    """FastAPI utilities CLI."""
    configure_logging()
    ctx.ensure_object(dict)
