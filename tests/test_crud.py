"""Tests for fastapi_toolsets.crud module."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_toolsets.crud import CrudFactory
from fastapi_toolsets.crud.factory import AsyncCrud
from fastapi_toolsets.exceptions import NotFoundError

from .conftest import (
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
        with pytest.raises(NotFoundError):
            await RoleCrud.get(db_session, [Role.id == 99999])

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
        with pytest.raises(NotFoundError):
            await RoleCrud.update(
                db_session,
                RoleUpdate(name="new"),
                [Role.id == 99999],
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
        data = RoleCreate(id=1, name="upsert_new")
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
        # First insert
        data = RoleCreate(id=100, name="original_name")
        await RoleCrud.upsert(db_session, data, index_elements=["id"])

        # Upsert with update
        updated_data = RoleCreate(id=100, name="updated_name")
        role = await RoleCrud.upsert(
            db_session,
            updated_data,
            index_elements=["id"],
            set_=RoleUpdate(name="updated_name"),
        )

        assert role is not None
        assert role.name == "updated_name"

        # Verify only one record exists
        count = await RoleCrud.count(db_session, [Role.id == 100])
        assert count == 1

    @pytest.mark.anyio
    async def test_upsert_do_nothing_on_conflict(self, db_session: AsyncSession):
        """Upsert does nothing on conflict when set_ is not provided."""
        # First insert
        data = RoleCreate(id=200, name="do_nothing_original")
        await RoleCrud.upsert(db_session, data, index_elements=["id"])

        # Upsert without set_ (do nothing)
        conflict_data = RoleCreate(id=200, name="do_nothing_conflict")
        await RoleCrud.upsert(db_session, conflict_data, index_elements=["id"])

        # Original value should be preserved
        role = await RoleCrud.first(db_session, [Role.id == 200])
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

        assert len(result["data"]) == 10
        assert result["pagination"]["total_count"] == 25
        assert result["pagination"]["page"] == 1
        assert result["pagination"]["items_per_page"] == 10
        assert result["pagination"]["has_more"] is True

    @pytest.mark.anyio
    async def test_paginate_last_page(self, db_session: AsyncSession):
        """Paginate returns last page with has_more=False."""
        for i in range(25):
            await RoleCrud.create(db_session, RoleCreate(name=f"role{i:02d}"))

        result = await RoleCrud.paginate(db_session, page=3, items_per_page=10)

        assert len(result["data"]) == 5
        assert result["pagination"]["has_more"] is False

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

        assert result["pagination"]["total_count"] == 5

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

        names = [r.name for r in result["data"]]
        assert names == ["alpha", "bravo", "charlie"]
