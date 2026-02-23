"""Generic async CRUD operations for SQLAlchemy models."""

from __future__ import annotations

import base64
import json
import uuid as uuid_module
import warnings
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar, Generic, Literal, Self, TypeVar, cast, overload

from pydantic import BaseModel
from sqlalchemy import Date, DateTime, Float, Integer, Numeric, Uuid, and_, func, select
from sqlalchemy import delete as sql_delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, QueryableAttribute, selectinload
from sqlalchemy.sql.base import ExecutableOption
from sqlalchemy.sql.roles import WhereHavingRole

from ..db import get_transaction
from ..exceptions import NotFoundError
from ..schemas import CursorPagination, OffsetPagination, PaginatedResponse, Response
from .search import SearchConfig, SearchFieldType, build_search_filters

ModelType = TypeVar("ModelType", bound=DeclarativeBase)
SchemaType = TypeVar("SchemaType", bound=BaseModel)
JoinType = list[tuple[type[DeclarativeBase], Any]]
M2MFieldType = Mapping[str, QueryableAttribute[Any]]


def _encode_cursor(value: Any) -> str:
    """Encode cursor column value as an base64 string."""
    return base64.b64encode(json.dumps(str(value)).encode()).decode()


def _decode_cursor(cursor: str) -> str:
    """Decode cursor base64 string."""
    return json.loads(base64.b64decode(cursor.encode()).decode())


