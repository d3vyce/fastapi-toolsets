"""Tests for fastapi_toolsets.crud module."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi_toolsets.crud import CrudFactory, PaginationType
from fastapi_toolsets.crud.factory import AsyncCrud, _CursorDirection
from fastapi_toolsets.exceptions import NotFoundError

from .conftest import (
    EventCreate,
    EventCrud,
    EventDateCursorCrud,
    EventDateTimeCursorCrud,
    EventRead,
    IntRoleCreate,
    IntRoleCursorCrud,
    IntRoleRead,
    Post,
    PostCreate,
    PostCrud,
    PostM2MCreate,
    PostM2MCrud,
    PostM2MUpdate,
    ProductCreate,
    ProductCrud,
    ProductNumericCursorCrud,
    ProductRead,
    Role,
    RoleCreate,
    RoleCrud,
    RoleCursorCrud,
    RoleRead,
    RoleUpdate,
    Tag,
    TagCreate,
    TagCrud,
    Transfer,
    TransferCreate,
    TransferCrud,
    TransferRead,
    User,
    UserCreate,
    UserCrud,
    UserCursorCrud,
    UserRead,
    UserUpdate,
)


class TestCrudFactory:
    """Tests for CrudFactory."""

    def test_creates_crud_class(self):
        """CrudFactory creates a properly configured CRUD class."""
        crud = CrudFactory(User)
        assert issubclass(crud, AsyncCrud)
        assert crud.model is User

    def test_creates_unique_classes(self):
        """Each call creates a unique class."""
        crud1 = CrudFactory(User)
        crud2 = CrudFactory(User)
        assert crud1 is not crud2

    def test_class_name_includes_model(self):
        """Generated class name includes model name."""
        crud = CrudFactory(User)
        assert "User" in crud.__name__

    def test_default_load_options_none_by_default(self):
        """default_load_options is None when not specified."""
        crud = CrudFactory(User)
        assert crud.default_load_options is None

    def test_default_load_options_set(self):
        """default_load_options is stored on the class."""
        options = [selectinload(User.role)]
        crud = CrudFactory(User, default_load_options=options)
        assert crud.default_load_options == options

    def test_default_load_options_not_shared_between_classes(self):
        """default_load_options is isolated per factory call."""
        options = [selectinload(User.role)]
        crud_with = CrudFactory(User, default_load_options=options)
        crud_without = CrudFactory(User)
        assert crud_with.default_load_options == options
        assert crud_without.default_load_options is None

    def test_base_class_custom_methods_inherited(self):
        """CrudFactory with base_class inherits custom methods from that base."""
        from typing import Generic, TypeVar

        from sqlalchemy.orm import DeclarativeBase

        T = TypeVar("T", bound=DeclarativeBase)

        class CustomBase(AsyncCrud[T], Generic[T]):
            @classmethod
            def custom_method(cls) -> str:
                return f"custom:{cls.model.__name__}"

        UserCrudCustom = CrudFactory(User, base_class=CustomBase)
        PostCrudCustom = CrudFactory(Post, base_class=CustomBase)

        assert issubclass(UserCrudCustom, CustomBase)
        assert issubclass(PostCrudCustom, CustomBase)
        assert UserCrudCustom.custom_method() == "custom:User"
        assert PostCrudCustom.custom_method() == "custom:Post"

    def test_base_class_pk_injected(self):
        """PK is still injected when using a custom base_class."""
        from typing import Generic, TypeVar

        from sqlalchemy.orm import DeclarativeBase

        T = TypeVar("T", bound=DeclarativeBase)

        class CustomBase(AsyncCrud[T], Generic[T]):
            pass

        crud = CrudFactory(User, base_class=CustomBase)
        assert crud.searchable_fields is not None
        assert User.id in crud.searchable_fields


class TestAsyncCrudSubclass:
    """Tests for direct AsyncCrud subclassing (alternative to CrudFactory)."""

    def test_subclass_with_model_only(self):
        """Subclassing with just model auto-injects PK into searchable_fields."""

        class UserCrudDirect(AsyncCrud[User]):
            model = User

        assert UserCrudDirect.searchable_fields == [User.id]

    def test_subclass_with_explicit_fields_prepends_pk(self):
        """Subclassing with searchable_fields prepends PK automatically."""

        class UserCrudDirect(AsyncCrud[User]):
            model = User
            searchable_fields = [User.username]

        assert UserCrudDirect.searchable_fields == [User.id, User.username]

    def test_subclass_with_pk_already_in_fields(self):
        """PK is not duplicated when already in searchable_fields."""

        class UserCrudDirect(AsyncCrud[User]):
            model = User
            searchable_fields = [User.id, User.username]

        assert UserCrudDirect.searchable_fields == [User.id, User.username]

    def test_subclass_has_default_class_vars(self):
        """Other ClassVars are None by default on a direct subclass."""

        class UserCrudDirect(AsyncCrud[User]):
            model = User

        assert UserCrudDirect.facet_fields is None
        assert UserCrudDirect.default_load_options is None
        assert UserCrudDirect.cursor_column is None

    def test_subclass_with_load_options(self):
        """Direct subclass can declare default_load_options."""
        opts = [selectinload(User.role)]

        class UserCrudDirect(AsyncCrud[User]):
            model = User
            default_load_options = opts

        assert UserCrudDirect.default_load_options is opts

    def test_abstract_base_without_model_not_processed(self):
        """Intermediate abstract class without model is not processed."""

        class AbstractCrud(AsyncCrud[User]):
            pass

        # Should not raise, and searchable_fields inherits base default (None)
        assert AbstractCrud.searchable_fields is None


class TestResolveLoadOptions:
    """Tests for _resolve_load_options logic."""

    def test_returns_load_options_when_provided(self):
        """Explicit load_options takes priority over default_load_options."""
        options = [selectinload(User.role)]
        default = [selectinload(Post.tags)]
        crud = CrudFactory(User, default_load_options=default)
        assert crud._resolve_load_options(options) == options

    def test_returns_default_when_load_options_is_none(self):
        """Falls back to default_load_options when load_options is None."""
        default = [selectinload(User.role)]
        crud = CrudFactory(User, default_load_options=default)
        assert crud._resolve_load_options(None) == default

    def test_returns_none_when_both_are_none(self):
        """Returns None when neither load_options nor default_load_options set."""
        crud = CrudFactory(User)
        assert crud._resolve_load_options(None) is None

    def test_empty_list_overrides_default(self):
        """An empty list is a valid override and disables default_load_options."""
        default = [selectinload(User.role)]
        crud = CrudFactory(User, default_load_options=default)
        # Empty list is not None, so it should replace default
        assert crud._resolve_load_options([]) == []


class TestResolveSearchColumns:
    """Tests for _resolve_search_columns logic."""

    def test_returns_none_when_no_searchable_fields(self):
        """Returns None when cls.searchable_fields is None and no search_fields passed."""

        class AbstractCrud(AsyncCrud[User]):
            pass

        assert AbstractCrud._resolve_search_columns(None) is None

    def test_returns_none_when_empty_search_fields_passed(self):
        """Returns None when an empty list is passed explicitly."""
        crud = CrudFactory(User)
        assert crud._resolve_search_columns([]) is None

    def test_returns_keys_from_class_searchable_fields(self):
        """Returns column keys from cls.searchable_fields when no override passed."""
        crud = CrudFactory(User, searchable_fields=[User.username])
        result = crud._resolve_search_columns(None)
        assert result is not None
        assert "username" in result

    def test_search_fields_override_takes_priority(self):
        """Explicit search_fields override cls.searchable_fields."""
        crud = CrudFactory(User, searchable_fields=[User.username])
        result = crud._resolve_search_columns([User.email])
        assert result is not None
        assert "email" in result
        assert "username" not in result


class TestDefaultLoadOptionsIntegration:
    """Integration tests for default_load_options with real DB queries."""

    @pytest.mark.anyio
    async def test_default_load_options_applied_to_get(self, db_session: AsyncSession):
        """default_load_options loads relationships automatically on get()."""
        UserWithDefaultLoad = CrudFactory(
            User, default_load_options=[selectinload(User.role)]
        )
        role = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        user = await UserCrud.create(
            db_session,
            UserCreate(username="alice", email="alice@test.com", role_id=role.id),
        )
        fetched = await UserWithDefaultLoad.get(db_session, [User.id == user.id])
        assert fetched.role is not None
        assert fetched.role.name == "admin"

    @pytest.mark.anyio
    async def test_default_load_options_applied_to_get_multi(
        self, db_session: AsyncSession
    ):
        """default_load_options loads relationships automatically on get_multi()."""
        UserWithDefaultLoad = CrudFactory(
            User, default_load_options=[selectinload(User.role)]
        )
        role = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        await UserCrud.create(
            db_session,
            UserCreate(username="alice", email="alice@test.com", role_id=role.id),
        )
        users = await UserWithDefaultLoad.get_multi(db_session)
        assert users[0].role is not None
        assert users[0].role.name == "admin"

    @pytest.mark.anyio
    async def test_default_load_options_applied_to_first(
        self, db_session: AsyncSession
    ):
        """default_load_options loads relationships automatically on first()."""
        UserWithDefaultLoad = CrudFactory(
            User, default_load_options=[selectinload(User.role)]
        )
        role = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        await UserCrud.create(
            db_session,
            UserCreate(username="alice", email="alice@test.com", role_id=role.id),
        )
        user = await UserWithDefaultLoad.first(db_session)
        assert user is not None
        assert user.role is not None
        assert user.role.name == "admin"

    @pytest.mark.anyio
    async def test_default_load_options_applied_to_paginate(
        self, db_session: AsyncSession
    ):
        """default_load_options loads relationships automatically on offset_paginate()."""
        from fastapi_toolsets.schemas import PydanticBase

        class UserWithRoleRead(PydanticBase):
            id: uuid.UUID
            username: str
            role: RoleRead | None = None

        UserWithDefaultLoad = CrudFactory(
            User, default_load_options=[selectinload(User.role)]
        )
        role = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        await UserCrud.create(
            db_session,
            UserCreate(username="alice", email="alice@test.com", role_id=role.id),
        )
        result = await UserWithDefaultLoad.offset_paginate(
            db_session, schema=UserWithRoleRead
        )
        assert result.data[0].role is not None
        assert result.data[0].role.name == "admin"

    @pytest.mark.anyio
    async def test_load_options_overrides_default_load_options(
        self, db_session: AsyncSession
    ):
        """Explicit load_options fully replaces default_load_options."""
        PostWithDefaultLoad = CrudFactory(
            Post,
            default_load_options=[selectinload(Post.tags)],
        )
        user = await UserCrud.create(
            db_session,
            UserCreate(username="alice", email="alice@test.com"),
        )
        post = await PostCrud.create(
            db_session,
            PostCreate(title="Hello", author_id=user.id),
        )
        # Pass empty load_options to override default — tags should not load
        fetched = await PostWithDefaultLoad.get(
            db_session,
            [Post.id == post.id],
            load_options=[],
        )
        # tags were not loaded — accessing them would lazy-load or return empty
        # We just assert the fetch itself succeeded with the override
        assert fetched.id == post.id


class TestCrudCreate:
    """Tests for CRUD create operations."""

    @pytest.mark.anyio
    async def test_create_single_record(self, db_session: AsyncSession):
        """Create a single record."""
        data = RoleCreate(name="admin")
        role = await RoleCrud.create(db_session, data)

        assert role.id is not None
        assert role.name == "admin"

    @pytest.mark.anyio
    async def test_create_with_relationship(self, db_session: AsyncSession):
        """Create records with foreign key relationships."""
        role = await RoleCrud.create(db_session, RoleCreate(name="user"))
        user_data = UserCreate(
            username="john",
            email="john@example.com",
            role_id=role.id,
        )
        user = await UserCrud.create(db_session, user_data)

        assert user.role_id == role.id

    @pytest.mark.anyio
    async def test_create_with_defaults(self, db_session: AsyncSession):
        """Create uses model defaults."""
        user_data = UserCreate(username="jane", email="jane@example.com")
        user = await UserCrud.create(db_session, user_data)

        assert user.is_active is True


class TestCrudGet:
    """Tests for CRUD get operations."""

    @pytest.mark.anyio
    async def test_get_existing_record(self, db_session: AsyncSession):
        """Get an existing record by filter."""
        created = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        fetched = await RoleCrud.get(db_session, [Role.id == created.id])

        assert fetched.id == created.id
        assert fetched.name == "admin"

    @pytest.mark.anyio
    async def test_get_raises_not_found(self, db_session: AsyncSession):
        """Get raises NotFoundError for missing records."""
        non_existent_id = uuid.uuid4()
        with pytest.raises(NotFoundError):
            await RoleCrud.get(db_session, [Role.id == non_existent_id])

    @pytest.mark.anyio
    async def test_get_with_multiple_filters(self, db_session: AsyncSession):
        """Get with multiple filter conditions."""
        await UserCrud.create(
            db_session,
            UserCreate(username="active", email="active@test.com", is_active=True),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="inactive", email="inactive@test.com", is_active=False),
        )

        user = await UserCrud.get(
            db_session,
            [User.username == "active", User.is_active == True],  # noqa: E712
        )
        assert user.username == "active"


class TestCrudGetOrNone:
    """Tests for CRUD get_or_none operations."""

    @pytest.mark.anyio
    async def test_returns_record_when_found(self, db_session: AsyncSession):
        """get_or_none returns the record when it exists."""
        created = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        fetched = await RoleCrud.get_or_none(db_session, [Role.id == created.id])

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == "admin"

    @pytest.mark.anyio
    async def test_returns_none_when_not_found(self, db_session: AsyncSession):
        """get_or_none returns None instead of raising NotFoundError."""
        result = await RoleCrud.get_or_none(db_session, [Role.id == uuid.uuid4()])
        assert result is None

    @pytest.mark.anyio
    async def test_with_schema_returns_response_when_found(
        self, db_session: AsyncSession
    ):
        """get_or_none with schema returns Response[schema] when found."""
        from fastapi_toolsets.schemas import Response

        created = await RoleCrud.create(db_session, RoleCreate(name="editor"))
        result = await RoleCrud.get_or_none(
            db_session, [Role.id == created.id], schema=RoleRead
        )

        assert isinstance(result, Response)
        assert isinstance(result.data, RoleRead)
        assert result.data.name == "editor"

    @pytest.mark.anyio
    async def test_with_schema_returns_none_when_not_found(
        self, db_session: AsyncSession
    ):
        """get_or_none with schema returns None (not Response) when not found."""
        result = await RoleCrud.get_or_none(
            db_session, [Role.id == uuid.uuid4()], schema=RoleRead
        )
        assert result is None

    @pytest.mark.anyio
    async def test_with_load_options(self, db_session: AsyncSession):
        """get_or_none respects load_options."""
        from sqlalchemy.orm import selectinload

        role = await RoleCrud.create(db_session, RoleCreate(name="member"))
        user = await UserCrud.create(
            db_session,
            UserCreate(username="alice", email="alice@test.com", role_id=role.id),
        )

        fetched = await UserCrud.get_or_none(
            db_session,
            [User.id == user.id],
            load_options=[selectinload(User.role)],
        )

        assert fetched is not None
        assert fetched.role is not None
        assert fetched.role.name == "member"

    @pytest.mark.anyio
    async def test_with_join(self, db_session: AsyncSession):
        """get_or_none respects joins."""
        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )
        await PostCrud.create(
            db_session,
            PostCreate(title="Published", author_id=user.id, is_published=True),
        )

        fetched = await UserCrud.get_or_none(
            db_session,
            [User.id == user.id, Post.is_published == True],  # noqa: E712
            joins=[(Post, Post.author_id == User.id)],
        )
        assert fetched is not None
        assert fetched.id == user.id

        # Filter that matches no join — returns None
        missing = await UserCrud.get_or_none(
            db_session,
            [User.id == user.id, Post.is_published == False],  # noqa: E712
            joins=[(Post, Post.author_id == User.id)],
        )
        assert missing is None


class TestCrudFirst:
    """Tests for CRUD first operations."""

    @pytest.mark.anyio
    async def test_first_returns_record(self, db_session: AsyncSession):
        """First returns the first matching record."""
        await RoleCrud.create(db_session, RoleCreate(name="admin"))
        role = await RoleCrud.first(db_session, [Role.name == "admin"])

        assert role is not None
        assert role.name == "admin"

    @pytest.mark.anyio
    async def test_first_returns_none_when_not_found(self, db_session: AsyncSession):
        """First returns None for missing records."""
        role = await RoleCrud.first(db_session, [Role.name == "nonexistent"])
        assert role is None

    @pytest.mark.anyio
    async def test_first_without_filters(self, db_session: AsyncSession):
        """First without filters returns any record."""
        await RoleCrud.create(db_session, RoleCreate(name="role1"))
        await RoleCrud.create(db_session, RoleCreate(name="role2"))

        role = await RoleCrud.first(db_session)
        assert role is not None

    @pytest.mark.anyio
    async def test_first_with_schema(self, db_session: AsyncSession):
        """First with schema returns a Response wrapping the serialized record."""
        await RoleCrud.create(db_session, RoleCreate(name="admin"))

        result = await RoleCrud.first(
            db_session, [Role.name == "admin"], schema=RoleRead
        )

        assert result is not None
        assert result.data is not None
        assert result.data.name == "admin"

    @pytest.mark.anyio
    async def test_first_with_schema_not_found(self, db_session: AsyncSession):
        """First with schema returns None when no record matches."""
        result = await RoleCrud.first(
            db_session, [Role.name == "ghost"], schema=RoleRead
        )
        assert result is None

    @pytest.mark.anyio
    async def test_first_with_for_update(self, db_session: AsyncSession):
        """First with with_for_update locks the row."""
        await RoleCrud.create(db_session, RoleCreate(name="admin"))

        role = await RoleCrud.first(
            db_session, [Role.name == "admin"], with_for_update=True
        )
        assert role is not None
        assert role.name == "admin"


class TestCrudGetMulti:
    """Tests for CRUD get_multi operations."""

    @pytest.mark.anyio
    async def test_get_multi_returns_all(self, db_session: AsyncSession):
        """Get multiple records."""
        await RoleCrud.create(db_session, RoleCreate(name="admin"))
        await RoleCrud.create(db_session, RoleCreate(name="user"))
        await RoleCrud.create(db_session, RoleCreate(name="guest"))

        roles = await RoleCrud.get_multi(db_session)
        assert len(roles) == 3

    @pytest.mark.anyio
    async def test_get_multi_with_filters(self, db_session: AsyncSession):
        """Get multiple with filter."""
        await UserCrud.create(
            db_session,
            UserCreate(username="active1", email="a1@test.com", is_active=True),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="active2", email="a2@test.com", is_active=True),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="inactive", email="i@test.com", is_active=False),
        )

        active_users = await UserCrud.get_multi(
            db_session,
            filters=[User.is_active == True],  # noqa: E712
        )
        assert len(active_users) == 2

    @pytest.mark.anyio
    async def test_get_multi_with_limit(self, db_session: AsyncSession):
        """Get multiple with limit."""
        for i in range(5):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i}"))

        roles = await RoleCrud.get_multi(db_session, limit=3)
        assert len(roles) == 3

    @pytest.mark.anyio
    async def test_get_multi_with_offset(self, db_session: AsyncSession):
        """Get multiple with offset."""
        for i in range(5):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i}"))

        roles = await RoleCrud.get_multi(db_session, offset=2)
        assert len(roles) == 3

    @pytest.mark.anyio
    async def test_get_multi_with_order_by(self, db_session: AsyncSession):
        """Get multiple with ordering."""
        await RoleCrud.create(db_session, RoleCreate(name="charlie"))
        await RoleCrud.create(db_session, RoleCreate(name="alpha"))
        await RoleCrud.create(db_session, RoleCreate(name="bravo"))

        roles = await RoleCrud.get_multi(db_session, order_by=Role.name)
        names = [r.name for r in roles]
        assert names == ["alpha", "bravo", "charlie"]


class TestCrudUpdate:
    """Tests for CRUD update operations."""

    @pytest.mark.anyio
    async def test_update_record(self, db_session: AsyncSession):
        """Update an existing record."""
        role = await RoleCrud.create(db_session, RoleCreate(name="old_name"))
        updated = await RoleCrud.update(
            db_session,
            RoleUpdate(name="new_name"),
            [Role.id == role.id],
        )

        assert updated.name == "new_name"
        assert updated.id == role.id

    @pytest.mark.anyio
    async def test_update_raises_not_found(self, db_session: AsyncSession):
        """Update raises NotFoundError for missing records."""
        non_existent_id = uuid.uuid4()
        with pytest.raises(NotFoundError):
            await RoleCrud.update(
                db_session,
                RoleUpdate(name="new"),
                [Role.id == non_existent_id],
            )

    @pytest.mark.anyio
    async def test_update_excludes_unset(self, db_session: AsyncSession):
        """Update excludes unset fields by default."""
        user = await UserCrud.create(
            db_session,
            UserCreate(username="john", email="john@test.com", is_active=True),
        )

        updated = await UserCrud.update(
            db_session,
            UserUpdate(username="johnny"),
            [User.id == user.id],
        )

        assert updated.username == "johnny"
        assert updated.email == "john@test.com"
        assert updated.is_active is True


class TestCrudDelete:
    """Tests for CRUD delete operations."""

    @pytest.mark.anyio
    async def test_delete_record(self, db_session: AsyncSession):
        """Delete an existing record."""
        role = await RoleCrud.create(db_session, RoleCreate(name="to_delete"))
        result = await RoleCrud.delete(db_session, [Role.id == role.id])

        assert result is None
        assert await RoleCrud.first(db_session, [Role.id == role.id]) is None

    @pytest.mark.anyio
    async def test_delete_multiple_records(self, db_session: AsyncSession):
        """Delete multiple records with filter."""
        await UserCrud.create(
            db_session,
            UserCreate(username="u1", email="u1@test.com", is_active=False),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="u2", email="u2@test.com", is_active=False),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="u3", email="u3@test.com", is_active=True),
        )

        await UserCrud.delete(db_session, [User.is_active == False])  # noqa: E712
        remaining = await UserCrud.get_multi(db_session)
        assert len(remaining) == 1
        assert remaining[0].username == "u3"

    @pytest.mark.anyio
    async def test_delete_return_response(self, db_session: AsyncSession):
        """Delete with return_response=True returns Response[None]."""
        from fastapi_toolsets.schemas import Response

        role = await RoleCrud.create(db_session, RoleCreate(name="to_delete_resp"))
        result = await RoleCrud.delete(
            db_session, [Role.id == role.id], return_response=True
        )

        assert isinstance(result, Response)
        assert result.data is None
        assert await RoleCrud.first(db_session, [Role.id == role.id]) is None

    @pytest.mark.anyio
    async def test_delete_m2m_cascade(self, db_session: AsyncSession):
        """Deleting a record with M2M relationships cleans up the association table."""
        from sqlalchemy import text

        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )
        tag1 = await TagCrud.create(db_session, TagCreate(name="python"))
        tag2 = await TagCrud.create(db_session, TagCreate(name="fastapi"))

        post = await PostM2MCrud.create(
            db_session,
            PostM2MCreate(
                title="M2M Delete Test",
                author_id=user.id,
                tag_ids=[tag1.id, tag2.id],
            ),
        )

        await PostM2MCrud.delete(db_session, [Post.id == post.id])

        # Post is gone
        assert await PostCrud.first(db_session, [Post.id == post.id]) is None

        # Association rows are gone — tags themselves must still exist
        assert await TagCrud.first(db_session, [Tag.id == tag1.id]) is not None
        assert await TagCrud.first(db_session, [Tag.id == tag2.id]) is not None

        # No orphaned rows in post_tags
        result = await db_session.execute(
            text("SELECT COUNT(*) FROM post_tags WHERE post_id = :pid").bindparams(
                pid=post.id
            )
        )
        assert result.scalar() == 0

    @pytest.mark.anyio
    async def test_delete_m2m_does_not_delete_related_records(
        self, db_session: AsyncSession
    ):
        """Deleting a post with M2M tags must not delete the tags themselves."""
        user = await UserCrud.create(
            db_session, UserCreate(username="author2", email="author2@test.com")
        )
        tag = await TagCrud.create(db_session, TagCreate(name="shared_tag"))

        post1 = await PostM2MCrud.create(
            db_session,
            PostM2MCreate(title="Post 1", author_id=user.id, tag_ids=[tag.id]),
        )
        post2 = await PostM2MCrud.create(
            db_session,
            PostM2MCreate(title="Post 2", author_id=user.id, tag_ids=[tag.id]),
        )

        # Delete only post1
        await PostM2MCrud.delete(db_session, [Post.id == post1.id])

        # Tag and post2 still exist
        assert await TagCrud.first(db_session, [Tag.id == tag.id]) is not None
        assert await PostCrud.first(db_session, [Post.id == post2.id]) is not None


class TestCrudExists:
    """Tests for CRUD exists operations."""

    @pytest.mark.anyio
    async def test_exists_returns_true(self, db_session: AsyncSession):
        """Exists returns True for existing records."""
        await RoleCrud.create(db_session, RoleCreate(name="admin"))
        assert await RoleCrud.exists(db_session, [Role.name == "admin"]) is True

    @pytest.mark.anyio
    async def test_exists_returns_false(self, db_session: AsyncSession):
        """Exists returns False for missing records."""
        assert await RoleCrud.exists(db_session, [Role.name == "nonexistent"]) is False


class TestCrudCount:
    """Tests for CRUD count operations."""

    @pytest.mark.anyio
    async def test_count_all(self, db_session: AsyncSession):
        """Count all records."""
        await RoleCrud.create(db_session, RoleCreate(name="role1"))
        await RoleCrud.create(db_session, RoleCreate(name="role2"))
        await RoleCrud.create(db_session, RoleCreate(name="role3"))

        count = await RoleCrud.count(db_session)
        assert count == 3

    @pytest.mark.anyio
    async def test_count_with_filter(self, db_session: AsyncSession):
        """Count records with filter."""
        await UserCrud.create(
            db_session,
            UserCreate(username="a1", email="a1@test.com", is_active=True),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="a2", email="a2@test.com", is_active=True),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="i1", email="i1@test.com", is_active=False),
        )

        active_count = await UserCrud.count(
            db_session,
            filters=[User.is_active == True],  # noqa: E712
        )
        assert active_count == 2


class TestCrudUpsert:
    """Tests for CRUD upsert operations (PostgreSQL-specific)."""

    @pytest.mark.anyio
    async def test_upsert_insert_new_record(self, db_session: AsyncSession):
        """Upsert inserts a new record when it doesn't exist."""
        role_id = uuid.uuid4()
        data = RoleCreate(id=role_id, name="upsert_new")
        role = await RoleCrud.upsert(
            db_session,
            data,
            index_elements=["id"],
        )

        assert role is not None
        assert role.name == "upsert_new"

    @pytest.mark.anyio
    async def test_upsert_update_existing_record(self, db_session: AsyncSession):
        """Upsert updates an existing record."""
        role_id = uuid.uuid4()
        # First insert
        data = RoleCreate(id=role_id, name="original_name")
        await RoleCrud.upsert(db_session, data, index_elements=["id"])

        # Upsert with update
        updated_data = RoleCreate(id=role_id, name="updated_name")
        role = await RoleCrud.upsert(
            db_session,
            updated_data,
            index_elements=["id"],
            set_=RoleUpdate(name="updated_name"),
        )

        assert role is not None
        assert role.name == "updated_name"

        # Verify only one record exists
        count = await RoleCrud.count(db_session, [Role.id == role_id])
        assert count == 1

    @pytest.mark.anyio
    async def test_upsert_do_nothing_on_conflict(self, db_session: AsyncSession):
        """Upsert does nothing on conflict when set_ is not provided."""
        role_id = uuid.uuid4()
        # First insert
        data = RoleCreate(id=role_id, name="do_nothing_original")
        await RoleCrud.upsert(db_session, data, index_elements=["id"])

        # Upsert without set_ (do nothing)
        conflict_data = RoleCreate(id=role_id, name="do_nothing_conflict")
        await RoleCrud.upsert(db_session, conflict_data, index_elements=["id"])

        # Original value should be preserved
        role = await RoleCrud.first(db_session, [Role.id == role_id])
        assert role is not None
        assert role.name == "do_nothing_original"

    @pytest.mark.anyio
    async def test_upsert_with_unique_constraint(self, db_session: AsyncSession):
        """Upsert works with unique constraint columns."""
        # Insert first role
        data1 = RoleCreate(name="unique_role")
        await RoleCrud.upsert(db_session, data1, index_elements=["name"])

        # Upsert with same name - should update (or do nothing)
        data2 = RoleCreate(name="unique_role")
        role = await RoleCrud.upsert(db_session, data2, index_elements=["name"])

        assert role is not None
        assert role.name == "unique_role"

        # Should still be only one record
        count = await RoleCrud.count(db_session, [Role.name == "unique_role"])
        assert count == 1


