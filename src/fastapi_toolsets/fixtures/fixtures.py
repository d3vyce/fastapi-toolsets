"""Fixture system with dependency management and context support."""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from ..db import get_transaction

logger = logging.getLogger(__name__)


class LoadStrategy(str, Enum):
    """Strategy for loading fixtures into the database."""

    INSERT = "insert"
    """Insert new records. Fails if record already exists."""

    MERGE = "merge"
    """Insert or update based on primary key (SQLAlchemy merge)."""

    SKIP_EXISTING = "skip_existing"
    """Insert only if record doesn't exist (based on primary key)."""


class Context(str, Enum):
    """Predefined fixture contexts."""

    BASE = "base"
    """Base fixtures loaded in all environments."""

    PRODUCTION = "production"
    """Production-only fixtures."""

    DEVELOPMENT = "development"
    """Development fixtures."""

    TESTING = "testing"
    """Test fixtures."""


@dataclass
class Fixture:
    """A fixture definition with metadata."""

    name: str
    func: Callable[[], Sequence[DeclarativeBase]]
    depends_on: list[str] = field(default_factory=list)
    contexts: list[str] = field(default_factory=lambda: [Context.BASE])


class FixtureRegistry:
    """Registry for managing fixtures with dependencies.

    Example:
        from fastapi_toolsets.fixtures import FixtureRegistry, Context

        fixtures = FixtureRegistry()

        @fixtures.register
        def roles():
            return [
                Role(id=1, name="admin"),
                Role(id=2, name="user"),
            ]

        @fixtures.register(depends_on=["roles"])
        def users():
            return [
                User(id=1, username="admin", role_id=1),
            ]

        @fixtures.register(depends_on=["users"], contexts=[Context.TESTING])
        def test_data():
            return [
                Post(id=1, title="Test", user_id=1),
            ]
    """

    def __init__(self) -> None:
        self._fixtures: dict[str, Fixture] = {}

    def register(
        self,
        func: Callable[[], Sequence[DeclarativeBase]] | None = None,
        *,
        name: str | None = None,
        depends_on: list[str] | None = None,
        contexts: list[str | Context] | None = None,
    ) -> Callable[..., Any]:
        """Register a fixture function.

        Can be used as a decorator with or without arguments.

        Args:
            func: Fixture function returning list of model instances
            name: Fixture name (defaults to function name)
            depends_on: List of fixture names this depends on
            contexts: List of contexts this fixture belongs to

        Example:
            @fixtures.register
            def roles():
                return [Role(id=1, name="admin")]

            @fixtures.register(depends_on=["roles"], contexts=[Context.TESTING])
            def test_users():
                return [User(id=1, username="test", role_id=1)]
        """

        def decorator(
            fn: Callable[[], Sequence[DeclarativeBase]],
        ) -> Callable[[], Sequence[DeclarativeBase]]:
            fixture_name = name or cast(Any, fn).__name__
            fixture_contexts = [
                c.value if isinstance(c, Context) else c
                for c in (contexts or [Context.BASE])
            ]

            self._fixtures[fixture_name] = Fixture(
                name=fixture_name,
                func=fn,
                depends_on=depends_on or [],
                contexts=fixture_contexts,
            )
            return fn

        if func is not None:
            return decorator(func)
        return decorator

    def get(self, name: str) -> Fixture:
        """Get a fixture by name."""
        if name not in self._fixtures:
            raise KeyError(f"Fixture '{name}' not found")
        return self._fixtures[name]

    def get_all(self) -> list[Fixture]:
        """Get all registered fixtures."""
        return list(self._fixtures.values())

    def get_by_context(self, *contexts: str | Context) -> list[Fixture]:
        """Get fixtures for specific contexts."""
        context_values = {c.value if isinstance(c, Context) else c for c in contexts}
        return [f for f in self._fixtures.values() if set(f.contexts) & context_values]

    def resolve_dependencies(self, *names: str) -> list[str]:
        """Resolve fixture dependencies in topological order.

        Args:
            *names: Fixture names to resolve

        Returns:
            List of fixture names in load order (dependencies first)

        Raises:
            KeyError: If a fixture is not found
            ValueError: If circular dependency detected
        """
        resolved: list[str] = []
        seen: set[str] = set()
        visiting: set[str] = set()

        def visit(name: str) -> None:
            if name in resolved:
                return
            if name in visiting:
                raise ValueError(f"Circular dependency detected: {name}")

            visiting.add(name)
            fixture = self.get(name)

            for dep in fixture.depends_on:
                visit(dep)

            visiting.remove(name)
            resolved.append(name)
            seen.add(name)

        for name in names:
            visit(name)

        return resolved

    def resolve_context_dependencies(self, *contexts: str | Context) -> list[str]:
        """Resolve all fixtures for contexts with dependencies.

        Args:
            *contexts: Contexts to load

        Returns:
            List of fixture names in load order
        """
        context_fixtures = self.get_by_context(*contexts)
        names = [f.name for f in context_fixtures]

        all_deps: set[str] = set()
        for name in names:
            deps = self.resolve_dependencies(name)
            all_deps.update(deps)

        return self.resolve_dependencies(*all_deps)


async def load_fixtures(
    session: AsyncSession,
    registry: FixtureRegistry,
    *names: str,
    strategy: LoadStrategy = LoadStrategy.MERGE,
) -> dict[str, list[DeclarativeBase]]:
    """Load specific fixtures by name with dependencies.

    Args:
        session: Database session
        registry: Fixture registry
        *names: Fixture names to load (dependencies auto-resolved)
        strategy: How to handle existing records

    Returns:
        Dict mapping fixture names to loaded instances

    Example:
        # Loads 'roles' first (dependency), then 'users'
        result = await load_fixtures(session, fixtures, "users")
        print(result["users"])  # [User(...), ...]
    """
    ordered = registry.resolve_dependencies(*names)
    return await _load_ordered(session, registry, ordered, strategy)


async def load_fixtures_by_context(
    session: AsyncSession,
    registry: FixtureRegistry,
    *contexts: str | Context,
    strategy: LoadStrategy = LoadStrategy.MERGE,
) -> dict[str, list[DeclarativeBase]]:
    """Load all fixtures for specific contexts.

    Args:
        session: Database session
        registry: Fixture registry
        *contexts: Contexts to load (e.g., Context.BASE, Context.TESTING)
        strategy: How to handle existing records

    Returns:
        Dict mapping fixture names to loaded instances

    Example:
        # Load base + testing fixtures
        await load_fixtures_by_context(
            session, fixtures,
            Context.BASE, Context.TESTING
        )
    """
    ordered = registry.resolve_context_dependencies(*contexts)
    return await _load_ordered(session, registry, ordered, strategy)


async def _load_ordered(
    session: AsyncSession,
    registry: FixtureRegistry,
    ordered_names: list[str],
    strategy: LoadStrategy,
) -> dict[str, list[DeclarativeBase]]:
    """Load fixtures in order."""
    results: dict[str, list[DeclarativeBase]] = {}

    for name in ordered_names:
        fixture = registry.get(name)
        instances = list(fixture.func())

        if not instances:
            results[name] = []
            continue

        model_name = type(instances[0]).__name__
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
                        session.add(instance)
                        loaded.append(instance)

        results[name] = loaded
        logger.info(f"Loaded fixture '{name}': {len(loaded)} {model_name}(s)")

    return results


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