class AsyncCrud(Generic[ModelType]):
    """Generic async CRUD operations for SQLAlchemy models.

    Subclass this and set the `model` class variable, or use `CrudFactory`.
    """

    model: ClassVar[type[DeclarativeBase]]
    searchable_fields: ClassVar[Sequence[SearchFieldType] | None] = None
    m2m_fields: ClassVar[M2MFieldType | None] = None
    default_load_options: ClassVar[list[ExecutableOption] | None] = None
    cursor_column: ClassVar[Any | None] = None

    @classmethod
    def _resolve_load_options(
        cls, load_options: list[ExecutableOption] | None
    ) -> list[ExecutableOption] | None:
        """Return load_options if provided, else fall back to default_load_options."""
        if load_options is not None:
            return load_options
        return cls.default_load_options

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

    @overload
    @classmethod
    async def create(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        *,
        schema: type[SchemaType],
        as_response: bool = ...,
    ) -> Response[SchemaType]: ...

    # Backward-compatible - will be removed in v2.0
    @overload
    @classmethod
    async def create(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        *,
        as_response: Literal[True],
        schema: None = ...,
    ) -> Response[ModelType]: ...

    @overload
    @classmethod
    async def create(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        *,
        as_response: Literal[False] = ...,
        schema: None = ...,
    ) -> ModelType: ...

    @classmethod
    async def create(
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        *,
        as_response: bool = False,
        schema: type[BaseModel] | None = None,
    ) -> ModelType | Response[ModelType] | Response[Any]:
        """Create a new record in the database.

        Args:
            session: DB async session
            obj: Pydantic model with data to create
            as_response: Deprecated. Use ``schema`` instead. Will be removed in v2.0.
            schema: Pydantic schema to serialize the result into. When provided,
                the result is automatically wrapped in a ``Response[schema]``.

        Returns:
            Created model instance, or ``Response[schema]`` when ``schema`` is given,
            or ``Response[ModelType]`` when ``as_response=True`` (deprecated).
        """
        if as_response and schema is None:
            warnings.warn(
                "as_response is deprecated and will be removed in v2.0. "
                "Use schema=YourSchema instead.",
                DeprecationWarning,
                stacklevel=2,
            )
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
        result = cast(ModelType, db_model)
        if as_response or schema:
            data_out = schema.model_validate(result) if schema else result
            return Response(data=data_out)
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
        load_options: list[ExecutableOption] | None = None,
        schema: type[SchemaType],
        as_response: bool = ...,
    ) -> Response[SchemaType]: ...

    # Backward-compatible - will be removed in v2.0
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
        load_options: list[ExecutableOption] | None = None,
        as_response: Literal[True],
        schema: None = ...,
    ) -> Response[ModelType]: ...

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
        load_options: list[ExecutableOption] | None = None,
        as_response: Literal[False] = ...,
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
        load_options: list[ExecutableOption] | None = None,
        as_response: bool = False,
        schema: type[BaseModel] | None = None,
    ) -> ModelType | Response[ModelType] | Response[Any]:
        """Get exactly one record. Raises NotFoundError if not found.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            joins: List of (model, condition) tuples for joining related tables
            outer_join: Use LEFT OUTER JOIN instead of INNER JOIN
            with_for_update: Lock the row for update
            load_options: SQLAlchemy loader options (e.g., selectinload)
            as_response: Deprecated. Use ``schema`` instead. Will be removed in v2.0.
            schema: Pydantic schema to serialize the result into. When provided,
                the result is automatically wrapped in a ``Response[schema]``.

        Returns:
            Model instance, or ``Response[schema]`` when ``schema`` is given,
            or ``Response[ModelType]`` when ``as_response=True`` (deprecated).

        Raises:
            NotFoundError: If no record found
            MultipleResultsFound: If more than one record found
        """
        if as_response and schema is None:
            warnings.warn(
                "as_response is deprecated and will be removed in v2.0. "
                "Use schema=YourSchema instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        q = select(cls.model)
        if joins:
            for model, condition in joins:
                q = (
                    q.outerjoin(model, condition)
                    if outer_join
                    else q.join(model, condition)
                )
        q = q.where(and_(*filters))
        if resolved := cls._resolve_load_options(load_options):
            q = q.options(*resolved)
        if with_for_update:
            q = q.with_for_update()
        result = await session.execute(q)
        item = result.unique().scalar_one_or_none()
        if not item:
            raise NotFoundError()
        result = cast(ModelType, item)
        if as_response or schema:
            data_out = schema.model_validate(result) if schema else result
            return Response(data=data_out)
        return result

    @classmethod
    async def first(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any] | None = None,
        *,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: list[ExecutableOption] | None = None,
    ) -> ModelType | None:
        """Get the first matching record, or None.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            joins: List of (model, condition) tuples for joining related tables
            outer_join: Use LEFT OUTER JOIN instead of INNER JOIN
            load_options: SQLAlchemy loader options

        Returns:
            Model instance or None
        """
        q = select(cls.model)
        if joins:
            for model, condition in joins:
                q = (
                    q.outerjoin(model, condition)
                    if outer_join
                    else q.join(model, condition)
                )
        if filters:
            q = q.where(and_(*filters))
        if resolved := cls._resolve_load_options(load_options):
            q = q.options(*resolved)
        result = await session.execute(q)
        return cast(ModelType | None, result.unique().scalars().first())

    @classmethod
    async def get_multi(
        cls: type[Self],
        session: AsyncSession,
        *,
        filters: list[Any] | None = None,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: list[ExecutableOption] | None = None,
        order_by: Any | None = None,
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
        if joins:
            for model, condition in joins:
                q = (
                    q.outerjoin(model, condition)
                    if outer_join
                    else q.join(model, condition)
                )
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
        as_response: bool = ...,
    ) -> Response[SchemaType]: ...

    # Backward-compatible - will be removed in v2.0
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
        as_response: Literal[True],
        schema: None = ...,
    ) -> Response[ModelType]: ...

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
        as_response: Literal[False] = ...,
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
        as_response: bool = False,
        schema: type[BaseModel] | None = None,
    ) -> ModelType | Response[ModelType] | Response[Any]:
        """Update a record in the database.

        Args:
            session: DB async session
            obj: Pydantic model with update data
            filters: List of SQLAlchemy filter conditions
            exclude_unset: Exclude fields not explicitly set in the schema
            exclude_none: Exclude fields with None value
            as_response: Deprecated. Use ``schema`` instead. Will be removed in v2.0.
            schema: Pydantic schema to serialize the result into. When provided,
                the result is automatically wrapped in a ``Response[schema]``.

        Returns:
            Updated model instance, or ``Response[schema]`` when ``schema`` is given,
            or ``Response[ModelType]`` when ``as_response=True`` (deprecated).

        Raises:
            NotFoundError: If no record found
        """
        if as_response and schema is None:
            warnings.warn(
                "as_response is deprecated and will be removed in v2.0. "
                "Use schema=YourSchema instead.",
                DeprecationWarning,
                stacklevel=2,
            )
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
        if as_response or schema:
            data_out = schema.model_validate(db_model) if schema else db_model
            return Response(data=data_out)
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
        as_response: Literal[True],
    ) -> Response[None]: ...

    @overload
    @classmethod
    async def delete(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
        *,
        as_response: Literal[False] = ...,
    ) -> bool: ...

    @classmethod
    async def delete(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
        *,
        as_response: bool = False,
    ) -> bool | Response[None]:
        """Delete records from the database.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            as_response: Deprecated. Will be removed in v2.0. When ``True``,
                returns ``Response[None]`` instead of ``bool``.

        Returns:
            ``True`` if deletion was executed, or ``Response[None]`` when
            ``as_response=True`` (deprecated).
        """
        if as_response:
            warnings.warn(
                "as_response is deprecated and will be removed in v2.0. "
                "Use schema=YourSchema instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        async with get_transaction(session):
            q = sql_delete(cls.model).where(and_(*filters))
            await session.execute(q)
        if as_response:
            return Response(data=None)
        return True

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
        if joins:
            for model, condition in joins:
                q = (
                    q.outerjoin(model, condition)
                    if outer_join
                    else q.join(model, condition)
                )
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
        if joins:
            for model, condition in joins:
                q = (
                    q.outerjoin(model, condition)
                    if outer_join
                    else q.join(model, condition)
                )
        q = q.where(and_(*filters)).exists().select()
        result = await session.execute(q)
        return bool(result.scalar())

    @overload
    @classmethod
    async def offset_paginate(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        *,
        filters: list[Any] | None = None,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: list[ExecutableOption] | None = None,
        order_by: Any | None = None,
        page: int = 1,
        items_per_page: int = 20,
        search: str | SearchConfig | None = None,
        search_fields: Sequence[SearchFieldType] | None = None,
        schema: type[SchemaType],
    ) -> PaginatedResponse[SchemaType]: ...

    # Backward-compatible - will be removed in v2.0
    @overload
    @classmethod
    async def offset_paginate(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        *,
        filters: list[Any] | None = None,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: list[ExecutableOption] | None = None,
        order_by: Any | None = None,
        page: int = 1,
        items_per_page: int = 20,
        search: str | SearchConfig | None = None,
        search_fields: Sequence[SearchFieldType] | None = None,
        schema: None = ...,
    ) -> PaginatedResponse[ModelType]: ...

    @classmethod
    async def offset_paginate(
        cls: type[Self],
        session: AsyncSession,
        *,
        filters: list[Any] | None = None,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: list[ExecutableOption] | None = None,
        order_by: Any | None = None,
        page: int = 1,
        items_per_page: int = 20,
        search: str | SearchConfig | None = None,
        search_fields: Sequence[SearchFieldType] | None = None,
        schema: type[BaseModel] | None = None,
    ) -> PaginatedResponse[ModelType] | PaginatedResponse[Any]:
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
            search: Search query string or SearchConfig object
            search_fields: Fields to search in (overrides class default)
            schema: Optional Pydantic schema to serialize each item into.

        Returns:
            PaginatedResponse with OffsetPagination metadata
        """
        filters = list(filters) if filters else []
        offset = (page - 1) * items_per_page
        search_joins: list[Any] = []

        # Build search filters
        if search:
            search_filters, search_joins = build_search_filters(
                cls.model,
                search,
                search_fields=search_fields,
                default_fields=cls.searchable_fields,
            )
            filters.extend(search_filters)

        # Build query with joins
        q = select(cls.model)

        # Apply explicit joins
        if joins:
            for model, condition in joins:
                q = (
                    q.outerjoin(model, condition)
                    if outer_join
                    else q.join(model, condition)
                )

        # Apply search joins (always outer joins for search)
        for join_rel in search_joins:
            q = q.outerjoin(join_rel)

        if filters:
            q = q.where(and_(*filters))
        if resolved := cls._resolve_load_options(load_options):
            q = q.options(*resolved)
        if order_by is not None:
            q = q.order_by(order_by)

        q = q.offset(offset).limit(items_per_page)
        result = await session.execute(q)
        raw_items = cast(list[ModelType], result.unique().scalars().all())
        items: list[Any] = (
            [schema.model_validate(item) for item in raw_items] if schema else raw_items
        )

        # Count query (with same joins and filters)
        pk_col = cls.model.__mapper__.primary_key[0]
        count_q = select(func.count(func.distinct(getattr(cls.model, pk_col.name))))
        count_q = count_q.select_from(cls.model)

        # Apply explicit joins to count query
        if joins:
            for model, condition in joins:
                count_q = (
                    count_q.outerjoin(model, condition)
                    if outer_join
                    else count_q.join(model, condition)
                )

        # Apply search joins to count query
        for join_rel in search_joins:
            count_q = count_q.outerjoin(join_rel)

        if filters:
            count_q = count_q.where(and_(*filters))

        count_result = await session.execute(count_q)
        total_count = count_result.scalar_one()

        return PaginatedResponse(
            data=items,
            pagination=OffsetPagination(
                total_count=total_count,
                items_per_page=items_per_page,
                page=page,
                has_more=page * items_per_page < total_count,
            ),
        )

    # Backward-compatible - will be removed in v2.0
    paginate = offset_paginate

    @overload
    @classmethod
    async def cursor_paginate(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        *,
        cursor: str | None = None,
        filters: list[Any] | None = None,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: list[ExecutableOption] | None = None,
        order_by: Any | None = None,
        items_per_page: int = 20,
        search: str | SearchConfig | None = None,
        search_fields: Sequence[SearchFieldType] | None = None,
        schema: type[SchemaType],
    ) -> PaginatedResponse[SchemaType]: ...

    # Backward-compatible - will be removed in v2.0
    @overload
    @classmethod
    async def cursor_paginate(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        *,
        cursor: str | None = None,
        filters: list[Any] | None = None,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: list[ExecutableOption] | None = None,
        order_by: Any | None = None,
        items_per_page: int = 20,
        search: str | SearchConfig | None = None,
        search_fields: Sequence[SearchFieldType] | None = None,
        schema: None = ...,
    ) -> PaginatedResponse[ModelType]: ...

    @classmethod
    async def cursor_paginate(
        cls: type[Self],
        session: AsyncSession,
        *,
        cursor: str | None = None,
        filters: list[Any] | None = None,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: list[ExecutableOption] | None = None,
        order_by: Any | None = None,
        items_per_page: int = 20,
        search: str | SearchConfig | None = None,
        search_fields: Sequence[SearchFieldType] | None = None,
        schema: type[BaseModel] | None = None,
    ) -> PaginatedResponse[ModelType] | PaginatedResponse[Any]:
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
                ``default_load_options`` when not provided.
            order_by: Additional ordering applied after the cursor column.
            items_per_page: Number of items per page (default 20).
            search: Search query string or SearchConfig object.
            search_fields: Fields to search in (overrides class default).
            schema: Optional Pydantic schema to serialize each item into.

        Returns:
            PaginatedResponse with CursorPagination metadata
        """
        filters = list(filters) if filters else []
        search_joins: list[Any] = []

        if cls.cursor_column is None:
            raise ValueError(
                f"{cls.__name__}.cursor_column is not set. "
                "Pass cursor_column=<column> to CrudFactory() to use cursor_paginate."
            )
        cursor_column: Any = cls.cursor_column
        cursor_col_name: str = cursor_column.key

        if cursor is not None:
            raw_val = _decode_cursor(cursor)
            col_type = cursor_column.property.columns[0].type
            if isinstance(col_type, Integer):
                cursor_val: Any = int(raw_val)
            elif isinstance(col_type, Uuid):
                cursor_val = uuid_module.UUID(raw_val)
            elif isinstance(col_type, DateTime):
                cursor_val = datetime.fromisoformat(raw_val)
            elif isinstance(col_type, Date):
                cursor_val = date.fromisoformat(raw_val)
            elif isinstance(col_type, (Float, Numeric)):
                cursor_val = Decimal(raw_val)
            else:
                raise ValueError(
                    f"Unsupported cursor column type: {type(col_type).__name__!r}. "
                    "Supported types: Integer, BigInteger, SmallInteger, Uuid, "
                    "DateTime, Date, Float, Numeric."
                )
            filters.append(cursor_column > cursor_val)

        # Build search filters
        if search:
            search_filters, search_joins = build_search_filters(
                cls.model,
                search,
                search_fields=search_fields,
                default_fields=cls.searchable_fields,
            )
            filters.extend(search_filters)

        # Build query
        q = select(cls.model)

        # Apply explicit joins
        if joins:
            for model, condition in joins:
                q = (
                    q.outerjoin(model, condition)
                    if outer_join
                    else q.join(model, condition)
                )

        # Apply search joins (always outer joins)
        for join_rel in search_joins:
            q = q.outerjoin(join_rel)

        if filters:
            q = q.where(and_(*filters))
        if resolved := cls._resolve_load_options(load_options):
            q = q.options(*resolved)

        # Cursor column is always the primary sort
        q = q.order_by(cursor_column)
        if order_by is not None:
            q = q.order_by(order_by)

        # Fetch one extra to detect whether a next page exists
        q = q.limit(items_per_page + 1)
        result = await session.execute(q)
        raw_items = cast(list[ModelType], result.unique().scalars().all())

        has_more = len(raw_items) > items_per_page
        items_page = raw_items[:items_per_page]

        # next_cursor points past the last item on this page
        next_cursor: str | None = None
        if has_more and items_page:
            next_cursor = _encode_cursor(getattr(items_page[-1], cursor_col_name))

        # prev_cursor points to the first item on this page or None when on the first page
        prev_cursor: str | None = None
        if cursor is not None and items_page:
            prev_cursor = _encode_cursor(getattr(items_page[0], cursor_col_name))

        items: list[Any] = (
            [schema.model_validate(item) for item in items_page]
            if schema
            else items_page
        )

        return PaginatedResponse(
            data=items,
            pagination=CursorPagination(
                next_cursor=next_cursor,
                prev_cursor=prev_cursor,
                items_per_page=items_per_page,
                has_more=has_more,
            ),
        )


def CrudFactory(
    model: type[ModelType],
    *,
    searchable_fields: Sequence[SearchFieldType] | None = None,
    m2m_fields: M2MFieldType | None = None,
    default_load_options: list[ExecutableOption] | None = None,
    cursor_column: Any | None = None,
) -> type[AsyncCrud[ModelType]]:
    """Create a CRUD class for a specific model.

    Args:
        model: SQLAlchemy model class
        searchable_fields: Optional list of searchable fields
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
        ```
    """
    cls = type(
        f"Async{model.__name__}Crud",
        (AsyncCrud,),
        {
            "model": model,
            "searchable_fields": searchable_fields,
            "m2m_fields": m2m_fields,
            "default_load_options": default_load_options,
            "cursor_column": cursor_column,
        },
    )
    return cast(type[AsyncCrud[ModelType]], cls)
