"""Search utilities for AsyncCrud."""

import asyncio
import functools
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import String, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.types import (
    ARRAY,
    Boolean,
    Date,
    DateTime,
    Enum,
    Integer,
    Numeric,
    Time,
    Uuid,
)

from ..exceptions import (
    InvalidFacetFilterError,
    InvalidSearchColumnError,
    NoSearchableFieldsError,
    UnsupportedFacetTypeError,
)
from ..types import FacetFieldType, SearchFieldType

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement


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


@functools.lru_cache(maxsize=128)
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
    search_column: str | None = None,
) -> tuple[list["ColumnElement[bool]"], list[InstrumentedAttribute[Any]]]:
    """Build SQLAlchemy filter conditions for search.

    Args:
        model: SQLAlchemy model class
        search: Search string or SearchConfig
        search_fields: Fields specified per-call (takes priority)
        default_fields: Default fields (from ClassVar)
        search_column: Optional key to narrow search to a single field.
            Must match one of the resolved search field keys.

    Returns:
        Tuple of (filter_conditions, joins_needed)

    Raises:
        NoSearchableFieldsError: If no searchable field has been configured
    """
    # Normalize input
    if isinstance(search, str):
        config = SearchConfig(query=search, fields=search_fields)
    else:
        config = (
            replace(search, fields=search_fields)
            if search_fields is not None
            else search
        )

    if not config.query or not config.query.strip():
        return [], []

    # Determine which fields to search
    fields = config.fields or default_fields or get_searchable_fields(model)

    if not fields:
        raise NoSearchableFieldsError(model)

    # Narrow to a single column when search_column is specified
    if search_column is not None:
        keys = search_field_keys(fields)
        index = {k: f for k, f in zip(keys, fields)}
        if search_column not in index:
            raise InvalidSearchColumnError(search_column, sorted(index))
        fields = [index[search_column]]

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

    if not filters:  # pragma: no cover
        return [], []

    # Combine based on match_mode
    if config.match_mode == "any":
        return [or_(*filters)], joins
    else:
        return filters, joins


def search_field_keys(fields: Sequence[SearchFieldType]) -> list[str]:
    """Return a human-readable key for each search field."""
    return facet_keys(fields)


def facet_keys(facet_fields: Sequence[FacetFieldType]) -> list[str]:
    """Return a key for each facet field.

    Args:
        facet_fields: Sequence of facet fields — either direct columns or
            relationship tuples ``(rel, ..., column)``.

    Returns:
        A list of string keys, one per facet field, in the same order.
    """
    keys: list[str] = []
    for field in facet_fields:
        if isinstance(field, tuple):
            keys.append("__".join(el.key for el in field))
        else:
            keys.append(field.key)
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

        col_type = column.property.columns[0].type
        is_array = isinstance(col_type, ARRAY)

        if is_array:
            unnested = func.unnest(column).label(column.key)
            q = select(unnested).select_from(model).distinct()
        else:
            q = select(column).select_from(model).distinct()

        # Apply base joins (deduplicated) — needed here independently
        seen_joins: set[str] = set()
        for rel in base_joins or []:
            rel_key = str(rel)
            if rel_key not in seen_joins:
                seen_joins.add(rel_key)
                q = q.outerjoin(rel)

        # Add any extra joins required by this facet field that aren't already applied
        for rel in rels:
            rel_key = str(rel)
            if rel_key not in existing_join_keys and rel_key not in seen_joins:
                seen_joins.add(rel_key)
                q = q.outerjoin(rel)

        if base_filters:
            q = q.where(and_(*base_filters))

        if is_array:
            q = q.order_by(unnested)
        else:
            q = q.order_by(column)
        result = await session.execute(q)
        values = [row[0] for row in result.all() if row[0] is not None]
        return key, values

    pairs = await asyncio.gather(
        *[_query_facet(f, k) for f, k in zip(facet_fields, keys)]
    )
    return dict(pairs)


_EQUALITY_TYPES = (String, Integer, Numeric, Date, DateTime, Time, Enum, Uuid)
"""Column types that support equality / IN filtering in build_filter_by."""


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

        col_type = column.property.columns[0].type
        if isinstance(col_type, ARRAY):
            if isinstance(value, list):
                filters.append(column.overlap(value))
            else:
                filters.append(column.any(value))
        elif isinstance(col_type, Boolean):
            if isinstance(value, list):
                filters.append(column.in_(value))
            else:
                filters.append(column.is_(value))
        elif isinstance(col_type, _EQUALITY_TYPES):
            if isinstance(value, list):
                filters.append(column.in_(value))
            else:
                filters.append(column == value)
        else:
            raise UnsupportedFacetTypeError(key, type(col_type).__name__)

    return filters, joins
