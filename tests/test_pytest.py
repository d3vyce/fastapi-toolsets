"""Tests for ``fastapi_toolsets.pytest``: generated fixtures, clients and databases."""

import uuid
from collections.abc import Callable
from typing import Any, cast

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import AsyncClient
from sqlalchemy import ForeignKey, String, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from fastapi_toolsets.db import transaction
from fastapi_toolsets.db.testing import create_database
from fastapi_toolsets.fixtures import Context, FixtureRegistry, LoadStrategy
from fastapi_toolsets.fixtures.utils import (
    _relationship_load_options,
    _reload_with_relationships,
)
from fastapi_toolsets.pytest import (
    create_async_client,
    create_db_session,
    create_worker_database,
    register_fixtures,
    worker_database_url,
)
from fastapi_toolsets.pytest.utils import (
    _override_layers,
    _pop_overrides,
    _push_overrides,
)

from .conftest import (
    DATABASE_URL,
    Base,
    IntRole,
    Role,
    RoleCrud,
    User,
    UserCrud,
    database_exists,
    drop_database,
)

test_registry = FixtureRegistry()

# Fixed UUIDs for test fixtures to allow consistent assertions
ROLE_ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-000000001000")
ROLE_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000001001")
USER_ADMIN_ID = uuid.UUID("00000000-0000-0000-0000-000000002000")
USER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000002001")
USER_EXTRA_ID = uuid.UUID("00000000-0000-0000-0000-000000002002")
ROLE_SKIP_ID = uuid.UUID("00000000-0000-0000-0000-000000001002")


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


def _dep(value: str) -> Callable[[], Any]:
    """An async dependency returning *value*, distinct per call."""

    async def dependency() -> str:
        return value

    return dependency


def _app_with(original: Callable[..., Any]) -> FastAPI:
    """An app whose ``/dep`` route returns what *original* resolves to."""
    app = FastAPI()

    @app.get("/dep")
    async def dep_endpoint(value: str = Depends(original)):
        return {"value": value}

    return app


async def _value(client: AsyncClient) -> str:
    return (await client.get("/dep")).json()["value"]


async def _generated_fixture(
    rows: Callable[[], list[Any]], strategy: LoadStrategy
) -> Callable[..., Any]:
    """The inner function of the pytest fixture the plugin builds for *rows*."""
    registry = FixtureRegistry()
    registry.register(name="entries")(rows)
    namespace: dict[str, Any] = {}
    register_fixtures(registry, namespace, strategy=strategy)
    return namespace["fixture_entries"].__wrapped__  # type: ignore[attr-defined]


class TestGeneratedFixtures:
    """``register_fixtures`` turns registry entries into pytest fixtures."""

    def test_fixtures_added_to_namespace(self):
        for name in ("fixture_roles", "fixture_users", "fixture_extra_users"):
            assert callable(globals()[name])

    @pytest.mark.anyio
    async def test_fixture_loads_rows_with_dependencies_and_relationships(
        self,
        db_session: AsyncSession,
        fixture_roles: list[Role],
        fixture_users: list[User],
    ):
        """A fixture loads its dependencies first and returns usable instances."""
        assert [r.name for r in fixture_roles] == ["plugin_admin", "plugin_user"]
        assert len(await RoleCrud.get_multi(db_session)) == 2
        assert await UserCrud.count(db_session) == 2

        admin = next(u for u in fixture_users if u.id == USER_ADMIN_ID)
        assert isinstance(admin, User)
        assert admin.username == "plugin_admin"
        assert admin.role is not None and admin.role.name == "plugin_admin"

        users = await UserCrud.get_multi(db_session, order_by=User.username)
        assert [u.username for u in users] == ["plugin_admin", "plugin_user"]

    @pytest.mark.anyio
    async def test_chained_dependencies(
        self, db_session: AsyncSession, fixture_extra_users: list[User]
    ):
        """extra_users -> users -> roles are all loaded."""
        assert len(fixture_extra_users) == 1
        assert await RoleCrud.count(db_session) == 2
        assert await UserCrud.count(db_session) == 3


