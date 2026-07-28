"""Tests for fastapi_toolsets.fixtures module."""

import uuid
from enum import Enum
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_toolsets.fixtures import (
    Context,
    FixtureRegistry,
    LoadStrategy,
    load_fixtures,
    load_fixtures_by_context,
)
from fastapi_toolsets.fixtures.utils import (
    _get_primary_key,
    _get_table_chain,
    _instance_to_dict,
    _instance_to_dict_for_cls,
)

from .conftest import (
    Challenge,
    ChallengeStandard,
    IntRole,
    Permission,
    Role,
    RoleCreate,
    RoleCrud,
    User,
    UserCrud,
)


class AppContext(str, Enum):
    """Example user-defined str+Enum context."""

    STAGING = "staging"
    DEMO = "demo"


class PlainEnumContext(Enum):
    """Example user-defined plain Enum context (no str mixin)."""

    STAGING = "staging"


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


class TestCustomEnumContext:
    """Custom Enum types are accepted wherever Context/str are expected."""

    def test_cannot_subclass_context_with_members(self):
        """Python prohibits extending an Enum that already has members."""
        with pytest.raises(TypeError):

            class MyContext(Context):  # ty: ignore[subclass-of-final-class]
                STAGING = "staging"

    def test_custom_enum_values_interchangeable_with_context(self):
        """A custom enum with the same .value as a built-in Context member is
        treated as the same context — fixtures registered under one are found
        by the other."""

        class AppContextFull(str, Enum):
            BASE = "base"
            STAGING = "staging"

        registry = FixtureRegistry()

        @registry.register(contexts=[Context.BASE])
        def roles():
            return []

        # AppContextFull.BASE has value "base" — same as Context.BASE
        fixtures = registry.get_by_context(AppContextFull.BASE)
        assert len(fixtures) == 1

    def test_custom_enum_registry_default_contexts(self):
        """FixtureRegistry(contexts=[...]) accepts a custom Enum."""
        registry = FixtureRegistry(contexts=[AppContext.STAGING])

        @registry.register
        def data():
            return []

        fixture = registry.get("data")
        assert fixture.contexts == ["staging"]

    def test_custom_enum_resolve_context_dependencies(self):
        """resolve_context_dependencies accepts a custom Enum context."""
        registry = FixtureRegistry()

        @registry.register(contexts=[AppContext.STAGING])
        def staging_roles():
            return []

        order = registry.resolve_context_dependencies(AppContext.STAGING)
        assert "staging_roles" in order

    @pytest.mark.anyio
    async def test_custom_enum_e2e(self, db_session: AsyncSession):
        """End-to-end: register with custom Enum, load with the same Enum."""
        registry = FixtureRegistry()

        @registry.register(contexts=[AppContext.STAGING])
        def staging_roles():
            return [Role(id=uuid.uuid4(), name="staging-admin")]

        result = await load_fixtures_by_context(
            db_session, registry, AppContext.STAGING
        )
        assert len(result["staging_roles"]) == 1

    @pytest.mark.anyio
    async def test_plain_enum_e2e(self, db_session: AsyncSession):
        """End-to-end: register with plain Enum, load with the same Enum."""
        registry = FixtureRegistry()

        @registry.register(contexts=[PlainEnumContext.STAGING])
        def staging_roles():
            return [Role(id=uuid.uuid4(), name="plain-staging-admin")]

        result = await load_fixtures_by_context(
            db_session, registry, PlainEnumContext.STAGING
        )
        assert len(result["staging_roles"]) == 1


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
        role_id = uuid.uuid4()

        @registry.register
        def roles():
            return [Role(id=role_id, name="admin")]

        assert "roles" in [f.name for f in registry.get_all()]

    def test_register_with_custom_name(self):
        """Register fixture with custom name."""
        registry = FixtureRegistry()
        role_id = uuid.uuid4()

        @registry.register(name="custom_roles")
        def roles():
            return [Role(id=role_id, name="admin")]

        fixture = registry.get("custom_roles")
        assert fixture.name == "custom_roles"

    def test_register_with_dependencies(self):
        """Register fixture with dependencies."""
        registry = FixtureRegistry()
        role_id = uuid.uuid4()
        user_id = uuid.uuid4()

        @registry.register
        def roles():
            return [Role(id=role_id, name="admin")]

        @registry.register(depends_on=["roles"])
        def users():
            return [
                User(
                    id=user_id,
                    username="admin",
                    email="admin@test.com",
                    role_id=role_id,
                )
            ]

        fixture = registry.get("users")
        assert fixture.depends_on == ["roles"]

    def test_register_with_contexts(self):
        """Register fixture with contexts."""
        registry = FixtureRegistry()
        role_id = uuid.uuid4()

        @registry.register(contexts=[Context.TESTING])
        def test_data():
            return [Role(id=role_id, name="test")]

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
        assert names == {"test_data", "base_data"}

    def test_get_by_context_always_includes_base(self):
        """Context.BASE fixtures load even for a fully custom context."""
        registry = FixtureRegistry()

        @registry.register(contexts=[Context.BASE])
        def base_data():
            return []

        @registry.register(contexts=["staging"])
        def staging_data():
            return []

        names = {f.name for f in registry.get_by_context("staging")}
        assert names == {"staging_data", "base_data"}

    def test_get_load_variants_falls_back_to_all_when_context_has_no_match(self):
        """get_load_variants returns every variant if none match the requested
        context (and none are Context.BASE either)."""
        registry = FixtureRegistry()

        @registry.register(contexts=["staging"])
        def env_data():
            return []

        variants = registry.get_load_variants("env_data", "production")
        assert [v.contexts for v in variants] == [["staging"]]


