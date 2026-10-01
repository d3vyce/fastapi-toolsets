"""Search utilities for AsyncCrud."""

import functools
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import (
    Column,
    String,
    Table,
    and_,
    any_,
    distinct,
    func,
    or_,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import operators, visitors
from sqlalchemy.sql.elements import BinaryExpression
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
    *,
    to_many_subqueries: bool = False,
) -> tuple[list["ColumnElement[bool]"], list[InstrumentedAttribute[Any]]]:
    """Build SQLAlchemy filter conditions for search.

    Args:
        model: SQLAlchemy model class
        search: Search string or SearchConfig
        search_fields: Fields specified per-call (takes priority)
        default_fields: Default fields (from ClassVar)
        search_column: Optional key to narrow search to a single field.
            Must match one of the resolved search field keys.
        to_many_subqueries: Filter fields reached through a to-many
            relationship with ``IN (subquery)`` instead of a join, so the
            query does not fan out. Meant for COUNT and facet queries.

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

    entries = [(_field_rels(f), _search_condition(f, query, config)) for f in fields]
    subquery_entries: list[tuple[tuple[Any, ...], ColumnElement[bool]]] = []
    if to_many_subqueries:
        to_many = [
            entry for entry in entries if any(r.property.uselist for r in entry[0])
        ]
        # All or nothing, so the result matches the join form exactly.
        if all(_semi_joinable(r) for rels, _ in to_many for r in rels):
            subquery_entries = to_many
            entries = [
                entry
                for entry in entries
                if not any(r.property.uselist for r in entry[0])
            ]

    # Remaining relationship fields are outer-joined. A to-one join cannot
    # fan out, and unlike a subquery it keeps the scan parallel and lets
    # LIMIT stop early.
    joins: list[InstrumentedAttribute[Any]] = []
    added_joins: set[str] = set()
    for rels, _ in entries:
        for rel in rels:
            rel_key = str(rel)
            if rel_key not in added_joins:
                joins.append(rel)
                added_joins.add(rel_key)
    filters = [condition for _, condition in entries]
    filters += _semi_join_tree(subquery_entries, config.match_mode)

    if not filters:  # pragma: no cover
        return [], []

    # Combine based on match_mode
    if config.match_mode == "any":
        return [or_(*filters)], joins
    else:
        return filters, joins


def _field_rels(field: SearchFieldType) -> tuple[Any, ...]:
    """Relationship path of a search field, empty for a direct column."""
    return tuple(field[:-1]) if isinstance(field, tuple) else ()


def _search_condition(
    field: SearchFieldType, query: str, config: SearchConfig
) -> "ColumnElement[bool]":
    """LIKE/ILIKE condition on the field's column."""
    column = field[-1] if isinstance(field, tuple) else field
    # Cast to String only when needed, to preserve pg_trgm GIN index
    # usability on already-String columns.
    column_as_string = (
        column
        if isinstance(column.type, String) and not isinstance(column.type, Enum)
        else column.cast(String)
    )
    if config.case_sensitive:
        return column_as_string.like(f"%{query}%")
    return column_as_string.ilike(f"%{query}%")


def _is_plain_join(condition: Any, pairs: Sequence[tuple[Any, Any]]) -> bool:
    """True if *condition* is exactly the column equalities listed in *pairs*."""
    expected = {frozenset(map(hash, pair)) for pair in pairs}
    found: set[frozenset[int]] = set()
    for node in visitors.iterate(condition):
        if isinstance(node, BinaryExpression):
            if node.operator is not operators.eq or not (
                isinstance(node.left, Column) and isinstance(node.right, Column)
            ):
                return False
            found.add(frozenset((hash(node.left), hash(node.right))))
    return found == expected


