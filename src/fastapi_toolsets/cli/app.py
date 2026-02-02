"""Main CLI application."""

import typer

from .config import load_config

cli = typer.Typer(
    name="manager",
    help="CLI utilities for FastAPI projects.",
    no_args_is_help=True,
)

_config = load_config()

if _config.fixtures:
    from .commands.fixtures import fixture_cli

    cli.add_typer(fixture_cli, name="fixtures")


@cli.callback()
def main(ctx: typer.Context) -> None:
    """FastAPI utilities CLI."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = _config