class TestCrudPaginate:
    """Tests for CRUD pagination."""

    @pytest.mark.anyio
    async def test_paginate_first_page(self, db_session: AsyncSession):
        """Paginate returns first page."""
        for i in range(25):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        from fastapi_toolsets.schemas import OffsetPagination

        result = await RoleCrud.offset_paginate(
            db_session, page=1, items_per_page=10, schema=RoleRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert len(result.data) == 10
        assert result.pagination.total_count == 25
        assert result.pagination.page == 1
        assert result.pagination.items_per_page == 10
        assert result.pagination.has_more is True

    @pytest.mark.anyio
    async def test_paginate_last_page(self, db_session: AsyncSession):
        """Paginate returns last page with has_more=False."""
        for i in range(25):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        result = await RoleCrud.offset_paginate(
            db_session, page=3, items_per_page=10, schema=RoleRead
        )

        assert len(result.data) == 5
        assert result.pagination.has_more is False

    @pytest.mark.anyio
    async def test_paginate_with_filters(self, db_session: AsyncSession):
        """Paginate with filter conditions."""
        for i in range(10):
            await UserCrud.create(
                db_session,
                UserCreate(
                    username=f"user{i}",
                    email=f"user{i}@test.com",
                    is_active=i % 2 == 0,
                ),
            )

        from fastapi_toolsets.schemas import OffsetPagination

        result = await UserCrud.offset_paginate(
            db_session,
            filters=[User.is_active == True],  # noqa: E712
            page=1,
            items_per_page=10,
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 5

    @pytest.mark.anyio
    async def test_paginate_with_ordering(self, db_session: AsyncSession):
        """Paginate with custom ordering."""
        await RoleCrud.create(db_session, RoleCreate(name="charlie"))
        await RoleCrud.create(db_session, RoleCreate(name="alpha"))
        await RoleCrud.create(db_session, RoleCreate(name="bravo"))

        result = await RoleCrud.offset_paginate(
            db_session,
            order_by=Role.name,
            page=1,
            items_per_page=10,
            schema=RoleRead,
        )

        names = [r.name for r in result.data]
        assert names == ["alpha", "bravo", "charlie"]


class TestCrudJoins:
    """Tests for CRUD operations with joins."""

    @pytest.mark.anyio
    async def test_get_with_join(self, db_session: AsyncSession):
        """Get with inner join filters correctly."""
        # Create user with posts
        user = await UserCrud.create(
            db_session,
            UserCreate(username="author", email="author@test.com"),
        )
        await PostCrud.create(
            db_session,
            PostCreate(title="Post 1", author_id=user.id, is_published=True),
        )

        # Get user with join on published posts
        fetched = await UserCrud.get(
            db_session,
            filters=[User.id == user.id, Post.is_published == True],  # noqa: E712
            joins=[(Post, Post.author_id == User.id)],
        )
        assert fetched.id == user.id

    @pytest.mark.anyio
    async def test_first_with_join(self, db_session: AsyncSession):
        """First with join returns matching record."""
        user = await UserCrud.create(
            db_session,
            UserCreate(username="writer", email="writer@test.com"),
        )
        await PostCrud.create(
            db_session,
            PostCreate(title="Draft", author_id=user.id, is_published=False),
        )

        # Find user with unpublished posts
        result = await UserCrud.first(
            db_session,
            filters=[Post.is_published == False],  # noqa: E712
            joins=[(Post, Post.author_id == User.id)],
        )
        assert result is not None
        assert result.id == user.id

    @pytest.mark.anyio
    async def test_first_with_outer_join(self, db_session: AsyncSession):
        """First with outer join includes records without related data."""
        # User without posts
        user = await UserCrud.create(
            db_session,
            UserCreate(username="no_posts", email="no_posts@test.com"),
        )

        # With outer join, user should be found even without posts
        result = await UserCrud.first(
            db_session,
            filters=[User.id == user.id],
            joins=[(Post, Post.author_id == User.id)],
            outer_join=True,
        )
        assert result is not None
        assert result.id == user.id

    @pytest.mark.anyio
    async def test_get_multi_with_inner_join(self, db_session: AsyncSession):
        """Get multiple with inner join only returns matching records."""
        # User with published post
        user1 = await UserCrud.create(
            db_session,
            UserCreate(username="publisher", email="pub@test.com"),
        )
        await PostCrud.create(
            db_session,
            PostCreate(title="Published", author_id=user1.id, is_published=True),
        )

        # User without posts
        await UserCrud.create(
            db_session,
            UserCreate(username="lurker", email="lurk@test.com"),
        )

        # Inner join should only return user with published post
        users = await UserCrud.get_multi(
            db_session,
            joins=[(Post, Post.author_id == User.id)],
            filters=[Post.is_published == True],  # noqa: E712
        )
        assert len(users) == 1
        assert users[0].username == "publisher"

    @pytest.mark.anyio
    async def test_get_multi_with_outer_join(self, db_session: AsyncSession):
        """Get multiple with outer join includes all records."""
        # User with post
        user1 = await UserCrud.create(
            db_session,
            UserCreate(username="has_post", email="has@test.com"),
        )
        await PostCrud.create(
            db_session,
            PostCreate(title="My Post", author_id=user1.id),
        )

        # User without posts
        await UserCrud.create(
            db_session,
            UserCreate(username="no_post", email="no@test.com"),
        )

        # Outer join should return both users
        users = await UserCrud.get_multi(
            db_session,
            joins=[(Post, Post.author_id == User.id)],
            outer_join=True,
        )
        assert len(users) == 2

    @pytest.mark.anyio
    async def test_count_with_join(self, db_session: AsyncSession):
        """Count with join counts correctly."""
        # Create users with different post statuses
        user1 = await UserCrud.create(
            db_session,
            UserCreate(username="active_author", email="active@test.com"),
        )
        await PostCrud.create(
            db_session,
            PostCreate(title="Published 1", author_id=user1.id, is_published=True),
        )

        user2 = await UserCrud.create(
            db_session,
            UserCreate(username="draft_author", email="draft@test.com"),
        )
        await PostCrud.create(
            db_session,
            PostCreate(title="Draft 1", author_id=user2.id, is_published=False),
        )

        # Count users with published posts
        count = await UserCrud.count(
            db_session,
            filters=[Post.is_published == True],  # noqa: E712
            joins=[(Post, Post.author_id == User.id)],
        )
        assert count == 1

    @pytest.mark.anyio
    async def test_exists_with_join(self, db_session: AsyncSession):
        """Exists with join checks correctly."""
        user = await UserCrud.create(
            db_session,
            UserCreate(username="poster", email="poster@test.com"),
        )
        await PostCrud.create(
            db_session,
            PostCreate(title="Exists Post", author_id=user.id, is_published=True),
        )

        # Check if user with published post exists
        exists = await UserCrud.exists(
            db_session,
            filters=[Post.is_published == True],  # noqa: E712
            joins=[(Post, Post.author_id == User.id)],
        )
        assert exists is True

        # Check if user with specific title exists
        exists = await UserCrud.exists(
            db_session,
            filters=[Post.title == "Nonexistent"],
            joins=[(Post, Post.author_id == User.id)],
        )
        assert exists is False

    @pytest.mark.anyio
    async def test_paginate_with_join(self, db_session: AsyncSession):
        """Paginate with join works correctly."""
        # Create users with posts
        for i in range(5):
            user = await UserCrud.create(
                db_session,
                UserCreate(username=f"author{i}", email=f"author{i}@test.com"),
            )
            await PostCrud.create(
                db_session,
                PostCreate(
                    title=f"Post {i}",
                    author_id=user.id,
                    is_published=i % 2 == 0,
                ),
            )

        from fastapi_toolsets.schemas import OffsetPagination

        # Paginate users with published posts
        result = await UserCrud.offset_paginate(
            db_session,
            joins=[(Post, Post.author_id == User.id)],
            filters=[Post.is_published == True],  # noqa: E712
            page=1,
            items_per_page=10,
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 3
        assert len(result.data) == 3

    @pytest.mark.anyio
    async def test_paginate_with_outer_join(self, db_session: AsyncSession):
        """Paginate with outer join includes all records."""
        # User with post
        user1 = await UserCrud.create(
            db_session,
            UserCreate(username="with_post", email="with@test.com"),
        )
        await PostCrud.create(
            db_session,
            PostCreate(title="A Post", author_id=user1.id),
        )

        # User without post
        await UserCrud.create(
            db_session,
            UserCreate(username="without_post", email="without@test.com"),
        )

        from fastapi_toolsets.schemas import OffsetPagination

        # Paginate with outer join
        result = await UserCrud.offset_paginate(
            db_session,
            joins=[(Post, Post.author_id == User.id)],
            outer_join=True,
            page=1,
            items_per_page=10,
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2
        assert len(result.data) == 2

    @pytest.mark.anyio
    async def test_multiple_joins(self, db_session: AsyncSession):
        """Multiple joins can be applied."""
        role = await RoleCrud.create(db_session, RoleCreate(name="author_role"))
        user = await UserCrud.create(
            db_session,
            UserCreate(
                username="multi_join",
                email="multi@test.com",
                role_id=role.id,
            ),
        )
        await PostCrud.create(
            db_session,
            PostCreate(title="Multi Join Post", author_id=user.id, is_published=True),
        )

        # Join both Role and Post
        users = await UserCrud.get_multi(
            db_session,
            joins=[
                (Role, Role.id == User.role_id),
                (Post, Post.author_id == User.id),
            ],
            filters=[Role.name == "author_role", Post.is_published == True],  # noqa: E712
        )
        assert len(users) == 1
        assert users[0].username == "multi_join"


class TestCrudAliasedJoins:
    """Tests for CRUD operations with aliased joins (same table joined twice)."""

    @pytest.mark.anyio
    async def test_get_multi_with_aliased_joins(self, db_session: AsyncSession):
        """Aliased joins allow joining the same table twice."""
        from sqlalchemy.orm import aliased

        alice = await UserCrud.create(
            db_session, UserCreate(username="alice", email="alice@test.com")
        )
        bob = await UserCrud.create(
            db_session, UserCreate(username="bob", email="bob@test.com")
        )
        await TransferCrud.create(
            db_session,
            TransferCreate(amount="100", sender_id=alice.id, receiver_id=bob.id),
        )

        Sender = aliased(User)
        Receiver = aliased(User)

        results = await TransferCrud.get_multi(
            db_session,
            joins=[
                (Sender, Transfer.sender_id == Sender.id),
                (Receiver, Transfer.receiver_id == Receiver.id),
            ],
            filters=[Sender.username == "alice", Receiver.username == "bob"],
        )
        assert len(results) == 1
        assert results[0].amount == "100"

    @pytest.mark.anyio
    async def test_get_multi_aliased_no_match(self, db_session: AsyncSession):
        """Aliased joins correctly filter out non-matching rows."""
        from sqlalchemy.orm import aliased

        alice = await UserCrud.create(
            db_session, UserCreate(username="alice", email="alice@test.com")
        )
        bob = await UserCrud.create(
            db_session, UserCreate(username="bob", email="bob@test.com")
        )
        await TransferCrud.create(
            db_session,
            TransferCreate(amount="100", sender_id=alice.id, receiver_id=bob.id),
        )

        Sender = aliased(User)
        Receiver = aliased(User)

        # bob is receiver, not sender — should return nothing
        results = await TransferCrud.get_multi(
            db_session,
            joins=[
                (Sender, Transfer.sender_id == Sender.id),
                (Receiver, Transfer.receiver_id == Receiver.id),
            ],
            filters=[Sender.username == "bob", Receiver.username == "alice"],
        )
        assert len(results) == 0

    @pytest.mark.anyio
    async def test_paginate_with_aliased_joins(self, db_session: AsyncSession):
        """Aliased joins work with offset_paginate."""
        from sqlalchemy.orm import aliased

        alice = await UserCrud.create(
            db_session, UserCreate(username="alice", email="alice@test.com")
        )
        bob = await UserCrud.create(
            db_session, UserCreate(username="bob", email="bob@test.com")
        )
        await TransferCrud.create(
            db_session,
            TransferCreate(amount="50", sender_id=alice.id, receiver_id=bob.id),
        )
        await TransferCrud.create(
            db_session,
            TransferCreate(amount="75", sender_id=bob.id, receiver_id=alice.id),
        )

        Sender = aliased(User)
        result = await TransferCrud.offset_paginate(
            db_session,
            joins=[(Sender, Transfer.sender_id == Sender.id)],
            filters=[Sender.username == "alice"],
            schema=TransferRead,
        )
        assert result.pagination.total_count == 1
        assert result.data[0].amount == "50"

    @pytest.mark.anyio
    async def test_count_with_aliased_join(self, db_session: AsyncSession):
        """Aliased joins work with count."""
        from sqlalchemy.orm import aliased

        alice = await UserCrud.create(
            db_session, UserCreate(username="alice", email="alice@test.com")
        )
        bob = await UserCrud.create(
            db_session, UserCreate(username="bob", email="bob@test.com")
        )
        await TransferCrud.create(
            db_session,
            TransferCreate(amount="10", sender_id=alice.id, receiver_id=bob.id),
        )
        await TransferCrud.create(
            db_session,
            TransferCreate(amount="20", sender_id=alice.id, receiver_id=bob.id),
        )

        Sender = aliased(User)
        count = await TransferCrud.count(
            db_session,
            joins=[(Sender, Transfer.sender_id == Sender.id)],
            filters=[Sender.username == "alice"],
        )
        assert count == 2


class TestCrudFactoryM2M:
    """Tests for CrudFactory with m2m_fields parameter."""

    def test_creates_crud_with_m2m_fields(self):
        """CrudFactory configures m2m_fields on the class."""
        crud = CrudFactory(Post, m2m_fields={"tag_ids": Post.tags})
        assert crud.m2m_fields is not None
        assert "tag_ids" in crud.m2m_fields

    def test_creates_crud_without_m2m_fields(self):
        """CrudFactory without m2m_fields has None."""
        crud = CrudFactory(Post)
        assert crud.m2m_fields is None

    def test_m2m_schema_fields(self):
        """_m2m_schema_fields returns correct field names."""
        crud = CrudFactory(Post, m2m_fields={"tag_ids": Post.tags})
        assert crud._m2m_schema_fields() == {"tag_ids"}

    def test_m2m_schema_fields_empty_when_none(self):
        """_m2m_schema_fields returns empty set when no m2m_fields."""
        crud = CrudFactory(Post)
        assert crud._m2m_schema_fields() == set()

    @pytest.mark.anyio
    async def test_resolve_m2m_returns_empty_without_m2m_fields(
        self, db_session: AsyncSession
    ):
        """_resolve_m2m returns empty dict when m2m_fields is not configured."""
        from pydantic import BaseModel

        class DummySchema(BaseModel):
            name: str

        result = await PostCrud._resolve_m2m(db_session, DummySchema(name="test"))
        assert result == {}


class TestM2MResolveNone:
    """Tests for _resolve_m2m when IDs field is None."""

    @pytest.mark.anyio
    async def test_resolve_m2m_with_none_ids(self, db_session: AsyncSession):
        """_resolve_m2m sets empty list when ids value is None."""
        from pydantic import BaseModel

        class SchemaWithNullableTags(BaseModel):
            tag_ids: list[uuid.UUID] | None = None

        result = await PostM2MCrud._resolve_m2m(
            db_session, SchemaWithNullableTags(tag_ids=None)
        )
        assert result == {"tags": []}


class TestM2MCreate:
    """Tests for create with M2M relationships."""

    @pytest.mark.anyio
    async def test_create_with_m2m_tags(self, db_session: AsyncSession):
        """Create a post with M2M tags resolves tag IDs."""
        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )
        tag1 = await TagCrud.create(db_session, TagCreate(name="python"))
        tag2 = await TagCrud.create(db_session, TagCreate(name="fastapi"))

        post = await PostM2MCrud.create(
            db_session,
            PostM2MCreate(
                title="M2M Post",
                author_id=user.id,
                tag_ids=[tag1.id, tag2.id],
            ),
        )

        assert post.id is not None
        assert post.title == "M2M Post"

        # Reload with tags eagerly loaded
        loaded = await PostM2MCrud.get(
            db_session,
            [Post.id == post.id],
            load_options=[selectinload(Post.tags)],
        )
        tag_names = sorted(t.name for t in loaded.tags)
        assert tag_names == ["fastapi", "python"]

    @pytest.mark.anyio
    async def test_create_with_empty_m2m(self, db_session: AsyncSession):
        """Create a post with empty tag_ids list works."""
        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )

        post = await PostM2MCrud.create(
            db_session,
            PostM2MCreate(
                title="No Tags Post",
                author_id=user.id,
                tag_ids=[],
            ),
        )

        assert post.id is not None

        loaded = await PostM2MCrud.get(
            db_session,
            [Post.id == post.id],
            load_options=[selectinload(Post.tags)],
        )
        assert loaded.tags == []

    @pytest.mark.anyio
    async def test_create_with_default_m2m(self, db_session: AsyncSession):
        """Create a post using default tag_ids (empty list) works."""
        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )

        post = await PostM2MCrud.create(
            db_session,
            PostM2MCreate(title="Default Tags", author_id=user.id),
        )

        loaded = await PostM2MCrud.get(
            db_session,
            [Post.id == post.id],
            load_options=[selectinload(Post.tags)],
        )
        assert loaded.tags == []

    @pytest.mark.anyio
    async def test_create_with_nonexistent_tag_id_raises(
        self, db_session: AsyncSession
    ):
        """Create with a nonexistent tag ID raises NotFoundError."""
        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )
        tag = await TagCrud.create(db_session, TagCreate(name="valid"))
        fake_id = uuid.uuid4()

        with pytest.raises(NotFoundError):
            await PostM2MCrud.create(
                db_session,
                PostM2MCreate(
                    title="Bad Tags",
                    author_id=user.id,
                    tag_ids=[tag.id, fake_id],
                ),
            )

    @pytest.mark.anyio
    async def test_create_with_single_tag(self, db_session: AsyncSession):
        """Create with a single tag works correctly."""
        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )
        tag = await TagCrud.create(db_session, TagCreate(name="solo"))

        post = await PostM2MCrud.create(
            db_session,
            PostM2MCreate(
                title="Single Tag",
                author_id=user.id,
                tag_ids=[tag.id],
            ),
        )

        loaded = await PostM2MCrud.get(
            db_session,
            [Post.id == post.id],
            load_options=[selectinload(Post.tags)],
        )
        assert len(loaded.tags) == 1
        assert loaded.tags[0].name == "solo"


