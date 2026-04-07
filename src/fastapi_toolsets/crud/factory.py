"""Generic async CRUD operations for SQLAlchemy models."""

from __future__ import annotations

import base64
import inspect
import json
import uuid as uuid_module
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar, Generic, Literal, NamedTuple, Self, cast, overload

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import (
    Date,
    DateTime,
    Float,
    Integer,
    Numeric,
    Uuid,
    and_,
    func,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import (
    DeclarativeBase,
    QueryableAttribute,
    RelationshipProperty,
    contains_eager,
    selectinload,
)
from sqlalchemy.sql.base import ExecutableOption
from sqlalchemy.sql.roles import WhereHavingRole

from ..db import get_transaction
from ..exceptions import InvalidOrderFieldError, NotFoundError
from ..schemas import (
    CursorPaginatedResponse,
    CursorPagination,
    OffsetPaginatedResponse,
    OffsetPagination,
    PaginationType,
    Response,
)
from ..types import (
    FacetFieldType,
    JoinType,
    LateralJoinType,
    M2MFieldType,
    ModelType,
    OrderByClause,
    OrderFieldType,
    SchemaType,
    SearchFieldType,
)
from .search import (
    SearchConfig,
    build_facets,
    build_filter_by,
    build_search_filters,
    facet_keys,
    search_field_keys,
)


class _CursorDirection(str, Enum):
    NEXT = "next"
    PREV = "prev"


def _encode_cursor(
    value: Any, *, direction: _CursorDirection = _CursorDirection.NEXT
) -> str:
    """Encode a cursor column value and navigation direction as a URL-safe base64 string."""
    return (
        base64.urlsafe_b64encode(
            json.dumps({"val": str(value), "dir": direction}).encode()
        )
        .decode()
        .rstrip("=")
    )


def _decode_cursor(cursor: str) -> tuple[str, _CursorDirection]:
    """Decode a URL-safe base64 cursor string into ``(raw_value, direction)``."""
    padded = cursor + "=" * (-len(cursor) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded).decode())
    return payload["val"], _CursorDirection(payload["dir"])


def _page_size_query(default: int, max_size: int) -> int:
    """Return a FastAPI ``Query`` for the ``items_per_page`` parameter."""
    return Query(
        default,
        ge=1,
        le=max_size,
        description=f"Number of items per page (max {max_size})",
    )


def _parse_cursor_value(raw_val: str, col_type: Any) -> Any:
    """Parse a raw cursor string value back into the appropriate Python type."""
    if isinstance(col_type, Integer):
        return int(raw_val)
    if isinstance(col_type, Uuid):
        return uuid_module.UUID(raw_val)
    if isinstance(col_type, DateTime):
        return datetime.fromisoformat(raw_val)
    if isinstance(col_type, Date):
        return date.fromisoformat(raw_val)
    if isinstance(col_type, (Float, Numeric)):
        return Decimal(raw_val)
    raise ValueError(
        f"Unsupported cursor column type: {type(col_type).__name__!r}. "
        "Supported types: Integer, BigInteger, SmallInteger, Uuid, "
        "DateTime, Date, Float, Numeric."
    )


def _apply_joins(q: Any, joins: JoinType | None, outer_join: bool) -> Any:
    """Apply a list of (model, condition) joins to a SQLAlchemy select query."""
    if not joins:
        return q
    for model, condition in joins:
        q = q.outerjoin(model, condition) if outer_join else q.join(model, condition)
    return q


class _ResolvedLateral(NamedTuple):
    joins: LateralJoinType
    eager: list[ExecutableOption]


class _LateralLoad:
    """Marker used inside ``default_load_options`` for lateral join loading.

    Supports only Many:One and One:One relationships (single row per parent).
    """

    __slots__ = ("rel_attr",)

    def __init__(self, rel_attr: QueryableAttribute) -> None:
        prop = rel_attr.property
        if not isinstance(prop, RelationshipProperty):
            raise TypeError(
                f"lateral_load() requires a relationship attribute, got {type(prop).__name__}. "
                "Example: lateral_load(User.team)"
            )
        if prop.secondary is not None:
            raise ValueError(
                f"lateral_load({rel_attr}) does not support Many:Many relationships. "
                "Use selectinload() instead."
            )
        if prop.uselist:
            raise ValueError(
                f"lateral_load({rel_attr}) does not support One:Many relationships. "
                "Use selectinload() instead."
            )
        self.rel_attr = rel_attr


def lateral_load(rel_attr: QueryableAttribute) -> _LateralLoad:
    """Mark a Many:One or One:One relationship for lateral join loading.

    Raises ``ValueError`` for One:Many or Many:Many relationships.
    """
    return _LateralLoad(rel_attr)


def _build_lateral_from_relationship(
    rel_attr: QueryableAttribute,
) -> tuple[Any, Any, ExecutableOption]:
    """Introspect a Many:One relationship and build (lateral_subquery, true(), contains_eager)."""
    prop = rel_attr.property
    target_class = prop.mapper.class_
    parent_class = prop.parent.class_

    conditions = [
        getattr(target_class, remote_col.key) == getattr(parent_class, local_col.key)
        for local_col, remote_col in prop.local_remote_pairs
    ]

    lateral_sub = (
        select(target_class)
        .where(and_(*conditions))
        .correlate(parent_class)
        .lateral(f"_lateral_{prop.key}")
    )
    return lateral_sub, true(), contains_eager(rel_attr, alias=lateral_sub)


def _apply_lateral_joins(q: Any, lateral_joins: LateralJoinType | None) -> Any:
    """Apply lateral subqueries as LEFT JOIN LATERAL to preserve all parent rows."""
    if not lateral_joins:
        return q
    for subquery, condition in lateral_joins:
        q = q.outerjoin(subquery, condition)
    return q


def _apply_search_joins(q: Any, search_joins: list[Any]) -> Any:
    """Apply relationship-based outer joins (from search/filter_by) to a query."""
    seen: set[str] = set()
    for join_rel in search_joins:
        key = str(join_rel)
        if key not in seen:
            seen.add(key)
            q = q.outerjoin(join_rel)
    return q


