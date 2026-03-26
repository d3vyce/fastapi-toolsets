"""Fixture loading utilities for database seeding."""

from collections.abc import Callable, Sequence
from enum import Enum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from ..db import get_transaction
from ..logger import get_logger
from ..types import ModelType
from .enum import LoadStrategy
from .registry import FixtureRegistry, _normalize_contexts

logger = get_logger()


async def _load_ordered(
    session: AsyncSession,
    registry: FixtureRegistry,
    ordered_names: list[str],
    strategy: LoadStrategy,
    contexts: tuple[str, ...] | None = None,
) -> dict[str, list[DeclarativeBase]]:
    """Load fixtures in order.

    When *contexts* is provided only variants whose context set intersects with
    *contexts* are called for each name; their instances are concatenated.
    When *contexts* is ``None`` all variants of each name are loaded.
    """
    results: dict[str, list[DeclarativeBase]] = {}

    for name in ordered_names:
        variants = (
            registry.get_variants(name, *contexts)
            if contexts is not None
            else registry.get_variants(name)
        )

        # Cross-context dependency fallback: if we're loading by context but
        # no variant matches (e.g. a "base"-only fixture required by a
        # "testing" fixture), load all available variants so the dependency
        # is satisfied.
        if contexts is not None and not variants:
            variants = registry.get_variants(name)

        if not variants:
            results[name] = []
            continue

        instances = [inst for v in variants for inst in v.func()]

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

                else:  # LoadStrategy.SKIP_EXISTING
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


def get_obj_by_attr(
    fixtures: Callable[[], Sequence[ModelType]], attr_name: str, value: Any
) -> ModelType:
    """Get a SQLAlchemy model instance by matching an attribute value.

    Args:
        fixtures: A fixture function registered via ``@registry.register``
            that returns a sequence of SQLAlchemy model instances.
        attr_name: Name of the attribute to match against.
        value: Value to match.

    Returns:
        The first model instance where the attribute matches the given value.

    Raises:
        StopIteration: If no matching object is found in the fixture group.
    """
    try:
        return next(obj for obj in fixtures() if getattr(obj, attr_name) == value)
    except StopIteration:
        raise StopIteration(
            f"No object with {attr_name}={value} found in fixture '{getattr(fixtures, '__name__', repr(fixtures))}'"
        ) from None


async def load_fixtures(
    session: AsyncSession,
    registry: FixtureRegistry,
    *names: str,
    strategy: LoadStrategy = LoadStrategy.MERGE,
) -> dict[str, list[DeclarativeBase]]:
    """Load specific fixtures by name with dependencies.

    All context variants of each requested fixture are loaded and merged.

    Args:
        session: Database session
        registry: Fixture registry
        *names: Fixture names to load (dependencies auto-resolved)
        strategy: How to handle existing records

    Returns:
        Dict mapping fixture names to loaded instances
    """
    ordered = registry.resolve_dependencies(*names)
    return await _load_ordered(session, registry, ordered, strategy)


async def load_fixtures_by_context(
    session: AsyncSession,
    registry: FixtureRegistry,
    *contexts: str | Enum,
    strategy: LoadStrategy = LoadStrategy.MERGE,
) -> dict[str, list[DeclarativeBase]]:
    """Load all fixtures for specific contexts.

    For each fixture name, only the variants whose context set intersects with
    *contexts* are loaded.  When a name has variants in multiple of the
    requested contexts, their instances are merged before being inserted.

    Args:
        session: Database session
        registry: Fixture registry
        *contexts: Contexts to load (e.g., ``Context.BASE``, ``Context.TESTING``,
            or plain strings for custom contexts)
        strategy: How to handle existing records

    Returns:
        Dict mapping fixture names to loaded instances
    """
    context_strings = tuple(_normalize_contexts(contexts))
    ordered = registry.resolve_context_dependencies(*contexts)
    return await _load_ordered(
        session, registry, ordered, strategy, contexts=context_strings
    )