class TestLoadStrategies:
    """The generated fixture honours the registry's load strategy."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("strategy", "rows", "expected"),
        [
            (LoadStrategy.MERGE, list, []),
            (
                LoadStrategy.INSERT,
                lambda: [IntRole(name="insert_role")],
                ["insert_role"],
            ),
            (
                LoadStrategy.SKIP_EXISTING,
                lambda: [Role(id=ROLE_SKIP_ID, name="skip_new")],
                ["skip_new"],
            ),
            (
                LoadStrategy.SKIP_EXISTING,
                lambda: [IntRole(name="auto_int")],
                ["auto_int"],
            ),
        ],
        ids=["merge/empty", "insert/no_relationships", "skip/new_pk", "skip/null_pk"],
    )
    async def test_returns_loaded_rows(
        self,
        db_session: AsyncSession,
        strategy: LoadStrategy,
        rows: Callable[[], list[Any]],
        expected: list[str],
    ):
        inner = await _generated_fixture(rows, strategy)

        result = await inner(db_session=db_session)

        assert [r.name for r in result] == expected

    @pytest.mark.anyio
    async def test_skip_existing_returns_the_existing_row(
        self, db_session: AsyncSession
    ):
        """A row already present is neither overwritten nor missing from the result."""
        role_id = uuid.uuid4()
        db_session.add(Role(id=role_id, name="already_there"))
        await db_session.flush()

        def dup_roles() -> list[Role]:
            return [Role(id=role_id, name="should_not_overwrite")]

        inner = await _generated_fixture(dup_roles, LoadStrategy.SKIP_EXISTING)
        result = await inner(db_session=db_session)

        assert [r.name for r in result] == ["already_there"]


class TestFixtureUtils:
    """Helpers from ``fixtures.utils`` the plugin relies on."""

    def test_relationship_load_options(self):
        assert _relationship_load_options(IntRole) == []
        assert len(_relationship_load_options(User)) >= 1

    @pytest.mark.anyio
    async def test_composite_pk_reload_falls_back_to_session_get(self):
        """Models with composite PKs are reloaded per-instance via session.get()."""
        async with create_db_session(DATABASE_URL, _LocalBase) as session:
            group = _Group(id=uuid.uuid4(), name="g1")
            session.add(group)
            await session.flush()
            item = _CompositeItem(group_id=group.id, item_code="A")
            session.add(item)
            await session.flush()

            load_opts = _relationship_load_options(_CompositeItem)
            assert load_opts
            reloaded = await _reload_with_relationships(session, [item], load_opts)

            assert len(reloaded) == 1
            assert cast(_CompositeItem, reloaded[0]).group.name == "g1"


class TestCreateAsyncClient:
    """``create_async_client`` wraps the app in an httpx client."""

    @pytest.mark.anyio
    async def test_client_talks_to_the_app_and_closes(self):
        """Requests reach the app, kwargs reach httpx, and exiting closes the client."""
        app = FastAPI()

        @app.get("/headers")
        async def headers_endpoint(request: Request):
            return {"x-custom": request.headers.get("x-custom")}

        async with create_async_client(
            app, base_url="http://custom", headers={"X-Custom": "sentinel"}
        ) as client:
            assert isinstance(client, AsyncClient)
            assert str(client.base_url) == "http://custom"
            response = await client.get("/headers")
            assert response.status_code == 200
            assert response.json() == {"x-custom": "sentinel"}

        assert client.is_closed


class TestDependencyOverrides:
    """Overrides are layered per client and put back exactly as they were."""

    @pytest.mark.anyio
    @pytest.mark.parametrize("app_level", [False, True], ids=["absent", "pre_existing"])
    async def test_override_applied_then_restored(self, app_level: bool):
        """The client's override wins while open; whatever was there before returns."""
        original = _dep("original")
        app_dep = _dep("app-level")
        app = _app_with(original)
        if app_level:
            app.dependency_overrides[original] = app_dep

        async with create_async_client(
            app, dependency_overrides={original: _dep("client-level")}
        ) as client:
            assert await _value(client) == "client-level"

        if app_level:
            assert app.dependency_overrides[original] is app_dep
        else:
            assert original not in app.dependency_overrides

    @pytest.mark.anyio
    async def test_nested_clients_same_key(self):
        """An inner client leaving does not strip the outer client's override."""
        original = _dep("original")
        app = _app_with(original)

        async with create_async_client(
            app, dependency_overrides={original: _dep("outer")}
        ) as outer:
            async with create_async_client(
                app, dependency_overrides={original: _dep("inner")}
            ) as inner:
                assert await _value(inner) == "inner"
            assert await _value(outer) == "outer"

        assert original not in app.dependency_overrides

    @pytest.mark.anyio
    async def test_nested_clients_different_keys(self):
        """Nested clients overriding different keys each clean up only their own."""
        dep_a, dep_b = _dep("a"), _dep("b")
        app = FastAPI()

        @app.get("/ab")
        async def ab_endpoint(a: str = Depends(dep_a), b: str = Depends(dep_b)):
            return {"a": a, "b": b}

        async with create_async_client(
            app, dependency_overrides={dep_a: _dep("override-a")}
        ) as outer:
            async with create_async_client(
                app, dependency_overrides={dep_b: _dep("override-b")}
            ) as inner:
                assert (await inner.get("/ab")).json() == {
                    "a": "override-a",
                    "b": "override-b",
                }
            assert dep_b not in app.dependency_overrides
            assert (await outer.get("/ab")).json() == {"a": "override-a", "b": "b"}

        assert dep_a not in app.dependency_overrides

    @pytest.mark.anyio
    @pytest.mark.parametrize("app_level", [False, True], ids=["absent", "pre_existing"])
    async def test_interleaved_clients(self, app_level: bool):
        """Overlapping (non-nested) lifetimes neither strip nor resurrect overrides.

        A is opened before B but closed first, so there is no LIFO order to rely
        on: B must keep working, and once both are gone the key must be back to
        the app-level override (or absent), not to A's or B's.
        """
        original = _dep("real")
        app = _app_with(original)
        if app_level:
            app.dependency_overrides[original] = _dep("app-level")

        a_ctx = create_async_client(app, dependency_overrides={original: _dep("a")})
        client_a = await a_ctx.__aenter__()
        assert await _value(client_a) == "a"
        b_ctx = create_async_client(app, dependency_overrides={original: _dep("b")})
        client_b = await b_ctx.__aenter__()
        try:
            assert await _value(client_b) == "b"
            await a_ctx.__aexit__(None, None, None)
            assert await _value(client_b) == "b"
        finally:
            await b_ctx.__aexit__(None, None, None)

        async with create_async_client(app) as client:
            assert await _value(client) == ("app-level" if app_level else "real")
        assert (original in app.dependency_overrides) is app_level

    @pytest.mark.anyio
    async def test_overrides_restored_when_client_construction_fails(self):
        original = _dep("original")
        app = _app_with(original)

        with pytest.raises(TypeError):
            async with create_async_client(
                app,
                dependency_overrides={original: _dep("overridden")},
                not_a_real_kwarg=object(),
            ):
                pass  # pragma: no cover

        assert original not in app.dependency_overrides

    def test_pop_ignores_unknown_app_key_and_owner(self):
        """Popping what was never pushed is a no-op and leaves other layers intact."""
        app = FastAPI()
        key, other, override = _dep("key"), _dep("other"), _dep("override")
        owner, stranger = object(), object()

        _pop_overrides(app, {key: override}, owner)
        assert app not in _override_layers

        _push_overrides(app, {key: override}, owner)
        _pop_overrides(app, {other: override}, owner)
        _pop_overrides(app, {key: override}, stranger)
        assert app.dependency_overrides[key] is override

        _pop_overrides(app, {key: override}, owner)
        assert key not in app.dependency_overrides
        assert app not in _override_layers


