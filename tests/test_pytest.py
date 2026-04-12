"""Tests for fastapi_toolsets.pytest module."""

import uuid
from typing import cast

import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient
from sqlalchemy import ForeignKey, String, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from fastapi_toolsets.db import get_transaction
from fastapi_toolsets.fixtures import Context, FixtureRegistry, LoadStrategy
from fastapi_toolsets.pytest import (
    create_async_client,
    create_db_session,
    create_worker_database,
    register_fixtures,
    worker_database_url,
)
from fastapi_toolsets.pytest.plugin import (
    _get_primary_key,
    _relationship_load_options,
    _reload_with_relationships,
)
from fastapi_toolsets.pytest.utils import _get_xdist_worker

from .conftest import (
    DATABASE_URL,
    Base,
    IntRole,
    Permission,
    Role,
    RoleCrud,
    User,
    UserCrud,
)

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
        """Loaded fixtures have working relationships directly accessible."""
        user = next(u for u in fixture_users if u.id == USER_ADMIN_ID)
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
    async def test_fixture_auto_loads_relationships(
        self, db_session: AsyncSession, fixture_users: list[User]
    ):
        """Fixtures automatically eager-load all direct relationships."""
        user = next(u for u in fixture_users if u.username == "plugin_admin")
        assert user.role is not None
        assert user.role.name == "plugin_admin"

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

    @pytest.mark.anyio
    async def test_dependency_overrides_applied_and_cleaned(self):
        """Dependency overrides are applied during the context and removed after."""
        app = FastAPI()

        async def original_dep() -> str:
            return "original"

        async def override_dep() -> str:
            return "overridden"

        @app.get("/dep")
        async def dep_endpoint(value: str = Depends(original_dep)):
            return {"value": value}

        async with create_async_client(
            app, dependency_overrides={original_dep: override_dep}
        ) as client:
            response = await client.get("/dep")
            assert response.json() == {"value": "overridden"}

        # Overrides should be cleaned up
        assert original_dep not in app.dependency_overrides


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

    @pytest.mark.anyio
    async def test_cleanup_truncates_tables(self):
        """Tables are truncated after session closes when cleanup=True."""
        role_id = uuid.uuid4()
        async with create_db_session(
            DATABASE_URL, Base, cleanup=True, drop_tables=False
        ) as session:
            role = Role(id=role_id, name="will_be_cleaned")
            session.add(role)
            await session.commit()

        # Data should have been truncated, but tables still exist
        async with create_db_session(DATABASE_URL, Base, drop_tables=True) as session:
            result = await session.execute(select(Role))
            assert result.all() == []

    @pytest.mark.anyio
    async def test_get_transaction_commits_visible_to_separate_session(self):
        """Data written via get_transaction() is committed and visible to other sessions."""
        role_id = uuid.uuid4()

        async with create_db_session(DATABASE_URL, Base, drop_tables=False) as session:
            # Simulate what _create_fixture_function does: insert via get_transaction
            # with no explicit commit afterward.
            async with get_transaction(session):
                role = Role(id=role_id, name="visible_to_other_session")
                session.add(role)

            # The data must have been committed (begin/commit, not a savepoint),
            # so a separate engine/session can read it.
            other_engine = create_async_engine(DATABASE_URL, echo=False)
            try:
                other_session_maker = async_sessionmaker(
                    other_engine, expire_on_commit=False
                )
                async with other_session_maker() as other:
                    result = await other.execute(select(Role).where(Role.id == role_id))
                    fetched = result.scalar_one_or_none()
                    assert fetched is not None, (
                        "Fixture data inserted via get_transaction() must be committed "
                        "and visible to a separate session. If create_db_session uses "
                        "create_db_context, auto-begin forces get_transaction() into "
                        "savepoints instead of real commits."
                    )
                    assert fetched.name == "visible_to_other_session"
            finally:
                await other_engine.dispose()

        # Cleanup
        async with create_db_session(DATABASE_URL, Base, drop_tables=True) as _:
            pass