class AsyncCrud(Generic[ModelType]):
    """Generic async CRUD operations for SQLAlchemy models.

    Subclass this and set the `model` class variable, or use `CrudFactory`.
    """

    _resolved_lateral: ClassVar[_ResolvedLateral | None] = None

    model: ClassVar[type[DeclarativeBase]]
    searchable_fields: ClassVar[Sequence[SearchFieldType] | None] = None
    facet_fields: ClassVar[Sequence[FacetFieldType] | None] = None
    order_fields: ClassVar[Sequence[OrderFieldType] | None] = None
    m2m_fields: ClassVar[M2MFieldType | None] = None
    default_load_options: ClassVar[Sequence[ExecutableOption | _LateralLoad] | None] = (
        None
    )
    lateral_joins: ClassVar[LateralJoinType | None] = None
    cursor_column: ClassVar[Any | None] = None

    @classmethod
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "model" not in cls.__dict__:
            return
        model: type[DeclarativeBase] = cls.__dict__["model"]
        pk_key = model.__mapper__.primary_key[0].key
        assert pk_key is not None
        pk_col = getattr(model, pk_key)

        raw_fields: Sequence[SearchFieldType] | None = cls.__dict__.get(
            "searchable_fields", None
        )
        if raw_fields is None:
            cls.searchable_fields = [pk_col]
        else:
            if not any(
                not isinstance(f, tuple) and f.key == pk_key for f in raw_fields
            ):
                cls.searchable_fields = [pk_col, *raw_fields]

        raw_default_opts = cls.__dict__.get("default_load_options", None)
        if raw_default_opts:
            joins: LateralJoinType = []
            eager: list[ExecutableOption] = []
            clean: list[ExecutableOption] = []
            for opt in raw_default_opts:
                if isinstance(opt, _LateralLoad):
                    lat_sub, condition, eager_opt = _build_lateral_from_relationship(
                        opt.rel_attr
                    )
                    joins.append((lat_sub, condition))
                    eager.append(eager_opt)
                else:
                    clean.append(opt)
            if joins:
                cls._resolved_lateral = _ResolvedLateral(joins=joins, eager=eager)
                cls.default_load_options = clean or None

    @classmethod
    def _get_lateral_joins(cls) -> LateralJoinType | None:
        """Merge manual lateral_joins with ones resolved from default_load_options."""
        resolved = cls._resolved_lateral
        all_lateral = [
            *(cls.lateral_joins or []),
            *(resolved.joins if resolved else []),
        ]
        return all_lateral or None

    @classmethod
    def _resolve_load_options(
        cls, load_options: Sequence[ExecutableOption] | None
    ) -> Sequence[ExecutableOption] | None:
        """Return merged load options."""
        if load_options is not None:
            return list(load_options) or None
        resolved = cls._resolved_lateral
        # default_load_options is cleaned of _LateralLoad markers in __init_subclass__,
        # but its declared type still includes them — cast to reflect the runtime invariant.
        base = cast(list[ExecutableOption], cls.default_load_options or [])
        lateral = resolved.eager if resolved else []
        merged = [*base, *lateral]
        return merged or None

    @classmethod
    async def _reload_with_options(
        cls: type[Self], session: AsyncSession, instance: ModelType
    ) -> ModelType:
        """Re-query instance by PK with default_load_options applied."""
        mapper = cls.model.__mapper__
        pk_filters = [
            getattr(cls.model, col.key) == getattr(instance, col.key)
            for col in mapper.primary_key
        ]
        return await cls.get(session, filters=pk_filters)

    @classmethod
    async def _resolve_m2m(
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        *,
        only_set: bool = False,
    ) -> dict[str, list[Any]]:
        """Resolve M2M fields from a Pydantic schema into related model instances.

        Args:
            session: DB async session
            obj: Pydantic model containing M2M ID fields
            only_set: If True, only process fields explicitly set on the schema

        Returns:
            Dict mapping relationship attr names to lists of related instances
        """
        result: dict[str, list[Any]] = {}
        if not cls.m2m_fields:
            return result

        for schema_field, rel in cls.m2m_fields.items():
            rel_attr = rel.property.key
            related_model = rel.property.mapper.class_
            if only_set and schema_field not in obj.model_fields_set:
                continue
            ids = getattr(obj, schema_field, None)
            if ids is not None:
                related = (
                    (
                        await session.execute(
                            select(related_model).where(related_model.id.in_(ids))
                        )
                    )
                    .scalars()
                    .all()
                )
                if len(related) != len(ids):
                    found_ids = {r.id for r in related}
                    missing = set(ids) - found_ids
                    raise NotFoundError(
                        f"Related {related_model.__name__} not found for IDs: {missing}"
                    )
                result[rel_attr] = list(related)
            else:
                result[rel_attr] = []
        return result

    @classmethod
    def _m2m_schema_fields(cls: type[Self]) -> set[str]:
        """Return the set of schema field names that are M2M fields."""
        if not cls.m2m_fields:
            return set()
        return set(cls.m2m_fields.keys())

    @classmethod
    def _resolve_facet_fields(
        cls: type[Self],
        facet_fields: Sequence[FacetFieldType] | None,
    ) -> Sequence[FacetFieldType] | None:
        """Return facet_fields if given, otherwise fall back to the class-level default."""
        return facet_fields if facet_fields is not None else cls.facet_fields

    @classmethod
    def _prepare_filter_by(
        cls: type[Self],
        filter_by: dict[str, Any] | BaseModel | None,
        facet_fields: Sequence[FacetFieldType] | None,
    ) -> tuple[list[Any], list[Any]]:
        """Normalize filter_by and return (filters, joins) to apply to the query."""
        if isinstance(filter_by, BaseModel):
            filter_by = filter_by.model_dump(exclude_none=True)
        if not filter_by:
            return [], []
        resolved = cls._resolve_facet_fields(facet_fields)
        return build_filter_by(filter_by, resolved or [])

    @classmethod
    async def _build_filter_attributes(
        cls: type[Self],
        session: AsyncSession,
        facet_fields: Sequence[FacetFieldType] | None,
        filters: list[Any],
        search_joins: list[Any],
    ) -> dict[str, list[Any]] | None:
        """Build facet filter_attributes, or return None if no facet fields configured."""
        resolved = cls._resolve_facet_fields(facet_fields)
        if not resolved:
            return None
        return await build_facets(
            session,
            cls.model,
            resolved,
            base_filters=filters,
            base_joins=search_joins,
        )

    @classmethod
    def _resolve_search_columns(
        cls: type[Self],
        search_fields: Sequence[SearchFieldType] | None,
    ) -> list[str] | None:
        """Return search column keys, or None if no searchable fields configured."""
        fields = search_fields if search_fields is not None else cls.searchable_fields
        if not fields:
            return None
        return search_field_keys(fields)

    @classmethod
    def _resolve_order_columns(
        cls: type[Self],
        order_fields: Sequence[OrderFieldType] | None,
    ) -> list[str] | None:
        """Return sort column keys, or None if no order fields configured."""
        fields = order_fields if order_fields is not None else cls.order_fields
        if not fields:
            return None
        return sorted(facet_keys(fields))

    @classmethod
    def _build_paginate_params(
        cls: type[Self],
        *,
        pagination_params: list[inspect.Parameter],
        pagination_fixed: dict[str, Any],
        dep_name: str,
        search: bool,
        filter: bool,
        order: bool,
        search_fields: Sequence[SearchFieldType] | None,
        facet_fields: Sequence[FacetFieldType] | None,
        order_fields: Sequence[OrderFieldType] | None,
        default_order_field: QueryableAttribute[Any] | None,
        default_order: Literal["asc", "desc"],
    ) -> Callable[..., Awaitable[dict[str, Any]]]:
        """Build a consolidated FastAPI dependency that merges pagination, search, filter, and order params."""
        all_params: list[inspect.Parameter] = list(pagination_params)
        pagination_param_names = tuple(p.name for p in pagination_params)
        reserved_names: set[str] = set(pagination_param_names)

        search_keys: list[str] | None = None
        if search:
            search_keys = cls._resolve_search_columns(search_fields)
            if search_keys:
                all_params.extend(
                    [
                        inspect.Parameter(
                            "search",
                            inspect.Parameter.KEYWORD_ONLY,
                            annotation=str | None,
                            default=Query(
                                default=None, description="Search query string"
                            ),
                        ),
                        inspect.Parameter(
                            "search_column",
                            inspect.Parameter.KEYWORD_ONLY,
                            annotation=str | None,
                            default=Query(
                                default=None,
                                description="Restrict search to a single column",
                                enum=search_keys,
                            ),
                        ),
                    ]
                )
                reserved_names.update({"search", "search_column"})

        filter_keys: list[str] | None = None
        if filter:
            resolved_facets = cls._resolve_facet_fields(facet_fields)
            if resolved_facets:
                filter_keys = facet_keys(resolved_facets)
                for k in filter_keys:
                    if k in reserved_names:
                        raise ValueError(
                            f"Facet field key {k!r} conflicts with a reserved "
                            f"parameter name. Reserved names: {sorted(reserved_names)}"
                        )
                all_params.extend(
                    inspect.Parameter(
                        k,
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=list[str] | None,
                        default=Query(default=None),
                    )
                    for k in filter_keys
                )
                reserved_names.update(filter_keys)

        order_field_map: dict[str, OrderFieldType] | None = None
        order_valid_keys: list[str] | None = None
        if order:
            resolved_order = (
                order_fields if order_fields is not None else cls.order_fields
            )
            if resolved_order:
                keys = facet_keys(resolved_order)
                order_field_map = dict(zip(keys, resolved_order))
                order_valid_keys = sorted(order_field_map.keys())
                all_params.extend(
                    [
                        inspect.Parameter(
                            "order_by",
                            inspect.Parameter.KEYWORD_ONLY,
                            annotation=str | None,
                            default=Query(
                                None,
                                description=f"Field to order by. Valid values: {order_valid_keys}",
                                enum=order_valid_keys,
                            ),
                        ),
                        inspect.Parameter(
                            "order",
                            inspect.Parameter.KEYWORD_ONLY,
                            annotation=Literal["asc", "desc"],
                            default=Query(default_order, description="Sort direction"),
                        ),
                    ]
                )

        async def dependency(**kwargs: Any) -> dict[str, Any]:
            result: dict[str, Any] = dict(pagination_fixed)
            for name in pagination_param_names:
                result[name] = kwargs[name]

            if search_keys is not None:
                search_val = kwargs.get("search")
                if search_val is not None:
                    result["search"] = search_val
                search_col_val = kwargs.get("search_column")
                if search_col_val is not None:
                    result["search_column"] = search_col_val

            if filter_keys is not None:
                filter_by = {
                    k: kwargs[k] for k in filter_keys if kwargs.get(k) is not None
                }
                result["filter_by"] = filter_by or None

            if order_field_map is not None:
                order_by_val = kwargs.get("order_by")
                order_dir = kwargs.get("order", default_order)
                if order_by_val is None:
                    field = default_order_field
                elif order_by_val not in order_field_map:
                    raise InvalidOrderFieldError(order_by_val, order_valid_keys or [])
                else:
                    field = order_field_map[order_by_val]
                if field is not None:
                    if isinstance(field, tuple):
                        col = field[-1]
                        result["order_by"] = (
                            col.asc() if order_dir == "asc" else col.desc()
                        )
                        result["order_joins"] = list(field[:-1])
                    else:
                        result["order_by"] = (
                            field.asc() if order_dir == "asc" else field.desc()
                        )
                else:
                    result["order_by"] = None

            return result

        dependency.__name__ = dep_name
        dependency.__signature__ = inspect.Signature(  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
            parameters=all_params,
        )
        return dependency

    @classmethod
    def offset_paginate_params(
        cls: type[Self],
        *,
        default_page_size: int = 20,
        max_page_size: int = 100,
        include_total: bool = True,
        search: bool = True,
        filter: bool = True,
        order: bool = True,
        search_fields: Sequence[SearchFieldType] | None = None,
        facet_fields: Sequence[FacetFieldType] | None = None,
        order_fields: Sequence[OrderFieldType] | None = None,
        default_order_field: QueryableAttribute[Any] | None = None,
        default_order: Literal["asc", "desc"] = "asc",
    ) -> Callable[..., Awaitable[dict[str, Any]]]:
        """Return a FastAPI dependency that collects all params for :meth:`offset_paginate`.

        Args:
            default_page_size: Default ``items_per_page`` value.
            max_page_size: Maximum ``items_per_page`` value.
            include_total: Whether to include total count (not a query param).
            search: Enable search query parameters.
            filter: Enable facet filter query parameters.
            order: Enable order query parameters.
            search_fields: Override searchable fields.
            facet_fields: Override facet fields.
            order_fields: Override order fields.
            default_order_field: Default field to order by when ``order_by`` is absent.
            default_order: Default sort direction.

        Returns:
            An async dependency that resolves to a dict ready to be unpacked
            into :meth:`offset_paginate`.
        """
        pagination_params = [
            inspect.Parameter(
                "page",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=int,
                default=Query(1, ge=1, description="Page number (1-indexed)"),
            ),
            inspect.Parameter(
                "items_per_page",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=int,
                default=_page_size_query(default_page_size, max_page_size),
            ),
        ]
        return cls._build_paginate_params(
            pagination_params=pagination_params,
            pagination_fixed={"include_total": include_total},
            dep_name=f"{cls.model.__name__}OffsetPaginateParams",
            search=search,
            filter=filter,
            order=order,
            search_fields=search_fields,
            facet_fields=facet_fields,
            order_fields=order_fields,
            default_order_field=default_order_field,
            default_order=default_order,
        )

    @classmethod
    def cursor_paginate_params(
        cls: type[Self],
        *,
        default_page_size: int = 20,
        max_page_size: int = 100,
        search: bool = True,
        filter: bool = True,
        order: bool = True,
        search_fields: Sequence[SearchFieldType] | None = None,
        facet_fields: Sequence[FacetFieldType] | None = None,
        order_fields: Sequence[OrderFieldType] | None = None,
        default_order_field: QueryableAttribute[Any] | None = None,
        default_order: Literal["asc", "desc"] = "asc",
    ) -> Callable[..., Awaitable[dict[str, Any]]]:
        """Return a FastAPI dependency that collects all params for :meth:`cursor_paginate`.

        Args:
            default_page_size: Default ``items_per_page`` value.
            max_page_size: Maximum ``items_per_page`` value.
            search: Enable search query parameters.
            filter: Enable facet filter query parameters.
            order: Enable order query parameters.
            search_fields: Override searchable fields.
            facet_fields: Override facet fields.
            order_fields: Override order fields.
            default_order_field: Default field to order by when ``order_by`` is absent.
            default_order: Default sort direction.

        Returns:
            An async dependency that resolves to a dict ready to be unpacked
            into :meth:`cursor_paginate`.
        """
        pagination_params = [
            inspect.Parameter(
                "cursor",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=str | None,
                default=Query(
                    None, description="Cursor token from a previous response"
                ),
            ),
            inspect.Parameter(
                "items_per_page",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=int,
                default=_page_size_query(default_page_size, max_page_size),
            ),
        ]
        return cls._build_paginate_params(
            pagination_params=pagination_params,
            pagination_fixed={},
            dep_name=f"{cls.model.__name__}CursorPaginateParams",
            search=search,
            filter=filter,
            order=order,
            search_fields=search_fields,
            facet_fields=facet_fields,
            order_fields=order_fields,
            default_order_field=default_order_field,
            default_order=default_order,
        )

    @classmethod
    def paginate_params(
        cls: type[Self],
        *,
        default_page_size: int = 20,
        max_page_size: int = 100,
        default_pagination_type: PaginationType = PaginationType.OFFSET,
        include_total: bool = True,
        search: bool = True,
        filter: bool = True,
        order: bool = True,
        search_fields: Sequence[SearchFieldType] | None = None,
        facet_fields: Sequence[FacetFieldType] | None = None,
        order_fields: Sequence[OrderFieldType] | None = None,
        default_order_field: QueryableAttribute[Any] | None = None,
        default_order: Literal["asc", "desc"] = "asc",
    ) -> Callable[..., Awaitable[dict[str, Any]]]:
        """Return a FastAPI dependency that collects all params for :meth:`paginate`.

        Args:
            default_page_size: Default ``items_per_page`` value.
            max_page_size: Maximum ``items_per_page`` value.
            default_pagination_type: Default pagination strategy.
            include_total: Whether to include total count (not a query param).
            search: Enable search query parameters.
            filter: Enable facet filter query parameters.
            order: Enable order query parameters.
            search_fields: Override searchable fields.
            facet_fields: Override facet fields.
            order_fields: Override order fields.
            default_order_field: Default field to order by when ``order_by`` is absent.
            default_order: Default sort direction.

        Returns:
            An async dependency that resolves to a dict ready to be unpacked
            into :meth:`paginate`.
        """
        pagination_params = [
            inspect.Parameter(
                "pagination_type",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=PaginationType,
                default=Query(
                    default_pagination_type, description="Pagination strategy"
                ),
            ),
            inspect.Parameter(
                "page",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=int,
                default=Query(
                    1, ge=1, description="Page number (1-indexed, offset only)"
                ),
            ),
            inspect.Parameter(
                "cursor",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=str | None,
                default=Query(
                    None,
                    description="Cursor token from a previous response (cursor only)",
                ),
            ),
            inspect.Parameter(
                "items_per_page",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=int,
                default=_page_size_query(default_page_size, max_page_size),
            ),
        ]
        return cls._build_paginate_params(
            pagination_params=pagination_params,
            pagination_fixed={"include_total": include_total},
            dep_name=f"{cls.model.__name__}PaginateParams",
            search=search,
            filter=filter,
            order=order,
            search_fields=search_fields,
            facet_fields=facet_fields,
            order_fields=order_fields,
            default_order_field=default_order_field,
            default_order=default_order,
        )

    @overload
    @classmethod
    async def create(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        *,
        schema: type[SchemaType],
    ) -> Response[SchemaType]: ...

    @overload
    @classmethod
    async def create(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        *,
        schema: None = ...,
    ) -> ModelType: ...

    @classmethod
    async def create(
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        *,
        schema: type[BaseModel] | None = None,
    ) -> ModelType | Response[Any]:
        """Create a new record in the database.

        Args:
            session: DB async session
            obj: Pydantic model with data to create
            schema: Pydantic schema to serialize the result into. When provided,
                the result is automatically wrapped in a ``Response[schema]``.

        Returns:
            Created model instance, or ``Response[schema]`` when ``schema`` is given.
        """
        async with get_transaction(session):
            m2m_exclude = cls._m2m_schema_fields()
            data = (
                obj.model_dump(exclude=m2m_exclude) if m2m_exclude else obj.model_dump()
            )
            db_model = cls.model(**data)

            if m2m_exclude:
                m2m_resolved = await cls._resolve_m2m(session, obj)
                for rel_attr, related_instances in m2m_resolved.items():
                    setattr(db_model, rel_attr, related_instances)

            session.add(db_model)
        await session.refresh(db_model)
        if cls.default_load_options:
            db_model = await cls._reload_with_options(session, db_model)
        result = cast(ModelType, db_model)
        if schema:
            return Response(data=schema.model_validate(result))
        return result

    @overload
    @classmethod
    async def get(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
        *,
        joins: JoinType | None = None,
        outer_join: bool = False,
        with_for_update: bool = False,
        load_options: Sequence[ExecutableOption] | None = None,
        schema: type[SchemaType],
    ) -> Response[SchemaType]: ...

    @overload
    @classmethod
    async def get(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
        *,
        joins: JoinType | None = None,
        outer_join: bool = False,
        with_for_update: bool = False,
        load_options: Sequence[ExecutableOption] | None = None,
        schema: None = ...,
    ) -> ModelType: ...

    @classmethod
    async def get(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
        *,
        joins: JoinType | None = None,
        outer_join: bool = False,
        with_for_update: bool = False,
        load_options: Sequence[ExecutableOption] | None = None,
        schema: type[BaseModel] | None = None,
    ) -> ModelType | Response[Any]:
        """Get exactly one record. Raises NotFoundError if not found.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            joins: List of (model, condition) tuples for joining related tables
            outer_join: Use LEFT OUTER JOIN instead of INNER JOIN
            with_for_update: Lock the row for update
            load_options: SQLAlchemy loader options (e.g., selectinload)
            schema: Pydantic schema to serialize the result into. When provided,
                the result is automatically wrapped in a ``Response[schema]``.

        Returns:
            Model instance, or ``Response[schema]`` when ``schema`` is given.

        Raises:
            NotFoundError: If no record found
            MultipleResultsFound: If more than one record found
        """
        result = await cls.get_or_none(
            session,
            filters,
            joins=joins,
            outer_join=outer_join,
            with_for_update=with_for_update,
            load_options=load_options,
            schema=schema,
        )
        if result is None:
            raise NotFoundError()
        return result

    @overload
    @classmethod
    async def get_or_none(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
        *,
        joins: JoinType | None = None,
        outer_join: bool = False,
        with_for_update: bool = False,
        load_options: Sequence[ExecutableOption] | None = None,
        schema: type[SchemaType],
    ) -> Response[SchemaType] | None: ...

    @overload
    @classmethod
    async def get_or_none(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
        *,
        joins: JoinType | None = None,
        outer_join: bool = False,
        with_for_update: bool = False,
        load_options: Sequence[ExecutableOption] | None = None,
        schema: None = ...,
    ) -> ModelType | None: ...

    @classmethod
    async def get_or_none(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
        *,
        joins: JoinType | None = None,
        outer_join: bool = False,
        with_for_update: bool = False,
        load_options: Sequence[ExecutableOption] | None = None,
        schema: type[BaseModel] | None = None,
    ) -> ModelType | Response[Any] | None:
        """Get exactly one record, or ``None`` if not found.

        Like :meth:`get` but returns ``None`` instead of raising
        :class:`~fastapi_toolsets.exceptions.NotFoundError` when no record
        matches the filters.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            joins: List of (model, condition) tuples for joining related tables
            outer_join: Use LEFT OUTER JOIN instead of INNER JOIN
            with_for_update: Lock the row for update
            load_options: SQLAlchemy loader options (e.g., selectinload)
            schema: Pydantic schema to serialize the result into. When provided,
                the result is automatically wrapped in a ``Response[schema]``.

        Returns:
            Model instance, ``Response[schema]`` when ``schema`` is given,
            or ``None`` when no record matches.

        Raises:
            MultipleResultsFound: If more than one record found
        """
        q = select(cls.model)
        q = _apply_joins(q, joins, outer_join)
        if load_options is None:
            q = _apply_lateral_joins(q, cls._get_lateral_joins())
        q = q.where(and_(*filters))
        if resolved := cls._resolve_load_options(load_options):
            q = q.options(*resolved)
        if with_for_update:
            q = q.with_for_update()
        result = await session.execute(q)
        item = result.unique().scalar_one_or_none()
        if item is None:
            return None
        db_model = cast(ModelType, item)
        if schema:
            return Response(data=schema.model_validate(db_model))
        return db_model

    @overload
    @classmethod
    async def first(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any] | None = None,
        *,
        joins: JoinType | None = None,
        outer_join: bool = False,
        with_for_update: bool = False,
        load_options: Sequence[ExecutableOption] | None = None,
        schema: type[SchemaType],
    ) -> Response[SchemaType] | None: ...

    @overload
    @classmethod
    async def first(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any] | None = None,
        *,
        joins: JoinType | None = None,
        outer_join: bool = False,
        with_for_update: bool = False,
        load_options: Sequence[ExecutableOption] | None = None,
        schema: None = ...,
    ) -> ModelType | None: ...

    @classmethod
    async def first(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any] | None = None,
        *,
        joins: JoinType | None = None,
        outer_join: bool = False,
        with_for_update: bool = False,
        load_options: Sequence[ExecutableOption] | None = None,
        schema: type[BaseModel] | None = None,
    ) -> ModelType | Response[Any] | None:
        """Get the first matching record, or None.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            joins: List of (model, condition) tuples for joining related tables
            outer_join: Use LEFT OUTER JOIN instead of INNER JOIN
            with_for_update: Lock the row for update
            load_options: SQLAlchemy loader options (e.g., selectinload)
            schema: Pydantic schema to serialize the result into. When provided,
                the result is automatically wrapped in a ``Response[schema]``.

        Returns:
            Model instance, ``Response[schema]`` when ``schema`` is given,
            or ``None`` when no record matches.
        """
        q = select(cls.model)
        q = _apply_joins(q, joins, outer_join)
        if load_options is None:
            q = _apply_lateral_joins(q, cls._get_lateral_joins())
        if filters:
            q = q.where(and_(*filters))
        if resolved := cls._resolve_load_options(load_options):
            q = q.options(*resolved)
        if with_for_update:
            q = q.with_for_update()
        result = await session.execute(q)
        item = result.unique().scalars().first()
        if item is None:
            return None
        db_model = cast(ModelType, item)
        if schema:
            return Response(data=schema.model_validate(db_model))
        return db_model

    @classmethod
    async def get_multi(
        cls: type[Self],
        session: AsyncSession,
        *,
        filters: list[Any] | None = None,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: Sequence[ExecutableOption] | None = None,
        order_by: OrderByClause | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Sequence[ModelType]:
        """Get multiple records from the database.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            joins: List of (model, condition) tuples for joining related tables
            outer_join: Use LEFT OUTER JOIN instead of INNER JOIN
            load_options: SQLAlchemy loader options
            order_by: Column or list of columns to order by
            limit: Max number of rows to return
            offset: Rows to skip

        Returns:
            List of model instances
        """
        q = select(cls.model)
        q = _apply_joins(q, joins, outer_join)
        if load_options is None:
            q = _apply_lateral_joins(q, cls._get_lateral_joins())
        if filters:
            q = q.where(and_(*filters))
        if resolved := cls._resolve_load_options(load_options):
            q = q.options(*resolved)
        if order_by is not None:
            q = q.order_by(order_by)
        if offset is not None:
            q = q.offset(offset)
        if limit is not None:
            q = q.limit(limit)
        result = await session.execute(q)
        return cast(Sequence[ModelType], result.unique().scalars().all())

    @overload
    @classmethod
    async def update(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        filters: list[Any],
        *,
        exclude_unset: bool = True,
        exclude_none: bool = False,
        schema: type[SchemaType],
    ) -> Response[SchemaType]: ...

    @overload
    @classmethod
    async def update(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        filters: list[Any],
        *,
        exclude_unset: bool = True,
        exclude_none: bool = False,
        schema: None = ...,
    ) -> ModelType: ...

    @classmethod
    async def update(
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        filters: list[Any],
        *,
        exclude_unset: bool = True,
        exclude_none: bool = False,
        schema: type[BaseModel] | None = None,
    ) -> ModelType | Response[Any]:
        """Update a record in the database.

        Args:
            session: DB async session
            obj: Pydantic model with update data
            filters: List of SQLAlchemy filter conditions
            exclude_unset: Exclude fields not explicitly set in the schema
            exclude_none: Exclude fields with None value
            schema: Pydantic schema to serialize the result into. When provided,
                the result is automatically wrapped in a ``Response[schema]``.

        Returns:
            Updated model instance, or ``Response[schema]`` when ``schema`` is given.

        Raises:
            NotFoundError: If no record found
        """
        async with get_transaction(session):
            m2m_exclude = cls._m2m_schema_fields()

            # Eagerly load M2M relationships that will be updated so that
            # setattr does not trigger a lazy load (which fails in async).
            m2m_load_options: list[ExecutableOption] = []
            if m2m_exclude and cls.m2m_fields:
                for schema_field, rel in cls.m2m_fields.items():
                    if schema_field in obj.model_fields_set:
                        m2m_load_options.append(selectinload(rel))

            db_model = await cls.get(
                session=session,
                filters=filters,
                load_options=m2m_load_options or None,
            )
            values = obj.model_dump(
                exclude_unset=exclude_unset,
                exclude_none=exclude_none,
                exclude=m2m_exclude,
            )
            for key, value in values.items():
                setattr(db_model, key, value)

            if m2m_exclude:
                m2m_resolved = await cls._resolve_m2m(session, obj, only_set=True)
                for rel_attr, related_instances in m2m_resolved.items():
                    setattr(db_model, rel_attr, related_instances)
        await session.refresh(db_model)
        if cls.default_load_options:
            db_model = await cls._reload_with_options(session, db_model)
        if schema:
            return Response(data=schema.model_validate(db_model))
        return db_model

    @classmethod
    async def upsert(
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        index_elements: list[str],
        *,
        set_: BaseModel | None = None,
        where: WhereHavingRole | None = None,
    ) -> ModelType | None:
        """Create or update a record (PostgreSQL only).

        Uses INSERT ... ON CONFLICT for atomic upsert.

        Args:
            session: DB async session
            obj: Pydantic model with data
            index_elements: Columns for ON CONFLICT (unique constraint)
            set_: Pydantic model for ON CONFLICT DO UPDATE SET
            where: WHERE clause for ON CONFLICT DO UPDATE

        Returns:
            Model instance
        """
        async with get_transaction(session):
            values = obj.model_dump(exclude_unset=True)
            q = insert(cls.model).values(**values)
            if set_:
                q = q.on_conflict_do_update(
                    index_elements=index_elements,
                    set_=set_.model_dump(exclude_unset=True),
                    where=where,
                )
            else:
                q = q.on_conflict_do_nothing(index_elements=index_elements)
            q = q.returning(cls.model)
            result = await session.execute(q)
            try:
                db_model = result.unique().scalar_one()
            except NoResultFound:
                db_model = await cls.first(
                    session=session,
                    filters=[getattr(cls.model, k) == v for k, v in values.items()],
                )
        return cast(ModelType | None, db_model)

    @overload
    @classmethod
    async def delete(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
        *,
        return_response: Literal[True],
    ) -> Response[None]: ...

    @overload
    @classmethod
    async def delete(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
        *,
        return_response: Literal[False] = ...,
    ) -> None: ...

    @classmethod
    async def delete(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
        *,
        return_response: bool = False,
    ) -> None | Response[None]:
        """Delete records from the database.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            return_response: When ``True``, returns ``Response[None]`` instead
                of ``None``. Useful for API endpoints that expect a consistent
                response envelope.

        Returns:
            ``None``, or ``Response[None]`` when ``return_response=True``.
        """
        async with get_transaction(session):
            result = await session.execute(select(cls.model).where(and_(*filters)))
            objects = result.scalars().all()
            for obj in objects:
                await session.delete(obj)
        if return_response:
            return Response(data=None)
        return None

    @classmethod
    async def count(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any] | None = None,
        *,
        joins: JoinType | None = None,
        outer_join: bool = False,
    ) -> int:
        """Count records matching the filters.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            joins: List of (model, condition) tuples for joining related tables
            outer_join: Use LEFT OUTER JOIN instead of INNER JOIN

        Returns:
            Number of matching records
        """
        q = select(func.count()).select_from(cls.model)
        q = _apply_joins(q, joins, outer_join)
        if filters:
            q = q.where(and_(*filters))
        result = await session.execute(q)
        return result.scalar_one()

    @classmethod
    async def exists(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
        *,
        joins: JoinType | None = None,
        outer_join: bool = False,
    ) -> bool:
        """Check if a record exists.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            joins: List of (model, condition) tuples for joining related tables
            outer_join: Use LEFT OUTER JOIN instead of INNER JOIN

        Returns:
            True if at least one record matches
        """
        q = select(cls.model)
        q = _apply_joins(q, joins, outer_join)
        q = q.where(and_(*filters)).exists().select()
        result = await session.execute(q)
        return bool(result.scalar())

    @classmethod
    async def offset_paginate(
        cls: type[Self],
        session: AsyncSession,
        *,
        filters: list[Any] | None = None,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: Sequence[ExecutableOption] | None = None,
        order_by: OrderByClause | None = None,
        order_joins: list[Any] | None = None,
        page: int = 1,
        items_per_page: int = 20,
        include_total: bool = True,
        search: str | SearchConfig | None = None,
        search_fields: Sequence[SearchFieldType] | None = None,
        search_column: str | None = None,
        order_fields: Sequence[OrderFieldType] | None = None,
        facet_fields: Sequence[FacetFieldType] | None = None,
        filter_by: dict[str, Any] | BaseModel | None = None,
        schema: type[BaseModel],
    ) -> OffsetPaginatedResponse[Any]:
        """Get paginated results using offset-based pagination.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            joins: List of (model, condition) tuples for joining related tables
            outer_join: Use LEFT OUTER JOIN instead of INNER JOIN
            load_options: SQLAlchemy loader options
            order_by: Column or list of columns to order by
            page: Page number (1-indexed)
            items_per_page: Number of items per page
            include_total: When ``False``, skip the ``COUNT`` query;
                ``pagination.total_count`` will be ``None``.
            search: Search query string or SearchConfig object
            search_fields: Fields to search in (overrides class default)
            search_column: Restrict search to a single column key.
            order_fields: Fields allowed for sorting (overrides class default).
            facet_fields: Columns to compute distinct values for (overrides class default)
            filter_by: Dict of {column_key: value} to filter by declared facet fields.
                Keys must match the column.key of a facet field. Scalar → equality,
                list → IN clause. Raises InvalidFacetFilterError for unknown keys.
            schema: Pydantic schema to serialize each item into.

        Returns:
            PaginatedResponse with OffsetPagination metadata
        """
        filters = list(filters) if filters else []
        offset = (page - 1) * items_per_page

        fb_filters, search_joins = cls._prepare_filter_by(filter_by, facet_fields)
        filters.extend(fb_filters)

        # Build search filters
        if search:
            search_filters, new_search_joins = build_search_filters(
                cls.model,
                search,
                search_fields=search_fields,
                default_fields=cls.searchable_fields,
                search_column=search_column,
            )
            filters.extend(search_filters)
            search_joins.extend(new_search_joins)

        # Build query with joins
        q = select(cls.model)

        # Apply explicit joins
        q = _apply_joins(q, joins, outer_join)

        # Apply lateral joins (Many:One relationship loading, excluded from count query)
        if load_options is None:
            q = _apply_lateral_joins(q, cls._get_lateral_joins())

        # Apply search joins (always outer joins for search)
        q = _apply_search_joins(q, search_joins)

        # Apply order joins (relation joins required for order_by field)
        if order_joins:
            q = _apply_search_joins(q, order_joins)

        if filters:
            q = q.where(and_(*filters))
        if resolved := cls._resolve_load_options(load_options):
            q = q.options(*resolved)
        if order_by is not None:
            q = q.order_by(order_by)

        if include_total:
            q = q.offset(offset).limit(items_per_page)
            result = await session.execute(q)
            raw_items = cast(list[ModelType], result.unique().scalars().all())

            # Count query (with same joins and filters)
            pk_col = cls.model.__mapper__.primary_key[0]
            count_q = select(func.count(func.distinct(getattr(cls.model, pk_col.name))))
            count_q = count_q.select_from(cls.model)

            # Apply explicit joins to count query
            count_q = _apply_joins(count_q, joins, outer_join)

            # Apply search joins to count query
            count_q = _apply_search_joins(count_q, search_joins)

            if filters:
                count_q = count_q.where(and_(*filters))

            count_result = await session.execute(count_q)
            total_count: int | None = count_result.scalar_one()
            has_more = page * items_per_page < total_count
        else:
            # Fetch one extra row to detect if a next page exists without COUNT
            q = q.offset(offset).limit(items_per_page + 1)
            result = await session.execute(q)
            raw_items = cast(list[ModelType], result.unique().scalars().all())
            has_more = len(raw_items) > items_per_page
            raw_items = raw_items[:items_per_page]
            total_count = None

        items: list[Any] = [schema.model_validate(item) for item in raw_items]

        filter_attributes = await cls._build_filter_attributes(
            session, facet_fields, filters, search_joins
        )
        search_columns = cls._resolve_search_columns(search_fields)
        order_columns = cls._resolve_order_columns(order_fields)

        return OffsetPaginatedResponse(
            data=items,
            pagination=OffsetPagination(
                total_count=total_count,
                items_per_page=items_per_page,
                page=page,
                has_more=has_more,
            ),
            filter_attributes=filter_attributes,
            search_columns=search_columns,
            order_columns=order_columns,
        )

    @classmethod
    async def cursor_paginate(
        cls: type[Self],
        session: AsyncSession,
        *,
        cursor: str | None = None,
        filters: list[Any] | None = None,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: Sequence[ExecutableOption] | None = None,
        order_by: OrderByClause | None = None,
        order_joins: list[Any] | None = None,
        items_per_page: int = 20,
        search: str | SearchConfig | None = None,
        search_fields: Sequence[SearchFieldType] | None = None,
        search_column: str | None = None,
        order_fields: Sequence[OrderFieldType] | None = None,
        facet_fields: Sequence[FacetFieldType] | None = None,
        filter_by: dict[str, Any] | BaseModel | None = None,
        schema: type[BaseModel],
    ) -> CursorPaginatedResponse[Any]:
        """Get paginated results using cursor-based pagination.

        Args:
            session: DB async session.
            cursor: Cursor string from a previous ``CursorPagination``.
                Omit (or pass ``None``) to start from the beginning.
            filters: List of SQLAlchemy filter conditions.
            joins: List of ``(model, condition)`` tuples for joining related
                tables.
            outer_join: Use LEFT OUTER JOIN instead of INNER JOIN.
            load_options: SQLAlchemy loader options. Falls back to
                ``default_load_options`` (including any lateral joins) when not
                provided. When explicitly supplied, the caller takes full control
                and lateral joins are skipped.
            order_by: Additional ordering applied after the cursor column.
            items_per_page: Number of items per page (default 20).
            search: Search query string or SearchConfig object.
            search_fields: Fields to search in (overrides class default).
            search_column: Restrict search to a single column key.
            order_fields: Fields allowed for sorting (overrides class default).
            facet_fields: Columns to compute distinct values for (overrides class default).
            filter_by: Dict of {column_key: value} to filter by declared facet fields.
                Keys must match the column.key of a facet field. Scalar → equality,
                list → IN clause. Raises InvalidFacetFilterError for unknown keys.
            schema: Optional Pydantic schema to serialize each item into.

        Returns:
            PaginatedResponse with CursorPagination metadata
        """
        filters = list(filters) if filters else []

        fb_filters, search_joins = cls._prepare_filter_by(filter_by, facet_fields)
        filters.extend(fb_filters)

        if cls.cursor_column is None:
            raise ValueError(
                f"{cls.__name__}.cursor_column is not set. "
                "Pass cursor_column=<column> to CrudFactory() to use cursor_paginate."
            )
        cursor_column: Any = cls.cursor_column
        cursor_col_name: str = cursor_column.key

        direction = _CursorDirection.NEXT
        if cursor is not None:
            raw_val, direction = _decode_cursor(cursor)
            col_type = cursor_column.property.columns[0].type
            cursor_val: Any = _parse_cursor_value(raw_val, col_type)
            if direction is _CursorDirection.PREV:
                filters.append(cursor_column < cursor_val)
            else:
                filters.append(cursor_column > cursor_val)

        # Build search filters
        if search:
            search_filters, new_search_joins = build_search_filters(
                cls.model,
                search,
                search_fields=search_fields,
                default_fields=cls.searchable_fields,
                search_column=search_column,
            )
            filters.extend(search_filters)
            search_joins.extend(new_search_joins)

        # Build query
        q = select(cls.model)

        # Apply explicit joins
        q = _apply_joins(q, joins, outer_join)

        # Apply lateral joins (Many:One relationship loading)
        if load_options is None:
            q = _apply_lateral_joins(q, cls._get_lateral_joins())

        # Apply search joins (always outer joins)
        q = _apply_search_joins(q, search_joins)

        # Apply order joins (relation joins required for order_by field)
        if order_joins:
            q = _apply_search_joins(q, order_joins)

        if filters:
            q = q.where(and_(*filters))
        if resolved := cls._resolve_load_options(load_options):
            q = q.options(*resolved)

        # Cursor column is always the primary sort; reverse direction for prev traversal
        if direction is _CursorDirection.PREV:
            q = q.order_by(cursor_column.desc())
        else:
            q = q.order_by(cursor_column)
        if order_by is not None:
            q = q.order_by(order_by)

        # Fetch one extra to detect whether another page exists in this direction
        q = q.limit(items_per_page + 1)
        result = await session.execute(q)
        raw_items = cast(list[ModelType], result.unique().scalars().all())

        has_more = len(raw_items) > items_per_page
        items_page = raw_items[:items_per_page]

        # Restore ascending order when traversing backward
        if direction is _CursorDirection.PREV:
            items_page = list(reversed(items_page))

        # next_cursor: points past the last item in ascending order
        next_cursor: str | None = None
        if direction is _CursorDirection.NEXT:
            if has_more and items_page:
                next_cursor = _encode_cursor(
                    getattr(items_page[-1], cursor_col_name),
                    direction=_CursorDirection.NEXT,
                )
        else:
            # Going backward: always provide a next_cursor to allow returning forward
            if items_page:
                next_cursor = _encode_cursor(
                    getattr(items_page[-1], cursor_col_name),
                    direction=_CursorDirection.NEXT,
                )

        # prev_cursor: points before the first item in ascending order
        prev_cursor: str | None = None
        if direction is _CursorDirection.NEXT and cursor is not None and items_page:
            prev_cursor = _encode_cursor(
                getattr(items_page[0], cursor_col_name), direction=_CursorDirection.PREV
            )
        elif direction is _CursorDirection.PREV and has_more and items_page:
            prev_cursor = _encode_cursor(
                getattr(items_page[0], cursor_col_name), direction=_CursorDirection.PREV
            )

        items: list[Any] = [schema.model_validate(item) for item in items_page]

        filter_attributes = await cls._build_filter_attributes(
            session, facet_fields, filters, search_joins
        )
        search_columns = cls._resolve_search_columns(search_fields)
        order_columns = cls._resolve_order_columns(order_fields)

        return CursorPaginatedResponse(
            data=items,
            pagination=CursorPagination(
                next_cursor=next_cursor,
                prev_cursor=prev_cursor,
                items_per_page=items_per_page,
                has_more=has_more,
            ),
            filter_attributes=filter_attributes,
            search_columns=search_columns,
            order_columns=order_columns,
        )

    @overload
    @classmethod
    async def paginate(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        *,
        pagination_type: Literal[PaginationType.OFFSET],
        filters: list[Any] | None = ...,
        joins: JoinType | None = ...,
        outer_join: bool = ...,
        load_options: Sequence[ExecutableOption] | None = ...,
        order_by: OrderByClause | None = ...,
        order_joins: list[Any] | None = ...,
        page: int = ...,
        cursor: str | None = ...,
        items_per_page: int = ...,
        include_total: bool = ...,
        search: str | SearchConfig | None = ...,
        search_fields: Sequence[SearchFieldType] | None = ...,
        search_column: str | None = ...,
        order_fields: Sequence[OrderFieldType] | None = ...,
        facet_fields: Sequence[FacetFieldType] | None = ...,
        filter_by: dict[str, Any] | BaseModel | None = ...,
        schema: type[BaseModel],
    ) -> OffsetPaginatedResponse[Any]: ...

    @overload
    @classmethod
    async def paginate(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        *,
        pagination_type: Literal[PaginationType.CURSOR],
        filters: list[Any] | None = ...,
        joins: JoinType | None = ...,
        outer_join: bool = ...,
        load_options: Sequence[ExecutableOption] | None = ...,
        order_by: OrderByClause | None = ...,
        order_joins: list[Any] | None = ...,
        page: int = ...,
        cursor: str | None = ...,
        items_per_page: int = ...,
        include_total: bool = ...,
        search: str | SearchConfig | None = ...,
        search_fields: Sequence[SearchFieldType] | None = ...,
        search_column: str | None = ...,
        order_fields: Sequence[OrderFieldType] | None = ...,
        facet_fields: Sequence[FacetFieldType] | None = ...,
        filter_by: dict[str, Any] | BaseModel | None = ...,
        schema: type[BaseModel],
    ) -> CursorPaginatedResponse[Any]: ...

    @classmethod
    async def paginate(
        cls: type[Self],
        session: AsyncSession,
        *,
        pagination_type: PaginationType = PaginationType.OFFSET,
        filters: list[Any] | None = None,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: Sequence[ExecutableOption] | None = None,
        order_by: OrderByClause | None = None,
        order_joins: list[Any] | None = None,
        page: int = 1,
        cursor: str | None = None,
        items_per_page: int = 20,
        include_total: bool = True,
        search: str | SearchConfig | None = None,
        search_fields: Sequence[SearchFieldType] | None = None,
        search_column: str | None = None,
        order_fields: Sequence[OrderFieldType] | None = None,
        facet_fields: Sequence[FacetFieldType] | None = None,
        filter_by: dict[str, Any] | BaseModel | None = None,
        schema: type[BaseModel],
    ) -> OffsetPaginatedResponse[Any] | CursorPaginatedResponse[Any]:
        """Get paginated results using either offset or cursor pagination.

        Args:
            session: DB async session.
            pagination_type: Pagination strategy.  Defaults to
                ``PaginationType.OFFSET``.
            filters: List of SQLAlchemy filter conditions.
            joins: List of ``(model, condition)`` tuples for joining related
                tables.
            outer_join: Use LEFT OUTER JOIN instead of INNER JOIN.
            load_options: SQLAlchemy loader options.  Falls back to
                ``default_load_options`` when not provided.
            order_by: Column or expression to order results by.
            page: Page number (1-indexed).  Only used when
                ``pagination_type`` is ``OFFSET``.
            cursor: Cursor token from a previous
                :class:`.CursorPaginatedResponse`.  Only used when
                ``pagination_type`` is ``CURSOR``.
            items_per_page: Number of items per page (default 20).
            include_total: When ``False``, skip the ``COUNT`` query;
                only applies when ``pagination_type`` is ``OFFSET``.
            search: Search query string or :class:`.SearchConfig` object.
            search_fields: Fields to search in (overrides class default).
            search_column: Restrict search to a single column key.
            order_fields: Fields allowed for sorting (overrides class default).
            facet_fields: Columns to compute distinct values for (overrides
                class default).
            filter_by: Dict of ``{column_key: value}`` to filter by declared
                facet fields.  Keys must match the ``column.key`` of a facet
                field.  Scalar → equality, list → IN clause.  Raises
                :exc:`.InvalidFacetFilterError` for unknown keys.
            schema: Pydantic schema to serialize each item into.

        Returns:
            :class:`.OffsetPaginatedResponse` when ``pagination_type`` is
            ``OFFSET``, :class:`.CursorPaginatedResponse` when it is
            ``CURSOR``.
        """
        if items_per_page < 1:
            raise ValueError(f"items_per_page must be >= 1, got {items_per_page}")
        match pagination_type:
            case PaginationType.CURSOR:
                return await cls.cursor_paginate(
                    session,
                    cursor=cursor,
                    filters=filters,
                    joins=joins,
                    outer_join=outer_join,
                    load_options=load_options,
                    order_by=order_by,
                    order_joins=order_joins,
                    items_per_page=items_per_page,
                    search=search,
                    search_fields=search_fields,
                    search_column=search_column,
                    order_fields=order_fields,
                    facet_fields=facet_fields,
                    filter_by=filter_by,
                    schema=schema,
                )
            case PaginationType.OFFSET:
                if page < 1:
                    raise ValueError(f"page must be >= 1, got {page}")
                return await cls.offset_paginate(
                    session,
                    filters=filters,
                    joins=joins,
                    outer_join=outer_join,
                    load_options=load_options,
                    order_by=order_by,
                    order_joins=order_joins,
                    page=page,
                    items_per_page=items_per_page,
                    include_total=include_total,
                    search=search,
                    search_fields=search_fields,
                    search_column=search_column,
                    order_fields=order_fields,
                    facet_fields=facet_fields,
                    filter_by=filter_by,
                    schema=schema,
                )
            case _:
                raise ValueError(f"Unknown pagination_type: {pagination_type!r}")


