"""Search utilities for AsyncCrud."""

import functools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import (
    Column,
    String,
    Table,
    and_,
    any_,
    func,
    or_,
    select,
)
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

from .._orm import key_expr
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


@dataclass(frozen=True)
class SearchPlan:
    """A search rendered for the page query and for the aggregate queries.

    The page form joins every relationship field: a to-one join cannot fan
    out, and a join keeps the scan parallel and lets LIMIT stop early. The
    aggregate form filters to-many fields with ``IN (subquery)`` instead,
    so COUNT and facets do not fan out.
    """

    page_filters: list["ColumnElement[bool]"]
    page_joins: list[InstrumentedAttribute[Any]]
    agg_filters: list["ColumnElement[bool]"]
    agg_joins: list[InstrumentedAttribute[Any]]


_EMPTY_PLAN = SearchPlan([], [], [], [])

_Entry = tuple[tuple[Any, ...], "ColumnElement[bool]"]


def build_search_plan(
    model: type[DeclarativeBase],
    search: str | SearchConfig,
    search_fields: Sequence[SearchFieldType] | None = None,
    default_fields: Sequence[SearchFieldType] | None = None,
    search_column: str | None = None,
) -> SearchPlan:
    """Build the search conditions once, in both forms.

    Args:
        model: SQLAlchemy model class
        search: Search string or SearchConfig
        search_fields: Fields specified per-call (takes priority)
        default_fields: Default fields (from ClassVar)
        search_column: Optional key to narrow search to a single field.
            Must match one of the resolved search field keys.

    Raises:
        NoSearchableFieldsError: If no searchable field has been configured
    """
    if isinstance(search, str):
        config = SearchConfig(query=search, fields=search_fields)
    else:
        config = (
            replace(search, fields=search_fields)
            if search_fields is not None
            else search
        )
    query = config.query.strip() if config.query else ""
    if not query:
        return _EMPTY_PLAN

    fields = config.fields or default_fields or get_searchable_fields(model)
    if not fields:
        raise NoSearchableFieldsError(model)
    if search_column is not None:
        index = {k: f for k, f in zip(search_field_keys(fields), fields)}
        if search_column not in index:
            raise InvalidSearchColumnError(search_column, sorted(index))
        fields = [index[search_column]]

    entries = [(_field_rels(f), _search_condition(f, query, config)) for f in fields]
    page = _render_search(entries, [], config.match_mode)
    to_many = [e for e in entries if any(r.property.uselist for r in e[0])]
    # All or nothing, so the aggregate form matches the page form exactly.
    if to_many and all(_semi_joinable(r) for rels, _ in to_many for r in rels):
        joined = [e for e in entries if e not in to_many]
        agg = _render_search(joined, to_many, config.match_mode)
    else:
        agg = page
    return SearchPlan(*page, *agg)


def _render_search(
    joined: Sequence[_Entry], subqueries: Sequence[_Entry], match_mode: str
) -> tuple[list["ColumnElement[bool]"], list[InstrumentedAttribute[Any]]]:
    """Conditions and joins for *joined* entries, plus subqueries for the rest."""
    joins = unique_relationships(rel for rels, _ in joined for rel in rels)
    filters = [condition for _, condition in joined]
    filters += _semi_join_tree(subqueries, match_mode)
    if not filters:  # pragma: no cover
        return [], []
    if match_mode == "any":
        return [or_(*filters)], joins
    return filters, joins


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

    See :func:`build_search_plan` for the arguments. With
    ``to_many_subqueries`` the aggregate form is returned.

    Returns:
        Tuple of (filter_conditions, joins_needed)
    """
    plan = build_search_plan(
        model, search, search_fields, default_fields, search_column
    )
    if to_many_subqueries:
        return plan.agg_filters, plan.agg_joins
    return plan.page_filters, plan.page_joins


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


def _owner_pairs(prop: Any) -> Sequence[tuple[Any, Any]]:
    """``(owner column, linked column)`` pairs of *prop*'s first hop."""
    return (
        prop.synchronize_pairs
        if prop.secondary is not None
        else prop.local_remote_pairs
    )


