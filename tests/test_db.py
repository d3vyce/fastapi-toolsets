"""Tests for ``fastapi_toolsets.db``: the ``Database`` facade, locks, M2M, watching."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from typing import Any, Self
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Depends, FastAPI, Security
from fastapi.responses import JSONResponse, StreamingResponse
from httpx import ASGITransport, AsyncClient
from pydantic import PostgresDsn
from sqlalchemy import (
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Table,
    Uuid,
    event,
    select,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    AsyncTransaction,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from starlette.requests import Request

from fastapi_toolsets.db import (
    Database,
    LockMode,
    advisory_lock,
    lock_tables,
    m2m_add,
    m2m_remove,
    m2m_set,
    transaction,
    wait_for_row_change,
)
from fastapi_toolsets.db.core import _CommitOnResponseMiddleware
from fastapi_toolsets.db.testing import cleanup_tables, create_database
from fastapi_toolsets.exceptions import (
    LockTimeoutError,
    NotFoundError,
    PoolExhaustedError,
    init_exceptions_handlers,
)
from fastapi_toolsets.pytest import create_async_client

from .conftest import (
    DATABASE_URL,
    Base,
    Post,
    Role,
    RoleCreate,
    RoleCrud,
    Tag,
    User,
    UserCrud,
    created_tables,
    database_exists,
    drop_database,
    post_tags,
    raises_if,
)


def _make_request() -> Request:
    """Minimal ASGI HTTP request for driving the dependency by hand."""
    return Request({"type": "http", "headers": []})


async def _role_exists(session_maker: async_sessionmaker, name: str) -> bool:
    async with session_maker() as session:
        return await RoleCrud.first(session, [Role.name == name]) is not None


async def _granted_locks(session_maker: async_sessionmaker, mode: str) -> int:
    """Granted locks of PostgreSQL *mode* on ``roles``, seen from another session."""
    async with session_maker() as observer:
        result = await observer.execute(
            text(
                "SELECT count(*) FROM pg_locks l "
                "JOIN pg_class c ON c.oid = l.relation "
                "WHERE c.relname = 'roles' AND l.mode = :mode AND l.granted"
            ),
            {"mode": mode},
        )
        return result.scalar_one()


def _dependency(db: Database) -> AbstractAsyncContextManager[AsyncSession]:
    """``Depends(db)`` driven by hand, teardown included."""
    return asynccontextmanager(db)(_make_request())


def _lock_tables(db: Database) -> AbstractAsyncContextManager[AsyncSession]:
    return db.lock_tables([Role, User])


class TestConstruction:
    """``Database`` takes exactly one of url / engine and owns only what it builds."""

    @pytest.mark.parametrize(
        "build",
        [
            lambda engine: Database(),
            lambda engine: Database(DATABASE_URL, engine=engine),
            lambda engine: Database(engine=engine, pool_size=5),
            lambda engine: Database(
                engine=engine, connect_args={"server_settings": {}}
            ),
        ],
        ids=[
            "neither",
            "both",
            "engine-options-with-engine",
            "connect-args-with-engine",
        ],
    )
    @pytest.mark.anyio
    async def test_rejects_ambiguous_arguments(self, engine, build):
        with pytest.raises(TypeError):
            build(engine)

    @pytest.mark.parametrize(
        "url", [DATABASE_URL, PostgresDsn(DATABASE_URL)], ids=["str", "dsn"]
    )
    def test_url_mode_builds_and_owns_the_engine(self, url):
        """The URL reaches ``create_async_engine`` as a string, with connect_args."""
        connect_args = {"server_settings": {"application_name": "ft_test"}}
        with patch("fastapi_toolsets.db.core.create_async_engine") as mocked:
            mocked.return_value = MagicMock()
            db = Database(url, connect_args=connect_args)

        (passed_url,), kwargs = mocked.call_args.args, mocked.call_args.kwargs
        assert isinstance(passed_url, str)
        assert make_url(passed_url) == make_url(DATABASE_URL)
        assert kwargs == {"connect_args": connect_args}
        assert db.engine is mocked.return_value

    @pytest.mark.anyio
    async def test_engine_mode_borrows_the_engine(self, engine):
        """A borrowed engine is used as is; each instance keeps its own session."""
        a, b = Database(engine=engine), Database(engine=engine)
        request = _make_request()

        assert a.engine is engine
        async with (
            asynccontextmanager(a)(request) as first,
            asynccontextmanager(b)(request) as second,
        ):
            assert first is not second

    @pytest.mark.parametrize("owned", [True, False], ids=["owned", "borrowed"])
    @pytest.mark.anyio
    async def test_lifespan_disposes_only_an_owned_engine(self, engine, owned):
        db = Database(DATABASE_URL) if owned else Database(engine=engine)
        # ``AsyncEngine.dispose`` is read-only on the instance, so patch the class.
        with patch.object(AsyncEngine, "dispose", new=AsyncMock()) as disposed:
            async with db.lifespan(None):
                disposed.assert_not_awaited()
            assert disposed.await_count == (1 if owned else 0)
        await db.engine.dispose()

    @pytest.mark.anyio
    async def test_install_disposes_after_the_apps_own_lifespan(self):
        events: list[str] = []

        @asynccontextmanager
        async def user_lifespan(app):
            events.append("startup")
            yield
            events.append("shutdown")

        db = Database(DATABASE_URL)
        app = FastAPI(lifespan=user_lifespan)
        db.install(app)

        with patch.object(AsyncEngine, "dispose", new=AsyncMock()) as disposed:
            async with app.router.lifespan_context(app):
                assert events == ["startup"]
                disposed.assert_not_awaited()
            assert events == ["startup", "shutdown"]
            disposed.assert_awaited_once()
        await db.engine.dispose()

    @pytest.mark.anyio
    async def test_lifespan_and_install_together_dispose_once(self):
        db = Database(DATABASE_URL)
        app = FastAPI(lifespan=db.lifespan)
        db.install(app)

        with patch.object(AsyncEngine, "dispose", new=AsyncMock()) as disposed:
            async with app.router.lifespan_context(app):
                pass
            disposed.assert_awaited_once()
        await db.engine.dispose()


class TestSessionExecutionOptions:
    """``session_execution_options`` apply to every statement of the sessions."""

    @pytest.mark.anyio
    async def test_options_reach_the_flush_and_the_queries(self, engine):
        # The table exists only in the tenant schema, so a statement that
        # missed the translation would fail.
        tenant = {None: "tenant_a"}
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS tenant_a CASCADE"))
            await conn.execute(text("CREATE SCHEMA tenant_a"))
            tenant_conn = await conn.execution_options(schema_translate_map=tenant)
            await tenant_conn.run_sync(Base.metadata.tables["roles"].create)
        db = Database(
            engine=engine, session_execution_options={"schema_translate_map": tenant}
        )
        try:
            async with db.begin() as session:
                session.add(Role(name="tenant-role"))
            async with db.session() as session:
                names = (await session.execute(select(Role.name))).scalars().all()
            async with engine.connect() as conn:
                stored = await conn.execute(text("SELECT name FROM tenant_a.roles"))
                stored_names = stored.scalars().all()
        finally:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA tenant_a CASCADE"))

        assert names == stored_names == ["tenant-role"]


class TestSessionLifecycle:
    """What ``Depends(db)``, ``session()``, ``begin()`` and ``lock_tables()`` commit."""

    @pytest.mark.anyio
    async def test_dependency_yields_one_session_per_request(self, engine):
        """The session is in a transaction and stashed on the request; a second
        resolution borrows it and must not close what it borrowed (1c806cc)."""
        db = Database(engine=engine)
        request = _make_request()

        owner_gen = db(request)
        owner = await anext(owner_gen)
        assert isinstance(owner, AsyncSession) and owner.in_transaction()
        borrower_gen = db(request)
        assert await anext(borrower_gen) is owner

        with pytest.raises(StopAsyncIteration):  # teardown runs borrower-first
            await anext(borrower_gen)
        assert owner.in_transaction()
        with pytest.raises(StopAsyncIteration):
            await anext(owner_gen)

    @pytest.mark.parametrize(
        "open_session",
        [_dependency, Database.session, Database.begin, _lock_tables],
        ids=["dependency", "session", "begin", "lock_tables"],
    )
    @pytest.mark.anyio
    async def test_commits_pending_work_on_clean_exit(
        self, engine, session_maker, open_session
    ):
        """``install()`` is per-``Database`` but the commit is per-request: the
        dependency commits itself when no middleware ran for the request."""
        db = Database(engine=engine)
        db.install(FastAPI())

        async with open_session(db) as session:
            session.add(Role(name="committed"))

        assert await _role_exists(session_maker, "committed")

    @pytest.mark.parametrize(
        "open_session", [_dependency, Database.session], ids=["dependency", "session"]
    )
    @pytest.mark.anyio
    async def test_skips_the_commit_when_the_block_already_committed(
        self, engine, session_maker, open_session
    ):
        db = Database(engine=engine)

        async with open_session(db) as session:
            session.add(Role(name="self_committed"))
            await session.commit()
            assert not session.in_transaction()

        assert await _role_exists(session_maker, "self_committed")

    @pytest.mark.parametrize(
        "open_session",
        [_dependency, Database.session, Database.begin, _lock_tables],
        ids=["dependency", "session", "begin", "lock_tables"],
    )
    @pytest.mark.anyio
    async def test_rolls_back_on_error(self, engine, session_maker, open_session):
        db = Database(engine=engine)

        with pytest.raises(ValueError, match="boom"):
            async with open_session(db) as session:
                session.add(Role(name="ghost"))
                await session.flush()
                raise ValueError("boom")

        assert not await _role_exists(session_maker, "ghost")

    @pytest.mark.parametrize(
        "open_session",
        [_dependency, Database.session, _lock_tables],
        ids=["dependency", "session", "lock_tables"],
    )
    @pytest.mark.anyio
    async def test_pool_exhaustion_raises_pool_exhausted_error(self, open_session):
        """Connections are acquired eagerly, so an exhausted pool fails on entry;
        the dedicated lock session needs a second connection, so it fails too."""
        db = Database(DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=0.1)
        try:
            async with db.session():  # checks out the single available connection
                with pytest.raises(PoolExhaustedError):
                    async with open_session(db):
                        pass  # pragma: no cover
        finally:
            await db.engine.dispose()


class TestTransaction:
    """``transaction()`` is a top-level transaction, or a savepoint inside one."""

    @pytest.mark.parametrize("fail", [False, True], ids=["commit", "rollback"])
    @pytest.mark.anyio
    async def test_top_level_block_commits_or_rolls_back(self, db_session, fail):
        with raises_if(ValueError, fail):
            async with transaction(db_session):
                db_session.add(Role(name="tx_role"))
                await db_session.flush()
                if fail:
                    raise ValueError("boom")

        found = await RoleCrud.first(db_session, [Role.name == "tx_role"])
        assert (found is None) is fail

    @pytest.mark.anyio
    async def test_nested_block_is_a_savepoint(self, db_session):
        """An inner failure loses only its own work; the outer block commits the rest."""
        async with transaction(db_session):
            db_session.add(Role(name="outer"))
            await db_session.flush()

            with pytest.raises(ValueError, match="boom"):
                async with transaction(db_session):
                    db_session.add(Role(name="rolled_back"))
                    await db_session.flush()
                    raise ValueError("boom")

            async with transaction(db_session):
                db_session.add(Role(name="inner"))

        names = {role.name for role in await RoleCrud.get_multi(db_session)}
        assert names == {"outer", "inner"}


_PG_LOCK_NAMES = {
    LockMode.ACCESS_SHARE: "AccessShareLock",
    LockMode.ROW_SHARE: "RowShareLock",
    LockMode.ROW_EXCLUSIVE: "RowExclusiveLock",
    LockMode.SHARE_UPDATE_EXCLUSIVE: "ShareUpdateExclusiveLock",
    LockMode.SHARE: "ShareLock",
    LockMode.SHARE_ROW_EXCLUSIVE: "ShareRowExclusiveLock",
    LockMode.EXCLUSIVE: "ExclusiveLock",
    LockMode.ACCESS_EXCLUSIVE: "AccessExclusiveLock",
}


class TestLockTables:
    """Table locks on a dedicated session (committed at exit) or the caller's own."""

    @pytest.mark.parametrize("mode", list(LockMode), ids=[m.name for m in LockMode])
    @pytest.mark.anyio
    async def test_holds_the_lock_for_the_block_and_commits_it(
        self, engine, session_maker, mode
    ):
        """Every ``LockMode`` is its PostgreSQL lock, released by the commit at exit."""
        db = Database(engine=engine)
        pg_mode = _PG_LOCK_NAMES[mode]

        async with db.lock_tables([Role, User], mode=mode) as session:
            assert await _granted_locks(session_maker, pg_mode) == 1
            session.add(Role(name="locked_role"))

        assert await _granted_locks(session_maker, pg_mode) == 0
        assert await _role_exists(session_maker, "locked_role")

    @pytest.mark.parametrize("on_caller", [False, True], ids=["dedicated", "caller"])
    @pytest.mark.anyio
    async def test_lock_timeout_leaves_the_callers_transaction_usable(
        self, engine, session_maker, on_caller
    ):
        """``LockTimeoutError`` aborts at most the lock's savepoint, so the caller's
        pending work survives and commits."""
        db = Database(engine=engine)
        async with db.session() as session:
            # Pending work on another table, so the caller does not itself
            # conflict with the lock it is about to wait for.
            session.add(Tag(name="survives_lock_timeout"))
            await session.flush()

            async with db.lock_tables([Role], mode=LockMode.EXCLUSIVE):
                with pytest.raises(LockTimeoutError):
                    async with db.lock_tables(
                        [Role], session=session if on_caller else None, timeout="100ms"
                    ):
                        pass  # pragma: no cover

            assert (await session.execute(select(1))).scalar_one() == 1
            await session.commit()

        async with session_maker() as verify:
            found = await verify.execute(
                select(Tag).where(Tag.name == "survives_lock_timeout")
            )
            assert found.scalar_one_or_none() is not None

    @pytest.mark.parametrize("both", [True, False], ids=["both", "neither"])
    def test_requires_exactly_one_of_session_maker_or_session(self, both):
        maker, session = (
            (async_sessionmaker(), AsyncSession()) if both else (None, None)
        )

        with pytest.raises(TypeError, match="exactly one"):
            lock_tables(maker, [Role], session=session)

    @pytest.mark.anyio
    async def test_caller_session_locks_and_writes_on_one_connection(
        self, session_maker
    ):
        """The block writes through the session holding the lock, so a locking
        request completes on a pool of one connection (TOOLS-11)."""
        db = Database(DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=0.1)
        try:
            async with db.session() as session:
                async with db.lock_tables(
                    [Role], session=session, mode=LockMode.EXCLUSIVE
                ) as locked:
                    assert locked is session
                    session.add(Role(name="caller_lock_role"))
                    await session.flush()
                await session.commit()
        finally:
            await db.engine.dispose()

        assert await _role_exists(session_maker, "caller_lock_role")

    @pytest.mark.anyio
    async def test_caller_session_keeps_the_timeout_and_the_lock_until_it_ends(
        self, engine, session_maker
    ):
        """``lock_timeout`` is set before the savepoint flushes pending changes (that
        flush can block on the table just like the LOCK), and the lock outlives
        the block until the caller's transaction ends."""
        db = Database(engine=engine)
        async with db.session() as session:
            session.add(Role(name="flushed_by_table_lock"))

            async with db.lock_tables(
                [Role], session=session, mode=LockMode.EXCLUSIVE, timeout="250ms"
            ):
                timeout = await session.execute(text("SHOW lock_timeout"))
                assert timeout.scalar_one() == "250ms"
                assert not session.new  # flushed when the savepoint opened
                assert await _granted_locks(session_maker, "ExclusiveLock") == 1

            # Block exited, savepoint released, but the lock is still held.
            assert await _granted_locks(session_maker, "ExclusiveLock") == 1
            await session.commit()

        assert await _granted_locks(session_maker, "ExclusiveLock") == 0
        assert await _role_exists(session_maker, "flushed_by_table_lock")

    @pytest.mark.anyio
    async def test_caller_session_other_errors_propagate_unchanged(self, db_session):
        """A failed flush stays an IntegrityError, not a LockTimeoutError."""
        db_session.add(Role(name="duplicate_under_lock"))
        await db_session.commit()

        db_session.add(Role(name="duplicate_under_lock"))  # violates unique
        with pytest.raises(IntegrityError):
            async with lock_tables(None, [Role], session=db_session):
                pass  # pragma: no cover
        await db_session.rollback()


