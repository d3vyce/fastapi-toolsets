"""Tests for fastapi_toolsets.pytest module."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi_toolsets.fixtures import Context, FixtureRegistry
from fastapi_toolsets.pytest import (
    create_async_client,
    create_db_session,
    register_fixtures,
)

from .conftest import DATABASE_URL, Base, Role, RoleCrud, User, UserCrud

test_registry = FixtureRegistry()

# Fixed UUIDs for test fixtures to allow consistent assertions
ROLE_ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-000000001000")
ROLE_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000001001")
USER_ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-000000002000")
USER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000002001")
USER_EXTRA_ID = uuid.UUID("00000000-0000-0000-0000-000000002002")


@test_registry.register(contexts=[Context.BASE])
def roles() -> list[Role]:
    return [
        Role(id=ROLE_ADMIN_ID, name="plugin_admin"),
        Role(id=ROLE_USER_ID, name="plugin_user"),
    ]


@test_registry.register(depends_on=["roles"], contexts=[Context.BASE])
def users() -> list[User]:
    return [
        User(
            id=USER_ADMIN_ID,
            username="plugin_admin",
            email="padmin@test.com",
            role_id=ROLE_ADMIN_ID,
        ),
        User(
            id=USER_USER_ID,
            username="plugin_user",
            email="puser@test.com",
            role_id=ROLE_USER_ID,
        ),
    ]


@test_registry.register(depends_on=["users"], contexts=[Context.TESTING])
def extra_users() -> list[User]:
    return [
        User(
            id=USER_EXTRA_ID,
            username="plugin_extra",
            email="pextra@test.com",
            role_id=ROLE_USER_ID,
        ),
    ]


register_fixtures(test_registry, globals())


class TestRegisterFixtures:
    """Tests for register_fixtures function."""

    def test_creates_fixtures_in_namespace(self):
        """Fixtures are created in the namespace."""
        assert "fixture_roles" in globals()
        assert "fixture_users" in globals()
        assert "fixture_extra_users" in globals()

    def test_fixtures_are_callable(self):
        """Created fixtures are callable."""
        assert callable(globals()["fixture_roles"])
        assert callable(globals()["fixture_users"])


class TestGeneratedFixtures:
    """Tests for the generated pytest fixtures."""

    @pytest.mark.anyio
    async def test_fixture_loads_data(
        self, db_session: AsyncSession, fixture_roles: list[Role]
    ):
        """Fixture loads data into database and returns it."""
        assert len(fixture_roles) == 2
        assert fixture_roles[0].name == "plugin_admin"
        assert fixture_roles[1].name == "plugin_user"

        # Verify data is in database
        count = await RoleCrud.count(db_session)
        assert count == 2

    @pytest.mark.anyio
    async def test_fixture_with_dependency(
        self, db_session: AsyncSession, fixture_users: list[User]
    ):
        """Fixture with dependency loads parent fixture first."""
        # fixture_users depends on fixture_roles
        # Both should be loaded
        assert len(fixture_users) == 2

        # Roles should also be in database
        roles_count = await RoleCrud.count(db_session)
        assert roles_count == 2

        # Users should be in database
        users_count = await UserCrud.count(db_session)
        assert users_count == 2

    @pytest.mark.anyio
    async def test_fixture_returns_models(
        self, db_session: AsyncSession, fixture_users: list[User]
    ):
        """Fixture returns actual model instances."""
        user = fixture_users[0]
        assert isinstance(user, User)
        assert user.id == USER_ADMIN_ID
        assert user.username == "plugin_admin"

    @pytest.mark.anyio
    async def test_fixture_relationships_work(
        self, db_session: AsyncSession, fixture_users: list[User]
    ):
        """Loaded fixtures have working relationships."""
        # Load user with role relationship
        user = await UserCrud.get(
            db_session,
            [User.id == USER_ADMIN_ID],
            load_options=[selectinload(User.role)],
        )

        assert user.role is not None
        assert user.role.name == "plugin_admin"

    @pytest.mark.anyio
    async def test_chained_dependencies(
        self, db_session: AsyncSession, fixture_extra_users: list[User]
    ):
        """Chained dependencies are resolved correctly."""
        # fixture_extra_users -> fixture_users -> fixture_roles
        assert len(fixture_extra_users) == 1

        # All fixtures should be loaded
        roles_count = await RoleCrud.count(db_session)
        users_count = await UserCrud.count(db_session)

        assert roles_count == 2
        assert users_count == 3  # 2 from users + 1 from extra_users

    @pytest.mark.anyio
    async def test_can_query_loaded_data(
        self, db_session: AsyncSession, fixture_users: list[User]
    ):
        """Can query the loaded fixture data."""
        # Get all users loaded by fixture
        users = await UserCrud.get_multi(
            db_session,
            order_by=User.username,
        )

        assert len(users) == 2
        assert users[0].username == "plugin_admin"
        assert users[1].username == "plugin_user"

    @pytest.mark.anyio
    async def test_multiple_fixtures_in_same_test(
        self,
        db_session: AsyncSession,
        fixture_roles: list[Role],
        fixture_users: list[User],
    ):
        """Multiple fixtures can be used in the same test."""
        assert len(fixture_roles) == 2
        assert len(fixture_users) == 2

        # Both should be in database
        roles = await RoleCrud.get_multi(db_session)
        users = await UserCrud.get_multi(db_session)

        assert len(roles) == 2
        assert len(users) == 2


class TestCreateAsyncClient:
    """Tests for create_async_client helper."""

    @pytest.mark.anyio
    async def test_creates_working_client(self):
        """Client can make requests to the app."""
        app = FastAPI()

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        async with create_async_client(app) as client:
            assert isinstance(client, AsyncClient)
            response = await client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

    @pytest.mark.anyio
    async def test_custom_base_url(self):
        """Client uses custom base URL."""
        app = FastAPI()

        @app.get("/test")
        async def test_endpoint():
            return {"url": "test"}

        async with create_async_client(app, base_url="http://custom") as client:
            assert str(client.base_url) == "http://custom"

    @pytest.mark.anyio
    async def test_client_closes_properly(self):
        """Client is properly closed after context exit."""
        app = FastAPI()

        async with create_async_client(app) as client:
            client_ref = client

        assert client_ref.is_closed


class TestCreateDbSession:
    """Tests for create_db_session helper."""

    @pytest.mark.anyio
    async def test_creates_working_session(self):
        """Session can perform database operations."""
        role_id = uuid.uuid4()
        async with create_db_session(DATABASE_URL, Base) as session:
            assert isinstance(session, AsyncSession)

            role = Role(id=role_id, name="test_helper_role")
            session.add(role)
            await session.commit()

            result = await session.execute(select(Role).where(Role.id == role_id))
            fetched = result.scalar_one()
            assert fetched.name == "test_helper_role"

    @pytest.mark.anyio
    async def test_tables_created_before_session(self):
        """Tables exist when session is yielded."""
        async with create_db_session(DATABASE_URL, Base) as session:
            # Should not raise - tables exist
            result = await session.execute(select(Role))
            assert result.all() == []

    @pytest.mark.anyio
    async def test_tables_dropped_after_session(self):
        """Tables are dropped after session closes when drop_tables=True."""
        role_id = uuid.uuid4()
        async with create_db_session(DATABASE_URL, Base, drop_tables=True) as session:
            role = Role(id=role_id, name="will_be_dropped")
            session.add(role)
            await session.commit()

        # Verify tables were dropped by creating new session
        async with create_db_session(DATABASE_URL, Base) as session:
            result = await session.execute(select(Role))
            assert result.all() == []

    @pytest.mark.anyio
    async def test_tables_preserved_when_drop_disabled(self):
        """Tables are preserved when drop_tables=False."""
        role_id = uuid.uuid4()
        async with create_db_session(DATABASE_URL, Base, drop_tables=False) as session:
            role = Role(id=role_id, name="preserved_role")
            session.add(role)
            await session.commit()

        # Create another session without dropping
        async with create_db_session(DATABASE_URL, Base, drop_tables=False) as session:
            result = await session.execute(select(Role).where(Role.id == role_id))
            fetched = result.scalar_one_or_none()
            assert fetched is not None
            assert fetched.name == "preserved_role"

        # Cleanup: drop tables manually
        async with create_db_session(DATABASE_URL, Base, drop_tables=True) as _:
            pass