def _semi_joinable(rel: Any) -> bool:
    """True if *rel* can be filtered with an `IN (subquery)` instead of a join."""
    prop = rel.property
    # Aliased or subclass access, and inheritance targets, keep the join path:
    # the subquery would miss the alias or the inheritance criteria.
    if rel.parent is not prop.parent or prop.mapper.inherits is not None:
        return False
    if prop.secondary is not None:
        return (
            isinstance(prop.secondary, Table)
            and _is_plain_join(prop.primaryjoin, prop.synchronize_pairs)
            and _is_plain_join(prop.secondaryjoin, prop.secondary_synchronize_pairs)
        )
    return _is_plain_join(prop.primaryjoin, prop.local_remote_pairs)


def _semi_join(rel: Any, condition: "ColumnElement[bool]") -> "ColumnElement[bool]":
    """Express "some related row matches *condition*" as `local IN (subquery)`."""
    prop = rel.property
    if prop.secondary is not None:
        pairs = prop.synchronize_pairs
        sub = select(*(assoc for _, assoc in pairs)).where(
            prop.secondaryjoin, condition
        )
    else:
        pairs = prop.local_remote_pairs
        sub = select(*(remote for _, remote in pairs)).where(condition)
    # Never correlate: a self-referential relationship would otherwise lose
    # its inner FROM to the outer query.
    sub = sub.correlate(None)
    local = [col for col, _ in pairs]
    if len(local) == 1:
        return local[0].in_(sub)
    return tuple_(*local).in_(sub)


def _semi_join_tree(
    entries: Sequence[tuple[tuple[Any, ...], "ColumnElement[bool]"]],
    match_mode: Literal["any", "all"],
) -> list["ColumnElement[bool]"]:
    """Turn (relationship path, condition) entries into conditions on the root model.

    Fields sharing a relationship prefix go into one subquery, so with
    ``match_mode="all"`` they must match the same related row, as with a join.
    """
    combine = or_ if match_mode == "any" else and_
    parts: list[Any] = []
    groups: dict[str, tuple[Any, list[Any]]] = {}
    for rels, condition in entries:
        if not rels:
            parts.append(condition)
            continue
        key = str(rels[0])
        if key not in groups:
            groups[key] = (rels[0], [])
            # Placeholder keeps the original field order.
            parts.append(key)
        groups[key][1].append((rels[1:], condition))
    return [
        _semi_join(
            groups[part][0],
            combine(*_semi_join_tree(groups[part][1], match_mode)),
        )
        if isinstance(part, str)
        else part
        for part in parts
    ]


def search_field_keys(fields: Sequence[SearchFieldType]) -> list[str]:
    """Return a human-readable key for each search field."""
    return facet_keys(fields)