class TestAdvisoryLock:
    """Session-level locks (released at block exit) and ``xact`` ones (at tx end)."""

    @pytest.mark.parametrize("key", [1001, (7, 42)], ids=["int", "pair"])
    @pytest.mark.parametrize("xact", [False, True], ids=["session", "xact"])
    @pytest.mark.anyio
    async def test_exclusive_lock_turns_away_a_nowait_contender(
        self, session_maker, key, xact
    ):
        async with session_maker() as holder, session_maker() as contender:
            async with holder.begin(), contender.begin():
                async with advisory_lock(holder, key, xact=xact) as acquired:
                    assert acquired is True
                    async with advisory_lock(
                        contender, key, xact=xact, nowait=True
                    ) as taken:
                        assert taken is False

    @pytest.mark.parametrize("xact", [False, True], ids=["session", "xact"])
    @pytest.mark.anyio
    async def test_shared_lock_admits_concurrent_holders(self, session_maker, xact):
        async with session_maker() as s1, session_maker() as s2:
            async with s1.begin(), s2.begin():
                async with advisory_lock(s1, 1004, shared=True, xact=xact) as a1:
                    async with advisory_lock(
                        s2, 1004, shared=True, xact=xact, nowait=True
                    ) as a2:
                        assert (a1, a2) == (True, True)

    @pytest.mark.parametrize("xact", [False, True], ids=["session", "xact"])
    @pytest.mark.anyio
    async def test_timeout_raises_lock_timeout_error(self, session_maker, xact):
        async with session_maker() as holder, session_maker() as contender:
            async with holder.begin(), contender.begin():
                async with advisory_lock(holder, 1006, xact=xact):
                    with pytest.raises(LockTimeoutError):
                        async with advisory_lock(
                            contender, 1006, xact=xact, timeout="10ms"
                        ):
                            pass  # pragma: no cover

    @pytest.mark.anyio
    async def test_session_lock_is_released_at_block_exit(self, session_maker):
        """Released even though the holder's transaction is still open."""
        async with session_maker() as holder, session_maker() as contender:
            async with holder.begin(), contender.begin():
                async with advisory_lock(holder, 1005):
                    pass

                async with advisory_lock(contender, 1005, nowait=True) as taken:
                    assert taken is True

    @pytest.mark.parametrize(
        "end", [AsyncSession.commit, AsyncSession.rollback], ids=["commit", "rollback"]
    )
    @pytest.mark.anyio
    async def test_xact_lock_is_held_until_the_transaction_ends(
        self, session_maker, end
    ):
        async with session_maker() as holder, session_maker() as contender:
            async with advisory_lock(holder, 3001, xact=True):
                pass

            # Block exited, but the holder's transaction still has the lock.
            async with contender.begin():
                async with advisory_lock(
                    contender, 3001, xact=True, nowait=True
                ) as taken:
                    assert taken is False

            await end(holder)

            async with contender.begin():
                async with advisory_lock(
                    contender, 3001, xact=True, nowait=True
                ) as taken:
                    assert taken is True

    @pytest.mark.anyio
    async def test_acquire_does_not_flush_pending_changes(self, db_session):
        """Lock SQL runs under ``no_autoflush``: SQLAlchemy 2.1 autoflushes on raw
        ``text()`` too, which would flush the caller's pending ORM changes."""
        role = Role(name="not_flushed_by_lock")
        db_session.add(role)

        async with advisory_lock(db_session, 2001):
            assert role in db_session.new

    @pytest.mark.anyio
    async def test_xact_lock_serializes_check_then_insert(self, session_maker):
        """The next waiter sees the previous holder's insert, the point of TOOLS-8.

        A session-level lock is released at block exit, before the request
        session commits, so the second caller would read a stale "not present".
        """

        async def claim(session: AsyncSession) -> bool:
            """Insert the role only if it is not there yet; True if inserted."""
            async with advisory_lock(session, 3005, xact=True):
                existing = await RoleCrud.first(session, [Role.name == "claimed_once"])
                if existing is not None:
                    return False
                session.add(Role(name="claimed_once"))
                await session.flush()
                return True

        async with session_maker() as first, session_maker() as second:
            assert await claim(first) is True
            # ``second`` blocks on the lock until ``first`` commits, then sees
            # the row; without xact=True it would insert a duplicate.
            task = asyncio.create_task(claim(second))
            await asyncio.sleep(0.1)
            assert not task.done()
            await first.commit()
            assert await task is False
            await second.rollback()