def CrudFactory(
    model: type[ModelType],
    *,
    base_class: type[AsyncCrud[Any]] = AsyncCrud,
    searchable_fields: Sequence[SearchFieldType] | None = None,
    facet_fields: Sequence[FacetFieldType] | None = None,
    order_fields: Sequence[OrderFieldType] | None = None,
    m2m_fields: M2MFieldType | None = None,
    default_load_options: Sequence[ExecutableOption] | None = None,
    cursor_column: Any | None = None,
) -> type[AsyncCrud[ModelType]]:
    """Create a CRUD class for a specific model.

    Args:
        model: SQLAlchemy model class
        base_class: Optional base class to inherit from instead of ``AsyncCrud``.
            Use this to share custom methods across multiple CRUD classes while
            still using the factory shorthand.
        searchable_fields: Optional list of searchable fields
        facet_fields: Optional list of columns to compute distinct values for in paginated
            responses. Supports direct columns (``User.status``) and relationship tuples
            (``(User.role, Role.name)``). Can be overridden per call.
        order_fields: Optional list of model attributes that callers are allowed to order by
            via ``offset_paginate_params()``. Can be overridden per call.
        m2m_fields: Optional mapping for many-to-many relationships.
            Maps schema field names (containing lists of IDs) to
            SQLAlchemy relationship attributes.
        default_load_options: Default SQLAlchemy loader options applied to all read
            queries when no explicit ``load_options`` are passed. Use this
            instead of ``lazy="selectin"`` on the model so that loading
            strategy is explicit and per-CRUD. Overridden entirely (not
            merged) when ``load_options`` is provided at call-site.
        cursor_column: Required to call ``cursor_paginate``.
            Must be monotonically ordered (e.g. integer PK, UUID v7, timestamp).
            See the cursor pagination docs for supported column types.

    Returns:
        AsyncCrud subclass bound to the model

    Example:
        ```python
        from fastapi_toolsets.crud import CrudFactory
        from myapp.models import User, Post

        UserCrud = CrudFactory(User)
        PostCrud = CrudFactory(Post)

        # With searchable fields:
        UserCrud = CrudFactory(
            User,
            searchable_fields=[User.username, User.email, (User.role, Role.name)]
        )

        # With many-to-many fields:
        # Schema has `tag_ids: list[UUID]`, model has `tags` relationship to Tag
        PostCrud = CrudFactory(
            Post,
            m2m_fields={"tag_ids": Post.tags},
        )

        # With facet fields for filter dropdowns / faceted search:
        UserCrud = CrudFactory(
            User,
            facet_fields=[User.status, User.country, (User.role, Role.name)],
        )

        # With a fixed cursor column for cursor_paginate:
        PostCrud = CrudFactory(
            Post,
            cursor_column=Post.created_at,
        )

        # With default load strategy (replaces lazy="selectin" on the model):
        ArticleCrud = CrudFactory(
            Article,
            default_load_options=[selectinload(Article.category), selectinload(Article.tags)],
        )

        # Override default_load_options for a specific call:
        article = await ArticleCrud.get(
            session,
            [Article.id == 1],
            load_options=[selectinload(Article.category)],  # tags won't load
        )

        # Usage
        user = await UserCrud.get(session, [User.id == 1])
        posts = await PostCrud.get_multi(session, filters=[Post.user_id == user.id])

        # Create with M2M - tag_ids are automatically resolved
        post = await PostCrud.create(session, PostCreate(title="Hello", tag_ids=[id1, id2]))

        # With search
        result = await UserCrud.offset_paginate(session, search="john")

        # With joins (inner join by default):
        users = await UserCrud.get_multi(
            session,
            joins=[(Post, Post.user_id == User.id)],
            filters=[Post.published == True],
        )

        # With outer join:
        users = await UserCrud.get_multi(
            session,
            joins=[(Post, Post.user_id == User.id)],
            outer_join=True,
        )

        # With a shared custom base class:
        from typing import Generic, TypeVar
        from sqlalchemy.orm import DeclarativeBase

        T = TypeVar("T", bound=DeclarativeBase)

        class AuditedCrud(AsyncCrud[T], Generic[T]):
            @classmethod
            async def get_active(cls, session):
                return await cls.get_multi(session, filters=[cls.model.is_active == True])

        UserCrud = CrudFactory(User, base_class=AuditedCrud)
        ```
    """
    cls = type(
        f"Async{model.__name__}Crud",
        (base_class,),
        {
            "model": model,
            "searchable_fields": searchable_fields,
            "facet_fields": facet_fields,
            "order_fields": order_fields,
            "m2m_fields": m2m_fields,
            "default_load_options": default_load_options,
            "cursor_column": cursor_column,
        },
    )
    return cast(type[AsyncCrud[ModelType]], cls)
