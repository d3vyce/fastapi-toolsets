"""Many-to-Many association-table helpers (direct, without loading collections)."""

from typing import Any, TypeVar, cast

from sqlalchemy import ColumnElement, Table, delete, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, QueryableAttribute
from sqlalchemy.orm.relationships import RelationshipProperty

_M = TypeVar("_M", bound=DeclarativeBase)


def _m2m_prop(rel_attr: QueryableAttribute) -> tuple[RelationshipProperty, Table]:  # type: ignore[type-arg]
    """Return the validated M2M RelationshipProperty and its secondary table.

    Raises TypeError if *rel_attr* is not a Many-to-Many relationship.
    """
    prop = rel_attr.property
    if not isinstance(prop, RelationshipProperty) or prop.secondary is None:
        raise TypeError(
            f"m2m helpers require a Many-to-Many relationship attribute, "
            f"got {rel_attr!r}. Use a relationship with a secondary table."
        )
    return prop, cast(Table, prop.secondary)


def _parent_where(
    prop: RelationshipProperty,  # type: ignore[type-arg]
    instance: DeclarativeBase,
) -> list[ColumnElement[bool]]:
    """Build the WHERE clauses matching the owner side of *instance*."""
    return [
        assoc_col == getattr(instance, cast(str, parent_col.key))
        for parent_col, assoc_col in prop.synchronize_pairs
    ]


async def m2m_add(
    session: AsyncSession,
    instance: DeclarativeBase,
    rel_attr: QueryableAttribute,
    *related: DeclarativeBase,
    ignore_conflicts: bool = False,
) -> None:
    """Insert rows into a Many-to-Many association table without loading the ORM collection.

    Args:
        session: DB async session.
        instance: The "owner" side model instance (e.g. the ``A`` in ``A.b_list``).
        rel_attr: The M2M relationship attribute on the model class (e.g. ``A.b_list``).
        *related: One or more related instances to associate with ``instance``.
        ignore_conflicts: When ``True``, silently skip rows that already exist
            in the association table (``ON CONFLICT DO NOTHING``).

    Raises:
        TypeError: If ``rel_attr`` is not a Many-to-Many relationship.

    Example:
        ```python
        from fastapi_toolsets.db import m2m_add, transaction

        async with transaction(session):
            await m2m_add(session, post, Post.tags, tag1, tag2)
        ```
    """
    prop, secondary = _m2m_prop(rel_attr)
    if not related:
        return

    sync_pairs = prop.secondary_synchronize_pairs
    assert sync_pairs is not None  # set whenever secondary is set

    # synchronize_pairs:           [(parent_col, assoc_col), ...]
    # secondary_synchronize_pairs: [(related_col, assoc_col), ...]
    rows: list[dict[str, Any]] = []
    for rel_instance in related:
        row: dict[str, Any] = {}
        for parent_col, assoc_col in prop.synchronize_pairs:
            row[assoc_col.name] = getattr(instance, cast(str, parent_col.key))
        for related_col, assoc_col in sync_pairs:
            row[assoc_col.name] = getattr(rel_instance, cast(str, related_col.key))
        rows.append(row)

    stmt = pg_insert(secondary).values(rows)
    if ignore_conflicts:
        stmt = stmt.on_conflict_do_nothing()
    await session.execute(stmt)


async def m2m_remove(
    session: AsyncSession,
    instance: DeclarativeBase,
    rel_attr: QueryableAttribute,
    *related: DeclarativeBase,
) -> None:
    """Remove rows from a Many-to-Many association table without loading the ORM collection.

    Args:
        session: DB async session.
        instance: The "owner" side model instance (e.g. the ``A`` in ``A.b_list``).
        rel_attr: The M2M relationship attribute on the model class (e.g. ``A.b_list``).
        *related: One or more related instances to disassociate from ``instance``.

    Raises:
        TypeError: If ``rel_attr`` is not a Many-to-Many relationship.

    Example:
        ```python
        from fastapi_toolsets.db import m2m_remove, transaction

        async with transaction(session):
            await m2m_remove(session, post, Post.tags, tag1)
        ```
    """
    prop, secondary = _m2m_prop(rel_attr)
    if not related:
        return

    related_pairs = prop.secondary_synchronize_pairs
    assert related_pairs is not None  # set whenever secondary is set

    parent_where = _parent_where(prop, instance)

    if len(related_pairs) == 1:
        related_col, assoc_col = related_pairs[0]
        related_values = [getattr(r, cast(str, related_col.key)) for r in related]
        related_where = assoc_col.in_(related_values)
    else:
        assoc_cols = [ac for _, ac in related_pairs]
        rel_cols = [rc for rc, _ in related_pairs]
        related_values_t = [
            tuple(getattr(r, cast(str, rc.key)) for rc in rel_cols) for r in related
        ]
        related_where = tuple_(*assoc_cols).in_(related_values_t)

    await session.execute(delete(secondary).where(*parent_where, related_where))


async def m2m_set(
    session: AsyncSession,
    instance: DeclarativeBase,
    rel_attr: QueryableAttribute,
    *related: DeclarativeBase,
) -> None:
    """Replace the entire Many-to-Many association set atomically.

    Args:
        session: DB async session.
        instance: The "owner" side model instance (e.g. the ``A`` in ``A.b_list``).
        rel_attr: The M2M relationship attribute on the model class (e.g. ``A.b_list``).
        *related: The new complete set of related instances.

    Raises:
        TypeError: If ``rel_attr`` is not a Many-to-Many relationship.

    Example:
        ```python
        from fastapi_toolsets.db import m2m_set, transaction

        async with transaction(session):
            await m2m_set(session, post, Post.tags, tag1, tag2)  # replaces all
        ```
    """
    prop, secondary = _m2m_prop(rel_attr)

    await session.execute(delete(secondary).where(*_parent_where(prop, instance)))

    if related:
        await m2m_add(session, instance, rel_attr, *related)