class TestCreateDbSession:
    """``create_db_session`` builds tables around a session and tears them down."""

    @pytest.mark.anyio
    async def test_session_is_usable_and_kwargs_forwarded(self):
        role_id = uuid.uuid4()
        async with create_db_session(
            DATABASE_URL,
            Base,
            engine_kwargs={"pool_pre_ping": True},
            session_kwargs={"autoflush": False},
        ) as session:
            assert isinstance(session, AsyncSession)
            assert session.autoflush is False
            assert (await session.execute(select(Role))).all() == []

            session.add(Role(id=role_id, name="test_helper_role"))
            await session.commit()
            fetched = await session.scalar(select(Role).where(Role.id == role_id))
            assert fetched is not None and fetched.name == "test_helper_role"

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("drop_tables", "cleanup", "survives"),
        [(True, False, False), (False, False, True), (False, True, False)],
        ids=["drop", "keep", "keep+cleanup"],
    )
    async def test_rows_after_exit(
        self, drop_tables: bool, cleanup: bool, survives: bool
    ):
        """Rows survive only when tables are neither dropped nor cleaned up."""
        role_id = uuid.uuid4()
        async with create_db_session(
            DATABASE_URL, Base, drop_tables=drop_tables, cleanup=cleanup
        ) as session:
            session.add(Role(id=role_id, name="row"))
            await session.commit()

        async with create_db_session(DATABASE_URL, Base) as session:
            fetched = await session.scalar(select(Role).where(Role.id == role_id))
            assert (fetched is not None) is survives

    @pytest.mark.anyio
    async def test_transaction_commits_visible_to_separate_session(self):
        """Data written via transaction() is committed, not held in a savepoint.

        If create_db_session used ``db.session()``, auto-begin would force
        ``transaction()`` into savepoints and fixture rows would never commit.
        """
        role_id = uuid.uuid4()
        async with create_db_session(DATABASE_URL, Base, drop_tables=False) as session:
            async with transaction(session):
                session.add(Role(id=role_id, name="visible_to_other_session"))

            other_engine = create_async_engine(DATABASE_URL)
            try:
                async with async_sessionmaker(other_engine)() as other:
                    fetched = await other.scalar(select(Role).where(Role.id == role_id))
                    assert fetched is not None
                    assert fetched.name == "visible_to_other_session"
            finally:
                await other_engine.dispose()

        async with create_db_session(DATABASE_URL, Base, drop_tables=True):
            pass


