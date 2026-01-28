"""Generic async CRUD operations for SQLAlchemy models."""

from collections.abc import Sequence
from typing import Any, ClassVar, Generic, Self, TypeVar, cast

from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy import delete as sql_delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql.roles import WhereHavingRole

from ..db import get_transaction
from ..exceptions import NotFoundError
from .search import SearchConfig, SearchFieldType, build_search_filters

ModelType = TypeVar("ModelType", bound=DeclarativeBase)


class AsyncCrud(Generic[ModelType]):
    """Generic async CRUD operations for SQLAlchemy models.

    Subclass this and set the `model` class variable, or use `CrudFactory`.
    """

    model: ClassVar[type[DeclarativeBase]]
    searchable_fields: ClassVar[Sequence[SearchFieldType] | None] = None

    @classmethod
    async def create(
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
    ) -> ModelType:
        """Create a new record in the database.

        Args:
            session: DB async session
            obj: Pydantic model with data to create

        Returns:
            Created model instance
        """
        async with get_transaction(session):
            db_model = cls.model(**obj.model_dump())
            session.add(db_model)
        await session.refresh(db_model)
        return cast(ModelType, db_model)

    @classmethod
    async def get(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
        *,
        with_for_update: bool = False,
        load_options: list[Any] | None = None,
    ) -> ModelType:
        """Get exactly one record. Raises NotFoundError if not found.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            with_for_update: Lock the row for update
            load_options: SQLAlchemy loader options (e.g., selectinload)

        Returns:
            Model instance

        Raises:
            NotFoundError: If no record found
            MultipleResultsFound: If more than one record found
        """
        q = select(cls.model).where(and_(*filters))
        if load_options:
            q = q.options(*load_options)
        if with_for_update:
            q = q.with_for_update()
        result = await session.execute(q)
        item = result.unique().scalar_one_or_none()
        if not item:
            raise NotFoundError()
        return cast(ModelType, item)

    @classmethod
    async def first(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any] | None = None,
        *,
        load_options: list[Any] | None = None,
    ) -> ModelType | None:
        """Get the first matching record, or None.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            load_options: SQLAlchemy loader options

        Returns:
            Model instance or None
        """
        q = select(cls.model)
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
        load_options: list[Any] | None = None,
        order_by: Any | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Sequence[ModelType]:
        """Get multiple records from the database.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
            load_options: SQLAlchemy loader options
            order_by: Column or list of columns to order by
            limit: Max number of rows to return
            offset: Rows to skip

        Returns:
            List of model instances
        """
        q = select(cls.model)
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

    @classmethod
    async def update(
        cls: type[Self],
        session: AsyncSession,
        obj: BaseModel,
        filters: list[Any],
        *,
        exclude_unset: bool = True,
        exclude_none: bool = False,
    ) -> ModelType:
        """Update a record in the database.

        Args:
            session: DB async session
            obj: Pydantic model with update data
            filters: List of SQLAlchemy filter conditions
            exclude_unset: Exclude fields not explicitly set in the schema
            exclude_none: Exclude fields with None value

        Returns:
            Updated model instance

        Raises:
            NotFoundError: If no record found
        """
        async with get_transaction(session):
            db_model = await cls.get(session=session, filters=filters)
            values = obj.model_dump(
                exclude_unset=exclude_unset, exclude_none=exclude_none
            )
            for key, value in values.items():
                setattr(db_model, key, value)
        await session.refresh(db_model)
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

    @classmethod
    async def delete(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
    ) -> bool:
        """Delete records from the database.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions

        Returns:
            True if deletion was executed
        """
        async with get_transaction(session):
            q = sql_delete(cls.model).where(and_(*filters))
            await session.execute(q)
        return True

    @classmethod
    async def count(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any] | None = None,
    ) -> int:
        """Count records matching the filters.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions

        Returns:
            Number of matching records
        """
        q = select(func.count()).select_from(cls.model)
        if filters:
            q = q.where(and_(*filters))
        result = await session.execute(q)
        return result.scalar_one()

    @classmethod
    async def exists(
        cls: type[Self],
        session: AsyncSession,
        filters: list[Any],
    ) -> bool:
        """Check if a record exists.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions

        Returns:
            True if at least one record matches
        """
        q = select(cls.model).where(and_(*filters)).exists().select()
        result = await session.execute(q)
        return bool(result.scalar())

    @classmethod
    async def paginate(
        cls: type[Self],
        session: AsyncSession,
        *,
        filters: list[Any] | None = None,
        load_options: list[Any] | None = None,
        order_by: Any | None = None,
        page: int = 1,
        items_per_page: int = 20,
        search: str | SearchConfig | None = None,
        search_fields: Sequence[SearchFieldType] | None = None,
    ) -> dict[str, Any]:
        """Get paginated results with metadata.

        Args:
            session: DB async session
            filters: List of SQLAlchemy filter conditions
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
        joins: list[Any] = []

        # Build search filters
        if search:
            search_filters, search_joins = build_search_filters(
                cls.model,
                search,
                search_fields=search_fields,
                default_fields=cls.searchable_fields,
            )
            filters.extend(search_filters)
            joins.extend(search_joins)

        # Build query with joins
        q = select(cls.model)
        for join_rel in joins:
            q = q.outerjoin(join_rel)

        if filters:
            q = q.where(and_(*filters))
        if load_options:
            q = q.options(*load_options)
        if order_by is not None:
            q = q.order_by(order_by)

        q = q.offset(offset).limit(items_per_page)
        result = await session.execute(q)
        items = result.unique().scalars().all()

        # Count query (with same joins and filters)
        pk_col = cls.model.__mapper__.primary_key[0]
        count_q = select(func.count(func.distinct(getattr(cls.model, pk_col.name))))
        count_q = count_q.select_from(cls.model)
        for join_rel in joins:
            count_q = count_q.outerjoin(join_rel)
        if filters:
            count_q = count_q.where(and_(*filters))

        count_result = await session.execute(count_q)
        total_count = count_result.scalar_one()

        return {
            "data": items,
            "pagination": {
                "total_count": total_count,
                "items_per_page": items_per_page,
                "page": page,
                "has_more": page * items_per_page < total_count,
            },
        }


def CrudFactory(
    model: type[ModelType],
    *,
    searchable_fields: Sequence[SearchFieldType] | None = None,
) -> type[AsyncCrud[ModelType]]:
    """Create a CRUD class for a specific model.

    Args:
        model: SQLAlchemy model class
        searchable_fields: Optional list of searchable fields

    Returns:
        AsyncCrud subclass bound to the model

    Example:
        from fastapi_toolsets.crud import CrudFactory
        from myapp.models import User, Post

        UserCrud = CrudFactory(User)
        PostCrud = CrudFactory(Post)

        # With searchable fields:
        UserCrud = CrudFactory(
            User,
            searchable_fields=[User.username, User.email, (User.role, Role.name)]
        )

        # Usage
        user = await UserCrud.get(session, [User.id == 1])
        posts = await PostCrud.get_multi(session, filters=[Post.user_id == user.id])

        # With search
        result = await UserCrud.paginate(session, search="john")
    """
    cls = type(
        f"Async{model.__name__}Crud",
        (AsyncCrud,),
        {
            "model": model,
            "searchable_fields": searchable_fields,
        },
    )
    return cast(type[AsyncCrud[ModelType]], cls)
