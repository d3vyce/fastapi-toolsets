"""Fixture system with dependency management and context support."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast

from sqlalchemy.orm import DeclarativeBase

from ..logger import get_logger
from .enum import Context

logger = get_logger()


def _normalize_contexts(
    contexts: list[str | Enum] | tuple[str | Enum, ...],
) -> list[str]:
    """Convert a sequence of any Enum subclass and/or plain strings to a list of strings."""
    return [c.value if isinstance(c, Enum) else c for c in contexts]


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
        ```python
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
        ```

    Fixtures with the same name may be registered for **different** contexts.
    When multiple contexts are loaded together, their instances are merged:

        ```python
        @fixtures.register(contexts=[Context.BASE])
        def users():
            return [User(id=1, username="admin")]

        @fixtures.register(contexts=[Context.TESTING])
        def users():
            return [User(id=2, username="tester")]
        # load_fixtures_by_context(..., Context.BASE, Context.TESTING)
        # → loads both User(admin) and User(tester) under the "users" name
        ```
    """

    def __init__(
        self,
        contexts: list[str | Enum] | None = None,
    ) -> None:
        self._fixtures: dict[str, list[Fixture]] = {}
        self._default_contexts: list[str] | None = (
            _normalize_contexts(contexts) if contexts else None
        )

    def _validate_no_context_overlap(self, name: str, new_contexts: list[str]) -> None:
        """Raise ``ValueError`` if any existing variant for *name* overlaps."""
        existing_variants = self._fixtures.get(name, [])
        new_set = set(new_contexts)
        for variant in existing_variants:
            if set(variant.contexts) & new_set:
                raise ValueError(
                    f"Fixture '{name}' already exists in the current registry "
                    f"with overlapping contexts. Use distinct context sets for "
                    f"each variant of the same fixture name."
                )

    def register(
        self,
        func: Callable[[], Sequence[DeclarativeBase]] | None = None,
        *,
        name: str | None = None,
        depends_on: list[str] | None = None,
        contexts: list[str | Enum] | None = None,
    ) -> Callable[..., Any]:
        """Register a fixture function.

        Can be used as a decorator with or without arguments.

        Args:
            func: Fixture function returning list of model instances
            name: Fixture name (defaults to function name)
            depends_on: List of fixture names this depends on
            contexts: List of contexts this fixture belongs to.  Both
                :class:`Context` enum values and plain strings are accepted.

        Example:
            ```python
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
                fixture_contexts = _normalize_contexts(contexts)
            elif self._default_contexts is not None:
                fixture_contexts = self._default_contexts
            else:
                fixture_contexts = [Context.BASE.value]

            self._validate_no_context_overlap(fixture_name, fixture_contexts)
            self._fixtures.setdefault(fixture_name, []).append(
                Fixture(
                    name=fixture_name,
                    func=fn,
                    depends_on=depends_on or [],
                    contexts=fixture_contexts,
                )
            )
            return fn

        if func is not None:
            return decorator(func)
        return decorator

    def include_registry(self, registry: "FixtureRegistry") -> None:
        """Include another `FixtureRegistry` in the same current `FixtureRegistry`.

        Fixtures with the same name are allowed as long as their context sets
        do not overlap.  Conflicting contexts raise :class:`ValueError`.

        Args:
            registry: The `FixtureRegistry` to include

        Raises:
            ValueError: If a fixture name already exists with overlapping contexts

        Example:
            ```python
            registry = FixtureRegistry()
            dev_registry = FixtureRegistry()

            @dev_registry.register
            def dev_data():
                return [...]

            registry.include_registry(registry=dev_registry)
            ```
        """
        for name, variants in registry._fixtures.items():
            for fixture in variants:
                self._validate_no_context_overlap(name, fixture.contexts)
                self._fixtures.setdefault(name, []).append(fixture)

    def get(self, name: str) -> Fixture:
        """Get a fixture by name.

        Raises:
            KeyError: If no fixture with *name* is registered.
            ValueError: If the fixture has multiple context variants — use
                :meth:`get_variants` in that case.
        """
        if name not in self._fixtures:
            raise KeyError(f"Fixture '{name}' not found")
        variants = self._fixtures[name]
        if len(variants) > 1:
            raise ValueError(
                f"Fixture '{name}' has {len(variants)} context variants. "
                f"Use get_variants('{name}') to retrieve them."
            )
        return variants[0]

    def get_variants(self, name: str, *contexts: str | Enum) -> list[Fixture]:
        """Return all registered variants for *name*, optionally filtered by context.

        Args:
            name: Fixture name.
            *contexts: If given, only return variants whose context set
                intersects with these values.  Both :class:`Context` enum
                values and plain strings are accepted.

        Returns:
            List of matching :class:`Fixture` objects (may be empty when a
            context filter is applied and nothing matches).

        Raises:
            KeyError: If no fixture with *name* is registered.
        """
        if name not in self._fixtures:
            raise KeyError(f"Fixture '{name}' not found")
        variants = self._fixtures[name]
        if not contexts:
            return list(variants)
        context_values = set(_normalize_contexts(contexts))
        return [v for v in variants if set(v.contexts) & context_values]

    def get_all(self) -> list[Fixture]:
        """Get all registered fixtures (all variants of all names)."""
        return [f for variants in self._fixtures.values() for f in variants]

    def get_by_context(self, *contexts: str | Enum) -> list[Fixture]:
        """Get fixtures for specific contexts."""
        context_values = set(_normalize_contexts(contexts))
        return [
            f
            for variants in self._fixtures.values()
            for f in variants
            if set(f.contexts) & context_values
        ]

    def resolve_dependencies(self, *names: str) -> list[str]:
        """Resolve fixture dependencies in topological order.

        When a fixture name has multiple context variants, the union of all
        variants' ``depends_on`` lists is used.

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
            variants = self._fixtures.get(name)
            if variants is None:
                raise KeyError(f"Fixture '{name}' not found")

            # Union of depends_on across all variants, preserving first-seen order.
            seen_deps: set[str] = set()
            all_deps: list[str] = []
            for variant in variants:
                for dep in variant.depends_on:
                    if dep not in seen_deps:
                        all_deps.append(dep)
                        seen_deps.add(dep)

            for dep in all_deps:
                visit(dep)

            visiting.remove(name)
            resolved.append(name)
            seen.add(name)

        for name in names:
            visit(name)

        return resolved

    def resolve_context_dependencies(self, *contexts: str | Enum) -> list[str]:
        """Resolve all fixtures for contexts with dependencies.

        Args:
            *contexts: Contexts to load

        Returns:
            List of fixture names in load order
        """
        context_fixtures = self.get_by_context(*contexts)
        # Deduplicate names while preserving first-seen order (a name can
        # appear multiple times if it has variants in different contexts).
        names = list(dict.fromkeys(f.name for f in context_fixtures))

        all_deps: set[str] = set()
        for name in names:
            deps = self.resolve_dependencies(name)
            all_deps.update(deps)

        return self.resolve_dependencies(*all_deps)
