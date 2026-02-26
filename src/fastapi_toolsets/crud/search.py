"""Search utilities for AsyncCrud."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import String, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.attributes import InstrumentedAttribute

from ..exceptions import InvalidFacetFilterError, NoSearchableFieldsError

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement

SearchFieldType = InstrumentedAttribute[Any] | tuple[InstrumentedAttribute[Any], ...]
FacetFieldType = SearchFieldType


@dataclass
class SearchConfig:
    """Advanced search configuration.

    Attributes:
        query: The search string
        fields: Fields to search (columns or tuples for relationships)
        case_sensitive: Case-sensitive search (default: False)
        match_mode: "any" (OR) or "all" (AND) to combine fields
    """

    query: str
    fields: Sequence[SearchFieldType] | None = None
    case_sensitive: bool = False
    match_mode: Literal["any", "all"] = "any"


def get_searchable_fields(
    model: type[DeclarativeBase],
    *,
    include_relationships: bool = True,
    max_depth: int = 1,
) -> list[SearchFieldType]:
    """Auto-detect String fields on a model and its relationships.

    Args:
        model: SQLAlchemy model class
        include_relationships: Include fields from many-to-one/one-to-one relationships
        max_depth: Max depth for relationship traversal (default: 1)

    Returns:
        List of columns and tuples (relationship, column)
    """
    fields: list[SearchFieldType] = []
    mapper = model.__mapper__

    # Direct String columns
    for col in mapper.columns:
        if isinstance(col.type, String):
            fields.append(getattr(model, col.key))

    # Relationships (one-to-one, many-to-one only)
    if include_relationships and max_depth > 0:
        for rel_name, rel_prop in mapper.relationships.items():
            if rel_prop.uselist:  # Skip collections (one-to-many, many-to-many)
                continue

            rel_attr = getattr(model, rel_name)
            related_model = rel_prop.mapper.class_

            for col in related_model.__mapper__.columns:
                if isinstance(col.type, String):
                    fields.append((rel_attr, getattr(related_model, col.key)))

    return fields


def build_search_filters(
    model: type[DeclarativeBase],
    search: str | SearchConfig,
    search_fields: Sequence[SearchFieldType] | None = None,
    default_fields: Sequence[SearchFieldType] | None = None,
) -> tuple[list["ColumnElement[bool]"], list[InstrumentedAttribute[Any]]]:
    """Build SQLAlchemy filter conditions for search.

    Args:
        model: SQLAlchemy model class
        search: Search string or SearchConfig
        search_fields: Fields specified per-call (takes priority)
        default_fields: Default fields (from ClassVar)

    Returns:
        Tuple of (filter_conditions, joins_needed)

    Raises:
        NoSearchableFieldsError: If no searchable field has been configured
    """
    # Normalize input
    if isinstance(search, str):
        config = SearchConfig(query=search, fields=search_fields)
    else:
        config = search
        if search_fields is not None:
            config = SearchConfig(
                query=config.query,
                fields=search_fields,
                case_sensitive=config.case_sensitive,
                match_mode=config.match_mode,
            )

    if not config.query or not config.query.strip():
        return [], []

    # Determine which fields to search
    fields = config.fields or default_fields or get_searchable_fields(model)

    if not fields:
        raise NoSearchableFieldsError(model)

    query = config.query.strip()
    filters: list[ColumnElement[bool]] = []
    joins: list[InstrumentedAttribute[Any]] = []
    added_joins: set[str] = set()

    for field in fields:
        if isinstance(field, tuple):
            # Relationship: (User.role, Role.name) or deeper
            for rel in field[:-1]:
                rel_key = str(rel)
                if rel_key not in added_joins:
                    joins.append(rel)
                    added_joins.add(rel_key)
            column = field[-1]
        else:
            column = field

        # Build the filter (cast to String for non-text columns)
        column_as_string = column.cast(String)
        if config.case_sensitive:
            filters.append(column_as_string.like(f"%{query}%"))
        else:
            filters.append(column_as_string.ilike(f"%{query}%"))

    if not filters:
        return [], []

    # Combine based on match_mode
    if config.match_mode == "any":
        return [or_(*filters)], joins
    else:
        return filters, joins


def facet_keys(facet_fields: Sequence[FacetFieldType]) -> list[str]:
    """Return a key for each facet field, disambiguating duplicate column keys.

    Args:
        facet_fields: Sequence of facet fields — either direct columns or
            relationship tuples ``(rel, ..., column)``.

    Returns:
        A list of string keys, one per facet field, in the same order.
    """
    raw: list[tuple[str, str | None]] = []  # (column_key, rel_key_or_None)
    for field in facet_fields:
        if isinstance(field, tuple):
            rel = field[-2]  # immediate parent relationship
            column = field[-1]
            raw.append((column.key, rel.key))
        else:
            raw.append((field.key, None))

    # Find which column keys appear more than once
    from collections import Counter

    counts = Counter(col_key for col_key, _ in raw)
    keys: list[str] = []
    for col_key, rel_key in raw:
        if counts[col_key] > 1 and rel_key is not None:
            keys.append(f"{rel_key}__{col_key}")
        else:
            keys.append(col_key)
    return keys


async def build_facets(
    session: "AsyncSession",
    model: type[DeclarativeBase],
    facet_fields: Sequence[FacetFieldType],
    *,
    base_filters: "list[ColumnElement[bool]] | None" = None,
    base_joins: list[InstrumentedAttribute[Any]] | None = None,
) -> dict[str, list[Any]]:
    """Return distinct values for each facet field, respecting current filters.

    Args:
        session: DB async session
        model: SQLAlchemy model class
        facet_fields: Columns or relationship tuples to facet on
        base_filters: Filter conditions already applied to the main query (search + caller filters)
        base_joins: Relationship joins already applied to the main query

    Returns:
        Dict mapping column key to sorted list of distinct non-None values
    """
    existing_join_keys: set[str] = {str(j) for j in (base_joins or [])}

    keys = facet_keys(facet_fields)

    async def _query_facet(field: FacetFieldType, key: str) -> tuple[str, list[Any]]:
        if isinstance(field, tuple):
            # Relationship chain: (User.role, Role.name) — last element is the column
            rels = field[:-1]
            column = field[-1]
        else:
            rels = ()
            column = field

        q = select(column).select_from(model).distinct()

        # Apply base joins (already done on main query, but needed here independently)
        for rel in base_joins or []:
            q = q.outerjoin(rel)

        # Add any extra joins required by this facet field that aren't already in base_joins
        for rel in rels:
            if str(rel) not in existing_join_keys:
                q = q.outerjoin(rel)

        if base_filters:
            from sqlalchemy import and_

            q = q.where(and_(*base_filters))

        q = q.order_by(column)
        result = await session.execute(q)
        values = [row[0] for row in result.all() if row[0] is not None]
        return key, values

    pairs = await asyncio.gather(
        *[_query_facet(f, k) for f, k in zip(facet_fields, keys)]
    )
    return dict(pairs)


def build_filter_by(
    filter_by: dict[str, Any],
    facet_fields: Sequence[FacetFieldType],
) -> tuple["list[ColumnElement[bool]]", list[InstrumentedAttribute[Any]]]:
    """Translate a {column_key: value} dict into SQLAlchemy filter conditions.

    Args:
        filter_by: Mapping of column key to scalar value or list of values
        facet_fields: Declared facet fields to validate keys against

    Returns:
        Tuple of (filter_conditions, joins_needed)

    Raises:
        InvalidFacetFilterError: If a key in filter_by is not a declared facet field
    """
    index: dict[
        str, tuple[InstrumentedAttribute[Any], list[InstrumentedAttribute[Any]]]
    ] = {}
    for key, field in zip(facet_keys(facet_fields), facet_fields):
        if isinstance(field, tuple):
            rels = list(field[:-1])
            column = field[-1]
        else:
            rels = []
            column = field
        index[key] = (column, rels)

    valid_keys = set(index)
    filters: list[ColumnElement[bool]] = []
    joins: list[InstrumentedAttribute[Any]] = []
    added_join_keys: set[str] = set()

    for key, value in filter_by.items():
        if key not in index:
            raise InvalidFacetFilterError(key, valid_keys)

        column, rels = index[key]

        for rel in rels:
            rel_key = str(rel)
            if rel_key not in added_join_keys:
                joins.append(rel)
                added_join_keys.add(rel_key)

        if isinstance(value, list):
            filters.append(column.in_(value))
        else:
            filters.append(column == value)

    return filters, joins
