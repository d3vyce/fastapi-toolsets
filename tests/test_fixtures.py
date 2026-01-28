"""Tests for fastapi_toolsets.fixtures module."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_toolsets.fixtures import (
    Context,
    FixtureRegistry,
    LoadStrategy,
    get_obj_by_attr,
    load_fixtures,
    load_fixtures_by_context,
)

from .conftest import Role, User


class TestContext:
    """Tests for Context enum."""

    def test_base_context(self):
        """BASE context has correct value."""
        assert Context.BASE.value == "base"

    def test_production_context(self):
        """PRODUCTION context has correct value."""
        assert Context.PRODUCTION.value == "production"

    def test_development_context(self):
        """DEVELOPMENT context has correct value."""
        assert Context.DEVELOPMENT.value == "development"

    def test_testing_context(self):
        """TESTING context has correct value."""
        assert Context.TESTING.value == "testing"


class TestLoadStrategy:
    """Tests for LoadStrategy enum."""

    def test_insert_strategy(self):
        """INSERT strategy has correct value."""
        assert LoadStrategy.INSERT.value == "insert"

    def test_merge_strategy(self):
        """MERGE strategy has correct value."""
        assert LoadStrategy.MERGE.value == "merge"

    def test_skip_existing_strategy(self):
        """SKIP_EXISTING strategy has correct value."""
        assert LoadStrategy.SKIP_EXISTING.value == "skip_existing"


class TestFixtureRegistry:
    """Tests for FixtureRegistry class."""

    def test_register_with_decorator(self):
        """Register fixture with decorator."""
        registry = FixtureRegistry()

        @registry.register
        def roles():
            return [Role(id=1, name="admin")]

        assert "roles" in [f.name for f in registry.get_all()]

    def test_register_with_custom_name(self):
        """Register fixture with custom name."""
        registry = FixtureRegistry()

        @registry.register(name="custom_roles")
        def roles():
            return [Role(id=1, name="admin")]

        fixture = registry.get("custom_roles")
        assert fixture.name == "custom_roles"

    def test_register_with_dependencies(self):
        """Register fixture with dependencies."""
        registry = FixtureRegistry()

        @registry.register
        def roles():
            return [Role(id=1, name="admin")]

        @registry.register(depends_on=["roles"])
        def users():
            return [User(id=1, username="admin", email="admin@test.com", role_id=1)]

        fixture = registry.get("users")
        assert fixture.depends_on == ["roles"]

    def test_register_with_contexts(self):
        """Register fixture with contexts."""
        registry = FixtureRegistry()

        @registry.register(contexts=[Context.TESTING])
        def test_data():
            return [Role(id=100, name="test")]

        fixture = registry.get("test_data")
        assert Context.TESTING.value in fixture.contexts

    def test_get_raises_key_error(self):
        """Get raises KeyError for missing fixture."""
        registry = FixtureRegistry()

        with pytest.raises(KeyError, match="not found"):
            registry.get("nonexistent")

    def test_get_all(self):
        """Get all registered fixtures."""
        registry = FixtureRegistry()

        @registry.register
        def fixture1():
            return []

        @registry.register
        def fixture2():
            return []

        fixtures = registry.get_all()
        names = {f.name for f in fixtures}
        assert names == {"fixture1", "fixture2"}

    def test_get_by_context(self):
        """Get fixtures by context."""
        registry = FixtureRegistry()

        @registry.register(contexts=[Context.BASE])
        def base_data():
            return []

        @registry.register(contexts=[Context.TESTING])
        def test_data():
            return []

        @registry.register(contexts=[Context.PRODUCTION])
        def prod_data():
            return []

        testing_fixtures = registry.get_by_context(Context.TESTING)
        names = {f.name for f in testing_fixtures}
        assert names == {"test_data"}


class TestDependencyResolution:
    """Tests for fixture dependency resolution."""

    def test_resolve_simple_dependency(self):
        """Resolve simple dependency chain."""
        registry = FixtureRegistry()

        @registry.register
        def roles():
            return []

        @registry.register(depends_on=["roles"])
        def users():
            return []

        order = registry.resolve_dependencies("users")
        assert order == ["roles", "users"]

    def test_resolve_multiple_dependencies(self):
        """Resolve multiple dependencies."""
        registry = FixtureRegistry()

        @registry.register
        def roles():
            return []

        @registry.register
        def permissions():
            return []

        @registry.register(depends_on=["roles", "permissions"])
        def users():
            return []

        order = registry.resolve_dependencies("users")
        assert "roles" in order
        assert "permissions" in order
        assert order.index("roles") < order.index("users")
        assert order.index("permissions") < order.index("users")

    def test_resolve_transitive_dependencies(self):
        """Resolve transitive dependencies."""
        registry = FixtureRegistry()

        @registry.register
        def base():
            return []

        @registry.register(depends_on=["base"])
        def middle():
            return []

        @registry.register(depends_on=["middle"])
        def top():
            return []

        order = registry.resolve_dependencies("top")
        assert order == ["base", "middle", "top"]

    def test_detect_circular_dependency(self):
        """Detect circular dependencies."""
        registry = FixtureRegistry()

        @registry.register(depends_on=["b"])
        def a():
            return []

        @registry.register(depends_on=["a"])
        def b():
            return []

        with pytest.raises(ValueError, match="Circular dependency"):
            registry.resolve_dependencies("a")

    def test_resolve_context_dependencies(self):
        """Resolve all fixtures for a context with dependencies."""
        registry = FixtureRegistry()

        @registry.register(contexts=[Context.BASE])
        def roles():
            return []

        @registry.register(depends_on=["roles"], contexts=[Context.TESTING])
        def test_users():
            return []

        order = registry.resolve_context_dependencies(Context.BASE, Context.TESTING)
        assert "roles" in order
        assert "test_users" in order
        assert order.index("roles") < order.index("test_users")


class TestLoadFixtures:
    """Tests for load_fixtures function."""

    @pytest.mark.anyio
    async def test_load_single_fixture(self, db_session: AsyncSession):
        """Load a single fixture."""
        registry = FixtureRegistry()

        @registry.register
        def roles():
            return [
                Role(id=1, name="admin"),
                Role(id=2, name="user"),
            ]

        result = await load_fixtures(db_session, registry, "roles")

        assert "roles" in result
        assert len(result["roles"]) == 2

        from .conftest import RoleCrud

        count = await RoleCrud.count(db_session)
        assert count == 2

    @pytest.mark.anyio
    async def test_load_with_dependencies(self, db_session: AsyncSession):
        """Load fixtures with dependencies."""
        registry = FixtureRegistry()

        @registry.register
        def roles():
            return [Role(id=1, name="admin")]

        @registry.register(depends_on=["roles"])
        def users():
            return [User(id=1, username="admin", email="admin@test.com", role_id=1)]

        result = await load_fixtures(db_session, registry, "users")

        assert "roles" in result
        assert "users" in result

        from .conftest import RoleCrud, UserCrud

        assert await RoleCrud.count(db_session) == 1
        assert await UserCrud.count(db_session) == 1

    @pytest.mark.anyio
    async def test_load_with_merge_strategy(self, db_session: AsyncSession):
        """Load fixtures with MERGE strategy updates existing."""
        registry = FixtureRegistry()

        @registry.register
        def roles():
            return [Role(id=1, name="admin")]

        await load_fixtures(db_session, registry, "roles", strategy=LoadStrategy.MERGE)
        await load_fixtures(db_session, registry, "roles", strategy=LoadStrategy.MERGE)

        from .conftest import RoleCrud

        count = await RoleCrud.count(db_session)
        assert count == 1

    @pytest.mark.anyio
    async def test_load_with_skip_existing_strategy(self, db_session: AsyncSession):
        """Load fixtures with SKIP_EXISTING strategy."""
        registry = FixtureRegistry()

        @registry.register
        def roles():
            return [Role(id=1, name="original")]

        await load_fixtures(
            db_session, registry, "roles", strategy=LoadStrategy.SKIP_EXISTING
        )

        @registry.register(name="roles_updated")
        def roles_v2():
            return [Role(id=1, name="updated")]

        registry._fixtures["roles"] = registry._fixtures.pop("roles_updated")

        await load_fixtures(
            db_session, registry, "roles", strategy=LoadStrategy.SKIP_EXISTING
        )

        from .conftest import RoleCrud

        role = await RoleCrud.first(db_session, [Role.id == 1])
        assert role is not None
        assert role.name == "original"

    @pytest.mark.anyio
    async def test_load_with_insert_strategy(self, db_session: AsyncSession):
        """Load fixtures with INSERT strategy."""
        registry = FixtureRegistry()

        @registry.register
        def roles():
            return [
                Role(id=1, name="admin"),
                Role(id=2, name="user"),
            ]

        result = await load_fixtures(
            db_session, registry, "roles", strategy=LoadStrategy.INSERT
        )

        assert "roles" in result
        assert len(result["roles"]) == 2

        from .conftest import RoleCrud

        count = await RoleCrud.count(db_session)
        assert count == 2

    @pytest.mark.anyio
    async def test_load_empty_fixture(self, db_session: AsyncSession):
        """Load a fixture that returns an empty list."""
        registry = FixtureRegistry()

        @registry.register
        def empty_roles():
            return []

        result = await load_fixtures(db_session, registry, "empty_roles")

        assert "empty_roles" in result
        assert result["empty_roles"] == []

    @pytest.mark.anyio
    async def test_load_multiple_fixtures_without_dependencies(
        self, db_session: AsyncSession
    ):
        """Load multiple independent fixtures."""
        registry = FixtureRegistry()

        @registry.register
        def roles():
            return [Role(id=1, name="admin")]

        @registry.register
        def other_roles():
            return [Role(id=2, name="user")]

        result = await load_fixtures(db_session, registry, "roles", "other_roles")

        assert "roles" in result
        assert "other_roles" in result

        from .conftest import RoleCrud

        count = await RoleCrud.count(db_session)
        assert count == 2


class TestLoadFixturesByContext:
    """Tests for load_fixtures_by_context function."""

    @pytest.mark.anyio
    async def test_load_by_single_context(self, db_session: AsyncSession):
        """Load fixtures by single context."""
        registry = FixtureRegistry()

        @registry.register(contexts=[Context.BASE])
        def base_roles():
            return [Role(id=1, name="base_role")]

        @registry.register(contexts=[Context.TESTING])
        def test_roles():
            return [Role(id=100, name="test_role")]

        await load_fixtures_by_context(db_session, registry, Context.BASE)

        from .conftest import RoleCrud

        count = await RoleCrud.count(db_session)
        assert count == 1

        role = await RoleCrud.first(db_session, [Role.id == 1])
        assert role is not None
        assert role.name == "base_role"

    @pytest.mark.anyio
    async def test_load_by_multiple_contexts(self, db_session: AsyncSession):
        """Load fixtures by multiple contexts."""
        registry = FixtureRegistry()

        @registry.register(contexts=[Context.BASE])
        def base_roles():
            return [Role(id=1, name="base_role")]

        @registry.register(contexts=[Context.TESTING])
        def test_roles():
            return [Role(id=100, name="test_role")]

        await load_fixtures_by_context(
            db_session, registry, Context.BASE, Context.TESTING
        )

        from .conftest import RoleCrud

        count = await RoleCrud.count(db_session)
        assert count == 2

    @pytest.mark.anyio
    async def test_load_context_with_dependencies(self, db_session: AsyncSession):
        """Load context fixtures with cross-context dependencies."""
        registry = FixtureRegistry()

        @registry.register(contexts=[Context.BASE])
        def roles():
            return [Role(id=1, name="admin")]

        @registry.register(depends_on=["roles"], contexts=[Context.TESTING])
        def test_users():
            return [User(id=1, username="tester", email="test@test.com", role_id=1)]

        await load_fixtures_by_context(db_session, registry, Context.TESTING)

        from .conftest import RoleCrud, UserCrud

        assert await RoleCrud.count(db_session) == 1
        assert await UserCrud.count(db_session) == 1


class TestGetObjByAttr:
    """Tests for get_obj_by_attr helper function."""

    def setup_method(self):
        """Set up test fixtures for each test."""
        self.registry = FixtureRegistry()

        @self.registry.register
        def roles() -> list[Role]:
            return [
                Role(id=1, name="admin"),
                Role(id=2, name="user"),
                Role(id=3, name="moderator"),
            ]

        @self.registry.register(depends_on=["roles"])
        def users() -> list[User]:
            return [
                User(id=1, username="alice", email="alice@example.com", role_id=1),
                User(id=2, username="bob", email="bob@example.com", role_id=1),
            ]

        self.roles = roles
        self.users = users

    def test_get_by_id(self):
        """Get an object by its id attribute."""
        role = get_obj_by_attr(self.roles, "id", 1)
        assert role.name == "admin"

    def test_get_user_by_username(self):
        """Get a user by username."""
        user = get_obj_by_attr(self.users, "username", "bob")
        assert user.id == 2
        assert user.email == "bob@example.com"

    def test_returns_first_match(self):
        """Returns the first matching object when multiple could match."""
        user = get_obj_by_attr(self.users, "role_id", 1)
        assert user.username == "alice"

    def test_no_match_raises_stop_iteration(self):
        """Raises StopIteration when no object matches."""
        with pytest.raises(StopIteration):
            get_obj_by_attr(self.roles, "name", "nonexistent")

    def test_no_match_on_wrong_value_type(self):
        """Raises StopIteration when value type doesn't match."""
        with pytest.raises(StopIteration):
            get_obj_by_attr(self.roles, "id", "1")