class TestIncludeRegistry:
    """Tests for FixtureRegistry.include_registry method."""

    def test_include_empty_registry(self):
        """Include an empty registry does nothing."""
        main_registry = FixtureRegistry()
        other_registry = FixtureRegistry()

        @main_registry.register
        def roles():
            return []

        main_registry.include_registry(other_registry)

        assert len(main_registry.get_all()) == 1

    def test_include_registry_adds_fixtures(self):
        """Include registry adds all fixtures from the other registry."""
        main_registry = FixtureRegistry()
        other_registry = FixtureRegistry()

        @main_registry.register
        def roles():
            return []

        @other_registry.register
        def users():
            return []

        @other_registry.register
        def posts():
            return []

        main_registry.include_registry(other_registry)

        names = {f.name for f in main_registry.get_all()}
        assert names == {"roles", "users", "posts"}

    def test_include_registry_preserves_dependencies(self):
        """Include registry preserves fixture dependencies."""
        main_registry = FixtureRegistry()
        other_registry = FixtureRegistry()

        @main_registry.register
        def roles():
            return []

        @other_registry.register(depends_on=["roles"])
        def users():
            return []

        main_registry.include_registry(other_registry)

        fixture = main_registry.get("users")
        assert fixture.depends_on == ["roles"]

    def test_include_registry_preserves_contexts(self):
        """Include registry preserves fixture contexts."""
        main_registry = FixtureRegistry()
        other_registry = FixtureRegistry()

        @other_registry.register(contexts=[Context.TESTING, Context.DEVELOPMENT])
        def test_data():
            return []

        main_registry.include_registry(other_registry)

        fixture = main_registry.get("test_data")
        assert Context.TESTING.value in fixture.contexts
        assert Context.DEVELOPMENT.value in fixture.contexts

    def test_include_registry_raises_on_duplicate(self):
        """Include registry raises ValueError on duplicate fixture names."""
        main_registry = FixtureRegistry()
        other_registry = FixtureRegistry()

        @main_registry.register(name="roles")
        def roles_main():
            return []

        @other_registry.register(name="roles")
        def roles_other():
            return []

        with pytest.raises(ValueError, match="already exists"):
            main_registry.include_registry(other_registry)

    def test_include_multiple_registries(self):
        """Include multiple registries sequentially."""
        main_registry = FixtureRegistry()
        dev_registry = FixtureRegistry()
        test_registry = FixtureRegistry()

        @main_registry.register
        def base():
            return []

        @dev_registry.register
        def dev_data():
            return []

        @test_registry.register
        def test_data():
            return []

        main_registry.include_registry(dev_registry)
        main_registry.include_registry(test_registry)

        names = {f.name for f in main_registry.get_all()}
        assert names == {"base", "dev_data", "test_data"}


