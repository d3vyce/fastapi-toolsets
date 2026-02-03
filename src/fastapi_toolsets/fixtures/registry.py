"""Fixture system with dependency management and context support."""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy.orm import DeclarativeBase

from .enum import Context

logger = logging.getLogger(__name__)


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

    def __init__(
        self,
        contexts: list[str | Context] | None = None,
    ) -> None:
        self._fixtures: dict[str, Fixture] = {}
        self._default_contexts: list[str] | None = (
            [c.value if isinstance(c, Context) else c for c in contexts]
            if contexts
            else None
        )

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
            if contexts is not None:
                fixture_contexts = [
                    c.value if isinstance(c, Context) else c for c in contexts
                ]
            elif self._default_contexts is not None:
                fixture_contexts = self._default_contexts
            else:
                fixture_contexts = [Context.BASE.value]

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

    def include_registry(self, registry: "FixtureRegistry") -> None:
        """Include another `FixtureRegistry` in the same current `FixtureRegistry`.

        Args:
            registry: The `FixtureRegistry` to include

        Raises:
            ValueError: If a fixture name already exists in the current registry

        Example:
            registry = FixtureRegistry()
            dev_registry = FixtureRegistry()

            @dev_registry.register
            def dev_data():
                return [...]

            registry.include_registry(registry=dev_registry)
        """
        for name, fixture in registry._fixtures.items():
            if name in self._fixtures:
                raise ValueError(
                    f"Fixture '{name}' already exists in the current registry"
                )
            self._fixtures[name] = fixture

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