class TestM2MUpdate:
    """Tests for update with M2M relationships."""

    @pytest.mark.anyio
    async def test_update_m2m_tags(self, db_session: AsyncSession):
        """Update replaces M2M tags when tag_ids is set."""
        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )
        tag1 = await TagCrud.create(db_session, TagCreate(name="old_tag"))
        tag2 = await TagCrud.create(db_session, TagCreate(name="new_tag"))

        # Create with tag1
        post = await PostM2MCrud.create(
            db_session,
            PostM2MCreate(
                title="Update Test",
                author_id=user.id,
                tag_ids=[tag1.id],
            ),
        )

        # Update to tag2
        updated = await PostM2MCrud.update(
            db_session,
            PostM2MUpdate(tag_ids=[tag2.id]),
            [Post.id == post.id],
        )

        loaded = await PostM2MCrud.get(
            db_session,
            [Post.id == updated.id],
            load_options=[selectinload(Post.tags)],
        )
        assert len(loaded.tags) == 1
        assert loaded.tags[0].name == "new_tag"

    @pytest.mark.anyio
    async def test_update_without_m2m_preserves_tags(self, db_session: AsyncSession):
        """Update without setting tag_ids preserves existing tags."""
        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )
        tag = await TagCrud.create(db_session, TagCreate(name="keep_me"))

        post = await PostM2MCrud.create(
            db_session,
            PostM2MCreate(
                title="Keep Tags",
                author_id=user.id,
                tag_ids=[tag.id],
            ),
        )

        # Update only title, tag_ids not set
        await PostM2MCrud.update(
            db_session,
            PostM2MUpdate(title="Updated Title"),
            [Post.id == post.id],
        )

        loaded = await PostM2MCrud.get(
            db_session,
            [Post.id == post.id],
            load_options=[selectinload(Post.tags)],
        )
        assert loaded.title == "Updated Title"
        assert len(loaded.tags) == 1
        assert loaded.tags[0].name == "keep_me"

    @pytest.mark.anyio
    async def test_update_clear_m2m_tags(self, db_session: AsyncSession):
        """Update with empty tag_ids clears all tags."""
        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )
        tag = await TagCrud.create(db_session, TagCreate(name="remove_me"))

        post = await PostM2MCrud.create(
            db_session,
            PostM2MCreate(
                title="Clear Tags",
                author_id=user.id,
                tag_ids=[tag.id],
            ),
        )

        # Explicitly set tag_ids to empty list
        await PostM2MCrud.update(
            db_session,
            PostM2MUpdate(tag_ids=[]),
            [Post.id == post.id],
        )

        loaded = await PostM2MCrud.get(
            db_session,
            [Post.id == post.id],
            load_options=[selectinload(Post.tags)],
        )
        assert loaded.tags == []

    @pytest.mark.anyio
    async def test_update_m2m_with_nonexistent_id_raises(
        self, db_session: AsyncSession
    ):
        """Update with nonexistent tag ID raises NotFoundError."""
        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )
        tag = await TagCrud.create(db_session, TagCreate(name="existing"))

        post = await PostM2MCrud.create(
            db_session,
            PostM2MCreate(
                title="Bad Update",
                author_id=user.id,
                tag_ids=[tag.id],
            ),
        )

        fake_id = uuid.uuid4()
        with pytest.raises(NotFoundError):
            await PostM2MCrud.update(
                db_session,
                PostM2MUpdate(tag_ids=[fake_id]),
                [Post.id == post.id],
            )

    @pytest.mark.anyio
    async def test_update_m2m_and_scalar_fields(self, db_session: AsyncSession):
        """Update both scalar fields and M2M tags together."""
        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )
        tag1 = await TagCrud.create(db_session, TagCreate(name="tag1"))
        tag2 = await TagCrud.create(db_session, TagCreate(name="tag2"))

        post = await PostM2MCrud.create(
            db_session,
            PostM2MCreate(
                title="Original",
                author_id=user.id,
                tag_ids=[tag1.id],
            ),
        )

        # Update title and tags simultaneously
        await PostM2MCrud.update(
            db_session,
            PostM2MUpdate(title="Updated", tag_ids=[tag1.id, tag2.id]),
            [Post.id == post.id],
        )

        loaded = await PostM2MCrud.get(
            db_session,
            [Post.id == post.id],
            load_options=[selectinload(Post.tags)],
        )
        assert loaded.title == "Updated"
        tag_names = sorted(t.name for t in loaded.tags)
        assert tag_names == ["tag1", "tag2"]