class TestDefaultContexts:
    """Tests for FixtureRegistry default contexts."""

    def test_default_contexts_applied_to_fixtures(self):
        """Default contexts are applied when no contexts specified."""
        registry = FixtureRegistry(contexts=[Context.TESTING])

        @registry.register
        def test_data():
            return []

        fixture = registry.get("test_data")
        assert fixture.contexts == [Context.TESTING.value]

    def test_explicit_contexts_override_default(self):
        """Explicit contexts override default contexts."""
        registry = FixtureRegistry(contexts=[Context.TESTING])

        @registry.register(contexts=[Context.PRODUCTION])
        def prod_data():
            return []

        fixture = registry.get("prod_data")
        assert fixture.contexts == [Context.PRODUCTION.value]

    def test_no_default_contexts_uses_base(self):
        """Without default contexts, BASE is used."""
        registry = FixtureRegistry()

        @registry.register
        def data():
            return []

        fixture = registry.get("data")
        assert fixture.contexts == [Context.BASE.value]

    def test_multiple_default_contexts(self):
        """Multiple default contexts are applied."""
        registry = FixtureRegistry(contexts=[Context.DEVELOPMENT, Context.TESTING])

        @registry.register
        def dev_test_data():
            return []

        fixture = registry.get("dev_test_data")
        assert Context.DEVELOPMENT.value in fixture.contexts
        assert Context.TESTING.value in fixture.contexts

    def test_default_contexts_with_string_values(self):
        """Default contexts work with string values."""
        registry = FixtureRegistry(contexts=["custom_context"])

        @registry.register
        def custom_data():
            return []

        fixture = registry.get("custom_data")
        assert fixture.contexts == ["custom_context"]


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

    def test_resolve_raises_for_unknown_dependency(self):
        """KeyError when depends_on references an unregistered fixture."""
        registry = FixtureRegistry()

        @registry.register(depends_on=["ghost"])
        def users():
            return []

        with pytest.raises(KeyError, match="ghost"):
            registry.resolve_dependencies("users")

    def test_resolve_deduplicates_shared_depends_on_across_variants(self):
        """A dep shared by two same-name variants appears only once in the order."""
        registry = FixtureRegistry()

        @registry.register(contexts=[Context.BASE])
        def roles():
            return []

        @registry.register(depends_on=["roles"], contexts=[Context.BASE])
        def items():
            return []

        @registry.register(depends_on=["roles"], contexts=[Context.TESTING])
        def items():  # noqa: F811
            return []

        order = registry.resolve_dependencies("items")
        assert order.count("roles") == 1
        assert order.index("roles") < order.index("items")

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
        role_id_1 = uuid.uuid4()
        role_id_2 = uuid.uuid4()

        @registry.register
        def roles():
            return [
                Role(id=role_id_1, name="admin"),
                Role(id=role_id_2, name="user"),
            ]

        result = await load_fixtures(db_session, registry, "roles")

        assert "roles" in result
        assert len(result["roles"]) == 2

        count = await RoleCrud.count(db_session)
        assert count == 2

    @pytest.mark.anyio
    async def test_load_with_dependencies(self, db_session: AsyncSession):
        """Load fixtures with dependencies."""
        registry = FixtureRegistry()
        role_id = uuid.uuid4()
        user_id = uuid.uuid4()

        @registry.register
        def roles():
            return [Role(id=role_id, name="admin")]

        @registry.register(depends_on=["roles"])
        def users():
            return [
                User(
                    id=user_id,
                    username="admin",
                    email="admin@test.com",
                    role_id=role_id,
                )
            ]

        result = await load_fixtures(db_session, registry, "users")

        assert "roles" in result
        assert "users" in result

        assert await RoleCrud.count(db_session) == 1
        assert await UserCrud.count(db_session) == 1

    @pytest.mark.anyio
    async def test_load_with_merge_strategy(self, db_session: AsyncSession):
        """Load fixtures with MERGE strategy updates existing."""
        registry = FixtureRegistry()
        role_id = uuid.uuid4()

        @registry.register
        def roles():
            return [Role(id=role_id, name="admin")]

        await load_fixtures(db_session, registry, "roles", strategy=LoadStrategy.MERGE)
        await load_fixtures(db_session, registry, "roles", strategy=LoadStrategy.MERGE)

        count = await RoleCrud.count(db_session)
        assert count == 1

    @pytest.mark.anyio
    async def test_merge_does_not_overwrite_omitted_nullable_columns(
        self, db_session: AsyncSession
    ):
        """MERGE must not clear nullable columns that the fixture didn't set.

        When a fixture omits a nullable column (e.g. role_id or notes), a re-merge
        must leave the existing DB value untouched — not overwrite it with NULL.
        """
        registry = FixtureRegistry()
        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        uid = uuid.uuid4()

        # First load: user has role_id and notes set
        @registry.register
        def users():
            return [
                User(
                    id=uid,
                    username="alice",
                    email="a@test.com",
                    role_id=admin.id,
                    notes="original",
                )
            ]

        await load_fixtures(db_session, registry, "users", strategy=LoadStrategy.MERGE)

        # Second load: fixture omits role_id and notes
        registry2 = FixtureRegistry()

        @registry2.register
        def users():  # noqa: F811
            return [User(id=uid, username="alice-updated", email="a@test.com")]

        await load_fixtures(db_session, registry2, "users", strategy=LoadStrategy.MERGE)

        from sqlalchemy import select

        row = (
            await db_session.execute(select(User).where(User.id == uid))
        ).scalar_one()
        assert row.username == "alice-updated"  # updated column changed
        assert row.role_id == admin.id  # omitted → preserved
        assert row.notes == "original"  # omitted → preserved

    @pytest.mark.anyio
    async def test_load_with_skip_existing_strategy(self, db_session: AsyncSession):
        """Load fixtures with SKIP_EXISTING strategy."""
        registry = FixtureRegistry()
        role_id = uuid.uuid4()

        @registry.register
        def roles():
            return [Role(id=role_id, name="original")]

        await load_fixtures(
            db_session, registry, "roles", strategy=LoadStrategy.SKIP_EXISTING
        )

        @registry.register(name="roles_updated")
        def roles_v2():
            return [Role(id=role_id, name="updated")]

        registry._fixtures["roles"] = registry._fixtures.pop("roles_updated")

        await load_fixtures(
            db_session, registry, "roles", strategy=LoadStrategy.SKIP_EXISTING
        )

        role = await RoleCrud.first(db_session, [Role.id == role_id])
        assert role is not None
        assert role.name == "original"

    @pytest.mark.anyio
    async def test_load_with_insert_strategy(self, db_session: AsyncSession):
        """Load fixtures with INSERT strategy."""
        registry = FixtureRegistry()
        role_id_1 = uuid.uuid4()
        role_id_2 = uuid.uuid4()

        @registry.register
        def roles():
            return [
                Role(id=role_id_1, name="admin"),
                Role(id=role_id_2, name="user"),
            ]

        result = await load_fixtures(
            db_session, registry, "roles", strategy=LoadStrategy.INSERT
        )

        assert "roles" in result
        assert len(result["roles"]) == 2

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
        role_id_1 = uuid.uuid4()
        role_id_2 = uuid.uuid4()

        @registry.register
        def roles():
            return [Role(id=role_id_1, name="admin")]

        @registry.register
        def other_roles():
            return [Role(id=role_id_2, name="user")]

        result = await load_fixtures(db_session, registry, "roles", "other_roles")

        assert "roles" in result
        assert "other_roles" in result

        count = await RoleCrud.count(db_session)
        assert count == 2

    @pytest.mark.anyio
    async def test_skip_existing_skips_if_record_exists(self, db_session: AsyncSession):
        """SKIP_EXISTING returns empty loaded list when the record already exists."""
        registry = FixtureRegistry()
        role_id = uuid.uuid4()

        @registry.register
        def roles():
            return [Role(id=role_id, name="admin")]

        # First load — inserts the record.
        result1 = await load_fixtures(
            db_session, registry, "roles", strategy=LoadStrategy.SKIP_EXISTING
        )
        assert len(result1["roles"]) == 1

        # Remove from identity map so session.get() queries the DB in the second load.
        db_session.expunge_all()

        # Second load — record exists in DB, nothing should be added.
        result2 = await load_fixtures(
            db_session, registry, "roles", strategy=LoadStrategy.SKIP_EXISTING
        )
        assert result2["roles"] == []

    @pytest.mark.anyio
    async def test_skip_existing_null_pk_inserts(self, db_session: AsyncSession):
        """SKIP_EXISTING inserts when the instance has no PK set (auto-increment)."""
        registry = FixtureRegistry()

        @registry.register
        def int_roles():
            # No id provided — PK is None before INSERT (autoincrement).
            return [IntRole(name="member")]

        result = await load_fixtures(
            db_session, registry, "int_roles", strategy=LoadStrategy.SKIP_EXISTING
        )
        assert len(result["int_roles"]) == 1
        # The generated autoincrement PK must be written back onto the
        # returned instance, not just visible via a fresh DB query.
        assert cast(IntRole, result["int_roles"][0]).id is not None

    @pytest.mark.anyio
    async def test_insert_refreshes_autoincrement_pk_on_returned_instance(
        self, db_session: AsyncSession
    ):
        """INSERT strategy writes the generated PK back onto the returned instance."""
        registry = FixtureRegistry()

        @registry.register
        def int_roles():
            return [IntRole(name="auto")]

        result = await load_fixtures(
            db_session, registry, "int_roles", strategy=LoadStrategy.INSERT
        )
        assert cast(IntRole, result["int_roles"][0]).id is not None

    @pytest.mark.anyio
    async def test_merge_refreshes_server_default_on_returned_instance(
        self, db_session: AsyncSession
    ):
        """MERGE strategy refreshes the returned instance with server-generated values."""
        registry = FixtureRegistry()

        @registry.register
        def challenges():
            return [
                Challenge(id=uuid.uuid4(), title="Solo", challenge_type="challenge")
            ]

        result = await load_fixtures(
            db_session, registry, "challenges", strategy=LoadStrategy.MERGE
        )
        # `points` has a column default of 0 applied by the DB, never set on
        # the in-memory instance — the returned object must reflect it.
        assert cast(Challenge, result["challenges"][0]).points == 0


