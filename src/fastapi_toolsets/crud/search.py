"""Search utilities for AsyncCrud."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import String, or_
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.attributes import InstrumentedAttribute

from ..exceptions import NoSearchableFieldsError

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement

SearchFieldType = InstrumentedAttribute[Any] | tuple[InstrumentedAttribute[Any], ...]


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
