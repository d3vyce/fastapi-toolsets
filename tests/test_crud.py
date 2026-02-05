"""Tests for fastapi_toolsets.crud module."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_toolsets.crud import CrudFactory
from fastapi_toolsets.crud.factory import AsyncCrud
from fastapi_toolsets.exceptions import NotFoundError

from .conftest import (
    Post,
    PostCreate,
    PostCrud,
    Role,
    RoleCreate,
    RoleCrud,
    RoleUpdate,
    User,
    UserCreate,
    UserCrud,
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

        assert result is True
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

        result = await RoleCrud.paginate(db_session, page=1, items_per_page=10)

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

        result = await RoleCrud.paginate(db_session, page=3, items_per_page=10)

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

        result = await UserCrud.paginate(
            db_session,
            filters=[User.is_active == True],  # noqa: E712
            page=1,
            items_per_page=10,
        )

        assert result.pagination.total_count == 5

    @pytest.mark.anyio
    async def test_paginate_with_ordering(self, db_session: AsyncSession):
        """Paginate with custom ordering."""
        await RoleCrud.create(db_session, RoleCreate(name="charlie"))
        await RoleCrud.create(db_session, RoleCreate(name="alpha"))
        await RoleCrud.create(db_session, RoleCreate(name="bravo"))

        result = await RoleCrud.paginate(
            db_session,
            order_by=Role.name,
            page=1,
            items_per_page=10,
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

        # Paginate users with published posts
        result = await UserCrud.paginate(
            db_session,
            joins=[(Post, Post.author_id == User.id)],
            filters=[Post.is_published == True],  # noqa: E712
            page=1,
            items_per_page=10,
        )

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

        # Paginate with outer join
        result = await UserCrud.paginate(
            db_session,
            joins=[(Post, Post.author_id == User.id)],
            outer_join=True,
            page=1,
            items_per_page=10,
        )

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


class TestAsResponse:
    """Tests for as_response parameter."""

    @pytest.mark.anyio
    async def test_create_as_response(self, db_session: AsyncSession):
        """Create with as_response=True returns Response."""
        from fastapi_toolsets.schemas import Response

        data = RoleCreate(name="response_role")
        result = await RoleCrud.create(db_session, data, as_response=True)

        assert isinstance(result, Response)
        assert result.data is not None
        assert result.data.name == "response_role"

    @pytest.mark.anyio
    async def test_get_as_response(self, db_session: AsyncSession):
        """Get with as_response=True returns Response."""
        from fastapi_toolsets.schemas import Response

        created = await RoleCrud.create(db_session, RoleCreate(name="get_response"))
        result = await RoleCrud.get(
            db_session, [Role.id == created.id], as_response=True
        )

        assert isinstance(result, Response)
        assert result.data is not None
        assert result.data.id == created.id

    @pytest.mark.anyio
    async def test_update_as_response(self, db_session: AsyncSession):
        """Update with as_response=True returns Response."""
        from fastapi_toolsets.schemas import Response

        created = await RoleCrud.create(db_session, RoleCreate(name="old_name"))
        result = await RoleCrud.update(
            db_session,
            RoleUpdate(name="new_name"),
            [Role.id == created.id],
            as_response=True,
        )

        assert isinstance(result, Response)
        assert result.data is not None
        assert result.data.name == "new_name"

    @pytest.mark.anyio
    async def test_delete_as_response(self, db_session: AsyncSession):
        """Delete with as_response=True returns Response."""
        from fastapi_toolsets.schemas import Response

        created = await RoleCrud.create(db_session, RoleCreate(name="to_delete"))
        result = await RoleCrud.delete(
            db_session, [Role.id == created.id], as_response=True
        )

        assert isinstance(result, Response)
        assert result.data is None