def _semi_join(rel: Any, condition: "ColumnElement[bool]") -> "ColumnElement[bool]":
    """Express "some related row matches *condition*" as `local IN (subquery)`."""
    prop = rel.property
    pairs = _owner_pairs(prop)
    sub = select(*(linked for _, linked in pairs)).where(condition)
    if prop.secondary is not None:
        sub = sub.where(prop.secondaryjoin)
    return _in_subquery([owner for owner, _ in pairs], sub)


def _in_subquery(columns: Sequence[Any], sub: Any) -> "ColumnElement[bool]":
    """``columns IN (sub)``, with a tuple for multi-column keys."""
    # Never correlate: a self-referential relationship would otherwise lose
    # its inner FROM to the outer query.
    return key_expr(columns).in_(sub.correlate(None))


def _related_to(rel: Any, parent_rows: Any) -> "ColumnElement[bool]":
    """Condition on *rel*'s target: linked to some row selected by *parent_rows*."""
    prop = rel.property
    pairs = _owner_pairs(prop)
    parent_keys = parent_rows.with_only_columns(*(owner for owner, _ in pairs))
    if prop.secondary is None:
        return _in_subquery([linked for _, linked in pairs], parent_keys)
    target_pairs = prop.secondary_synchronize_pairs
    linked = select(*(assoc for _, assoc in target_pairs)).where(
        _in_subquery([assoc for _, assoc in pairs], parent_keys)
    )
    return _in_subquery([target for target, _ in target_pairs], linked)


def _semi_join_tree(
    entries: Sequence[_Entry], match_mode: str
) -> list["ColumnElement[bool]"]:
    """Turn (relationship path, condition) entries into conditions on the root model.

    Fields sharing a relationship prefix go into one subquery, so with
    ``match_mode="all"`` they must match the same related row, as with a join.
    """
    combine = or_ if match_mode == "any" else and_
    direct = [condition for rels, condition in entries if not rels]
    groups: dict[str, tuple[Any, list[_Entry]]] = {}
    for rels, condition in entries:
        if rels:
            groups.setdefault(str(rels[0]), (rels[0], []))[1].append(
                (rels[1:], condition)
            )
    return direct + [
        _semi_join(rel, combine(*_semi_join_tree(sub, match_mode)))
        for rel, sub in groups.values()
    ]


def search_field_keys(fields: Sequence[SearchFieldType]) -> list[str]:
    """Return a human-readable key for each search field."""
    return facet_keys(fields)


def unique_relationships(rels: Iterable[Any]) -> list[Any]:
    """*rels* without repeats, by relationship identity, in order."""
    seen: set[str] = set()
    unique: list[Any] = []
    for rel in rels:
        key = str(rel)
        if key not in seen:
            seen.add(key)
            unique.append(rel)
    return unique


def apply_search_joins(q: Any, joins: Sequence[Any]) -> Any:
    """Apply relationship-based outer joins (from search/filter_by/facets) to a query.

    Deduplicates by relationship identity so a join used by several fields
    (e.g. search + a facet on the same relation) is only applied once.
    """
    for rel in unique_relationships(joins):
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


def facet_source_columns(
    model: type[DeclarativeBase], facet_fields: Sequence[FacetFieldType]
) -> list[Any]:
    """The model's columns the facets read: its key and each path's first hop."""
    columns: dict[Any, Any] = {col.key: col for col in model.__mapper__.primary_key}
    for field in facet_fields:
        if isinstance(field, tuple):
            for owner, _ in _owner_pairs(field[0].property):
                columns.setdefault(owner.key, owner)
        else:
            for col in field.property.columns:
                columns.setdefault(col.key, col)
    return list(columns.values())