class _Polls:
    """Counts the SELECTs completed on an engine, to sequence changes between polls."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.count = 0

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT"):
            self.count += 1

    def __enter__(self) -> Self:
        event.listen(self.engine.sync_engine, "after_cursor_execute", self._record)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self.engine.sync_engine, "after_cursor_execute", self._record)

    async def next(self) -> None:
        """Return once one more SELECT has completed."""
        seen = self.count
        while self.count <= seen:
            await asyncio.sleep(0.01)


_Change = Callable[[AsyncSession, _Polls], Awaitable[None]]


@asynccontextmanager
async def _changing_between_polls(
    engine: AsyncEngine, change: _Change
) -> AsyncIterator[None]:
    """Run *change* in its own session once the watcher's first poll completed."""
    with _Polls(engine) as polls:

        async def _run() -> None:
            await polls.next()
            async with async_sessionmaker(engine, expire_on_commit=False)() as other:
                await change(other, polls)

        task = asyncio.create_task(_run())
        try:
            yield
            await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def _connection_bound(
    engine: AsyncEngine,
) -> AsyncIterator[tuple[AsyncTransaction, AsyncSession]]:
    """A session over a connection whose outer transaction the caller owns."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        yield outer, session
        await outer.rollback()


def _failing_second_get() -> Any:
    """Patch ``AsyncSession.get`` so every call after the first errors in SQL."""
    original = AsyncSession.get
    calls = 0

    async def failing_get(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            await self.execute(text("SELECT 1 / 0"))
        return await original(self, *args, **kwargs)

    return patch.object(AsyncSession, "get", failing_get)


class TestWaitForRowChange:
    """Polling a row from a throwaway session bound to the caller's engine."""

    @pytest.mark.parametrize(
        ("columns", "expected"),
        [
            (None, ("watched", "new@test.com")),
            (["username"], ("renamed", "new@test.com")),
        ],
        ids=["any-column", "named-columns"],
    )
    @pytest.mark.anyio
    async def test_returns_the_row_once_a_watched_column_changes(
        self, db_session, engine, columns, expected
    ):
        """With ``columns``, a poll that sees only the email change keeps waiting."""
        user = User(username="watched", email="old@test.com")
        db_session.add(user)
        await db_session.commit()

        async def change(other: AsyncSession, polls: _Polls) -> None:
            same = await other.get_one(User, user.id)
            same.email = "new@test.com"
            await other.commit()
            if columns:
                await polls.next()
                same.username = "renamed"
                await other.commit()

        async with _changing_between_polls(engine, change):
            result = await wait_for_row_change(
                db_session, User, user.id, columns=columns, interval=0.05
            )

        assert (result.username, result.email) == expected

    @pytest.mark.anyio
    async def test_missing_row_raises_not_found(self, db_session, engine):
        """A row deleted before the call is not found, even one the caller holds."""
        role = Role(name="stale_role")
        db_session.add(role)
        await db_session.commit()
        async with async_sessionmaker(engine)() as other:
            await other.delete(await other.get_one(Role, role.id))
            await other.commit()

        with pytest.raises(NotFoundError, match="not found"):
            await wait_for_row_change(db_session, Role, role.id, interval=0.05)

    @pytest.mark.anyio
    async def test_row_deleted_while_polling_raises_not_found(self, db_session, engine):
        role = Role(name="doomed_role")
        db_session.add(role)
        await db_session.commit()

        async def delete(other: AsyncSession, polls: _Polls) -> None:
            await other.delete(await other.get_one(Role, role.id))
            await other.commit()

        with pytest.raises(NotFoundError, match="was deleted"):
            async with _changing_between_polls(engine, delete):
                await wait_for_row_change(db_session, Role, role.id, interval=0.05)

    @pytest.mark.anyio
    async def test_unbound_session_raises_type_error(self):
        with pytest.raises(TypeError, match="requires a session bound to an engine"):
            await wait_for_row_change(AsyncSession(), Role, uuid.uuid4())

    @pytest.mark.anyio
    async def test_times_out_without_a_change(self, db_session):
        role = Role(name="timeout_role")
        db_session.add(role)
        await db_session.commit()

        with pytest.raises(TimeoutError, match="No change detected"):
            await wait_for_row_change(
                db_session, Role, role.id, interval=0.05, timeout=0.2
            )

    @pytest.mark.anyio
    @pytest.mark.usefixtures("session_maker")
    async def test_detects_update_under_repeatable_read(self, engine):
        """Each poll is its own transaction, hence a fresh snapshot even when the
        caller's engine pins one per transaction."""
        rr_engine = engine.execution_options(isolation_level="REPEATABLE READ")
        factory = async_sessionmaker(rr_engine, expire_on_commit=False)
        async with factory() as setup:
            role = Role(name="rr_role")
            setup.add(role)
            await setup.commit()

        async def rename(other: AsyncSession, polls: _Polls) -> None:
            (await other.get_one(Role, role.id)).name = "rr_updated"
            await other.commit()

        async with factory() as caller:
            await caller.get(Role, role.id)  # pins a snapshot before the update
            async with _changing_between_polls(rr_engine, rename):
                result = await wait_for_row_change(
                    caller, Role, role.id, interval=0.05, timeout=2.0
                )

        assert result.name == "rr_updated"

    @pytest.mark.anyio
    async def test_does_not_disturb_the_ambient_transaction(self, db_session, engine):
        """The caller's open transaction stays usable once the watch returns (#339)."""
        role = Role(name="ambient_role")
        db_session.add(role)
        await db_session.commit()

        async def rename(other: AsyncSession, polls: _Polls) -> None:
            (await other.get_one(Role, role.id)).name = "ambient_updated"
            await other.commit()

        async with transaction(db_session):
            await db_session.get(Role, role.id)  # establishes the ambient transaction
            async with _changing_between_polls(engine, rename):
                result = await wait_for_row_change(
                    db_session, Role, role.id, interval=0.05, timeout=2.0
                )
            assert result.name == "ambient_updated"
            assert db_session.in_transaction()
            db_session.add(Role(name="added_within_ambient_tx"))

        assert await RoleCrud.first(
            db_session, [Role.name == "added_within_ambient_tx"]
        )

    @pytest.mark.anyio
    async def test_holds_neither_a_connection_nor_a_table_lock_between_polls(
        self, db_session, engine
    ):
        """Each poll ends its transaction, so while the watcher sleeps its connection
        is back in the pool and the watched table is not locked (#423)."""
        role = Role(name="release_role")
        db_session.add(role)
        await db_session.commit()

        small = create_async_engine(
            DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=1
        )
        caller = AsyncSession(bind=small)
        watch = asyncio.create_task(
            wait_for_row_change(caller, Role, role.id, interval=0.3, timeout=0.7)
        )
        try:
            await asyncio.sleep(0.15)
            async with small.connect() as conn:  # the pool's only connection is free
                assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
            async with engine.connect() as conn:
                await conn.execute(
                    text(
                        f"LOCK TABLE {Role.__tablename__} "
                        "IN ACCESS EXCLUSIVE MODE NOWAIT"
                    )
                )
                await conn.rollback()
            with pytest.raises(TimeoutError):
                await watch
        finally:
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
            await caller.close()
            await small.dispose()

    @pytest.mark.anyio
    async def test_survives_idle_in_transaction_session_timeout(self, db_session):
        """An ``idle_in_transaction_session_timeout`` shorter than the interval is
        harmless, because no transaction stays open across the sleep (#423)."""
        role = Role(name="idle_timeout_role")
        db_session.add(role)
        await db_session.commit()

        strict = create_async_engine(
            DATABASE_URL,
            connect_args={
                "server_settings": {"idle_in_transaction_session_timeout": "200"}
            },
        )
        caller = AsyncSession(bind=strict)

        async def rename(other: AsyncSession, polls: _Polls) -> None:
            await asyncio.sleep(0.8)  # several idle timeouts' worth of polling
            (await other.get_one(Role, role.id)).name = "idle_timeout_updated"
            await other.commit()

        try:
            async with _changing_between_polls(strict, rename):
                result = await wait_for_row_change(
                    caller, Role, role.id, interval=0.5, timeout=3.0
                )
            assert result.name == "idle_timeout_updated"
        finally:
            await caller.close()
            await strict.dispose()

    @pytest.mark.parametrize(
        ("poll_fails", "error"),
        [(False, TimeoutError), (True, DBAPIError)],
        ids=["timed-out", "failed-poll"],
    )
    @pytest.mark.anyio
    @pytest.mark.usefixtures("session_maker")
    async def test_connection_bound_caller_keeps_its_outer_transaction(
        self, engine, poll_fails, error
    ):
        """Polls join the caller's connection through a savepoint, so neither the
        polling nor a poll that errors rolls back the outer transaction (4341fd1)."""
        async with _connection_bound(engine) as (outer, session):
            role = Role(name="outer_tx_role")
            session.add(role)
            await session.commit()

            with _failing_second_get() if poll_fails else nullcontext():
                with pytest.raises(error):
                    await wait_for_row_change(
                        session, Role, role.id, interval=0.05, timeout=0.3
                    )

            assert outer.is_active
            assert await session.get(Role, role.id) is not None


