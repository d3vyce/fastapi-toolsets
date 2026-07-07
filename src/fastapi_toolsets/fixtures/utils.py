"""Fixture loading utilities for database seeding."""

from collections.abc import Iterator
from enum import Enum
from typing import Any, cast

from sqlalchemy import Table, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, selectinload
from sqlalchemy.orm.interfaces import ExecutableOption, ORMOption

from ..db import transaction
from ..logger import get_logger
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


def _get_table_chain(model_cls: type[DeclarativeBase]) -> list[type[DeclarativeBase]]:
    """Return [root, ..., model_cls] for joined-table inheritance, or [model_cls]."""
    chain: list[type[DeclarativeBase]] = []
    current = sa_inspect(model_cls)
    while current is not None:
        chain.append(current.class_)
        current = current.inherits
    chain.reverse()
    seen: set[int] = set()
    result: list[type[DeclarativeBase]] = []
    for cls in chain:
        tid = id(cls.__table__)
        if tid not in seen:  # pragma: no branch
            seen.add(tid)
            result.append(cls)
    return result


def _instance_to_dict_for_cls(
    instance: DeclarativeBase, cls: type[DeclarativeBase]
) -> dict[str, Any]:
    """Like _instance_to_dict but limited to columns belonging to cls's own table."""
    own_cols = {col.key for col in cls.__table__.columns}
    return {k: v for k, v in _instance_to_dict(instance).items() if k in own_cols}


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


def _grouped_table_dicts(
    model_cls: type[DeclarativeBase], instances: list[DeclarativeBase]
) -> Iterator[
    tuple[type[DeclarativeBase], list[dict[str, Any]], list[DeclarativeBase]]
]:
    """Yield (cls, group_dicts, group_instances) per table in the inheritance
    chain and per column-set group, skipping empty groups.
    """
    for cls in _get_table_chain(model_cls):
        dicts = [_instance_to_dict_for_cls(i, cls) for i in instances]
        for group_dicts, group_instances in _group_by_column_set(dicts, instances):
            if group_dicts and group_dicts[0]:  # pragma: no branch
                yield cls, group_dicts, group_instances


async def _batch_insert(
    session: AsyncSession,
    model_cls: type[DeclarativeBase],
    instances: list[DeclarativeBase],
) -> None:
    """INSERT all instances, raises on conflict."""
    for cls, group_dicts, group_instances in _grouped_table_dicts(model_cls, instances):
        table = cast(Table, cls.__table__)
        missing_pk_cols = [
            col for col in table.primary_key.columns if col.key not in group_dicts[0]
        ]
        if not missing_pk_cols:
            await session.execute(pg_insert(table), group_dicts)
            continue
        stmt = pg_insert(table).returning(
            *missing_pk_cols, sort_by_parameter_order=True
        )
        result = await session.execute(stmt, group_dicts)
        for inst, row in zip(group_instances, result):
            for col, val in zip(missing_pk_cols, row):
                setattr(inst, col.key, val)


async def _batch_merge(
    session: AsyncSession,
    model_cls: type[DeclarativeBase],
    instances: list[DeclarativeBase],
) -> None:
    """UPSERT: insert new rows, update existing ones with the provided values."""
    for cls, group_dicts, _ in _grouped_table_dicts(model_cls, instances):
        table = cast(Table, cls.__table__)
        pk_names = [col.name for col in table.primary_key]
        pk_names_set = set(pk_names)
        own_col_keys = {col.key for col in table.columns}
        non_pk_cols = [k for k in own_col_keys if k not in pk_names_set]

        stmt = pg_insert(table).values(group_dicts)

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
    if len(_get_table_chain(model_cls)) > 1:
        loaded: list[DeclarativeBase] = []
        for inst in instances:
            pk = _get_primary_key(inst)
            if pk is None or not await session.get(model_cls, pk):
                session.add(inst)
                loaded.append(inst)
        await session.flush()
        return loaded

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

    loaded = list(no_pk)
    if no_pk:
        no_pk_dicts = [_instance_to_dict(i) for i in no_pk]
        for group_dicts, group_instances in _group_by_column_set(no_pk_dicts, no_pk):
            stmt = pg_insert(cast(Table, model_cls.__table__)).returning(
                *mapper.primary_key, sort_by_parameter_order=True
            )
            result = await session.execute(stmt, group_dicts)
            for inst, row in zip(group_instances, result):
                for col, val in zip(mapper.primary_key, row):
                    setattr(inst, col.key, val)

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


def _relationship_load_options(model: type[DeclarativeBase]) -> list[ExecutableOption]:
    """Build selectinload options for all direct relationships on a model."""
    return [
        selectinload(getattr(model, rel.key)) for rel in model.__mapper__.relationships
    ]


async def _reload_with_relationships(
    session: AsyncSession,
    instances: list[DeclarativeBase],
    load_options: list[ExecutableOption],
) -> list[DeclarativeBase]:
    """Reload instances in a single bulk query with relationship eager-loading."""
    model = type(instances[0])
    mapper = model.__mapper__
    pk_cols = mapper.primary_key

    if len(pk_cols) == 1:
        pk_attr = getattr(model, pk_cols[0].key)
        pks = [getattr(inst, pk_cols[0].key) for inst in instances]
        result = await session.execute(
            select(model).where(pk_attr.in_(pks)).options(*load_options)
        )
        by_pk = {getattr(row, pk_cols[0].key): row for row in result.unique().scalars()}
        return [by_pk[pk] for pk in pks]

    # Composite PK: fall back to per-instance reload
    reloaded: list[DeclarativeBase] = []
    for instance in instances:
        pk = _get_primary_key(instance)
        refreshed = await session.get(
            model,
            pk,
            options=cast(list[ORMOption], load_options),
            populate_existing=True,
        )
        if refreshed is not None:  # pragma: no branch
            reloaded.append(refreshed)
    return reloaded


async def _refresh_loaded(
    session: AsyncSession, instances: list[DeclarativeBase]
) -> list[DeclarativeBase]:
    """Re-select freshly written rows, eager-loading relationships."""
    if not instances:
        return []
    refreshed: list[DeclarativeBase | None] = [None] * len(instances)
    for model_cls, group in _group_by_type(instances):
        positions = [i for i, inst in enumerate(instances) if type(inst) is model_cls]
        load_options = _relationship_load_options(model_cls)
        for pos, new in zip(
            positions, await _reload_with_relationships(session, group, load_options)
        ):
            refreshed[pos] = new
    return cast(list[DeclarativeBase], refreshed)


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
            registry.get_load_variants(name, *contexts)
            if contexts is not None
            else registry.get_variants(name)
        )

        if not variants:  # pragma: no cover
            results[name] = []
            continue

        instances = [inst for v in variants for inst in v.func()]

        if not instances:
            results[name] = []
            continue

        model_name = type(instances[0]).__name__
        loaded: list[DeclarativeBase] = []

        async with transaction(session):
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
                    case _:  # pragma: no cover
                        pass

            loaded = await _refresh_loaded(session, loaded)

        results[name] = loaded
        logger.info("Loaded fixture '%s': %d %s(s)", name, len(loaded), model_name)

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
        *contexts: Contexts to load (e.g., ``Context.TESTING``, or plain
            strings for custom contexts)
        strategy: How to handle existing records

    Returns:
        Dict mapping fixture names to loaded instances
    """
    context_strings = tuple(_normalize_contexts(contexts))
    ordered = registry.resolve_context_dependencies(*contexts)
    return await _load_ordered(
        session, registry, ordered, strategy, contexts=context_strings
    )