def apply_search_joins(q: Any, joins: Sequence[Any]) -> Any:
    """Apply relationship-based outer joins (from search/filter_by/facets) to a query.

    Deduplicates by relationship identity so a join used by several fields
    (e.g. search + a facet on the same relation) is only applied once.
    """
    seen: set[str] = set()
    for rel in joins:
        rel_key = str(rel)
        if rel_key not in seen:
            seen.add(rel_key)
            q = q.outerjoin(rel)
    return q


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
    own_filters: "dict[str, ColumnElement[bool]] | None" = None,
) -> dict[str, list[Any]]:
    """Return distinct values for each facet field, respecting current filters.

    Args:
        session: DB async session
        model: SQLAlchemy model class
        facet_fields: Columns or relationship tuples to facet on
        base_filters: Filter conditions already applied to the main query (search + caller filters)
        base_joins: Relationship joins already applied to the main query
        own_filters: Map of facet key -> the ``filter_by`` condition for that
            same key (if any). Excluded from that facet's own subquery so
            filtering on a facet doesn't collapse its own value list down to
            just the filtered value.

    Returns:
        Dict mapping column key to sorted list of distinct non-None values
    """
    if not facet_fields:
        return {}

    keys = facet_keys(facet_fields)
    own_filters = own_filters or {}

    scalars: list[Any] = []
    enum_classes: dict[str, Any] = {}

    for field, key in zip(facet_fields, keys):
        if isinstance(field, tuple):
            # Relationship chain: (User.role, Role.name) — last element is the column
            rels = field[:-1]
            column = field[-1]
        else:
            rels = ()
            column = field

        col_type = column.property.columns[0].type
        is_array = isinstance(col_type, ARRAY)
        enum_classes[key] = getattr(col_type, "enum_class", None)

        filters = [
            *(base_filters or []),
            *(f for k, f in own_filters.items() if k != key),
        ]
        joins = [*(base_joins or []), *rels]

        if is_array:
            unnested = apply_search_joins(
                select(func.unnest(column).label("v")).select_from(model), joins
            )
            if filters:
                unnested = unnested.where(and_(*filters))
            unnested_sq = unnested.subquery()
            v = unnested_sq.c.v
            agg = (
                select(func.array_agg(aggregate_order_by(distinct(v), v)))
                .select_from(unnested_sq)
                .where(v.isnot(None))
            )
        else:
            agg = apply_search_joins(
                select(
                    func.array_agg(aggregate_order_by(distinct(column), column))
                ).select_from(model),
                joins,
            )
            agg = agg.where(and_(*filters, column.isnot(None)))

        scalars.append(agg.scalar_subquery().label(key))

    row = (await session.execute(select(*scalars))).one()

    facets: dict[str, list[Any]] = {}
    for key, values in zip(keys, row):
        enum_class = enum_classes[key]
        facets[key] = [
            v.name if (enum_class is not None and isinstance(v, enum_class)) else v
            for v in (values or [])
        ]
    return facets


_EQUALITY_TYPES = (String, Integer, Numeric, Date, DateTime, Time, Enum, Uuid)
"""Column types that support equality / IN filtering in build_filter_by."""


def _coerce_bool(value: Any) -> bool:
    """Coerce a string value to a Python bool for Boolean column filtering."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    raise ValueError(f"Cannot coerce {value!r} to bool")


def build_filter_by(
    filter_by: dict[str, Any],
    facet_fields: Sequence[FacetFieldType],
) -> tuple["dict[str, ColumnElement[bool]]", list[InstrumentedAttribute[Any]]]:
    """Translate a {column_key: value} dict into SQLAlchemy filter conditions.

    Args:
        filter_by: Mapping of column key to scalar value or list of values
        facet_fields: Declared facet fields to validate keys against

    Returns:
        Tuple of ({facet_key: filter_condition}, joins_needed). One filter
        condition per key, so callers can identify (and exclude) a facet's
        own filter when computing that facet's distinct values.

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
    filters: dict[str, ColumnElement[bool]] = {}
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
        if isinstance(col_type, Boolean):
            coerce = _coerce_bool
            if isinstance(value, list):
                filters[key] = column.in_([coerce(v) for v in value])
            else:
                filters[key] = column == coerce(value)
        elif isinstance(col_type, ARRAY):
            if isinstance(value, list):
                filters[key] = column.overlap(value)
            else:
                filters[key] = any_(column) == value
        elif isinstance(col_type, Enum):
            enum_class = col_type.enum_class
            if enum_class is not None:

                def _coerce_enum(v: Any, enum_class: Any = enum_class) -> Any:
                    if isinstance(v, enum_class):
                        return v
                    return enum_class[v]  # lookup by name: "PENDING", "RED"

                if isinstance(value, list):
                    filters[key] = column.in_([_coerce_enum(v) for v in value])
                else:
                    filters[key] = column == _coerce_enum(value)
            else:  # pragma: no cover
                if isinstance(value, list):
                    filters[key] = column.in_(value)
                else:
                    filters[key] = column == value
        elif isinstance(col_type, _EQUALITY_TYPES):
            if isinstance(value, list):
                filters[key] = column.in_(value)
            else:
                filters[key] = column == value
        else:
            raise UnsupportedFacetTypeError(key, type(col_type).__name__)

    return filters, joins