class TestLoadFixturesByContext:
    """Tests for load_fixtures_by_context function."""

    @pytest.mark.anyio
    async def test_load_by_single_context(self, db_session: AsyncSession):
        """Load fixtures by single context."""
        registry = FixtureRegistry()
        base_role_id = uuid.uuid4()
        test_role_id = uuid.uuid4()

        @registry.register(contexts=[Context.BASE])
        def base_roles():
            return [Role(id=base_role_id, name="base_role")]

        @registry.register(contexts=[Context.TESTING])
        def test_roles():
            return [Role(id=test_role_id, name="test_role")]

        await load_fixtures_by_context(db_session, registry, Context.BASE)

        count = await RoleCrud.count(db_session)
        assert count == 1

        role = await RoleCrud.first(db_session, [Role.id == base_role_id])
        assert role is not None
        assert role.name == "base_role"

    @pytest.mark.anyio
    async def test_load_by_multiple_contexts(self, db_session: AsyncSession):
        """Load fixtures by multiple contexts."""
        registry = FixtureRegistry()
        base_role_id = uuid.uuid4()
        test_role_id = uuid.uuid4()

        @registry.register(contexts=[Context.BASE])
        def base_roles():
            return [Role(id=base_role_id, name="base_role")]

        @registry.register(contexts=[Context.TESTING])
        def test_roles():
            return [Role(id=test_role_id, name="test_role")]

        await load_fixtures_by_context(
            db_session, registry, Context.BASE, Context.TESTING
        )

        count = await RoleCrud.count(db_session)
        assert count == 2

    @pytest.mark.anyio
    async def test_load_context_with_dependencies(self, db_session: AsyncSession):
        """Load context fixtures with cross-context dependencies."""
        registry = FixtureRegistry()
        role_id = uuid.uuid4()
        user_id = uuid.uuid4()

        @registry.register(contexts=[Context.BASE])
        def roles():
            return [Role(id=role_id, name="admin")]

        @registry.register(depends_on=["roles"], contexts=[Context.TESTING])
        def test_users():
            return [
                User(
                    id=user_id,
                    username="tester",
                    email="test@test.com",
                    role_id=role_id,
                )
            ]

        await load_fixtures_by_context(db_session, registry, Context.TESTING)

        assert await RoleCrud.count(db_session) == 1
        assert await UserCrud.count(db_session) == 1


class TestRegistryObj:
    """Tests for FixtureRegistry.obj."""

    def setup_method(self):
        """Set up test fixtures for each test."""
        self.registry = FixtureRegistry()
        self.role_id_1 = uuid.uuid4()
        self.role_id_2 = uuid.uuid4()
        self.role_id_3 = uuid.uuid4()
        self.user_id_1 = uuid.uuid4()
        self.user_id_2 = uuid.uuid4()

        role_id_1 = self.role_id_1
        role_id_2 = self.role_id_2
        role_id_3 = self.role_id_3
        user_id_1 = self.user_id_1
        user_id_2 = self.user_id_2

        @self.registry.register
        def roles() -> list[Role]:
            return [
                Role(id=role_id_1, name="admin"),
                Role(id=role_id_2, name="user"),
                Role(id=role_id_3, name="moderator"),
            ]

        @self.registry.register(depends_on=["roles"])
        def users() -> list[User]:
            return [
                User(
                    id=user_id_1,
                    username="alice",
                    email="alice@example.com",
                    role_id=role_id_1,
                ),
                User(
                    id=user_id_2,
                    username="bob",
                    email="bob@example.com",
                    role_id=role_id_1,
                ),
            ]

    def test_get_by_id(self):
        """Get an object by its id attribute."""
        role = self.registry.obj("roles", "id", self.role_id_1)
        assert cast(Role, role).name == "admin"

    def test_get_user_by_username(self):
        """Get a user by username."""
        user = cast(User, self.registry.obj("users", "username", "bob"))
        assert user.id == self.user_id_2
        assert user.email == "bob@example.com"

    def test_returns_first_match(self):
        """Returns the first matching object when multiple could match."""
        user = cast(User, self.registry.obj("users", "role_id", self.role_id_1))
        assert user.username == "alice"

    def test_no_match_raises_stop_iteration(self):
        """Raises StopIteration with contextual message when no object matches."""
        with pytest.raises(
            StopIteration,
            match="No object with name=nonexistent found in fixture 'roles'",
        ):
            self.registry.obj("roles", "name", "nonexistent")

    def test_no_match_on_wrong_value_type(self):
        """Raises StopIteration when value type doesn't match."""
        with pytest.raises(StopIteration):
            self.registry.obj("roles", "id", "not-a-uuid")

    def test_unknown_fixture_raises_key_error(self):
        """Raises KeyError when the fixture name isn't registered."""
        with pytest.raises(KeyError):
            self.registry.obj("unknown", "id", self.role_id_1)

    def test_searches_across_context_variants(self):
        """obj() finds matches across all context variants of a fixture name, not just one."""
        registry = FixtureRegistry()
        tester_id = uuid.uuid4()

        @registry.register(contexts=[Context.BASE])
        def variant_users() -> list[User]:
            return [User(id=uuid.uuid4(), username="admin", email="admin@x.com")]

        @registry.register(contexts=[Context.TESTING])
        def variant_users() -> list[User]:  # noqa: F811
            return [User(id=tester_id, username="tester", email="tester@x.com")]

        user = cast(User, registry.obj("variant_users", "username", "tester"))
        assert user.id == tester_id