def facet_scalars(
    model: Any,
    facet_fields: Sequence[FacetFieldType],
    *,
    base_filters: Sequence["ColumnElement[bool]"],
    base_joins: Sequence[InstrumentedAttribute[Any]],
    own_filters: "dict[str, ColumnElement[bool]]",
    prefiltered: bool = False,
) -> list[tuple[str, Any, Any]]:
    """One ``(key, scalar subquery, enum class)`` per facet field.

    Args:
        model: SQLAlchemy model class, or an alias of it holding the rows to facet
        facet_fields: Columns or relationship tuples to facet on
        base_filters: Filter conditions already applied to the main query (search + caller filters)
        base_joins: Relationship joins already applied to the main query
        own_filters: Map of facet key -> the ``filter_by`` condition for that
            same key (if any). Excluded from that facet's own subquery so
            filtering on a facet doesn't collapse its own value list down to
            just the filtered value.
        prefiltered: *model* already holds only the filtered rows (a CTE).
    """
    scalars: list[tuple[str, Any, Any]] = []

    for field, key in zip(facet_fields, facet_keys(facet_fields)):
        # Read the model's own attributes from *model*, which may be an alias.
        if isinstance(field, tuple):
            rels = (getattr(model, field[0].key), *field[1:-1])
            column = field[-1]
        else:
            rels = ()
            column = getattr(model, field.key)

        col_type = column.property.columns[0].type
        filters = [*base_filters, *(f for k, f in own_filters.items() if k != key)]
        rows = _facet_rows(model, rels, filters, base_joins, prefiltered=prefiltered)
        value = func.unnest(column) if isinstance(col_type, ARRAY) else column
        values_sq = rows.with_only_columns(value.label("v")).subquery()
        # DISTINCT in a subquery can hash in parallel workers, where
        # array_agg(DISTINCT ...) always sorts every row in a single process.
        distinct_sq = (
            select(values_sq.c.v).where(values_sq.c.v.isnot(None)).distinct().subquery()
        )
        v = distinct_sq.c.v
        agg = select(func.array_agg(v).aggregate_order_by(v)).select_from(distinct_sq)

        enum_class = getattr(col_type, "enum_class", None)
        scalars.append((key, agg.scalar_subquery().label(key), enum_class))
    return scalars


def decode_facet(values: Any, enum_class: Any) -> list[Any]:
    """The facet values of one row, enum members by name."""
    return [
        v.name if (enum_class is not None and isinstance(v, enum_class)) else v
        for v in (values or [])
    ]


def _facet_rows(
    model: Any,
    rels: Sequence[Any],
    filters: Sequence[Any],
    base_joins: Sequence[Any],
    *,
    prefiltered: bool,
) -> Any:
    """Select the rows of the facet column's table that relate to the filtered rows."""
    joined = {str(rel) for rel in base_joins}
    # Join the path when the rows are prefiltered (a subquery over the related
    # table may be looped once per row otherwise), when it cannot be expressed
    # as subqueries, when the filters join a to-many relationship on it (the
    # facet must read the same related row), or when a to-one path meets
    # joined filters (the planner tends to re-run those joins per value).
    keep_join = (
        prefiltered
        or not all(_semi_joinable(rel) for rel in rels)
        or any(rel.property.uselist and str(rel) in joined for rel in rels)
        or (base_joins and not any(rel.property.uselist for rel in rels))
    )
    joins = [*base_joins, *rels] if keep_join else base_joins
    rows = apply_search_joins(select(model), joins)
    if filters:
        rows = rows.where(and_(*filters))
    if keep_join:
        return rows
    # Walk the path from the related side: each table is filtered on keys
    # linked to the previous one, so a to-many path never fans out and a
    # small lookup table is probed through the foreign key index.
    for rel in rels:
        rows = select(rel.property.entity).where(_related_to(rel, rows))
    return rows


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

    for key, value in filter_by.items():
        if key not in index:
            raise InvalidFacetFilterError(key, valid_keys)

        column, rels = index[key]
        joins.extend(rels)

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

    return filters, unique_relationships(joins)
