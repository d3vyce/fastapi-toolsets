"""Tests for fastapi_toolsets.pytest_plugin module."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi_toolsets.fixtures import Context, FixtureRegistry, register_fixtures

from .conftest import Role, RoleCrud, User, UserCrud

test_registry = FixtureRegistry()


@test_registry.register(contexts=[Context.BASE])
def roles() -> list[Role]:
    return [
        Role(id=1000, name="plugin_admin"),
        Role(id=1001, name="plugin_user"),
    ]


@test_registry.register(depends_on=["roles"], contexts=[Context.BASE])
def users() -> list[User]:
    return [
        User(id=1000, username="plugin_admin", email="padmin@test.com", role_id=1000),
        User(id=1001, username="plugin_user", email="puser@test.com", role_id=1001),
    ]


@test_registry.register(depends_on=["users"], contexts=[Context.TESTING])
def extra_users() -> list[User]:
    return [
        User(id=1002, username="plugin_extra", email="pextra@test.com", role_id=1001),
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
        count = await RoleCrud.count(db_session, [Role.id >= 1000])
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
        roles_count = await RoleCrud.count(db_session, [Role.id >= 1000])
        assert roles_count == 2

        # Users should be in database
        users_count = await UserCrud.count(db_session, [User.id >= 1000])
        assert users_count == 2

    @pytest.mark.anyio
    async def test_fixture_returns_models(
        self, db_session: AsyncSession, fixture_users: list[User]
    ):
        """Fixture returns actual model instances."""
        user = fixture_users[0]
        assert isinstance(user, User)
        assert user.id == 1000
        assert user.username == "plugin_admin"

    @pytest.mark.anyio
    async def test_fixture_relationships_work(
        self, db_session: AsyncSession, fixture_users: list[User]
    ):
        """Loaded fixtures have working relationships."""
        # Load user with role relationship
        user = await UserCrud.get(
            db_session,
            [User.id == 1000],
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
        roles_count = await RoleCrud.count(db_session, [Role.id >= 1000])
        users_count = await UserCrud.count(db_session, [User.id >= 1000])

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
            filters=[User.id >= 1000],
            order_by=User.id,
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
        roles = await RoleCrud.get_multi(db_session, filters=[Role.id >= 1000])
        users = await UserCrud.get_multi(db_session, filters=[User.id >= 1000])

        assert len(roles) == 2
        assert len(users) == 2
