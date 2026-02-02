"""Fixture management commands."""

import asyncio
from typing import Annotated

import typer

from ...fixtures import Context, LoadStrategy, load_fixtures_by_context
from ..config import CliConfig

fixture_cli = typer.Typer(
    name="fixtures",
    help="Manage database fixtures.",
    no_args_is_help=True,
)


def _get_config(ctx: typer.Context) -> CliConfig:
    """Get CLI config from context."""
    return ctx.obj["config"]


@fixture_cli.command("list")
def list_fixtures(
    ctx: typer.Context,
    context: Annotated[
        str | None,
        typer.Option(
            "--context",
            "-c",
            help="Filter by context (base, production, development, testing).",
        ),
    ] = None,
) -> None:
    """List all registered fixtures."""
    config = _get_config(ctx)
    registry = config.get_fixtures_registry()
    fixtures = registry.get_by_context(context) if context else registry.get_all()

    if not fixtures:
        typer.echo("No fixtures found.")
        return

    typer.echo(f"\n{'Name':<30} {'Contexts':<30} {'Dependencies'}")
    typer.echo("-" * 80)

    for fixture in fixtures:
        contexts = ", ".join(fixture.contexts)
        deps = ", ".join(fixture.depends_on) if fixture.depends_on else "-"
        typer.echo(f"{fixture.name:<30} {contexts:<30} {deps}")

    typer.echo(f"\nTotal: {len(fixtures)} fixture(s)")


@fixture_cli.command("graph")
def show_graph(
    ctx: typer.Context,
    fixture_name: Annotated[
        str | None,
        typer.Argument(help="Show dependencies for a specific fixture."),
    ] = None,
) -> None:
    """Show fixture dependency graph."""
    config = _get_config(ctx)
    registry = config.get_fixtures_registry()

    if fixture_name:
        try:
            order = registry.resolve_dependencies(fixture_name)
        except KeyError:
            typer.echo(f"Fixture '{fixture_name}' not found.", err=True)
            raise typer.Exit(1)

        typer.echo(f"\nDependency chain for '{fixture_name}':\n")
        for i, name in enumerate(order):
            indent = "  " * i
            arrow = "└─> " if i > 0 else ""
            typer.echo(f"{indent}{arrow}{name}")
    else:
        fixtures = registry.get_all()
        typer.echo("\nFixture Dependency Graph:\n")
        for fixture in fixtures:
            deps = (
                f" -> [{', '.join(fixture.depends_on)}]" if fixture.depends_on else ""
            )
            typer.echo(f"  {fixture.name}{deps}")


@fixture_cli.command("load")
def load(
    ctx: typer.Context,
    contexts: Annotated[
        list[str] | None,
        typer.Argument(
            help="Contexts to load (base, production, development, testing)."
        ),
    ] = None,
    strategy: Annotated[
        str,
        typer.Option(
            "--strategy", "-s", help="Load strategy: merge, insert, skip_existing."
        ),
    ] = "merge",
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", "-n", help="Show what would be loaded without loading."
        ),
    ] = False,
) -> None:
    """Load fixtures into the database."""
    config = _get_config(ctx)
    registry = config.get_fixtures_registry()
    get_db_context = config.get_db_context()

    context_list = contexts if contexts else [Context.BASE]

    try:
        load_strategy = LoadStrategy(strategy)
    except ValueError:
        typer.echo(
            f"Invalid strategy: {strategy}. Use: merge, insert, skip_existing", err=True
        )
        raise typer.Exit(1)

    ordered = registry.resolve_context_dependencies(*context_list)

    if not ordered:
        typer.echo("No fixtures to load for the specified context(s).")
        return

    typer.echo(f"\nFixtures to load ({load_strategy.value} strategy):")
    for name in ordered:
        fixture = registry.get(name)
        instances = list(fixture.func())
        model_name = type(instances[0]).__name__ if instances else "?"
        typer.echo(f"  - {name}: {len(instances)} {model_name}(s)")

    if dry_run:
        typer.echo("\n[Dry run - no changes made]")
        return

    typer.echo("\nLoading...")

    async def do_load():
        async with get_db_context() as session:
            return await load_fixtures_by_context(
                session, registry, *context_list, strategy=load_strategy
            )

    result = asyncio.run(do_load())
    total = sum(len(items) for items in result.values())
    typer.echo(f"\nLoaded {total} record(s) successfully.")


@fixture_cli.command("show")
def show_fixture(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Fixture name to show.")],
) -> None:
    """Show details of a specific fixture."""
    config = _get_config(ctx)
    registry = config.get_fixtures_registry()

    try:
        fixture = registry.get(name)
    except KeyError:
        typer.echo(f"Fixture '{name}' not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"\nFixture: {fixture.name}")
    typer.echo(f"Contexts: {', '.join(fixture.contexts)}")
    typer.echo(
        f"Dependencies: {', '.join(fixture.depends_on) if fixture.depends_on else 'None'}"
    )

    instances = list(fixture.func())
    if instances:
        model_name = type(instances[0]).__name__
        typer.echo(f"\nInstances ({len(instances)} {model_name}):")
        for instance in instances[:10]:
            typer.echo(f"  - {instance!r}")
        if len(instances) > 10:
            typer.echo(f"  ... and {len(instances) - 10} more")
    else:
        typer.echo("\nNo instances (empty fixture)")
