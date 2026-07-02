"""Fixture management commands."""

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ...fixtures import Context, LoadStrategy
from ...logger import get_logger
from ..config import get_db_context, get_fixtures_registry
from ..utils import async_command

fixture_cli = typer.Typer(
    name="fixtures",
    help="Manage database fixtures.",
    no_args_is_help=True,
)
console = Console()
logger = get_logger()


@fixture_cli.command("list")
def list_fixtures(
    ctx: typer.Context,
    context: Annotated[
        Context | None,
        typer.Option(
            "--context",
            "-c",
            help="Filter by context.",
        ),
    ] = None,
) -> None:
    """List all registered fixtures."""
    registry = get_fixtures_registry()
    fixtures = registry.get_by_context(context) if context else registry.get_all()

    if not fixtures:
        logger.info("No fixtures found.")
        return

    table = Table("Name", "Contexts", "Dependencies")

    for fixture in fixtures:
        contexts = ", ".join(fixture.contexts)
        deps = ", ".join(fixture.depends_on) if fixture.depends_on else "-"
        table.add_row(fixture.name, contexts, deps)

    console.print(table)
    logger.info("Total: %d fixture(s)", len(fixtures))


@fixture_cli.command("load")
@async_command
async def load(
    ctx: typer.Context,
    contexts: Annotated[
        list[Context] | None,
        typer.Argument(help="Contexts to load."),
    ] = None,
    strategy: Annotated[
        LoadStrategy,
        typer.Option("--strategy", "-s", help="Load strategy."),
    ] = LoadStrategy.MERGE,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", "-n", help="Show what would be loaded without loading."
        ),
    ] = False,
) -> None:
    """Load fixtures into the database."""
    from ...fixtures import load_fixtures_by_context

    registry = get_fixtures_registry()
    db_context = get_db_context()

    context_list = contexts or [Context.BASE]

    ordered = registry.resolve_context_dependencies(*context_list)

    if not ordered:
        logger.info("No fixtures to load for the specified context(s).")
        return

    if dry_run:
        logger.info("Fixtures to load (%s strategy):", strategy.value)
        for name in ordered:
            variants = registry.get_load_variants(name, *context_list)
            instances = [inst for v in variants for inst in v.func()]
            model_name = type(instances[0]).__name__ if instances else "?"
            logger.info("  - %s: %d %s(s)", name, len(instances), model_name)
        logger.info("[Dry run - no changes made]")
        return

    async with db_context() as session:
        result = await load_fixtures_by_context(
            session, registry, *context_list, strategy=strategy
        )

    total = sum(len(items) for items in result.values())
    logger.info("Loaded %d record(s) successfully.", total)