class TestTestingHelpers:
    """``create_database`` and ``cleanup_tables`` from ``db.testing``."""

    @pytest.mark.anyio
    async def test_create_database_creates_it(self):
        name = "ft_test_db_created_by_db_tests"
        await drop_database(name)
        try:
            await create_database(name, server_url=DATABASE_URL)

            assert await database_exists(name)
        finally:
            await drop_database(name)

    @pytest.mark.anyio
    async def test_cleanup_tables_truncates_every_table(self, db_session):
        """Rows in every table go; a metadata without tables is a no-op."""
        role = Role(name="cleanup_role")
        db_session.add(role)
        await db_session.flush()
        db_session.add(
            User(username="cleanup", email="cleanup@test.com", role_id=role.id)
        )
        await db_session.commit()
        assert (await RoleCrud.count(db_session), await UserCrud.count(db_session)) == (
            1,
            1,
        )

        await cleanup_tables(db_session, Base)

        assert (await RoleCrud.count(db_session), await UserCrud.count(db_session)) == (
            0,
            0,
        )

        class EmptyBase(DeclarativeBase):
            pass

        await cleanup_tables(db_session, EmptyBase)


class _LocalBase(DeclarativeBase):
    pass


_comp_assoc = Table(
    "_comp_assoc",
    _LocalBase.metadata,
    Column("owner_id", Uuid, ForeignKey("_comp_owners.id"), primary_key=True),
    Column("item_group", String(50), primary_key=True),
    Column("item_code", String(50), primary_key=True),
    ForeignKeyConstraint(
        ["item_group", "item_code"],
        ["_comp_items.group_id", "_comp_items.item_code"],
    ),
)


