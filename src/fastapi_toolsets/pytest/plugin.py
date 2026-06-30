"""Pytest plugin for using FixtureRegistry fixtures in tests."""

from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from ..fixtures import FixtureRegistry, LoadStrategy
from ..fixtures.utils import _get_primary_key, _load_ordered, _refresh_loaded


def register_fixtures(
    registry: FixtureRegistry,
    namespace: dict[str, Any],
    *,
    prefix: str = "fixture_",
    session_fixture: str = "db_session",
    strategy: LoadStrategy = LoadStrategy.MERGE,
) -> list[str]:
    """Register pytest fixtures from a FixtureRegistry.

    Automatically creates pytest fixtures for each fixture in the registry.
    Dependencies are resolved via pytest fixture dependencies.

    Args:
        registry: The FixtureRegistry containing fixtures
        namespace: The module's globals() dict to add fixtures to
        prefix: Prefix for generated fixture names (default: "fixture_")
        session_fixture: Name of the db session fixture (default: "db_session")
        strategy: Loading strategy for fixtures (default: MERGE)

    Returns:
        List of created fixture names

    Example:
        ```python
        # conftest.py
        from app.fixtures import fixtures
        from fastapi_toolsets.pytest_plugin import register_fixtures

        register_fixtures(fixtures, globals())

        # Creates fixtures like:
        # - fixture_roles
        # - fixture_users (depends on fixture_roles if users depends on roles)
        # - fixture_posts (depends on fixture_users if posts depends on users)
        ```
    """
    created_fixtures: list[str] = []

    for fixture in registry.get_all():
        fixture_name = f"{prefix}{fixture.name}"

        # Build list of pytest fixture dependencies
        pytest_deps = [session_fixture]
        for dep in fixture.depends_on:
            pytest_deps.append(f"{prefix}{dep}")

        # Create the fixture function
        fixture_func = _create_fixture_function(
            registry=registry,
            fixture_name=fixture.name,
            dependencies=pytest_deps,
            strategy=strategy,
        )

        # Apply pytest.fixture decorator
        decorated = pytest.fixture(fixture_func)

        # Add to namespace
        namespace[fixture_name] = decorated
        created_fixtures.append(fixture_name)

    return created_fixtures


def _create_fixture_function(
    registry: FixtureRegistry,
    fixture_name: str,
    dependencies: list[str],
    strategy: LoadStrategy,
) -> Any:
    """Create a fixture function with the correct signature.

    The function signature must include all dependencies as parameters
    for pytest (and pytest-anyio's fixture chaining) to resolve them
    correctly — dynamic resolution via ``request.getfixturevalue`` deadlocks
    when called from inside an already-running async fixture.
    """
    fixture_def = registry.get(fixture_name)

    async def fixture_func(**kwargs: Any) -> Sequence[DeclarativeBase]:
        session: AsyncSession = kwargs[dependencies[0]]
        result = (await _load_ordered(session, registry, [fixture_name], strategy))[
            fixture_name
        ]

        if strategy is LoadStrategy.SKIP_EXISTING:
            # _load_ordered only returns newly-inserted rows for this
            # strategy (the CLI seeding contract). A test fixture should
            # still hand back the full, usable set including rows that
            # were already present, so top up with those.
            declared = list(fixture_def.func())
            result_pks = {_get_primary_key(r) for r in result}
            missing = [
                d
                for d in declared
                if (pk := _get_primary_key(d)) is not None and pk not in result_pks
            ]
            if missing:
                result = result + await _refresh_loaded(session, missing)

        return result

    # Update function signature to include dependencies
    # This is needed for pytest to inject the right fixtures
    params = ", ".join(dependencies)
    code = f"async def {fixture_name}_fixture({params}):\n    return await _impl({', '.join(f'{d}={d}' for d in dependencies)})"

    local_ns: dict[str, Any] = {"_impl": fixture_func}
    exec(code, local_ns)  # noqa: S102

    created_func = local_ns[f"{fixture_name}_fixture"]
    created_func.__doc__ = f"Load {fixture_name} fixture data."

    return created_func