class TestRegistryField:
    """Tests for FixtureRegistry.field."""

    def setup_method(self):
        self.registry = FixtureRegistry()
        self.role_id_1 = uuid.uuid4()
        self.role_id_2 = uuid.uuid4()
        role_id_1 = self.role_id_1
        role_id_2 = self.role_id_2

        @self.registry.register
        def roles() -> list[Role]:
            return [
                Role(id=role_id_1, name="admin"),
                Role(id=role_id_2, name="user"),
            ]

    def test_returns_id_by_default(self):
        """Returns the id field when no field is specified."""
        result = self.registry.field("roles", "name", "admin")
        assert result == self.role_id_1

    def test_returns_specified_field(self):
        """Returns the requested field instead of id."""
        result = self.registry.field("roles", "id", self.role_id_2, field="name")
        assert result == "user"

    def test_no_match_raises_stop_iteration(self):
        """Propagates StopIteration from obj() when no match found."""
        with pytest.raises(StopIteration, match="No object with name=missing"):
            self.registry.field("roles", "name", "missing")


class TestGetPrimaryKey:
    """Unit tests for the _get_primary_key helper (composite PK paths)."""

    def test_composite_pk_all_set(self):
        """Returns a tuple when all composite PK values are set."""
        instance = Permission(subject="post", action="read")
        pk = _get_primary_key(instance)
        assert pk == ("post", "read")

    def test_composite_pk_partial_none(self):
        """Returns None when any composite PK value is None."""
        instance = Permission(subject="post")  # action is None
        pk = _get_primary_key(instance)
        assert pk is None


class TestRegistryGetVariants:
    """Tests for FixtureRegistry.get and get_variants edge cases."""

    def test_get_raises_value_error_for_multi_variant(self):
        """get() raises ValueError when the fixture has multiple context variants."""
        registry = FixtureRegistry()

        @registry.register(contexts=[Context.BASE])
        def items():
            return []

        @registry.register(contexts=[Context.TESTING])
        def items():  # noqa: F811
            return []

        with pytest.raises(ValueError, match="get_variants"):
            registry.get("items")

    def test_get_variants_raises_key_error_for_unknown(self):
        """get_variants() raises KeyError for an unregistered name."""
        registry = FixtureRegistry()
        with pytest.raises(KeyError, match="not found"):
            registry.get_variants("no_such_fixture")


class TestInstanceToDict:
    """Unit tests for the _instance_to_dict helper."""

    def test_explicit_values_included(self):
        """All explicitly set column values appear in the result."""
        role_id = uuid.uuid4()
        instance = Role(id=role_id, name="admin")
        d = _instance_to_dict(instance)
        assert d["id"] == role_id
        assert d["name"] == "admin"

    def test_callable_default_none_excluded(self):
        """A column whose value is None but has a callable Python-side default
        (e.g. ``default=uuid.uuid4``) is excluded so the DB generates it."""
        instance = Role(id=None, name="admin")
        d = _instance_to_dict(instance)
        assert "id" not in d
        assert d["name"] == "admin"

    def test_autoincrement_none_excluded(self):
        """A column whose value is None but has autoincrement=True is excluded
        so the DB generates the value via its sequence."""
        instance = IntRole(id=None, name="admin")
        d = _instance_to_dict(instance)
        assert "id" not in d
        assert d["name"] == "admin"

    def test_nullable_none_included(self):
        """None on a nullable column with no default is kept (explicit NULL)."""
        instance = User(id=uuid.uuid4(), username="u", email="e@e.com", role_id=None)
        d = _instance_to_dict(instance)
        assert "role_id" in d
        assert d["role_id"] is None

    def test_nullable_str_no_default_omitted_not_in_dict(self):
        """Mapped[str | None] with no default, not provided in constructor, is absent from dict."""
        instance = User(id=uuid.uuid4(), username="u", email="e@e.com")
        d = _instance_to_dict(instance)
        assert "notes" not in d

    def test_nullable_str_no_default_explicit_none_included(self):
        """Mapped[str | None] with no default, explicitly set to None, is included as NULL."""
        instance = User(id=uuid.uuid4(), username="u", email="e@e.com", notes=None)
        d = _instance_to_dict(instance)
        assert "notes" in d
        assert d["notes"] is None

    def test_nullable_str_no_default_with_value_included(self):
        """Mapped[str | None] with no default and a value set is included normally."""
        instance = User(id=uuid.uuid4(), username="u", email="e@e.com", notes="hello")
        d = _instance_to_dict(instance)
        assert d["notes"] == "hello"

    @pytest.mark.anyio
    async def test_nullable_str_no_default_insert_roundtrip(
        self, db_session: AsyncSession
    ):
        """Fixture loading works for models with Mapped[str | None] (no default).

        Both the omitted-value (→ NULL) and explicit-None paths must insert without error.
        """
        registry = FixtureRegistry()

        uid_a = uuid.uuid4()
        uid_b = uuid.uuid4()
        uid_c = uuid.uuid4()

        @registry.register
        def users():
            return [
                User(
                    id=uid_a, username="no_notes", email="a@test.com"
                ),  # notes omitted
                User(
                    id=uid_b, username="null_notes", email="b@test.com", notes=None
                ),  # explicit None
                User(
                    id=uid_c, username="has_notes", email="c@test.com", notes="hi"
                ),  # value set
            ]

        result = await load_fixtures(db_session, registry, "users")

        from sqlalchemy import select

        rows = (
            (await db_session.execute(select(User).order_by(User.username)))
            .scalars()
            .all()
        )
        by_username = {r.username: r for r in rows}

        assert by_username["no_notes"].notes is None
        assert by_username["null_notes"].notes is None
        assert by_username["has_notes"].notes == "hi"
        assert len(result["users"]) == 3


