"""CLI utility functions."""

import asyncio
import functools
from collections.abc import Callable, Coroutine
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


def async_command(func: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, T]:
    """Decorator to run an async function as a sync CLI command.

    Example:
        @fixture_cli.command("load")
        @async_command
        async def load(ctx: typer.Context) -> None:
            async with get_db_context() as session:
                await load_fixtures(session, registry)
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        return asyncio.run(func(*args, **kwargs))

    return wrapper