class _CompOwner(_LocalBase):
    __tablename__ = "_comp_owners"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    items: Mapped[list["_CompItem"]] = relationship(secondary=_comp_assoc)


class _CompItem(_LocalBase):
    __tablename__ = "_comp_items"
    group_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    item_code: Mapped[str] = mapped_column(String(50), primary_key=True)


async def _post_and_tags(session: AsyncSession, *names: str) -> tuple[Post, list[Tag]]:
    """A flushed post and flushed tags named *names*, not yet associated."""
    user = User(username="author", email="author@test.com")
    session.add(user)
    await session.flush()
    post = Post(title="Post", author_id=user.id)
    tags = [Tag(name=name) for name in names]
    session.add_all([post, *tags])
    await session.flush()
    return post, tags


async def _tag_ids(session: AsyncSession, post: Post) -> set[uuid.UUID]:
    """The tag ids associated with *post*, read from the association table."""
    rows = await session.execute(
        select(post_tags.c.tag_id).where(post_tags.c.post_id == post.id)
    )
    return set(rows.scalars())


class TestM2M:
    """``m2m_add`` / ``m2m_remove`` / ``m2m_set`` write the association table directly."""

    @pytest.mark.parametrize("count", [0, 1, 3])
    @pytest.mark.anyio
    async def test_add_inserts_one_row_per_related(self, db_session, count):
        post, tags = await _post_and_tags(db_session, *[f"t{i}" for i in range(count)])

        async with transaction(db_session):
            await m2m_add(db_session, post, Post.tags, *tags)

        assert await _tag_ids(db_session, post) == {tag.id for tag in tags}

    @pytest.mark.parametrize(
        "ignore_conflicts", [False, True], ids=["raises", "ignored"]
    )
    @pytest.mark.anyio
    async def test_add_of_an_existing_association(self, db_session, ignore_conflicts):
        post, (tag,) = await _post_and_tags(db_session, "dup")
        async with transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag)

        with raises_if(IntegrityError, not ignore_conflicts):
            async with transaction(db_session):
                await m2m_add(
                    db_session, post, Post.tags, tag, ignore_conflicts=ignore_conflicts
                )

        assert await _tag_ids(db_session, post) == {tag.id}

    @pytest.mark.parametrize(
        ("remove", "left"),
        [((0,), (1, 2)), ((0, 2), (1,)), ((), (0, 1, 2)), ((3,), (0, 1, 2))],
        ids=["one", "several", "none", "never-associated"],
    )
    @pytest.mark.anyio
    async def test_remove_deletes_only_the_given_associations(
        self, db_session, remove, left
    ):
        post, tags = await _post_and_tags(db_session, "a", "b", "c", "d")

        async with transaction(db_session):
            await m2m_add(db_session, post, Post.tags, *tags[:3])  # "d" stays apart
            await m2m_remove(db_session, post, Post.tags, *[tags[i] for i in remove])

        assert await _tag_ids(db_session, post) == {tags[i].id for i in left}

    @pytest.mark.parametrize(
        ("before", "after"),
        [((0, 1), (2,)), ((0,), ()), ((), (0, 1))],
        ids=["replace", "clear", "from-empty"],
    )
    @pytest.mark.anyio
    async def test_set_replaces_the_whole_association_set(
        self, db_session, before, after
    ):
        post, tags = await _post_and_tags(db_session, "a", "b", "c")

        async with transaction(db_session):
            await m2m_add(db_session, post, Post.tags, *[tags[i] for i in before])
            await m2m_set(db_session, post, Post.tags, *[tags[i] for i in after])

        assert await _tag_ids(db_session, post) == {tags[i].id for i in after}

    @pytest.mark.parametrize(
        "helper", [m2m_add, m2m_remove, m2m_set], ids=["add", "remove", "set"]
    )
    @pytest.mark.anyio
    async def test_non_m2m_relationship_raises_type_error(self, helper):
        """Rejected before touching the database."""
        user = User(username="u", email="u@test.com")

        with pytest.raises(TypeError, match="Many-to-Many"):
            await helper(AsyncSession(), user, User.role, Role(name="r"))

    @pytest.mark.anyio
    async def test_remove_matches_a_composite_related_key_as_a_tuple(self, engine):
        async with (
            created_tables(engine, _LocalBase.metadata),
            async_sessionmaker(engine, expire_on_commit=False)() as session,
        ):
            owner = _CompOwner()
            item1 = _CompItem(group_id="g1", item_code="c1")
            item2 = _CompItem(group_id="g1", item_code="c2")
            session.add_all([owner, item1, item2])
            await session.flush()

            async with transaction(session):
                await m2m_add(session, owner, _CompOwner.items, item1, item2)
                await m2m_remove(session, owner, _CompOwner.items, item1)

            rows = await session.execute(
                select(_comp_assoc.c.item_group, _comp_assoc.c.item_code).where(
                    _comp_assoc.c.owner_id == owner.id
                )
            )
            assert rows.all() == [("g1", "c2")]


