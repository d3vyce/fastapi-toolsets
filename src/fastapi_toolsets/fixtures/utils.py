"""Fixture loading utilities for database seeding."""

from collections.abc import Callable, Sequence
from enum import Enum
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from ..db import get_transaction
from ..logger import get_logger
from ..types import ModelType
from .enum import LoadStrategy
from .registry import FixtureRegistry, _normalize_contexts

logger = get_logger()


def _instance_to_dict(instance: DeclarativeBase) -> dict[str, Any]:
    """Extract column values from a model instance, skipping unset server-default columns."""
    state = sa_inspect(instance)
    state_dict = state.dict
    result: dict[str, Any] = {}
    for prop in state.mapper.column_attrs:
        if prop.key not in state_dict:
            continue
        val = state_dict[prop.key]
        if val is None:
            col = prop.columns[0]

            if (
                col.server_default is not None
                or (col.default is not None and col.default.is_callable)
                or col.autoincrement is True
            ):
                continue
        result[prop.key] = val
    return result


def _group_by_type(
    instances: list[DeclarativeBase],
) -> list[tuple[type[DeclarativeBase], list[DeclarativeBase]]]:
    """Group instances by their concrete model class, preserving insertion order."""
    groups: dict[type[DeclarativeBase], list[DeclarativeBase]] = {}
    for instance in instances:
        groups.setdefault(type(instance), []).append(instance)
    return list(groups.items())


def _group_by_column_set(
    dicts: list[dict[str, Any]],
    instances: list[DeclarativeBase],
) -> list[tuple[list[dict[str, Any]], list[DeclarativeBase]]]:
    """Group (dict, instance) pairs by their dict key sets."""
    groups: dict[
        frozenset[str], tuple[list[dict[str, Any]], list[DeclarativeBase]]
    ] = {}
    for d, inst in zip(dicts, instances):
        key = frozenset(d)
        if key not in groups:
            groups[key] = ([], [])
        groups[key][0].append(d)
        groups[key][1].append(inst)
    return list(groups.values())


async def _batch_insert(
    session: AsyncSession,
    model_cls: type[DeclarativeBase],
    instances: list[DeclarativeBase],
) -> None:
    """INSERT all instances — raises on conflict (no duplicate handling)."""
    dicts = [_instance_to_dict(i) for i in instances]
    for group_dicts, _ in _group_by_column_set(dicts, instances):
        await session.execute(pg_insert(model_cls).values(group_dicts))


async def _batch_merge(
    session: AsyncSession,
    model_cls: type[DeclarativeBase],
    instances: list[DeclarativeBase],
) -> None:
    """UPSERT: insert new rows, update existing ones with the provided values."""
    mapper = model_cls.__mapper__
    pk_names = [col.name for col in mapper.primary_key]
    pk_names_set = set(pk_names)
    non_pk_cols = [
        prop.key
        for prop in mapper.column_attrs
        if not any(col.name in pk_names_set for col in prop.columns)
    ]

    dicts = [_instance_to_dict(i) for i in instances]
    for group_dicts, _ in _group_by_column_set(dicts, instances):
        stmt = pg_insert(model_cls).values(group_dicts)

        inserted_keys = set(group_dicts[0])
        update_cols = [col for col in non_pk_cols if col in inserted_keys]

        if update_cols:
            stmt = stmt.on_conflict_do_update(
                index_elements=pk_names,
                set_={col: stmt.excluded[col] for col in update_cols},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=pk_names)

        await session.execute(stmt)


async def _batch_skip_existing(
    session: AsyncSession,
    model_cls: type[DeclarativeBase],
    instances: list[DeclarativeBase],
) -> list[DeclarativeBase]:
    """INSERT only rows that do not already exist; return the inserted ones."""
    mapper = model_cls.__mapper__
    pk_names = [col.name for col in mapper.primary_key]

    no_pk: list[DeclarativeBase] = []
    with_pk_pairs: list[tuple[DeclarativeBase, Any]] = []
    for inst in instances:
        pk = _get_primary_key(inst)
        if pk is None:
            no_pk.append(inst)
        else:
            with_pk_pairs.append((inst, pk))

    loaded: list[DeclarativeBase] = list(no_pk)
    if no_pk:
        no_pk_dicts = [_instance_to_dict(i) for i in no_pk]
        for group_dicts, _ in _group_by_column_set(no_pk_dicts, no_pk):
            await session.execute(pg_insert(model_cls).values(group_dicts))

    if with_pk_pairs:
        with_pk = [i for i, _ in with_pk_pairs]
        with_pk_dicts = [_instance_to_dict(i) for i in with_pk]
        for group_dicts, group_insts in _group_by_column_set(with_pk_dicts, with_pk):
            stmt = (
                pg_insert(model_cls)
                .values(group_dicts)
                .on_conflict_do_nothing(index_elements=pk_names)
            )
            result = await session.execute(stmt.returning(*mapper.primary_key))
            inserted_pks = {
                row[0] if len(pk_names) == 1 else tuple(row) for row in result
            }
            loaded.extend(
                inst
                for inst, pk in zip(
                    group_insts, [_get_primary_key(i) for i in group_insts]
                )
                if pk in inserted_pks
            )

    return loaded


async def _load_ordered(
    session: AsyncSession,
    registry: FixtureRegistry,
    ordered_names: list[str],
    strategy: LoadStrategy,
    contexts: tuple[str, ...] | None = None,
) -> dict[str, list[DeclarativeBase]]:
    """Load fixtures in order using batch Core INSERT statements."""
    results: dict[str, list[DeclarativeBase]] = {}

    for name in ordered_names:
        variants = (
            registry.get_variants(name, *contexts)
            if contexts is not None
            else registry.get_variants(name)
        )

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
            for model_cls, group in _group_by_type(instances):
                match strategy:
                    case LoadStrategy.INSERT:
                        await _batch_insert(session, model_cls, group)
                        loaded.extend(group)
                    case LoadStrategy.MERGE:
                        await _batch_merge(session, model_cls, group)
                        loaded.extend(group)
                    case LoadStrategy.SKIP_EXISTING:
                        inserted = await _batch_skip_existing(session, model_cls, group)
                        loaded.extend(inserted)

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