@pytest.fixture
def xdist_worker(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """``PYTEST_XDIST_WORKER`` set to the parameter, or unset for ``None``."""
    if request.param is None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    else:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", request.param)
    return request.param


class TestWorkerDatabaseUrl:
    """``worker_database_url`` names the database after the xdist worker."""

    @pytest.mark.parametrize(
        ("xdist_worker", "default", "prefix", "expected"),
        [
            (None, "fallback", None, "fallback"),
            ("gw2", "unused", None, "gw2"),
            ("gw0", "unused", "myapp", "myapp_gw0"),
            (None, "test", "myapp", "myapp_test"),
        ],
        ids=["no_xdist", "xdist", "xdist+prefix", "no_xdist+prefix"],
        indirect=["xdist_worker"],
    )
    @pytest.mark.usefixtures("xdist_worker")
    def test_database_name(self, default: str, prefix: str | None, expected: str):
        """Only the database name changes; the other URL components are kept."""
        url = "postgresql+asyncpg://myuser:secret@dbhost:6543/testdb"

        result = make_url(
            worker_database_url(url, default_test_db=default, prefix=prefix)
        )

        assert result.database == expected
        assert (result.drivername, result.username, result.password) == (
            "postgresql+asyncpg",
            "myuser",
            "secret",
        )
        assert (result.host, result.port) == ("dbhost", 6543)


class TestCreateWorkerDatabase:
    """``create_worker_database`` creates the worker database and drops it after."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("xdist_worker", "kwargs", "expected"),
        [
            (None, {"default_test_db": "no_xdist_default"}, "no_xdist_default"),
            ("gw_test_create", {}, "gw_test_create"),
            ("gw_prefix", {"prefix": "pfx"}, "pfx_gw_prefix"),
            ("gw_explicit_srv", {"server_url": DATABASE_URL}, "gw_explicit_srv"),
        ],
        ids=["no_xdist", "xdist", "prefix", "explicit_server_url"],
        indirect=["xdist_worker"],
    )
    @pytest.mark.usefixtures("xdist_worker")
    async def test_creates_then_drops(self, kwargs: dict[str, Any], expected: str):
        async with create_worker_database(DATABASE_URL, **kwargs) as url:
            assert make_url(url).database == expected
            assert await database_exists(expected)

        assert not await database_exists(expected)

    @pytest.mark.anyio
    async def test_works_when_database_url_db_does_not_exist(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Regression: the DDL engine must not connect to database_url's own database."""
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw_noexist")
        nonexistent_url = (
            make_url(DATABASE_URL)
            .set(database="no_such_db")
            .render_as_string(hide_password=False)
        )

        async with create_worker_database(nonexistent_url) as url:
            assert make_url(url).database == "gw_noexist"
            assert await database_exists("gw_noexist")

        assert not await database_exists("gw_noexist")

    @pytest.mark.anyio
    async def test_stale_database_is_replaced(self, monkeypatch: pytest.MonkeyPatch):
        """A pre-existing worker database is dropped and recreated."""
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw_test_stale")
        await drop_database("gw_test_stale")
        await create_database("gw_test_stale", server_url=DATABASE_URL)

        async with create_worker_database(DATABASE_URL) as url:
            assert make_url(url).database == "gw_test_stale"

        assert not await database_exists("gw_test_stale")

    @pytest.mark.anyio
    async def test_drops_database_with_active_connections(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """DROP DATABASE succeeds even when a connection is still open to it."""
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw_active_conn")

        async with create_worker_database(DATABASE_URL) as url:
            lingering_engine = create_async_engine(url)
            async with lingering_engine.connect():
                pass  # connection returned to pool but engine not disposed

        # Without WITH (FORCE) the DROP above raises; reaching here means it worked.
        assert not await database_exists("gw_active_conn")
        await lingering_engine.dispose()