_STATE_ATTR = "test_db_session"


class _FakeSession:
    """Records commit() calls into a shared event log."""

    def __init__(self, events: list[str], *, in_txn: bool) -> None:
        self.events = events
        self._in_txn = in_txn

    def in_transaction(self) -> bool:
        return self._in_txn

    async def commit(self) -> None:
        self.events.append("COMMIT")
        self._in_txn = False


async def _respond(scope, receive, send) -> None:
    """An ASGI app sending a minimal response."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _receive():  # pragma: no cover - not exercised
    return {"type": "http.disconnect"}


async def _send(message) -> None:  # pragma: no cover - not exercised
    return None


class TestCommitMiddleware:
    """``_CommitOnResponseMiddleware`` on fake sessions: ordering and pass-through."""

    @pytest.mark.parametrize(
        ("in_txn", "expected"),
        [
            (True, ["COMMIT", "http.response.start", "http.response.body"]),
            (False, ["http.response.start", "http.response.body"]),
            (None, ["http.response.start", "http.response.body"]),
        ],
        ids=["in-transaction", "already-committed", "no-session"],
    )
    @pytest.mark.anyio
    async def test_commits_before_the_response_starts(self, in_txn, expected):
        """Commit and response messages land in *events* in the order they happen."""
        events: list[str] = []
        state = (
            {} if in_txn is None else {_STATE_ATTR: _FakeSession(events, in_txn=in_txn)}
        )

        async def send(message) -> None:
            events.append(message["type"])

        app = _CommitOnResponseMiddleware(_respond, state_attr=_STATE_ATTR)
        await app({"type": "http", "state": state}, _receive, send)

        assert events == expected

    @pytest.mark.anyio
    async def test_non_http_scope_passes_through(self):
        seen: list[dict] = []

        async def inner(scope, receive, send) -> None:
            seen.append(scope)

        app = _CommitOnResponseMiddleware(inner, state_attr=_STATE_ATTR)
        await app({"type": "lifespan"}, _receive, _send)

        assert seen == [{"type": "lifespan"}]


class _ProbeMiddleware:
    """Outer middleware that records, at response start, whether a row created
    in the request is already visible to a *separate* session."""

    def __init__(self, app, *, session_maker, name: str, result: dict) -> None:
        self.app = app
        self.session_maker = session_maker
        self.name = name
        self.result = result

    async def __call__(self, scope, receive, send):
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                self.result["visible_at_start"] = await _role_exists(
                    self.session_maker, self.name
                )
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _build_app(db: Database) -> FastAPI:
    """A FastAPI app wired with the Database dependency and commit middleware."""
    app = FastAPI()

    @app.post("/roles")
    async def create_role(
        body: RoleCreate, session: AsyncSession = Depends(db)
    ) -> dict:
        role = await RoleCrud.create(session, body)
        return {"id": str(role.id), "name": role.name}

    @app.post("/roles-then-boom")
    async def create_then_raise(
        body: RoleCreate, session: AsyncSession = Depends(db)
    ) -> dict:
        await RoleCrud.create(session, body)
        raise RuntimeError("boom after write")

    @app.post("/two-roles")
    async def create_two_roles(
        body: RoleCreate, session: AsyncSession = Depends(db)
    ) -> dict:
        # First write succeeds, second collides on the unique name and must
        # take the whole request transaction down with it.
        await RoleCrud.create(session, body)
        await RoleCrud.create(session, body)
        return {"ok": True}

    @app.post("/roles-self-commit")
    async def create_then_self_commit(
        body: RoleCreate, session: AsyncSession = Depends(db)
    ) -> dict:
        # Endpoint commits explicitly; the middleware must not double-commit or
        # error — it finds no open transaction and no-ops.
        await RoleCrud.create(session, body)
        await session.commit()
        return {"ok": True}

    async def _scoped_writer(
        body: RoleCreate, session: AsyncSession = Security(db, scopes=["roles:write"])
    ) -> int:
        # Security scopes give this a different dependency cache key than the
        # endpoint's plain ``Depends(db)``. Without borrowing it opens a second
        # session, and whichever one the middleware does not hold is discarded.
        await RoleCrud.create(session, RoleCreate(name=f"{body.name}_sub"))
        return id(session)

    @app.post("/roles-two-cache-keys")
    async def create_via_two_cache_keys(
        body: RoleCreate,
        sub_session_id: int = Depends(_scoped_writer),
        session: AsyncSession = Depends(db),
    ) -> dict:
        await RoleCrud.create(session, body)
        return {"same_session": sub_session_id == id(session)}

    async def _fn_writer(
        body: RoleCreate, session: AsyncSession = Depends(db, scope="function")
    ) -> None:
        # ``scope="function"`` unwinds before the response is sent, taking the
        # session with it — so the commit cannot be left to the middleware.
        await RoleCrud.create(session, RoleCreate(name=f"{body.name}_fn"))

    @app.post("/roles-function-scope")
    async def create_with_function_scope(
        body: RoleCreate,
        boom: bool = False,
        _: None = Depends(_fn_writer),
        session: AsyncSession = Depends(db),
    ) -> dict:
        await RoleCrud.create(session, body)
        if boom:
            raise RuntimeError("boom after write")
        return {"ok": True}

    @app.post("/roles-function-scope-borrower")
    async def function_scope_borrows(
        body: RoleCreate,
        session: AsyncSession = Depends(db),
        _: None = Depends(_fn_writer),
    ) -> dict:
        # Flipped order: the request-scoped dependency owns the session and the
        # function-scoped one borrows it. The borrower unwinds early but must not
        # commit or close — the commit still belongs to the middleware.
        await RoleCrud.create(session, body)
        return {"ok": True}

    @app.get("/roles-stream/{name}")
    async def stream_role(
        name: str, session: AsyncSession = Depends(db)
    ) -> StreamingResponse:
        # A write before the stream begins: the middleware commits it at
        # response-start, before the generator runs.
        await RoleCrud.create(session, RoleCreate(name=name))

        async def gen():
            # Read-only DB use during the stream, via the request session.
            row = (
                await session.execute(select(Role).where(Role.name == name))
            ).scalar_one()
            yield f"data: {row.name}\n\n".encode()

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/roles-add")
    async def add_only(body: RoleCreate, session: AsyncSession = Depends(db)) -> dict:
        # Nothing is flushed here: a violation surfaces in the middleware's commit.
        session.add(Role(name=body.name))
        return {"ok": True}

    db.install(app)
    return app


class TestCommitIntegration:
    """``Depends(db)`` plus the installed middleware, through real requests."""

    @pytest.fixture
    def app(self, engine) -> FastAPI:
        return _build_app(Database(engine=engine))

    @pytest.mark.anyio
    async def test_write_is_visible_to_others_before_the_response_starts(
        self, app, session_maker
    ):
        """The read-after-write guarantee the middleware exists for."""
        result: dict = {}
        app.add_middleware(
            _ProbeMiddleware, session_maker=session_maker, name="probe", result=result
        )

        async with create_async_client(app) as client:
            resp = await client.post("/roles", json={"name": "probe"})

        assert resp.status_code == 200
        assert result == {"visible_at_start": True}
        assert await _role_exists(session_maker, "probe")

    @pytest.mark.parametrize(
        ("path", "names", "body"),
        [
            ("/roles-self-commit", ["r"], {"ok": True}),
            ("/roles-two-cache-keys", ["r", "r_sub"], {"same_session": True}),
            ("/roles-function-scope", ["r", "r_fn"], {"ok": True}),
            ("/roles-function-scope-borrower", ["r", "r_fn"], {"ok": True}),
        ],
        ids=[
            "endpoint-commits-itself",
            "two-cache-keys-share-one-session",
            "function-scoped-owner-commits-early",
            "function-scoped-borrower-leaves-the-commit-to-the-middleware",
        ],
    )
    @pytest.mark.anyio
    async def test_successful_request_persists_every_write(
        self, app, session_maker, path, names, body
    ):
        """One session per request, whoever resolves it and whenever it unwinds."""
        async with create_async_client(app) as client:
            resp = await client.post(path, json={"name": "r"})

        assert (resp.status_code, resp.json()) == (200, body)
        for name in names:
            assert await _role_exists(session_maker, name)

    @pytest.mark.parametrize(
        ("path", "names"),
        [
            ("/roles-then-boom", ["r"]),
            ("/two-roles", ["r"]),
            ("/roles-function-scope?boom=true", ["r", "r_fn"]),
        ],
        ids=["exception", "second-write-conflicts", "function-scoped-dependency"],
    )
    @pytest.mark.anyio
    async def test_failed_request_rolls_back_every_write(
        self, app, session_maker, path, names
    ):
        """One transaction per request: an early commit must never fire on failure."""
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(path, json={"name": "r"})

        assert resp.status_code >= 400
        for name in names:
            assert not await _role_exists(session_maker, name)

    @pytest.mark.anyio
    async def test_streaming_response_keeps_the_session_readable(
        self, app, session_maker
    ):
        """The commit fires at stream start; the generator still reads through
        the request session."""
        async with create_async_client(app) as client:
            resp = await client.get("/roles-stream/streamed")

        assert resp.status_code == 200
        assert "data: streamed" in resp.text
        assert await _role_exists(session_maker, "streamed")


class TestCommitFailure:
    """A failed commit answers through the app's exception handlers."""

    @pytest.fixture
    async def taken(self, session_maker) -> str:
        async with session_maker.begin() as session:
            session.add(Role(name="taken"))
        return "taken"

    @pytest.mark.parametrize(
        "path", ["/roles", "/roles-add"], ids=["flush-in-route", "commit-only"]
    )
    @pytest.mark.anyio
    async def test_duplicate_is_a_conflict_wherever_it_surfaces(
        self, engine, taken, path
    ):
        app = init_exceptions_handlers(_build_app(Database(engine=engine)))

        async with create_async_client(app) as client:
            resp = await client.post(path, json={"name": taken})

        assert resp.status_code == 409
        assert resp.json()["error_code"] == "DB-409-UNIQUE"

    @pytest.mark.anyio
    async def test_a_sync_handler_of_the_app_answers_the_commit_failure(
        self, engine, taken
    ):
        app = _build_app(Database(engine=engine))

        def conflict(request: Request, exc: Exception) -> JSONResponse:
            return JSONResponse({"handled": True}, status_code=409)

        app.add_exception_handler(IntegrityError, conflict)

        async with create_async_client(app) as client:
            resp = await client.post("/roles-add", json={"name": taken})

        assert (resp.status_code, resp.json()) == (409, {"handled": True})

    @pytest.mark.anyio
    async def test_without_a_handler_the_commit_failure_stays_a_server_error(
        self, engine, taken
    ):
        app = _build_app(Database(engine=engine))
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/roles-add", json={"name": taken})

        assert resp.status_code == 500