class TestM2MWithNonM2MCrud:
    """Tests that non-M2M CRUD classes are unaffected."""

    @pytest.mark.anyio
    async def test_create_without_m2m_unchanged(self, db_session: AsyncSession):
        """Regular PostCrud.create still works without M2M logic."""
        from .conftest import PostCreate

        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )
        post = await PostCrud.create(
            db_session,
            PostCreate(title="Plain Post", author_id=user.id),
        )
        assert post.id is not None
        assert post.title == "Plain Post"

    @pytest.mark.anyio
    async def test_update_without_m2m_unchanged(self, db_session: AsyncSession):
        """Regular PostCrud.update still works without M2M logic."""
        from .conftest import PostCreate, PostUpdate

        user = await UserCrud.create(
            db_session, UserCreate(username="author", email="author@test.com")
        )
        post = await PostCrud.create(
            db_session,
            PostCreate(title="Plain Post", author_id=user.id),
        )
        updated = await PostCrud.update(
            db_session,
            PostUpdate(title="Updated Plain"),
            [Post.id == post.id],
        )
        assert updated.title == "Updated Plain"


class TestSchemaResponse:
    """Tests for the schema parameter on as_response methods."""

    @pytest.mark.anyio
    async def test_create_with_schema(self, db_session: AsyncSession):
        """create with schema returns Response[SchemaType]."""
        from fastapi_toolsets.schemas import Response

        result = await RoleCrud.create(
            db_session, RoleCreate(name="schema_role"), schema=RoleRead
        )

        assert isinstance(result, Response)
        assert isinstance(result.data, RoleRead)
        assert result.data.name == "schema_role"

    @pytest.mark.anyio
    async def test_create_schema_implies_as_response(self, db_session: AsyncSession):
        """create with schema alone wraps in Response without as_response=True."""
        from fastapi_toolsets.schemas import Response

        result = await RoleCrud.create(
            db_session, RoleCreate(name="implicit"), schema=RoleRead
        )

        assert isinstance(result, Response)

    @pytest.mark.anyio
    async def test_create_schema_filters_fields(self, db_session: AsyncSession):
        """create with schema only exposes schema fields, not all model fields."""
        result = await UserCrud.create(
            db_session,
            UserCreate(username="filtered", email="filtered@test.com"),
            schema=UserRead,
        )

        assert isinstance(result.data, UserRead)
        assert result.data.username == "filtered"
        assert not hasattr(result.data, "email")

    @pytest.mark.anyio
    async def test_get_with_schema(self, db_session: AsyncSession):
        """get with schema returns Response[SchemaType]."""
        from fastapi_toolsets.schemas import Response

        created = await RoleCrud.create(db_session, RoleCreate(name="get_schema"))
        result = await RoleCrud.get(
            db_session, [Role.id == created.id], schema=RoleRead
        )

        assert isinstance(result, Response)
        assert isinstance(result.data, RoleRead)
        assert result.data.id == created.id
        assert result.data.name == "get_schema"

    @pytest.mark.anyio
    async def test_get_schema_implies_as_response(self, db_session: AsyncSession):
        """get with schema alone wraps in Response without as_response=True."""
        from fastapi_toolsets.schemas import Response

        created = await RoleCrud.create(db_session, RoleCreate(name="implicit_get"))
        result = await RoleCrud.get(
            db_session, [Role.id == created.id], schema=RoleRead
        )

        assert isinstance(result, Response)

    @pytest.mark.anyio
    async def test_update_with_schema(self, db_session: AsyncSession):
        """update with schema returns Response[SchemaType]."""
        from fastapi_toolsets.schemas import Response

        created = await RoleCrud.create(db_session, RoleCreate(name="before"))
        result = await RoleCrud.update(
            db_session,
            RoleUpdate(name="after"),
            [Role.id == created.id],
            schema=RoleRead,
        )

        assert isinstance(result, Response)
        assert isinstance(result.data, RoleRead)
        assert result.data.name == "after"

    @pytest.mark.anyio
    async def test_update_schema_implies_as_response(self, db_session: AsyncSession):
        """update with schema alone wraps in Response without as_response=True."""
        from fastapi_toolsets.schemas import Response

        created = await RoleCrud.create(db_session, RoleCreate(name="before2"))
        result = await RoleCrud.update(
            db_session,
            RoleUpdate(name="after2"),
            [Role.id == created.id],
            schema=RoleRead,
        )

        assert isinstance(result, Response)

    @pytest.mark.anyio
    async def test_offset_paginate_with_schema(self, db_session: AsyncSession):
        """offset_paginate with schema returns PaginatedResponse[SchemaType]."""
        from fastapi_toolsets.schemas import PaginatedResponse

        await RoleCrud.create(db_session, RoleCreate(name="p_role1"))
        await RoleCrud.create(db_session, RoleCreate(name="p_role2"))

        result = await RoleCrud.offset_paginate(db_session, schema=RoleRead)

        assert isinstance(result, PaginatedResponse)
        assert len(result.data) == 2
        assert all(isinstance(item, RoleRead) for item in result.data)

    @pytest.mark.anyio
    async def test_offset_paginate_schema_filters_fields(
        self, db_session: AsyncSession
    ):
        """offset_paginate with schema only exposes schema fields per item."""
        await UserCrud.create(
            db_session,
            UserCreate(username="pg_user", email="pg@test.com"),
        )

        result = await UserCrud.offset_paginate(db_session, schema=UserRead)

        assert isinstance(result.data[0], UserRead)
        assert result.data[0].username == "pg_user"
        assert not hasattr(result.data[0], "email")

    @pytest.mark.anyio
    async def test_include_total_false_skips_count(self, db_session: AsyncSession):
        """offset_paginate with include_total=False returns total_count=None."""
        from fastapi_toolsets.schemas import OffsetPagination

        for i in range(5):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        result = await RoleCrud.offset_paginate(
            db_session, items_per_page=10, include_total=False, schema=RoleRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count is None
        assert len(result.data) == 5
        assert result.pagination.has_more is False

    @pytest.mark.anyio
    async def test_include_total_false_has_more_true(self, db_session: AsyncSession):
        """offset_paginate with include_total=False sets has_more via extra-row probe."""
        for i in range(15):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        result = await RoleCrud.offset_paginate(
            db_session, items_per_page=10, include_total=False, schema=RoleRead
        )

        assert result.pagination.total_count is None
        assert result.pagination.has_more is True
        assert len(result.data) == 10

    @pytest.mark.anyio
    async def test_include_total_false_exact_page_boundary(
        self, db_session: AsyncSession
    ):
        """offset_paginate with include_total=False: has_more=False when items == page size."""
        for i in range(10):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        result = await RoleCrud.offset_paginate(
            db_session, items_per_page=10, include_total=False, schema=RoleRead
        )

        assert result.pagination.has_more is False
        assert len(result.data) == 10


class TestCursorPaginate:
    """Tests for cursor-based pagination via cursor_paginate()."""

    @pytest.mark.anyio
    async def test_first_page_no_cursor(self, db_session: AsyncSession):
        """cursor_paginate without cursor returns the first page."""
        from fastapi_toolsets.schemas import CursorPagination, PaginatedResponse

        for i in range(25):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        result = await RoleCursorCrud.cursor_paginate(
            db_session, items_per_page=10, schema=RoleRead
        )

        assert isinstance(result, PaginatedResponse)
        assert isinstance(result.pagination, CursorPagination)
        assert len(result.data) == 10
        assert result.pagination.has_more is True
        assert result.pagination.next_cursor is not None
        assert result.pagination.prev_cursor is None
        assert result.pagination.items_per_page == 10

    @pytest.mark.anyio
    async def test_last_page(self, db_session: AsyncSession):
        """cursor_paginate returns has_more=False and next_cursor=None on last page."""
        from fastapi_toolsets.schemas import CursorPagination

        for i in range(5):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        result = await RoleCursorCrud.cursor_paginate(
            db_session, items_per_page=10, schema=RoleRead
        )

        assert isinstance(result.pagination, CursorPagination)
        assert len(result.data) == 5
        assert result.pagination.has_more is False
        assert result.pagination.next_cursor is None

    @pytest.mark.anyio
    async def test_advances_correctly(self, db_session: AsyncSession):
        """Providing next_cursor from the first page returns the next page."""
        for i in range(15):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        from fastapi_toolsets.schemas import CursorPagination

        page1 = await RoleCursorCrud.cursor_paginate(
            db_session, items_per_page=10, schema=RoleRead
        )
        assert isinstance(page1.pagination, CursorPagination)
        assert len(page1.data) == 10
        assert page1.pagination.has_more is True

        cursor = page1.pagination.next_cursor
        page2 = await RoleCursorCrud.cursor_paginate(
            db_session, cursor=cursor, items_per_page=10, schema=RoleRead
        )
        assert isinstance(page2.pagination, CursorPagination)
        assert len(page2.data) == 5
        assert page2.pagination.has_more is False
        assert page2.pagination.next_cursor is None

    @pytest.mark.anyio
    async def test_no_duplicates_across_pages(self, db_session: AsyncSession):
        """Items from consecutive cursor pages are non-overlapping and cover all rows."""
        for i in range(7):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        from fastapi_toolsets.schemas import CursorPagination

        page1 = await RoleCursorCrud.cursor_paginate(
            db_session, items_per_page=4, schema=RoleRead
        )
        assert isinstance(page1.pagination, CursorPagination)
        page2 = await RoleCursorCrud.cursor_paginate(
            db_session,
            cursor=page1.pagination.next_cursor,
            items_per_page=4,
            schema=RoleRead,
        )

        ids_page1 = {r.id for r in page1.data}
        ids_page2 = {r.id for r in page2.data}
        assert ids_page1.isdisjoint(ids_page2)
        assert len(ids_page1 | ids_page2) == 7

    @pytest.mark.anyio
    async def test_empty_table(self, db_session: AsyncSession):
        """cursor_paginate on an empty table returns empty data with no cursor."""
        from fastapi_toolsets.schemas import CursorPagination

        result = await RoleCursorCrud.cursor_paginate(
            db_session, items_per_page=10, schema=RoleRead
        )

        assert isinstance(result.pagination, CursorPagination)
        assert result.data == []
        assert result.pagination.has_more is False
        assert result.pagination.next_cursor is None
        assert result.pagination.prev_cursor is None

    @pytest.mark.anyio
    async def test_with_filters(self, db_session: AsyncSession):
        """cursor_paginate respects filters."""
        for i in range(10):
            await UserCrud.create(
                db_session,
                UserCreate(
                    username=f"user{i}",
                    email=f"user{i}@test.com",
                    is_active=i % 2 == 0,
                ),
            )

        result = await UserCursorCrud.cursor_paginate(
            db_session,
            filters=[User.is_active == True],  # noqa: E712
            items_per_page=20,
            schema=UserRead,
        )

        assert len(result.data) == 5
        assert all(u.is_active for u in result.data)

    @pytest.mark.anyio
    async def test_with_schema(self, db_session: AsyncSession):
        """cursor_paginate with schema serializes items into the schema."""
        from fastapi_toolsets.schemas import PaginatedResponse

        for i in range(3):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        result = await RoleCursorCrud.cursor_paginate(db_session, schema=RoleRead)

        assert isinstance(result, PaginatedResponse)
        assert all(isinstance(item, RoleRead) for item in result.data)
        assert all(
            hasattr(item, "id") and hasattr(item, "name") for item in result.data
        )

    @pytest.mark.anyio
    async def test_with_cursor_column(self, db_session: AsyncSession):
        """cursor_paginate uses cursor_column set on CrudFactory."""
        from fastapi_toolsets.crud import CrudFactory
        from fastapi_toolsets.schemas import CursorPagination

        RoleNameCrud = CrudFactory(Role, cursor_column=Role.name)

        for i in range(5):
            await RoleNameCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        result = await RoleNameCrud.cursor_paginate(
            db_session, items_per_page=3, schema=RoleRead
        )

        assert isinstance(result.pagination, CursorPagination)
        assert len(result.data) == 3
        assert result.pagination.has_more is True
        assert result.pagination.next_cursor is not None

    @pytest.mark.anyio
    async def test_raises_without_cursor_column(self, db_session: AsyncSession):
        """cursor_paginate raises ValueError when cursor_column is not configured."""
        with pytest.raises(ValueError, match="cursor_column is not set"):
            await RoleCrud.cursor_paginate(db_session, schema=RoleRead)


class TestCursorPaginatePrevCursor:
    """Tests for prev_cursor behavior in cursor_paginate()."""

    @pytest.mark.anyio
    async def test_prev_cursor_none_on_first_page(self, db_session: AsyncSession):
        """prev_cursor is None when no cursor was provided (first page)."""
        for i in range(5):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        from fastapi_toolsets.schemas import CursorPagination

        result = await RoleCursorCrud.cursor_paginate(
            db_session, items_per_page=3, schema=RoleRead
        )

        assert isinstance(result.pagination, CursorPagination)
        assert result.pagination.prev_cursor is None

    @pytest.mark.anyio
    async def test_prev_cursor_set_on_subsequent_pages(self, db_session: AsyncSession):
        """prev_cursor is set when a cursor was provided (subsequent pages)."""
        for i in range(10):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        from fastapi_toolsets.schemas import CursorPagination

        page1 = await RoleCursorCrud.cursor_paginate(
            db_session, items_per_page=5, schema=RoleRead
        )
        assert isinstance(page1.pagination, CursorPagination)
        page2 = await RoleCursorCrud.cursor_paginate(
            db_session,
            cursor=page1.pagination.next_cursor,
            items_per_page=5,
            schema=RoleRead,
        )
        assert isinstance(page2.pagination, CursorPagination)
        assert page2.pagination.prev_cursor is not None

    @pytest.mark.anyio
    async def test_prev_cursor_navigates_back(self, db_session: AsyncSession):
        """prev_cursor on page 2 navigates back to the same items as page 1."""
        for i in range(10):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        from fastapi_toolsets.schemas import CursorPagination

        page1 = await RoleCursorCrud.cursor_paginate(
            db_session, items_per_page=5, schema=RoleRead
        )
        assert isinstance(page1.pagination, CursorPagination)
        page2 = await RoleCursorCrud.cursor_paginate(
            db_session,
            cursor=page1.pagination.next_cursor,
            items_per_page=5,
            schema=RoleRead,
        )
        assert isinstance(page2.pagination, CursorPagination)
        assert page2.pagination.prev_cursor is not None

        # Using prev_cursor should return the same items as page 1
        back_to_page1 = await RoleCursorCrud.cursor_paginate(
            db_session,
            cursor=page2.pagination.prev_cursor,
            items_per_page=5,
            schema=RoleRead,
        )
        assert isinstance(back_to_page1.pagination, CursorPagination)
        assert [r.id for r in back_to_page1.data] == [r.id for r in page1.data]
        assert back_to_page1.pagination.prev_cursor is None

    @pytest.mark.anyio
    async def test_prev_cursor_empty_result_when_no_items_before(
        self, db_session: AsyncSession
    ):
        """Going backward past the first item returns an empty page."""
        from fastapi_toolsets.crud.factory import _encode_cursor
        from fastapi_toolsets.schemas import CursorPagination

        await IntRoleCursorCrud.create(db_session, IntRoleCreate(name="role00"))

        page1 = await IntRoleCursorCrud.cursor_paginate(
            db_session, items_per_page=5, schema=IntRoleRead
        )
        assert isinstance(page1.pagination, CursorPagination)

        # Manually craft a backward cursor before any existing id
        before_all = _encode_cursor(0, direction=_CursorDirection.PREV)
        empty = await IntRoleCursorCrud.cursor_paginate(
            db_session, cursor=before_all, items_per_page=5, schema=IntRoleRead
        )

        assert isinstance(empty.pagination, CursorPagination)
        assert empty.data == []
        assert empty.pagination.next_cursor is None
        assert empty.pagination.prev_cursor is None

    @pytest.mark.anyio
    async def test_prev_cursor_set_when_more_pages_behind(
        self, db_session: AsyncSession
    ):
        """Going backward on page 2 (of 3) still exposes a prev_cursor for page 1."""
        for i in range(9):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        from fastapi_toolsets.schemas import CursorPagination

        page1 = await RoleCursorCrud.cursor_paginate(
            db_session, items_per_page=3, schema=RoleRead
        )
        assert isinstance(page1.pagination, CursorPagination)
        page2 = await RoleCursorCrud.cursor_paginate(
            db_session,
            cursor=page1.pagination.next_cursor,
            items_per_page=3,
            schema=RoleRead,
        )
        assert isinstance(page2.pagination, CursorPagination)
        page3 = await RoleCursorCrud.cursor_paginate(
            db_session,
            cursor=page2.pagination.next_cursor,
            items_per_page=3,
            schema=RoleRead,
        )
        assert isinstance(page3.pagination, CursorPagination)
        assert page3.pagination.prev_cursor is not None

        # Going back to page 2 should still have a prev_cursor pointing at page 1
        back_to_page2 = await RoleCursorCrud.cursor_paginate(
            db_session,
            cursor=page3.pagination.prev_cursor,
            items_per_page=3,
            schema=RoleRead,
        )
        assert isinstance(back_to_page2.pagination, CursorPagination)
        assert [r.id for r in back_to_page2.data] == [r.id for r in page2.data]
        assert back_to_page2.pagination.prev_cursor is not None


class TestCursorPaginateWithSearch:
    """Tests for cursor_paginate() combined with search."""

    @pytest.mark.anyio
    async def test_cursor_paginate_with_search(self, db_session: AsyncSession):
        """cursor_paginate respects search filters alongside cursor predicate."""
        from fastapi_toolsets.crud import CrudFactory

        # Create a CRUD with searchable fields and cursor column
        SearchableRoleCrud = CrudFactory(
            Role, searchable_fields=[Role.name], cursor_column=Role.id
        )

        for i in range(5):
            await RoleCrud.create(db_session, RoleCreate(name=f"admin{i:02d}"))
        for i in range(5):
            await RoleCrud.create(db_session, RoleCreate(name=f"user{i:02d}"))

        result = await SearchableRoleCrud.cursor_paginate(
            db_session,
            search="admin",
            items_per_page=20,
            schema=RoleRead,
        )

        assert len(result.data) == 5
        assert all("admin" in r.name for r in result.data)


class TestCursorPaginateExtraOptions:
    """Tests for cursor_paginate() covering joins, load_options, and order_by."""

    @pytest.mark.anyio
    async def test_with_joins(self, db_session: AsyncSession):
        """cursor_paginate applies explicit inner joins."""
        from fastapi_toolsets.schemas import CursorPagination

        role = await RoleCrud.create(db_session, RoleCreate(name="member"))
        for i in range(5):
            await UserCrud.create(
                db_session,
                UserCreate(
                    username=f"u{i}",
                    email=f"u{i}@test.com",
                    role_id=role.id,
                ),
            )
        # One user without a role to confirm inner join excludes them
        await UserCrud.create(
            db_session,
            UserCreate(username="norole", email="norole@test.com"),
        )

        result = await UserCursorCrud.cursor_paginate(
            db_session,
            joins=[(Role, User.role_id == Role.id)],
            items_per_page=20,
            schema=UserRead,
        )

        assert isinstance(result.pagination, CursorPagination)
        # Only users with a role are returned (inner join)
        assert len(result.data) == 5

    @pytest.mark.anyio
    async def test_with_outer_join(self, db_session: AsyncSession):
        """cursor_paginate applies LEFT OUTER JOIN when outer_join=True."""
        from fastapi_toolsets.schemas import CursorPagination

        role = await RoleCrud.create(db_session, RoleCreate(name="member"))
        for i in range(3):
            await UserCrud.create(
                db_session,
                UserCreate(
                    username=f"u{i}",
                    email=f"u{i}@test.com",
                    role_id=role.id,
                ),
            )
        await UserCrud.create(
            db_session,
            UserCreate(username="norole", email="norole@test.com"),
        )

        result = await UserCursorCrud.cursor_paginate(
            db_session,
            joins=[(Role, User.role_id == Role.id)],
            outer_join=True,
            items_per_page=20,
            schema=UserRead,
        )

        assert isinstance(result.pagination, CursorPagination)
        # All users are included (outer join)
        assert len(result.data) == 4

    @pytest.mark.anyio
    async def test_with_load_options(self, db_session: AsyncSession):
        """cursor_paginate passes load_options to the query."""
        from fastapi_toolsets.schemas import CursorPagination, PydanticBase

        class UserWithRoleRead(PydanticBase):
            id: uuid.UUID
            username: str
            role: RoleRead | None = None

        role = await RoleCrud.create(db_session, RoleCreate(name="manager"))
        for i in range(3):
            await UserCrud.create(
                db_session,
                UserCreate(
                    username=f"u{i}",
                    email=f"u{i}@test.com",
                    role_id=role.id,
                ),
            )

        result = await UserCursorCrud.cursor_paginate(
            db_session,
            load_options=[selectinload(User.role)],
            items_per_page=20,
            schema=UserWithRoleRead,
        )

        assert isinstance(result.pagination, CursorPagination)
        assert len(result.data) == 3
        # Relationship was eagerly loaded
        assert all(u.role is not None for u in result.data)

    @pytest.mark.anyio
    async def test_with_order_by(self, db_session: AsyncSession):
        """cursor_paginate applies additional order_by after the cursor column."""
        from fastapi_toolsets.schemas import CursorPagination

        for i in range(5):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        result = await RoleCursorCrud.cursor_paginate(
            db_session,
            order_by=Role.name.desc(),
            items_per_page=3,
            schema=RoleRead,
        )

        assert isinstance(result.pagination, CursorPagination)
        assert len(result.data) == 3

    @pytest.mark.anyio
    async def test_integer_cursor_column(self, db_session: AsyncSession):
        """cursor_paginate decodes Integer cursor values correctly."""
        from fastapi_toolsets.schemas import CursorPagination

        for i in range(5):
            await IntRoleCursorCrud.create(db_session, IntRoleCreate(name=f"role{i}"))

        page1 = await IntRoleCursorCrud.cursor_paginate(
            db_session, items_per_page=3, schema=IntRoleRead
        )

        assert isinstance(page1.pagination, CursorPagination)
        assert len(page1.data) == 3
        assert page1.pagination.has_more is True

        page2 = await IntRoleCursorCrud.cursor_paginate(
            db_session,
            cursor=page1.pagination.next_cursor,
            items_per_page=3,
            schema=IntRoleRead,
        )

        assert isinstance(page2.pagination, CursorPagination)
        assert len(page2.data) == 2
        assert page2.pagination.has_more is False

    @pytest.mark.anyio
    async def test_unsupported_cursor_column_type_raises(
        self, db_session: AsyncSession
    ):
        """cursor_paginate raises ValueError when cursor column type is not supported."""
        from fastapi_toolsets.crud import CrudFactory
        from fastapi_toolsets.schemas import CursorPagination

        RoleNameCursorCrud = CrudFactory(Role, cursor_column=Role.name)

        await RoleCrud.create(db_session, RoleCreate(name="role00"))
        await RoleCrud.create(db_session, RoleCreate(name="role01"))

        # First page succeeds (no cursor to decode)
        page1 = await RoleNameCursorCrud.cursor_paginate(
            db_session, items_per_page=1, schema=RoleRead
        )
        assert page1.pagination.has_more is True
        assert isinstance(page1.pagination, CursorPagination)

        # Second page fails because String type is unsupported
        with pytest.raises(ValueError, match="Unsupported cursor column type"):
            await RoleNameCursorCrud.cursor_paginate(
                db_session,
                cursor=page1.pagination.next_cursor,
                items_per_page=1,
                schema=RoleRead,
            )


class TestCursorPaginateSearchJoins:
    """Tests for cursor_paginate() search that traverses relationships (search_joins)."""

    @pytest.mark.anyio
    async def test_search_via_relationship(self, db_session: AsyncSession):
        """cursor_paginate outerjoin search-join when searching through a relationship."""
        from fastapi_toolsets.schemas import CursorPagination

        role_admin = await RoleCrud.create(db_session, RoleCreate(name="administrator"))
        role_user = await RoleCrud.create(db_session, RoleCreate(name="regularuser"))

        for i in range(3):
            await UserCrud.create(
                db_session,
                UserCreate(
                    username=f"admin_u{i}",
                    email=f"admin_u{i}@test.com",
                    role_id=role_admin.id,
                ),
            )
        for i in range(2):
            await UserCrud.create(
                db_session,
                UserCreate(
                    username=f"reg_u{i}",
                    email=f"reg_u{i}@test.com",
                    role_id=role_user.id,
                ),
            )

        result = await UserCursorCrud.cursor_paginate(
            db_session,
            search="administrator",
            search_fields=[(User.role, Role.name)],
            items_per_page=20,
            schema=UserRead,
        )

        assert isinstance(result.pagination, CursorPagination)
        assert len(result.data) == 3


class TestGetWithForUpdate:
    """Tests for get() with with_for_update=True."""

    @pytest.mark.anyio
    async def test_get_with_for_update(self, db_session: AsyncSession):
        """get() with with_for_update=True locks the row."""
        role = await RoleCrud.create(db_session, RoleCreate(name="locked"))

        result = await RoleCrud.get(
            db_session,
            filters=[Role.id == role.id],
            with_for_update=True,
        )

        assert result.id == role.id
        assert result.name == "locked"


class TestCursorPaginateColumnTypes:
    """Tests for cursor_paginate() covering DateTime, Date and Numeric column types."""

    @pytest.mark.anyio
    async def test_datetime_cursor_column(self, db_session: AsyncSession):
        """cursor_paginate decodes DateTime cursor values to datetime objects."""
        import datetime

        from fastapi_toolsets.schemas import CursorPagination

        base = datetime.datetime(2024, 1, 1, 0, 0, 0)
        for i in range(5):
            await EventCrud.create(
                db_session,
                EventCreate(
                    name=f"event{i}",
                    occurred_at=base + datetime.timedelta(hours=i),
                    scheduled_date=datetime.date(2024, 1, i + 1),
                ),
            )

        page1 = await EventDateTimeCursorCrud.cursor_paginate(
            db_session, items_per_page=3, schema=EventRead
        )

        assert isinstance(page1.pagination, CursorPagination)
        assert len(page1.data) == 3
        assert page1.pagination.has_more is True

        page2 = await EventDateTimeCursorCrud.cursor_paginate(
            db_session,
            cursor=page1.pagination.next_cursor,
            items_per_page=3,
            schema=EventRead,
        )

        assert isinstance(page2.pagination, CursorPagination)
        assert len(page2.data) == 2
        assert page2.pagination.has_more is False
        # No overlap between pages
        page1_ids = {e.id for e in page1.data}
        page2_ids = {e.id for e in page2.data}
        assert page1_ids.isdisjoint(page2_ids)

    @pytest.mark.anyio
    async def test_date_cursor_column(self, db_session: AsyncSession):
        """cursor_paginate decodes Date cursor values to date objects."""
        import datetime

        from fastapi_toolsets.schemas import CursorPagination

        for i in range(5):
            await EventCrud.create(
                db_session,
                EventCreate(
                    name=f"event{i}",
                    occurred_at=datetime.datetime(2024, 1, 1),
                    scheduled_date=datetime.date(2024, 1, i + 1),
                ),
            )

        page1 = await EventDateCursorCrud.cursor_paginate(
            db_session, items_per_page=3, schema=EventRead
        )

        assert isinstance(page1.pagination, CursorPagination)
        assert len(page1.data) == 3
        assert page1.pagination.has_more is True

        page2 = await EventDateCursorCrud.cursor_paginate(
            db_session,
            cursor=page1.pagination.next_cursor,
            items_per_page=3,
            schema=EventRead,
        )

        assert isinstance(page2.pagination, CursorPagination)
        assert len(page2.data) == 2
        assert page2.pagination.has_more is False
        page1_ids = {e.id for e in page1.data}
        page2_ids = {e.id for e in page2.data}
        assert page1_ids.isdisjoint(page2_ids)

    @pytest.mark.anyio
    async def test_numeric_cursor_column(self, db_session: AsyncSession):
        """cursor_paginate decodes Numeric cursor values to Decimal objects."""
        import decimal

        from fastapi_toolsets.schemas import CursorPagination

        for i in range(5):
            await ProductCrud.create(
                db_session,
                ProductCreate(
                    name=f"product{i}",
                    price=decimal.Decimal(f"{i + 1}.99"),
                ),
            )

        page1 = await ProductNumericCursorCrud.cursor_paginate(
            db_session, items_per_page=3, schema=ProductRead
        )

        assert isinstance(page1.pagination, CursorPagination)
        assert len(page1.data) == 3
        assert page1.pagination.has_more is True

        page2 = await ProductNumericCursorCrud.cursor_paginate(
            db_session,
            cursor=page1.pagination.next_cursor,
            items_per_page=3,
            schema=ProductRead,
        )

        assert isinstance(page2.pagination, CursorPagination)
        assert len(page2.data) == 2
        assert page2.pagination.has_more is False
        page1_ids = {p.id for p in page1.data}
        page2_ids = {p.id for p in page2.data}
        assert page1_ids.isdisjoint(page2_ids)


class TestPaginate:
    """Tests for the unified paginate() method."""

    @pytest.mark.anyio
    async def test_offset_pagination(self, db_session: AsyncSession):
        """paginate() with OFFSET returns OffsetPaginatedResponse."""
        from fastapi_toolsets.schemas import OffsetPagination

        await RoleCrud.create(db_session, RoleCreate(name="admin"))
        await RoleCrud.create(db_session, RoleCreate(name="user"))

        result = await RoleCrud.paginate(
            db_session,
            pagination_type=PaginationType.OFFSET,
            schema=RoleRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination_type == PaginationType.OFFSET

    @pytest.mark.anyio
    async def test_cursor_pagination(self, db_session: AsyncSession):
        """paginate() with CURSOR returns CursorPaginatedResponse."""
        from fastapi_toolsets.schemas import CursorPagination

        await RoleCursorCrud.create(db_session, RoleCreate(name="admin"))

        result = await RoleCursorCrud.paginate(
            db_session,
            pagination_type=PaginationType.CURSOR,
            schema=RoleRead,
        )

        assert isinstance(result.pagination, CursorPagination)
        assert result.pagination_type == PaginationType.CURSOR

    @pytest.mark.anyio
    async def test_invalid_items_per_page_raises(self, db_session: AsyncSession):
        """paginate() raises ValueError when items_per_page < 1."""
        with pytest.raises(ValueError, match="items_per_page"):
            await RoleCrud.paginate(
                db_session,
                pagination_type=PaginationType.OFFSET,
                items_per_page=0,
                schema=RoleRead,
            )

    @pytest.mark.anyio
    async def test_invalid_page_raises(self, db_session: AsyncSession):
        """paginate() raises ValueError when page < 1 for offset pagination."""
        with pytest.raises(ValueError, match="page"):
            await RoleCrud.paginate(
                db_session,
                pagination_type=PaginationType.OFFSET,
                page=0,
                schema=RoleRead,
            )

    @pytest.mark.anyio
    async def test_unknown_pagination_type_raises(self, db_session: AsyncSession):
        """paginate() raises ValueError for unknown pagination_type."""
        with pytest.raises(ValueError, match="Unknown pagination_type"):
            await RoleCrud.paginate(
                db_session,
                pagination_type="unknown",
                schema=RoleRead,
            )  # type: ignore[no-matching-overload]  # ty:ignore[no-matching-overload]

    @pytest.mark.anyio
    async def test_offset_include_total_false(self, db_session: AsyncSession):
        """paginate() passes include_total=False through to offset_paginate."""
        from fastapi_toolsets.schemas import OffsetPagination

        await RoleCrud.create(db_session, RoleCreate(name="admin"))

        result = await RoleCrud.paginate(
            db_session,
            pagination_type=PaginationType.OFFSET,
            include_total=False,
            schema=RoleRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count is None