class TestBatchMergeNonPkColumns:
    """Batch MERGE on a model with no non-PK columns (PK-only table)."""

    @pytest.mark.anyio
    async def test_merge_pk_only_model(self, db_session: AsyncSession):
        """MERGE strategy on a PK-only model uses on_conflict_do_nothing."""
        registry = FixtureRegistry()

        @registry.register
        def permissions():
            return [
                Permission(subject="post", action="read"),
                Permission(subject="post", action="write"),
            ]

        result = await load_fixtures(
            db_session, registry, "permissions", strategy=LoadStrategy.MERGE
        )
        assert len(result["permissions"]) == 2

        # Run again — conflicts are silently ignored.
        result2 = await load_fixtures(
            db_session, registry, "permissions", strategy=LoadStrategy.MERGE
        )
        assert len(result2["permissions"]) == 2


class TestBatchNullableColumnEdgeCases:
    """Deep tests for nullable column handling during batch import."""

    @pytest.mark.anyio
    async def test_insert_batch_mixed_nullable_fk(self, db_session: AsyncSession):
        """INSERT batch where some rows set a nullable FK and others don't.

        After normalization the omitted role_id becomes None.  For INSERT this
        is acceptable — both rows should insert successfully with the correct
        values (one with FK, one with NULL).
        """
        registry = FixtureRegistry()
        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        uid1 = uuid.uuid4()
        uid2 = uuid.uuid4()

        @registry.register
        def users():
            return [
                User(
                    id=uid1, username="with_role", email="a@test.com", role_id=admin.id
                ),
                User(id=uid2, username="no_role", email="b@test.com"),
            ]

        await load_fixtures(db_session, registry, "users", strategy=LoadStrategy.INSERT)

        from sqlalchemy import select

        rows = {
            r.username: r
            for r in (await db_session.execute(select(User))).scalars().all()
        }
        assert rows["with_role"].role_id == admin.id
        assert rows["no_role"].role_id is None

    @pytest.mark.anyio
    async def test_insert_batch_mixed_nullable_notes(self, db_session: AsyncSession):
        """INSERT batch where some rows have notes and others don't.

        Ensures normalization doesn't break the insert and that each row gets
        the intended value.
        """
        registry = FixtureRegistry()
        uid1 = uuid.uuid4()
        uid2 = uuid.uuid4()
        uid3 = uuid.uuid4()

        @registry.register
        def users():
            return [
                User(
                    id=uid1,
                    username="has_notes",
                    email="a@test.com",
                    notes="important",
                ),
                User(id=uid2, username="no_notes", email="b@test.com"),
                User(id=uid3, username="null_notes", email="c@test.com", notes=None),
            ]

        await load_fixtures(db_session, registry, "users", strategy=LoadStrategy.INSERT)

        from sqlalchemy import select

        rows = {
            r.username: r
            for r in (await db_session.execute(select(User))).scalars().all()
        }
        assert rows["has_notes"].notes == "important"
        assert rows["no_notes"].notes is None
        assert rows["null_notes"].notes is None

    @pytest.mark.anyio
    async def test_merge_batch_mixed_nullable_does_not_overwrite(
        self, db_session: AsyncSession
    ):
        """MERGE batch where one row sets a nullable column and another omits it.

        If both rows already exist in DB, the row that omits the column must
        NOT have its existing value overwritten with NULL.

        This is the core normalization bug: _normalize_rows fills missing keys
        with None, and then MERGE's SET clause includes that column for ALL rows.
        """
        from sqlalchemy import select

        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        uid1 = uuid.uuid4()
        uid2 = uuid.uuid4()

        # Pre-populate: both users have role_id and notes
        registry_initial = FixtureRegistry()

        @registry_initial.register
        def users():
            return [
                User(
                    id=uid1,
                    username="alice",
                    email="a@test.com",
                    role_id=admin.id,
                    notes="alice notes",
                ),
                User(
                    id=uid2,
                    username="bob",
                    email="b@test.com",
                    role_id=admin.id,
                    notes="bob notes",
                ),
            ]

        await load_fixtures(
            db_session, registry_initial, "users", strategy=LoadStrategy.INSERT
        )

        # Re-merge: alice updates notes, bob omits notes entirely
        registry_merge = FixtureRegistry()

        @registry_merge.register
        def users():  # noqa: F811
            return [
                User(
                    id=uid1,
                    username="alice",
                    email="a@test.com",
                    role_id=admin.id,
                    notes="updated",
                ),
                User(
                    id=uid2,
                    username="bob",
                    email="b@test.com",
                    role_id=admin.id,
                ),  # notes omitted
            ]

        await load_fixtures(
            db_session, registry_merge, "users", strategy=LoadStrategy.MERGE
        )

        rows = {
            r.username: r
            for r in (await db_session.execute(select(User))).scalars().all()
        }
        assert rows["alice"].notes == "updated"
        # Bob's notes must be preserved, NOT overwritten with NULL
        assert rows["bob"].notes == "bob notes"

    @pytest.mark.anyio
    async def test_merge_batch_mixed_nullable_fk_preserves_existing(
        self, db_session: AsyncSession
    ):
        """MERGE batch where one row sets role_id and another omits it.

        The row that omits role_id must keep its existing DB value.
        """
        from sqlalchemy import select

        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        editor = await RoleCrud.create(db_session, RoleCreate(name="editor"))
        uid1 = uuid.uuid4()
        uid2 = uuid.uuid4()

        # Pre-populate
        registry_initial = FixtureRegistry()

        @registry_initial.register
        def users():
            return [
                User(
                    id=uid1,
                    username="alice",
                    email="a@test.com",
                    role_id=admin.id,
                ),
                User(
                    id=uid2,
                    username="bob",
                    email="b@test.com",
                    role_id=editor.id,
                ),
            ]

        await load_fixtures(
            db_session, registry_initial, "users", strategy=LoadStrategy.INSERT
        )

        # Re-merge: alice changes role, bob omits role_id
        registry_merge = FixtureRegistry()

        @registry_merge.register
        def users():  # noqa: F811
            return [
                User(
                    id=uid1,
                    username="alice",
                    email="a@test.com",
                    role_id=editor.id,
                ),
                User(id=uid2, username="bob", email="b@test.com"),  # role_id omitted
            ]

        await load_fixtures(
            db_session, registry_merge, "users", strategy=LoadStrategy.MERGE
        )

        rows = {
            r.username: r
            for r in (await db_session.execute(select(User))).scalars().all()
        }
        assert rows["alice"].role_id == editor.id  # updated
        assert rows["bob"].role_id == editor.id  # must be preserved, NOT NULL

    @pytest.mark.anyio
    async def test_insert_batch_mixed_pk_presence(self, db_session: AsyncSession):
        """INSERT batch where some rows have explicit PK and others rely on
        the callable default (uuid.uuid4).

        Normalization adds the PK key with None to rows that omitted it,
        which can cause NOT NULL violations on the PK column.
        """
        registry = FixtureRegistry()
        explicit_id = uuid.uuid4()

        @registry.register
        def roles():
            return [
                Role(id=explicit_id, name="admin"),
                Role(name="user"),  # PK omitted, relies on default=uuid.uuid4
            ]

        await load_fixtures(db_session, registry, "roles", strategy=LoadStrategy.INSERT)

        from sqlalchemy import select

        rows = (await db_session.execute(select(Role))).scalars().all()
        assert len(rows) == 2
        names = {r.name for r in rows}
        assert names == {"admin", "user"}
        # The "admin" row must have the explicit ID
        admin = next(r for r in rows if r.name == "admin")
        assert admin.id == explicit_id
        # The "user" row must have a generated UUID (not None)
        user = next(r for r in rows if r.name == "user")
        assert user.id is not None

    @pytest.mark.anyio
    async def test_skip_existing_batch_mixed_nullable(self, db_session: AsyncSession):
        """SKIP_EXISTING with mixed nullable columns inserts correctly.

        Only new rows are inserted; existing rows are untouched regardless of
        which columns the fixture provides.
        """
        from sqlalchemy import select

        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        uid1 = uuid.uuid4()
        uid2 = uuid.uuid4()

        # Pre-populate uid1 with notes
        registry_initial = FixtureRegistry()

        @registry_initial.register
        def users():
            return [
                User(
                    id=uid1,
                    username="alice",
                    email="a@test.com",
                    role_id=admin.id,
                    notes="keep me",
                ),
            ]

        await load_fixtures(
            db_session, registry_initial, "users", strategy=LoadStrategy.INSERT
        )

        # Load again with SKIP_EXISTING: uid1 already exists, uid2 is new
        registry_skip = FixtureRegistry()

        @registry_skip.register
        def users():  # noqa: F811
            return [
                User(id=uid1, username="alice-updated", email="a@test.com"),  # exists
                User(
                    id=uid2,
                    username="bob",
                    email="b@test.com",
                    notes="new user",
                ),  # new
            ]

        result = await load_fixtures(
            db_session, registry_skip, "users", strategy=LoadStrategy.SKIP_EXISTING
        )
        assert len(result["users"]) == 1  # only bob inserted

        rows = {
            r.username: r
            for r in (await db_session.execute(select(User))).scalars().all()
        }
        # alice untouched
        assert rows["alice"].role_id == admin.id
        assert rows["alice"].notes == "keep me"
        # bob inserted correctly
        assert rows["bob"].notes == "new user"

    @pytest.mark.anyio
    async def test_insert_batch_every_row_different_nullable_columns(
        self, db_session: AsyncSession
    ):
        """Each row in the batch sets a different combination of nullable columns.

        Tests that normalization produces valid SQL for all rows.
        """
        registry = FixtureRegistry()
        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        uid1 = uuid.uuid4()
        uid2 = uuid.uuid4()
        uid3 = uuid.uuid4()

        @registry.register
        def users():
            return [
                User(
                    id=uid1,
                    username="all_set",
                    email="a@test.com",
                    role_id=admin.id,
                    notes="full",
                ),
                User(
                    id=uid2, username="only_role", email="b@test.com", role_id=admin.id
                ),
                User(
                    id=uid3, username="only_notes", email="c@test.com", notes="partial"
                ),
            ]

        await load_fixtures(db_session, registry, "users", strategy=LoadStrategy.INSERT)

        from sqlalchemy import select

        rows = {
            r.username: r
            for r in (await db_session.execute(select(User))).scalars().all()
        }
        assert rows["all_set"].role_id == admin.id
        assert rows["all_set"].notes == "full"
        assert rows["only_role"].role_id == admin.id
        assert rows["only_role"].notes is None
        assert rows["only_notes"].role_id is None
        assert rows["only_notes"].notes == "partial"


