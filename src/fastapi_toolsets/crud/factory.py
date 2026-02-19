"""Generic async CRUD operations for SQLAlchemy models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Generic, Literal, Self, TypeVar, cast, overload

from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy import delete as sql_delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, QueryableAttribute, selectinload
from sqlalchemy.sql.roles import WhereHavingRole

from ..db import get_transaction
from ..exceptions import NotFoundError
from ..schemas import PaginatedResponse, Pagination, Response
from .search import SearchConfig, SearchFieldType, build_search_filters

ModelType = TypeVar("ModelType", bound=DeclarativeBase)
JoinType = list[tuple[type[DeclarativeBase], Any]]
M2MFieldType = Mapping[str, QueryableAttribute[Any]]


class AsyncCrud(Generic[ModelType]):
    """Generic async CRUD operations for SQLAlchemy models.

    Subclass this and set the `model` class variable, or use `CrudFactory`.
    """

    model: ClassVar[type[DeclarativeBase]]
    searchable_fields: ClassVar[Sequence[SearchFieldType] | None] = None
    m2m_fields: ClassVar[M2MFieldType | None] = None

    @overload
    @classmethod
    async def create(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        *,
        as_response: Literal[True],
    ) -> Response[ModelType]: ...

    @overload
    @classmethod
    async def create(  # pragma: no cover
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        *,
        as_response: Literal[False] = ...,
    ) -> ModelType: ...

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
    async def create(
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        *,
        as_response: bool = False,
    ) -> ModelType | Response[ModelType]:
        """Create a new record in the database.

        Args:
            session: DB async session
            obj: Pydantic model with data to create
            as_response: If True, wrap result in Response object

        Returns:
            Created model instance or Response wrapping it
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
        result = cast(ModelType, db_model)
        if as_response:
            return Response(data=result)
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
        load_options: list[Any] | None = None,
        as_response: Literal[True],
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
        load_options: list[Any] | None = None,
        as_response: Literal[False] = ...,
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
        load_options: list[Any] | None = None,
        as_response: bool = False,
    ) -> ModelType | Response[ModelType]:
        """Get exactly one record. Raises NotFoundError if not found.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            joins: List of (model, condition) tuples for joining related tables
            outer_join: Use LEFT OUTER JOIN instead of INNER JOIN
            with_for_update: Lock the row for update
            load_options: SQLAlchemy loader options (e.g., selectinload)
            as_response: If True, wrap result in Response object

        Returns:
            Model instance or Response wrapping it

        Raises:
            NotFoundError: If no record found
            MultipleResultsFound: If more than one record found
        """
        q = select(cls.model)
        if joins:
            for model, condition in joins:
                q = (
                    q.outerjoin(model, condition)
                    if outer_join
                    else q.join(model, condition)
                )
        q = q.where(and_(*filters))
        if load_options:
            q = q.options(*load_options)
        if with_for_update:
            q = q.with_for_update()
        result = await session.execute(q)
        item = result.unique().scalar_one_or_none()
        if not item:
            raise NotFoundError()
        result = cast(ModelType, item)
        if as_response:
            return Response(data=result)
        return result

    @classmethod
    async def first(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any] | None = None,
        *,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: list[Any] | None = None,
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
        if load_options:
            q = q.options(*load_options)
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
        load_options: list[Any] | None = None,
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
        if load_options:
            q = q.options(*load_options)
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
        as_response: Literal[True],
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
    ) -> ModelType | Response[ModelType]:
        """Update a record in the database.

        Args:
            session: DB async session
            obj: Pydantic model with update data
            filters: List of SQLAlchemy filter conditions
            exclude_unset: Exclude fields not explicitly set in the schema
            exclude_none: Exclude fields with None value
            as_response: If True, wrap result in Response object

        Returns:
            Updated model instance or Response wrapping it

        Raises:
            NotFoundError: If no record found
        """
        async with get_transaction(session):
            m2m_exclude = cls._m2m_schema_fields()

            # Eagerly load M2M relationships that will be updated so that
            # setattr does not trigger a lazy load (which fails in async).
            m2m_load_options: list[Any] = []
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
        if as_response:
            return Response(data=db_model)
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
            as_response: If True, wrap result in Response object

        Returns:
            True if deletion was executed, or Response wrapping it
        """
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

    @classmethod
    async def paginate(
        cls: type[Self],
        session: AsyncSession,
        *,
        filters: list[Any] | None = None,
        joins: JoinType | None = None,
        outer_join: bool = False,
        load_options: list[Any] | None = None,
        order_by: Any | None = None,
        page: int = 1,
        items_per_page: int = 20,
        search: str | SearchConfig | None = None,
        search_fields: Sequence[SearchFieldType] | None = None,
    ) -> PaginatedResponse[ModelType]:
        """Get paginated results with metadata.

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

        Returns:
            Dict with 'data' and 'pagination' keys
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
        if load_options:
            q = q.options(*load_options)
        if order_by is not None:
            q = q.order_by(order_by)

        q = q.offset(offset).limit(items_per_page)
        result = await session.execute(q)
        items = cast(list[ModelType], result.unique().scalars().all())

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
            pagination=Pagination(
                total_count=total_count,
                items_per_page=items_per_page,
                page=page,
                has_more=page * items_per_page < total_count,
            ),
        )


def CrudFactory(
    model: type[ModelType],
    *,
    searchable_fields: Sequence[SearchFieldType] | None = None,
    m2m_fields: M2MFieldType | None = None,
) -> type[AsyncCrud[ModelType]]:
    """Create a CRUD class for a specific model.

    Args:
        model: SQLAlchemy model class
        searchable_fields: Optional list of searchable fields
        m2m_fields: Optional mapping for many-to-many relationships.
            Maps schema field names (containing lists of IDs) to
            SQLAlchemy relationship attributes.

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

        # Usage
        user = await UserCrud.get(session, [User.id == 1])
        posts = await PostCrud.get_multi(session, filters=[Post.user_id == user.id])

        # Create with M2M - tag_ids are automatically resolved
        post = await PostCrud.create(session, PostCreate(title="Hello", tag_ids=[id1, id2]))

        # With search
        result = await UserCrud.paginate(session, search="john")

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
        },
    )
    return cast(type[AsyncCrud[ModelType]], cls)