class TestGetXdistWorker:
    """Tests for _get_xdist_worker helper."""

    def test_returns_default_test_db_without_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Returns default_test_db when PYTEST_XDIST_WORKER is not set."""
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
        assert _get_xdist_worker("my_default") == "my_default"

    def test_returns_worker_name(self, monkeypatch: pytest.MonkeyPatch):
        """Returns the worker name from the environment variable."""
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
        assert _get_xdist_worker("ignored") == "gw0"


class TestWorkerDatabaseUrl:
    """Tests for worker_database_url helper."""

    def test_appends_default_test_db_without_xdist(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """default_test_db is appended when not running under xdist."""
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
        url = "postgresql+asyncpg://user:pass@localhost:5432/mydb"
        result = worker_database_url(url, default_test_db="fallback")
        assert make_url(result).database == "mydb_fallback"

    def test_appends_worker_id_to_database_name(self, monkeypatch: pytest.MonkeyPatch):
        """Worker name is appended to the database name."""
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
        url = "postgresql+asyncpg://user:pass@localhost:5432/db"
        result = worker_database_url(url, default_test_db="unused")
        assert make_url(result).database == "db_gw0"

    def test_preserves_url_components(self, monkeypatch: pytest.MonkeyPatch):
        """Host, port, username, password, and driver are preserved."""
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw2")
        url = "postgresql+asyncpg://myuser:secret@dbhost:6543/testdb"
        result = make_url(worker_database_url(url, default_test_db="unused"))

        assert result.drivername == "postgresql+asyncpg"
        assert result.username == "myuser"
        assert result.password == "secret"
        assert result.host == "dbhost"
        assert result.port == 6543
        assert result.database == "testdb_gw2"


class TestCreateWorkerDatabase:
    """Tests for create_worker_database context manager."""

    @pytest.mark.anyio
    async def test_creates_default_db_without_xdist(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Without xdist, creates a database suffixed with default_test_db."""
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
        default_test_db = "no_xdist_default"
        expected_db = make_url(
            worker_database_url(DATABASE_URL, default_test_db=default_test_db)
        ).database

        async with create_worker_database(
            DATABASE_URL, default_test_db=default_test_db
        ) as url:
            assert make_url(url).database == expected_db

            engine = create_async_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": expected_db},
                )
                assert result.scalar() == 1
            await engine.dispose()

        engine = create_async_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": expected_db},
            )
            assert result.scalar() is None
        await engine.dispose()

    @pytest.mark.anyio
    async def test_creates_and_drops_worker_database(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Worker database exists inside the context and is dropped after."""
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw_test_create")
        expected_db = make_url(
            worker_database_url(DATABASE_URL, default_test_db="unused")
        ).database

        async with create_worker_database(DATABASE_URL) as url:
            assert make_url(url).database == expected_db

            engine = create_async_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": expected_db},
                )
                assert result.scalar() == 1
            await engine.dispose()

        engine = create_async_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": expected_db},
            )
            assert result.scalar() is None
        await engine.dispose()

    @pytest.mark.anyio
    async def test_cleans_up_stale_database(self, monkeypatch: pytest.MonkeyPatch):
        """A pre-existing worker database is dropped and recreated."""
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw_test_stale")
        expected_db = make_url(
            worker_database_url(DATABASE_URL, default_test_db="unused")
        ).database

        engine = create_async_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {expected_db}"))
            await conn.execute(text(f"CREATE DATABASE {expected_db}"))
        await engine.dispose()

        async with create_worker_database(DATABASE_URL) as url:
            assert make_url(url).database == expected_db

        engine = create_async_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": expected_db},
            )
            assert result.scalar() is None
        await engine.dispose()


# ---------------------------------------------------------------------------
# Local models for composite-PK coverage (own Base → own tables, isolated)
# ---------------------------------------------------------------------------


class _LocalBase(DeclarativeBase):
    pass


class _Group(_LocalBase):
    __tablename__ = "_test_groups"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50))


class _CompositeItem(_LocalBase):
    """Model with composite PK and a relationship — exercises the fallback path."""

    __tablename__ = "_test_composite_items"
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("_test_groups.id"), primary_key=True
    )
    item_code: Mapped[str] = mapped_column(String(50), primary_key=True)
    group: Mapped["_Group"] = relationship()


class TestGetPrimaryKey:
    """Unit tests for _get_primary_key — no DB needed."""

    def test_single_pk_returns_value(self):
        rid = uuid.UUID("00000000-0000-0000-0000-000000000001")
        role = Role(id=rid, name="x")
        assert _get_primary_key(role) == rid

    def test_composite_pk_all_set_returns_tuple(self):
        perm = Permission(subject="posts", action="read")
        assert _get_primary_key(perm) == ("posts", "read")

    def test_composite_pk_partial_none_returns_none(self):
        perm = Permission(subject=None, action="read")
        assert _get_primary_key(perm) is None

    def test_composite_pk_all_none_returns_none(self):
        perm = Permission(subject=None, action=None)
        assert _get_primary_key(perm) is None


class TestRelationshipLoadOptions:
    """Unit tests for _relationship_load_options — no DB needed."""

    def test_empty_for_model_with_no_relationships(self):
        assert _relationship_load_options(IntRole) == []

    def test_returns_options_for_model_with_relationships(self):
        opts = _relationship_load_options(User)
        assert len(opts) >= 1


class TestFixtureStrategies:
    """Integration tests covering INSERT, SKIP_EXISTING, empty fixture, no-rels model."""

    @pytest.mark.anyio
    async def test_empty_fixture_returns_empty_list(self, db_session: AsyncSession):
        """Fixture function returning [] produces an empty list."""
        registry = FixtureRegistry()

        @registry.register()
        def empty() -> list[Role]:
            return []

        local_ns: dict = {}
        register_fixtures(registry, local_ns, session_fixture="db_session")
        inner = local_ns["fixture_empty"].__wrapped__  # type: ignore[attr-defined]
        result = await inner(db_session=db_session)
        assert result == []

    @pytest.mark.anyio
    async def test_insert_strategy_no_relationships(self, db_session: AsyncSession):
        """INSERT strategy adds instances; model with no rels skips reload (line 135)."""
        registry = FixtureRegistry()

        @registry.register()
        def int_roles() -> list[IntRole]:
            return [IntRole(name="insert_role")]

        local_ns: dict = {}
        register_fixtures(
            registry,
            local_ns,
            session_fixture="db_session",
            strategy=LoadStrategy.INSERT,
        )
        inner = local_ns["fixture_int_roles"].__wrapped__  # type: ignore[attr-defined]
        result = await inner(db_session=db_session)
        assert len(result) == 1
        assert result[0].name == "insert_role"

    @pytest.mark.anyio
    async def test_skip_existing_inserts_new_record(self, db_session: AsyncSession):
        """SKIP_EXISTING inserts when the record does not yet exist."""
        registry = FixtureRegistry()
        role_id = uuid.uuid4()

        @registry.register()
        def new_roles() -> list[Role]:
            return [Role(id=role_id, name="skip_new")]

        local_ns: dict = {}
        register_fixtures(
            registry,
            local_ns,
            session_fixture="db_session",
            strategy=LoadStrategy.SKIP_EXISTING,
        )
        inner = local_ns["fixture_new_roles"].__wrapped__  # type: ignore[attr-defined]
        result = await inner(db_session=db_session)
        assert len(result) == 1
        assert result[0].id == role_id

    @pytest.mark.anyio
    async def test_skip_existing_returns_existing_record(
        self, db_session: AsyncSession
    ):
        """SKIP_EXISTING returns the existing DB record when PK already present."""
        role_id = uuid.uuid4()
        existing = Role(id=role_id, name="already_there")
        db_session.add(existing)
        await db_session.flush()

        registry = FixtureRegistry()

        @registry.register()
        def dup_roles() -> list[Role]:
            return [Role(id=role_id, name="should_not_overwrite")]

        local_ns: dict = {}
        register_fixtures(
            registry,
            local_ns,
            session_fixture="db_session",
            strategy=LoadStrategy.SKIP_EXISTING,
        )
        inner = local_ns["fixture_dup_roles"].__wrapped__  # type: ignore[attr-defined]
        result = await inner(db_session=db_session)
        assert len(result) == 1
        assert result[0].name == "already_there"

    @pytest.mark.anyio
    async def test_skip_existing_null_pk_inserts(self, db_session: AsyncSession):
        """SKIP_EXISTING with null PK (auto-increment) falls through to session.add()."""
        registry = FixtureRegistry()

        @registry.register()
        def auto_roles() -> list[IntRole]:
            return [IntRole(name="auto_int")]

        local_ns: dict = {}
        register_fixtures(
            registry,
            local_ns,
            session_fixture="db_session",
            strategy=LoadStrategy.SKIP_EXISTING,
        )
        inner = local_ns["fixture_auto_roles"].__wrapped__  # type: ignore[attr-defined]
        result = await inner(db_session=db_session)
        assert len(result) == 1
        assert result[0].name == "auto_int"


class TestReloadWithRelationshipsCompositePK:
    """Integration test for _reload_with_relationships composite-PK fallback."""

    @pytest.mark.anyio
    async def test_composite_pk_fallback_loads_relationships(self):
        """Models with composite PKs are reloaded per-instance via session.get()."""
        async with create_db_session(DATABASE_URL, _LocalBase) as session:
            group = _Group(id=uuid.uuid4(), name="g1")
            session.add(group)
            await session.flush()

            item = _CompositeItem(group_id=group.id, item_code="A")
            session.add(item)
            await session.flush()

            load_opts = _relationship_load_options(_CompositeItem)
            assert load_opts  # _CompositeItem has 'group' relationship

            reloaded = await _reload_with_relationships(session, [item], load_opts)
            assert len(reloaded) == 1
            reloaded_item = cast(_CompositeItem, reloaded[0])
            assert reloaded_item.group is not None
            assert reloaded_item.group.name == "g1"