class TestJoinedTableInheritance:
    """Tests for fixture batch helpers with SQLAlchemy joined-table inheritance.

    Regression coverage for the KeyError raised when _batch_insert/_batch_merge
    used model_cls.__mapper__.column_attrs (which includes inherited parent columns)
    against a pg_insert targeting only the child table.
    """

    def test_get_table_chain_plain_model(self):
        """_get_table_chain returns [model_cls] for a non-inherited model."""
        chain = _get_table_chain(Role)
        assert chain == [Role]

    def test_get_table_chain_jti_child(self):
        """_get_table_chain returns [root, child] for a joined-table child."""
        chain = _get_table_chain(ChallengeStandard)
        assert chain == [Challenge, ChallengeStandard]

    def test_instance_to_dict_for_cls_root(self):
        """_instance_to_dict_for_cls scopes to root table columns only."""
        cid = uuid.uuid4()
        inst = ChallengeStandard(
            id=cid, title="root-only", challenge_type="standard", difficulty="easy"
        )
        d = _instance_to_dict_for_cls(inst, Challenge)
        assert "id" in d
        assert "title" in d
        assert "challenge_type" in d
        assert "difficulty" not in d  # child column excluded

    def test_instance_to_dict_for_cls_child(self):
        """_instance_to_dict_for_cls scopes to child table columns only."""
        cid = uuid.uuid4()
        inst = ChallengeStandard(
            id=cid, title="child-only", challenge_type="standard", difficulty="hard"
        )
        d = _instance_to_dict_for_cls(inst, ChallengeStandard)
        assert "id" in d
        assert "difficulty" in d
        assert "title" not in d  # parent column excluded
        assert "challenge_type" not in d  # parent column excluded

    @pytest.mark.anyio
    async def test_insert_strategy_jti(self, db_session: AsyncSession):
        """INSERT strategy correctly inserts both root and child table rows."""
        from sqlalchemy import select

        registry = FixtureRegistry()
        cid1 = uuid.uuid4()
        cid2 = uuid.uuid4()

        @registry.register
        def challenges():
            return [
                ChallengeStandard(
                    id=cid1, title="Alpha", challenge_type="standard", difficulty="easy"
                ),
                ChallengeStandard(
                    id=cid2, title="Beta", challenge_type="standard", difficulty="hard"
                ),
            ]

        result = await load_fixtures(
            db_session, registry, "challenges", strategy=LoadStrategy.INSERT
        )
        assert len(result["challenges"]) == 2

        rows = (await db_session.execute(select(ChallengeStandard))).scalars().all()
        by_title = {r.title: r for r in rows}
        assert by_title["Alpha"].difficulty == "easy"
        assert by_title["Beta"].difficulty == "hard"
        assert by_title["Alpha"].challenge_type == "standard"

    @pytest.mark.anyio
    async def test_merge_strategy_jti_insert(self, db_session: AsyncSession):
        """MERGE strategy inserts new JTI rows correctly."""
        from sqlalchemy import select

        registry = FixtureRegistry()
        cid = uuid.uuid4()

        @registry.register
        def challenges():
            return [
                ChallengeStandard(
                    id=cid,
                    title="Gamma",
                    challenge_type="standard",
                    difficulty="medium",
                )
            ]

        result = await load_fixtures(
            db_session, registry, "challenges", strategy=LoadStrategy.MERGE
        )
        assert len(result["challenges"]) == 1

        row = (
            await db_session.execute(
                select(ChallengeStandard).where(ChallengeStandard.id == cid)
            )
        ).scalar_one()
        assert row.title == "Gamma"
        assert row.difficulty == "medium"

    @pytest.mark.anyio
    async def test_merge_strategy_jti_upsert(self, db_session: AsyncSession):
        """MERGE strategy updates existing JTI rows on re-load."""
        from sqlalchemy import select

        registry = FixtureRegistry()
        cid = uuid.uuid4()

        @registry.register
        def challenges():
            return [
                ChallengeStandard(
                    id=cid,
                    title="Original",
                    challenge_type="standard",
                    difficulty="easy",
                )
            ]

        await load_fixtures(
            db_session, registry, "challenges", strategy=LoadStrategy.MERGE
        )

        registry2 = FixtureRegistry()

        @registry2.register
        def challenges():  # noqa: F811
            return [
                ChallengeStandard(
                    id=cid,
                    title="Updated",
                    challenge_type="standard",
                    difficulty="hard",
                )
            ]

        await load_fixtures(
            db_session, registry2, "challenges", strategy=LoadStrategy.MERGE
        )

        row = (
            await db_session.execute(
                select(ChallengeStandard).where(ChallengeStandard.id == cid)
            )
        ).scalar_one()
        assert row.title == "Updated"
        assert row.difficulty == "hard"

    @pytest.mark.anyio
    async def test_skip_existing_strategy_jti_inserts_new(
        self, db_session: AsyncSession
    ):
        """SKIP_EXISTING inserts a new JTI row and returns it."""
        from sqlalchemy import select

        registry = FixtureRegistry()
        cid = uuid.uuid4()

        @registry.register
        def challenges():
            return [
                ChallengeStandard(
                    id=cid, title="New", challenge_type="standard", difficulty="easy"
                )
            ]

        result = await load_fixtures(
            db_session, registry, "challenges", strategy=LoadStrategy.SKIP_EXISTING
        )
        assert len(result["challenges"]) == 1

        row = (
            await db_session.execute(
                select(ChallengeStandard).where(ChallengeStandard.id == cid)
            )
        ).scalar_one()
        assert row.title == "New"

    @pytest.mark.anyio
    async def test_skip_existing_strategy_jti_skips_existing(
        self, db_session: AsyncSession
    ):
        """SKIP_EXISTING does not overwrite an existing JTI row."""
        from sqlalchemy import select

        registry = FixtureRegistry()
        cid = uuid.uuid4()

        @registry.register
        def challenges():
            return [
                ChallengeStandard(
                    id=cid, title="First", challenge_type="standard", difficulty="easy"
                )
            ]

        await load_fixtures(
            db_session, registry, "challenges", strategy=LoadStrategy.SKIP_EXISTING
        )
        db_session.expunge_all()

        registry2 = FixtureRegistry()

        @registry2.register
        def challenges():  # noqa: F811
            return [
                ChallengeStandard(
                    id=cid,
                    title="Overwrite",
                    challenge_type="standard",
                    difficulty="hard",
                )
            ]

        result = await load_fixtures(
            db_session, registry2, "challenges", strategy=LoadStrategy.SKIP_EXISTING
        )
        assert result["challenges"] == []

        row = (
            await db_session.execute(
                select(ChallengeStandard).where(ChallengeStandard.id == cid)
            )
        ).scalar_one()
        assert row.title == "First"
        assert row.difficulty == "easy"
