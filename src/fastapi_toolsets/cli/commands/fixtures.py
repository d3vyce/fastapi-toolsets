"""Fixture management commands."""

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ...fixtures import Context, LoadStrategy, load_fixtures_by_context
from ..config import get_db_context, get_fixtures_registry
from ..utils import async_command

fixture_cli = typer.Typer(
    name="fixtures",
    help="Manage database fixtures.",
    no_args_is_help=True,
)
console = Console()


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
    fixtures = registry.get_by_context(context.value) if context else registry.get_all()

    if not fixtures:
        print("No fixtures found.")
        return

    table = Table("Name", "Contexts", "Dependencies")

    for fixture in fixtures:
        contexts = ", ".join(fixture.contexts)
        deps = ", ".join(fixture.depends_on) if fixture.depends_on else "-"
        table.add_row(fixture.name, contexts, deps)

    console.print(table)
    print(f"\nTotal: {len(fixtures)} fixture(s)")


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
    registry = get_fixtures_registry()
    db_context = get_db_context()

    context_list = [c.value for c in contexts] if contexts else [Context.BASE]

    ordered = registry.resolve_context_dependencies(*context_list)

    if not ordered:
        print("No fixtures to load for the specified context(s).")
        return

    print(f"\nFixtures to load ({strategy.value} strategy):")
    for name in ordered:
        fixture = registry.get(name)
        instances = list(fixture.func())
        model_name = type(instances[0]).__name__ if instances else "?"
        print(f"  - {name}: {len(instances)} {model_name}(s)")

    if dry_run:
        print("\n[Dry run - no changes made]")
        return

    async with db_context() as session:
        result = await load_fixtures_by_context(
            session, registry, *context_list, strategy=strategy
        )

    total = sum(len(items) for items in result.values())
    print(f"\nLoaded {total} record(s) successfully.")
