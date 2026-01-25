"""Pytest plugin for using FixtureRegistry fixtures in tests.

This module provides utilities to automatically generate pytest fixtures
from your FixtureRegistry, with proper dependency resolution.

Example:
    # conftest.py
    import pytest
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.fixtures import fixtures  # Your FixtureRegistry
    from app.models import Base
    from fastapi_toolsets.pytest_plugin import register_fixtures

    DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/test_db"

    @pytest.fixture
    async def engine():
        engine = create_async_engine(DATABASE_URL)
        yield engine
        await engine.dispose()

    @pytest.fixture
    async def db_session(engine):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        session = session_factory()

        try:
            yield session
        finally:
            await session.close()
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)

    # Automatically generate pytest fixtures from registry
    # Creates: fixture_roles, fixture_users, fixture_posts, etc.
    register_fixtures(fixtures, globals())

Usage in tests:
    # test_users.py
    async def test_user_count(db_session, fixture_users):
        # fixture_users automatically loads fixture_roles first (if dependency)
        # and returns the list of User models
        assert len(fixture_users) > 0

    async def test_user_role(db_session, fixture_users):
        user = fixture_users[0]
        assert user.role_id is not None
"""

from collections.abc import Callable, Sequence
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from ..db import get_transaction
from .fixtures import FixtureRegistry, LoadStrategy


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
        # conftest.py
        from app.fixtures import fixtures
        from fastapi_toolsets.pytest_plugin import register_fixtures

        register_fixtures(fixtures, globals())

        # Creates fixtures like:
        # - fixture_roles
        # - fixture_users (depends on fixture_roles if users depends on roles)
        # - fixture_posts (depends on fixture_users if posts depends on users)
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
) -> Callable[..., Any]:
    """Create a fixture function with the correct signature.

    The function signature must include all dependencies as parameters
    for pytest to resolve them correctly.
    """
    # Get the fixture definition
    fixture_def = registry.get(fixture_name)

    # Build the function dynamically with correct parameters
    # We need the session as first param, then all dependencies
    async def fixture_func(**kwargs: Any) -> Sequence[DeclarativeBase]:
        # Get session from kwargs (first dependency)
        session: AsyncSession = kwargs[dependencies[0]]

        # Load the fixture data
        instances = list(fixture_def.func())

        if not instances:
            return []

        loaded: list[DeclarativeBase] = []

        async with get_transaction(session):
            for instance in instances:
                if strategy == LoadStrategy.INSERT:
                    session.add(instance)
                    loaded.append(instance)
                elif strategy == LoadStrategy.MERGE:
                    merged = await session.merge(instance)
                    loaded.append(merged)
                elif strategy == LoadStrategy.SKIP_EXISTING:
                    pk = _get_primary_key(instance)
                    if pk is not None:
                        existing = await session.get(type(instance), pk)
                        if existing is None:
                            session.add(instance)
                            loaded.append(instance)
                        else:
                            loaded.append(existing)
                    else:
                        session.add(instance)
                        loaded.append(instance)

        return loaded

    # Update function signature to include dependencies
    # This is needed for pytest to inject the right fixtures
    params = ", ".join(dependencies)
    code = f"async def {fixture_name}_fixture({params}):\n    return await _impl({', '.join(f'{d}={d}' for d in dependencies)})"

    local_ns: dict[str, Any] = {"_impl": fixture_func}
    exec(code, local_ns)  # noqa: S102

    created_func = local_ns[f"{fixture_name}_fixture"]
    created_func.__doc__ = f"Load {fixture_name} fixture data."

    return created_func


def _get_primary_key(instance: DeclarativeBase) -> Any | None:
    """Get the primary key value of a model instance."""
    mapper = instance.__class__.__mapper__
    pk_cols = mapper.primary_key

    if len(pk_cols) == 1:
        return getattr(instance, pk_cols[0].name, None)

    pk_values = tuple(getattr(instance, col.name, None) for col in pk_cols)
    if all(v is not None for v in pk_values):
        return pk_values
    return None
