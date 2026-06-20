"""Tests for fastapi_toolsets.db module."""

import asyncio
import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import (
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Table,
    Uuid,
    select,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    selectinload,
)
from starlette.requests import Request

from fastapi_toolsets.db import (
    DB_SESSION_STATE_ATTR,
    CommitOnResponseMiddleware,
    LockMode,
    advisory_lock,
    cleanup_tables,
    create_database,
    create_db_context,
    create_db_dependency,
    get_transaction,
    lock_tables,
    m2m_add,
    m2m_remove,
    m2m_set,
    wait_for_row_change,
)
from fastapi_toolsets.exceptions import (
    LockTimeoutError,
    NotFoundError,
    PoolExhaustedError,
)
from fastapi_toolsets.pytest import create_db_session

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
)


def _make_request() -> Request:
    """Minimal ASGI HTTP request for exercising the get_db dependency directly."""
    return Request({"type": "http", "headers": []})


class TestCreateDbDependency:
    """Tests for create_db_dependency."""

    @pytest.mark.anyio
    async def test_yields_session(self):
        """Dependency yields a valid session."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db = create_db_dependency(session_factory)

        async for session in get_db(_make_request()):
            assert isinstance(session, AsyncSession)
            break

        await engine.dispose()

    @pytest.mark.anyio
    async def test_auto_commits_transaction(self):
        """Dependency auto-commits if transaction is active."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        try:
            get_db = create_db_dependency(session_factory)

            async for session in get_db(_make_request()):
                role = Role(name="test_role_dep")
                session.add(role)
                await session.flush()

            async with session_factory() as verify_session:
                result = await RoleCrud.first(
                    verify_session, [Role.name == "test_role_dep"]
                )
                assert result is not None
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

    @pytest.mark.anyio
    async def test_in_transaction_on_yield(self):
        """Session is already in a transaction when the endpoint body starts."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db = create_db_dependency(session_factory)

        async for session in get_db(_make_request()):
            assert session.in_transaction()
            break

        await engine.dispose()

    @pytest.mark.anyio
    async def test_no_commit_when_not_in_transaction(self):
        """Dependency skips commit if the session is no longer in a transaction on exit."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db = create_db_dependency(session_factory)

        async for session in get_db(_make_request()):
            # Manually commit — session exits the transaction
            await session.commit()
            assert not session.in_transaction()
            # The dependency's post-yield path must not call commit again (no error)

        await engine.dispose()

    @pytest.mark.anyio
    async def test_stashes_session_on_request_state(self):
        """Dependency exposes the session on request.state for the commit middleware."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db = create_db_dependency(session_factory)
        request = _make_request()

        async for session in get_db(request):
            assert getattr(request.state, DB_SESSION_STATE_ATTR) is session
            break

        await engine.dispose()

    @pytest.mark.anyio
    async def test_custom_state_attr(self):
        """state_attr controls where the session is stashed."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db = create_db_dependency(session_factory, state_attr="my_session")
        request = _make_request()

        async for session in get_db(request):
            assert request.state.my_session is session
            break

        await engine.dispose()

    @pytest.mark.anyio
    async def test_data_inside_lock_is_committed(self):
        """Changes made inside lock_tables are committed when the context exits."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        try:
            async with lock_tables(session_factory, [Role]) as session:
                role = Role(name="lock_committed")
                session.add(role)

            async with session_factory() as verify:
                result = await RoleCrud.first(verify, [Role.name == "lock_committed"])
                assert result is not None
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()


class TestCreateDbContext:
    """Tests for create_db_context."""

    @pytest.mark.anyio
    async def test_context_manager_yields_session(self):
        """Context manager yields a valid session."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db_context = create_db_context(session_factory)

        async with get_db_context() as session:
            assert isinstance(session, AsyncSession)

        await engine.dispose()

    @pytest.mark.anyio
    async def test_context_manager_commits(self):
        """Context manager commits on exit."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        try:
            get_db_context = create_db_context(session_factory)

            async with get_db_context() as session:
                role = Role(name="context_role")
                session.add(role)
                await session.flush()

            async with session_factory() as verify_session:
                result = await RoleCrud.first(
                    verify_session, [Role.name == "context_role"]
                )
                assert result is not None
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

    @pytest.mark.anyio
    async def test_no_commit_when_not_in_transaction(self):
        """Context skips commit if the session is no longer in a transaction on exit."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db_context = create_db_context(session_factory)

        async with get_db_context() as session:
            # Manually commit — session exits the transaction
            await session.commit()
            assert not session.in_transaction()
            # The post-yield path must not call commit again (no error)

        await engine.dispose()

    @pytest.mark.anyio
    async def test_pool_exhausted_raises_pool_exhausted_error(self):
        """PoolExhaustedError is raised when the pool is exhausted on context entry."""
        engine = create_async_engine(
            DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=0.1
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db_context = create_db_context(session_factory)

        try:
            async with session_factory() as holder:
                await holder.connection()  # check out the single available connection
                with pytest.raises(PoolExhaustedError):
                    async with get_db_context():
                        pass
        finally:
            await engine.dispose()


class TestGetTransaction:
    """Tests for get_transaction context manager."""

    @pytest.mark.anyio
    async def test_starts_transaction(self, db_session: AsyncSession):
        """get_transaction starts a new transaction."""
        async with get_transaction(db_session):
            role = Role(name="tx_role")
            db_session.add(role)

        result = await RoleCrud.first(db_session, [Role.name == "tx_role"])
        assert result is not None

    @pytest.mark.anyio
    async def test_nested_transaction_uses_savepoint(self, db_session: AsyncSession):
        """Nested transactions use savepoints."""
        async with get_transaction(db_session):
            role1 = Role(name="outer_role")
            db_session.add(role1)
            await db_session.flush()

            async with get_transaction(db_session):
                role2 = Role(name="inner_role")
                db_session.add(role2)

        results = await RoleCrud.get_multi(db_session)
        names = {r.name for r in results}
        assert "outer_role" in names
        assert "inner_role" in names

    @pytest.mark.anyio
    async def test_rollback_on_exception(self, db_session: AsyncSession):
        """Transaction rolls back on exception."""
        try:
            async with get_transaction(db_session):
                role = Role(name="rollback_role")
                db_session.add(role)
                await db_session.flush()
                raise ValueError("Simulated error")
        except ValueError:
            pass

        result = await RoleCrud.first(db_session, [Role.name == "rollback_role"])
        assert result is None

    @pytest.mark.anyio
    async def test_nested_rollback_preserves_outer(self, db_session: AsyncSession):
        """Nested rollback preserves outer transaction."""
        async with get_transaction(db_session):
            role1 = Role(name="preserved_role")
            db_session.add(role1)
            await db_session.flush()

            try:
                async with get_transaction(db_session):
                    role2 = Role(name="rolled_back_role")
                    db_session.add(role2)
                    await db_session.flush()
                    raise ValueError("Inner error")
            except ValueError:
                pass

        outer = await RoleCrud.first(db_session, [Role.name == "preserved_role"])
        inner = await RoleCrud.first(db_session, [Role.name == "rolled_back_role"])
        assert outer is not None
        assert inner is None


class TestLockMode:
    """Tests for LockMode enum."""

    def test_lock_modes_exist(self):
        """All expected lock modes are defined."""
        assert LockMode.ACCESS_SHARE == "ACCESS SHARE"
        assert LockMode.ROW_SHARE == "ROW SHARE"
        assert LockMode.ROW_EXCLUSIVE == "ROW EXCLUSIVE"
        assert LockMode.SHARE_UPDATE_EXCLUSIVE == "SHARE UPDATE EXCLUSIVE"
        assert LockMode.SHARE == "SHARE"
        assert LockMode.SHARE_ROW_EXCLUSIVE == "SHARE ROW EXCLUSIVE"
        assert LockMode.EXCLUSIVE == "EXCLUSIVE"
        assert LockMode.ACCESS_EXCLUSIVE == "ACCESS EXCLUSIVE"

    def test_lock_mode_is_string(self):
        """Lock modes are string enums."""
        assert isinstance(LockMode.EXCLUSIVE, str)
        assert LockMode.EXCLUSIVE.value == "EXCLUSIVE"


class TestLockTables:
    """Tests for lock_tables context manager (PostgreSQL-specific)."""

    @pytest.mark.anyio
    async def test_lock_single_table(self, session_maker):
        """Lock a single table; changes inside are committed on context exit."""
        async with lock_tables(session_maker, [Role]) as session:
            role = Role(name="locked_role")
            session.add(role)

        async with session_maker() as verify:
            result = await RoleCrud.first(verify, [Role.name == "locked_role"])
            assert result is not None

    @pytest.mark.anyio
    async def test_lock_multiple_tables(self, session_maker):
        """Lock multiple tables."""
        async with lock_tables(session_maker, [Role, User]) as session:
            role = Role(name="multi_lock_role")
            session.add(role)

        async with session_maker() as verify:
            result = await RoleCrud.first(verify, [Role.name == "multi_lock_role"])
            assert result is not None

    @pytest.mark.anyio
    async def test_lock_with_custom_mode(self, session_maker):
        """Lock with custom lock mode."""
        async with lock_tables(
            session_maker, [Role], mode=LockMode.EXCLUSIVE
        ) as session:
            role = Role(name="exclusive_lock_role")
            session.add(role)

        async with session_maker() as verify:
            result = await RoleCrud.first(verify, [Role.name == "exclusive_lock_role"])
            assert result is not None

    @pytest.mark.anyio
    async def test_lock_rollback_on_exception(self, session_maker):
        """Lock context rolls back on exception."""
        try:
            async with lock_tables(session_maker, [Role]) as session:
                role = Role(name="lock_rollback_role")
                session.add(role)
                await session.flush()
                raise ValueError("Simulated error")
        except ValueError:
            pass

        async with session_maker() as verify:
            result = await RoleCrud.first(verify, [Role.name == "lock_rollback_role"])
            assert result is None


class TestAdvisoryLock:
    """Tests for advisory_lock context manager (PostgreSQL-specific)."""

    @pytest.mark.anyio
    async def test_blocking_exclusive_acquires(self, db_session: AsyncSession):
        """Blocking exclusive lock acquires and yields True."""
        async with advisory_lock(db_session, 1001) as acquired:
            assert acquired is True

    @pytest.mark.anyio
    async def test_nowait_returns_true_when_free(self, db_session: AsyncSession):
        """nowait=True yields True when the lock is available."""
        async with advisory_lock(db_session, 1002, nowait=True) as acquired:
            assert acquired is True

    @pytest.mark.anyio
    async def test_nowait_returns_false_when_contended(self, session_maker):
        """nowait=True yields False when another session holds the lock."""
        async with session_maker() as holder:
            async with holder.begin():
                async with advisory_lock(holder, 1003):
                    async with session_maker() as contender:
                        async with contender.begin():
                            async with advisory_lock(
                                contender, 1003, nowait=True
                            ) as acquired:
                                assert acquired is False

    @pytest.mark.anyio
    async def test_shared_allows_concurrent_readers(self, session_maker):
        """Two shared locks on the same key are both acquired."""
        async with session_maker() as s1, session_maker() as s2:
            async with s1.begin(), s2.begin():
                async with advisory_lock(s1, 1004, shared=True) as a1:
                    async with advisory_lock(s2, 1004, shared=True, nowait=True) as a2:
                        assert a1 is True
                        assert a2 is True

    @pytest.mark.anyio
    async def test_tuple_key(self, db_session: AsyncSession):
        """(int, int) key variant acquires the lock."""
        async with advisory_lock(db_session, (7, 42)) as acquired:
            assert acquired is True

    @pytest.mark.anyio
    async def test_tuple_key_nowait_contended(self, session_maker):
        """Tuple key nowait returns False when contended."""
        async with session_maker() as holder:
            async with holder.begin():
                async with advisory_lock(holder, (7, 99)):
                    async with session_maker() as contender:
                        async with contender.begin():
                            async with advisory_lock(
                                contender, (7, 99), nowait=True
                            ) as acquired:
                                assert acquired is False

    @pytest.mark.anyio
    async def test_lock_released_at_context_exit(self, session_maker):
        """Lock is released when the context exits, even while the transaction is still open."""
        async with session_maker() as s1:
            async with s1.begin():
                async with advisory_lock(s1, 1005):
                    pass  # lock released here — transaction still active

                async with session_maker() as s2:
                    async with s2.begin():
                        async with advisory_lock(s2, 1005, nowait=True) as acquired:
                            assert (
                                acquired is True
                            )  # s1 still in transaction but lock is free

    @pytest.mark.anyio
    async def test_timeout_raises_when_contended(self, session_maker):
        """timeout= raises LockTimeoutError when the lock cannot be acquired."""
        async with session_maker() as holder:
            async with holder.begin():
                async with advisory_lock(holder, 1006):
                    async with session_maker() as contender:
                        async with contender.begin():
                            with pytest.raises(LockTimeoutError):
                                async with advisory_lock(
                                    contender, 1006, timeout="10ms"
                                ):
                                    pass


class TestWaitForRowChange:
    """Tests for wait_for_row_change polling function."""

    @pytest.mark.anyio
    async def test_detects_update(self, db_session: AsyncSession, engine):
        """Returns updated instance when a column value changes."""
        role = Role(name="watch_role")
        db_session.add(role)
        await db_session.commit()

        async def update_later():
            await asyncio.sleep(0.15)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as other:
                r = await other.get(Role, role.id)
                assert r is not None
                r.name = "updated_role"
                await other.commit()

        update_task = asyncio.create_task(update_later())
        result = await wait_for_row_change(db_session, Role, role.id, interval=0.05)
        await update_task

        assert result.name == "updated_role"

    @pytest.mark.anyio
    async def test_watches_specific_columns(self, db_session: AsyncSession, engine):
        """Only triggers on changes to specified columns."""
        user = User(username="testuser", email="test@example.com")
        db_session.add(user)
        await db_session.commit()

        async def update_later():
            factory = async_sessionmaker(engine, expire_on_commit=False)
            # First: change email (not watched) — should not trigger
            await asyncio.sleep(0.15)
            async with factory() as other:
                u = await other.get(User, user.id)
                assert u is not None
                u.email = "new@example.com"
                await other.commit()
            # Second: change username (watched) — should trigger
            await asyncio.sleep(0.15)
            async with factory() as other:
                u = await other.get(User, user.id)
                assert u is not None
                u.username = "newuser"
                await other.commit()

        update_task = asyncio.create_task(update_later())
        result = await wait_for_row_change(
            db_session, User, user.id, columns=["username"], interval=0.05
        )
        await update_task

        assert result.username == "newuser"
        assert result.email == "new@example.com"

    @pytest.mark.anyio
    async def test_nonexistent_row_raises(self, db_session: AsyncSession):
        """Raises NotFoundError when the row does not exist."""
        fake_id = uuid.uuid4()
        with pytest.raises(NotFoundError, match="not found"):
            await wait_for_row_change(db_session, Role, fake_id, interval=0.05)

    @pytest.mark.anyio
    async def test_timeout_raises(self, db_session: AsyncSession):
        """Raises TimeoutError when no change is detected within timeout."""
        role = Role(name="timeout_role")
        db_session.add(role)
        await db_session.commit()

        with pytest.raises(TimeoutError):
            await wait_for_row_change(
                db_session, Role, role.id, interval=0.05, timeout=0.2
            )

    @pytest.mark.anyio
    async def test_deleted_row_raises(self, db_session: AsyncSession, engine):
        """Raises NotFoundError when the row is deleted during polling."""
        role = Role(name="delete_role")
        db_session.add(role)
        await db_session.commit()

        async def delete_later():
            await asyncio.sleep(0.15)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as other:
                r = await other.get(Role, role.id)
                await other.delete(r)
                await other.commit()

        delete_task = asyncio.create_task(delete_later())
        with pytest.raises(NotFoundError):
            await wait_for_row_change(db_session, Role, role.id, interval=0.05)
        await delete_task


class TestCreateDatabase:
    """Tests for create_database."""

    @pytest.mark.anyio
    async def test_creates_database(self):
        """Database is created by create_database."""
        target_url = (
            make_url(DATABASE_URL)
            .set(database="test_create_db_general")
            .render_as_string(hide_password=False)
        )
        expected_db = make_url(target_url).database
        assert expected_db is not None

        engine = create_async_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f"DROP DATABASE IF EXISTS {expected_db}"))

            await create_database(db_name=expected_db, server_url=DATABASE_URL)

            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": expected_db},
                )
                assert result.scalar() == 1

            # Cleanup
            async with engine.connect() as conn:
                await conn.execute(text(f"DROP DATABASE IF EXISTS {expected_db}"))
        finally:
            await engine.dispose()


class TestCleanupTables:
    """Tests for cleanup_tables helper."""

    @pytest.mark.anyio
    async def test_truncates_all_tables(self):
        """All table rows are removed after cleanup_tables."""
        async with create_db_session(DATABASE_URL, Base, drop_tables=True) as session:
            role = Role(id=uuid.uuid4(), name="cleanup_role")
            session.add(role)
            await session.flush()

            user = User(
                id=uuid.uuid4(),
                username="cleanup_user",
                email="cleanup@test.com",
                role_id=role.id,
            )
            session.add(user)
            await session.commit()

            # Verify rows exist
            roles_count = await RoleCrud.count(session)
            users_count = await UserCrud.count(session)
            assert roles_count == 1
            assert users_count == 1

            await cleanup_tables(session, Base)

            # Verify tables are empty
            roles_count = await RoleCrud.count(session)
            users_count = await UserCrud.count(session)
            assert roles_count == 0
            assert users_count == 0

    @pytest.mark.anyio
    async def test_noop_for_empty_metadata(self):
        """cleanup_tables does not raise when metadata has no tables."""

        class EmptyBase(DeclarativeBase):
            pass

        async with create_db_session(DATABASE_URL, Base, drop_tables=True) as session:
            # Should not raise
            await cleanup_tables(session, EmptyBase)


class TestM2MAdd:
    """Tests for m2m_add helper."""

    @pytest.mark.anyio
    async def test_adds_single_related(self, db_session: AsyncSession):
        """Associates one related instance via the secondary table."""
        user = User(username="m2m_author", email="m2m@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post A", author_id=user.id)
        tag = Tag(name="python")
        db_session.add_all([post, tag])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag)

        result = await db_session.execute(
            select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
        )
        loaded = result.scalar_one()
        assert len(loaded.tags) == 1
        assert loaded.tags[0].id == tag.id

    @pytest.mark.anyio
    async def test_adds_multiple_related(self, db_session: AsyncSession):
        """Associates multiple related instances in a single call."""
        user = User(username="m2m_author2", email="m2m2@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post B", author_id=user.id)
        tag1 = Tag(name="web")
        tag2 = Tag(name="api")
        tag3 = Tag(name="async")
        db_session.add_all([post, tag1, tag2, tag3])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag1, tag2, tag3)

        result = await db_session.execute(
            select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
        )
        loaded = result.scalar_one()
        assert {t.id for t in loaded.tags} == {tag1.id, tag2.id, tag3.id}

    @pytest.mark.anyio
    async def test_noop_for_empty_related(self, db_session: AsyncSession):
        """Calling with no related instances is a no-op."""
        user = User(username="m2m_author3", email="m2m3@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post C", author_id=user.id)
        db_session.add(post)
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags)  # no related instances

        result = await db_session.execute(
            select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
        )
        loaded = result.scalar_one()
        assert loaded.tags == []

    @pytest.mark.anyio
    async def test_ignore_conflicts_true(self, db_session: AsyncSession):
        """Duplicate inserts are silently skipped when ignore_conflicts=True."""
        user = User(username="m2m_author4", email="m2m4@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post D", author_id=user.id)
        tag = Tag(name="duplicate_tag")
        db_session.add_all([post, tag])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag)

        # Second call with ignore_conflicts=True must not raise
        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag, ignore_conflicts=True)

        result = await db_session.execute(
            select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
        )
        loaded = result.scalar_one()
        assert len(loaded.tags) == 1

    @pytest.mark.anyio
    async def test_ignore_conflicts_false_raises(self, db_session: AsyncSession):
        """Duplicate inserts raise IntegrityError when ignore_conflicts=False (default)."""
        user = User(username="m2m_author5", email="m2m5@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post E", author_id=user.id)
        tag = Tag(name="conflict_tag")
        db_session.add_all([post, tag])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag)

        with pytest.raises(IntegrityError):
            async with get_transaction(db_session):
                await m2m_add(db_session, post, Post.tags, tag)

    @pytest.mark.anyio
    async def test_non_m2m_raises_type_error(self, db_session: AsyncSession):
        """Passing a non-M2M relationship attribute raises TypeError."""
        user = User(username="m2m_author6", email="m2m6@test.com")
        db_session.add(user)
        await db_session.flush()

        role = Role(name="type_err_role")
        db_session.add(role)
        await db_session.flush()

        with pytest.raises(TypeError, match="Many-to-Many"):
            await m2m_add(db_session, user, User.role, role)

    @pytest.mark.anyio
    async def test_works_inside_lock_tables(self, session_maker):
        """m2m_add works correctly inside a lock_tables context."""
        async with lock_tables(session_maker, [Tag]) as session:
            user = User(username="m2m_lock_author", email="m2m_lock@test.com")
            session.add(user)
            await session.flush()

            tag = Tag(name="locked_tag")
            session.add(tag)
            await session.flush()

            post = Post(title="Post Lock", author_id=user.id)
            session.add(post)
            await session.flush()

            await m2m_add(session, post, Post.tags, tag)

        async with session_maker() as verify:
            result = await verify.execute(
                select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
            )
            loaded = result.scalar_one()
            assert len(loaded.tags) == 1
            assert loaded.tags[0].name == "locked_tag"


class TestDbErrors:
    """Tests for structured error handling in db utilities."""

    @pytest.mark.anyio
    async def test_pool_exhausted_on_get_db_raises_pool_exhausted_error(self):
        """PoolExhaustedError is raised when the connection pool is exhausted on get_db."""
        engine = create_async_engine(
            DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=0.1
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db = create_db_dependency(session_factory)

        try:
            async with session_factory() as holder:
                await holder.connection()  # check out the single available connection
                with pytest.raises(PoolExhaustedError):
                    async for _ in get_db(_make_request()):
                        pass
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_pool_exhausted_on_lock_tables_raises_pool_exhausted_error(self):
        """PoolExhaustedError is raised when the connection pool is exhausted on lock_tables."""
        engine = create_async_engine(
            DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=0.1
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        try:
            async with session_factory() as holder:
                await holder.connection()  # check out the single available connection
                with pytest.raises(PoolExhaustedError):
                    async with lock_tables(session_factory, [Role]) as _:
                        pass
        finally:
            await engine.dispose()

    @pytest.mark.anyio
    async def test_lock_timeout_raises_lock_timeout_error(self, session_maker):
        """LockTimeoutError is raised when a table lock cannot be acquired within timeout."""
        async with lock_tables(session_maker, [Role]) as _:
            with pytest.raises(LockTimeoutError):
                async with lock_tables(session_maker, [Role], timeout="100ms") as _:
                    pass


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


class TestM2MRemove:
    """Tests for m2m_remove helper."""

    async def _setup(
        self, session: AsyncSession, username: str, email: str, *tag_names: str
    ):
        """Create a user, post, and tags; associate all tags with the post."""
        user = User(username=username, email=email)
        session.add(user)
        await session.flush()

        post = Post(title=f"Post {username}", author_id=user.id)
        tags = [Tag(name=n) for n in tag_names]
        session.add(post)
        session.add_all(tags)
        await session.flush()

        async with get_transaction(session):
            await m2m_add(session, post, Post.tags, *tags)

        return post, tags

    async def _load_tags(self, session: AsyncSession, post: Post) -> list[Tag]:
        result = await session.execute(
            select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
        )
        return result.scalar_one().tags

    @pytest.mark.anyio
    async def test_removes_single(self, db_session: AsyncSession):
        """Removes one association, leaving others intact."""
        post, (tag1, tag2) = await self._setup(
            db_session, "rm_author1", "rm1@test.com", "tag_rm_a", "tag_rm_b"
        )

        async with get_transaction(db_session):
            await m2m_remove(db_session, post, Post.tags, tag1)

        remaining = await self._load_tags(db_session, post)
        assert len(remaining) == 1
        assert remaining[0].id == tag2.id

    @pytest.mark.anyio
    async def test_removes_multiple(self, db_session: AsyncSession):
        """Removes multiple associations in one call."""
        post, (tag1, tag2, tag3) = await self._setup(
            db_session, "rm_author2", "rm2@test.com", "tag_rm_c", "tag_rm_d", "tag_rm_e"
        )

        async with get_transaction(db_session):
            await m2m_remove(db_session, post, Post.tags, tag1, tag3)

        remaining = await self._load_tags(db_session, post)
        assert len(remaining) == 1
        assert remaining[0].id == tag2.id

    @pytest.mark.anyio
    async def test_noop_for_empty_related(self, db_session: AsyncSession):
        """Calling with no related instances is a no-op."""
        post, (tag,) = await self._setup(
            db_session, "rm_author3", "rm3@test.com", "tag_rm_f"
        )

        async with get_transaction(db_session):
            await m2m_remove(db_session, post, Post.tags)

        remaining = await self._load_tags(db_session, post)
        assert len(remaining) == 1

    @pytest.mark.anyio
    async def test_idempotent_for_missing_association(self, db_session: AsyncSession):
        """Removing a non-existent association does not raise."""
        post, (tag1,) = await self._setup(
            db_session, "rm_author4", "rm4@test.com", "tag_rm_g"
        )
        tag2 = Tag(name="tag_rm_h")
        db_session.add(tag2)
        await db_session.flush()

        # tag2 was never associated — should not raise
        async with get_transaction(db_session):
            await m2m_remove(db_session, post, Post.tags, tag2)

        remaining = await self._load_tags(db_session, post)
        assert len(remaining) == 1

    @pytest.mark.anyio
    async def test_non_m2m_raises_type_error(self, db_session: AsyncSession):
        """Passing a non-M2M relationship attribute raises TypeError."""
        user = User(username="rm_author5", email="rm5@test.com")
        db_session.add(user)
        await db_session.flush()

        role = Role(name="rm_type_err_role")
        db_session.add(role)
        await db_session.flush()

        with pytest.raises(TypeError, match="Many-to-Many"):
            await m2m_remove(db_session, user, User.role, role)

    @pytest.mark.anyio
    async def test_removes_composite_pk_related(self):
        """Composite-PK branch: DELETE uses tuple IN when related side has multi-col PK."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(_LocalBase.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                owner = _CompOwner()
                item1 = _CompItem(group_id="g1", item_code="c1")
                item2 = _CompItem(group_id="g1", item_code="c2")
                session.add_all([owner, item1, item2])
                await session.flush()

                async with get_transaction(session):
                    await m2m_add(session, owner, _CompOwner.items, item1, item2)

                async with get_transaction(session):
                    await m2m_remove(session, owner, _CompOwner.items, item1)

                await session.commit()

            async with session_factory() as verify:
                from sqlalchemy import select
                from sqlalchemy.orm import selectinload

                result = await verify.execute(
                    select(_CompOwner)
                    .where(_CompOwner.id == owner.id)
                    .options(selectinload(_CompOwner.items))
                )
                loaded = result.scalar_one()
                assert len(loaded.items) == 1
                assert (loaded.items[0].group_id, loaded.items[0].item_code) == (
                    "g1",
                    "c2",
                )
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(_LocalBase.metadata.drop_all)
            await engine.dispose()


class TestM2MSet:
    """Tests for m2m_set helper."""

    async def _load_tags(self, session: AsyncSession, post: Post) -> list[Tag]:
        result = await session.execute(
            select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
        )
        return result.scalar_one().tags

    @pytest.mark.anyio
    async def test_replaces_existing_set(self, db_session: AsyncSession):
        """Replaces the full association set atomically."""
        user = User(username="set_author1", email="set1@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post Set A", author_id=user.id)
        tag1 = Tag(name="tag_set_a")
        tag2 = Tag(name="tag_set_b")
        tag3 = Tag(name="tag_set_c")
        db_session.add_all([post, tag1, tag2, tag3])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag1, tag2)

        async with get_transaction(db_session):
            await m2m_set(db_session, post, Post.tags, tag3)

        remaining = await self._load_tags(db_session, post)
        assert len(remaining) == 1
        assert remaining[0].id == tag3.id

    @pytest.mark.anyio
    async def test_clears_all_when_no_related(self, db_session: AsyncSession):
        """Passing no related instances clears all associations."""
        user = User(username="set_author2", email="set2@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post Set B", author_id=user.id)
        tag = Tag(name="tag_set_d")
        db_session.add_all([post, tag])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag)

        async with get_transaction(db_session):
            await m2m_set(db_session, post, Post.tags)

        remaining = await self._load_tags(db_session, post)
        assert remaining == []

    @pytest.mark.anyio
    async def test_set_on_empty_then_populate(self, db_session: AsyncSession):
        """m2m_set works on a post with no existing associations."""
        user = User(username="set_author3", email="set3@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post Set C", author_id=user.id)
        tag1 = Tag(name="tag_set_e")
        tag2 = Tag(name="tag_set_f")
        db_session.add_all([post, tag1, tag2])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_set(db_session, post, Post.tags, tag1, tag2)

        remaining = await self._load_tags(db_session, post)
        assert {t.id for t in remaining} == {tag1.id, tag2.id}

    @pytest.mark.anyio
    async def test_non_m2m_raises_type_error(self, db_session: AsyncSession):
        """Passing a non-M2M relationship attribute raises TypeError."""
        user = User(username="set_author4", email="set4@test.com")
        db_session.add(user)
        await db_session.flush()

        role = Role(name="set_type_err_role")
        db_session.add(role)
        await db_session.flush()

        with pytest.raises(TypeError, match="Many-to-Many"):
            await m2m_set(db_session, user, User.role, role)


class _FakeSession:
    """Records commit() calls into a shared event log."""

    def __init__(self, events: list[str], *, in_txn: bool = True) -> None:
        self.events = events
        self._in_txn = in_txn
        self.commits = 0

    def in_transaction(self) -> bool:
        return self._in_txn

    async def commit(self) -> None:
        self.events.append("COMMIT")
        self.commits += 1
        self._in_txn = False


async def _drive(app, scope, events: list[str]) -> list[str]:
    """Run an ASGI app, appending the response messages it emits to *events*
    (shared with the fake session so commit/response ordering is captured)."""

    async def receive():  # pragma: no cover - not exercised
        return {"type": "http.disconnect"}

    async def send(message) -> None:
        events.append(message["type"])

    await app(scope, receive, send)
    return events


class TestCommitOrdering:
    """The commit must precede the forwarded response, and be skipped otherwise."""

    @pytest.mark.anyio
    async def test_commits_before_response_start(self):
        events: list[str] = []
        session = _FakeSession(events)

        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        app = CommitOnResponseMiddleware(inner)
        scope = {"type": "http", "state": {DB_SESSION_STATE_ATTR: session}}

        result = await _drive(app, scope, events)

        assert session.commits == 1
        assert result == ["COMMIT", "http.response.start", "http.response.body"]

    @pytest.mark.anyio
    async def test_no_commit_when_not_in_transaction(self):
        events: list[str] = []
        session = _FakeSession(events, in_txn=False)

        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        app = CommitOnResponseMiddleware(inner)
        scope = {"type": "http", "state": {DB_SESSION_STATE_ATTR: session}}

        result = await _drive(app, scope, events)

        assert session.commits == 0
        assert result == ["http.response.start", "http.response.body"]

    @pytest.mark.anyio
    async def test_no_session_is_noop(self):
        events: list[str] = []

        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        app = CommitOnResponseMiddleware(inner)
        scope = {"type": "http", "state": {}}

        result = await _drive(app, scope, events)

        assert result == ["http.response.start", "http.response.body"]

    @pytest.mark.anyio
    async def test_non_http_scope_passes_through(self):
        called = False

        async def inner(scope, receive, send):
            nonlocal called
            called = True

        async def receive():  # pragma: no cover - not exercised
            return {"type": "lifespan.startup"}

        async def send(message):  # pragma: no cover - not exercised
            return None

        app = CommitOnResponseMiddleware(inner)
        await app({"type": "lifespan"}, receive, send)

        assert called is True


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
                async with self.session_maker() as probe:
                    row = (
                        await probe.execute(select(Role).where(Role.name == self.name))
                    ).scalar_one_or_none()
                    self.result["visible_at_start"] = row is not None
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _build_app(session_maker) -> FastAPI:
    """A FastAPI app wired with the commit middleware and a few routes."""
    app = FastAPI()
    get_db = create_db_dependency(session_maker)

    @app.post("/roles")
    async def create_role(
        body: RoleCreate, session: AsyncSession = Depends(get_db)
    ) -> dict:
        role = await RoleCrud.create(session, body)
        return {"id": str(role.id), "name": role.name}

    @app.post("/roles-then-boom")
    async def create_then_raise(
        body: RoleCreate, session: AsyncSession = Depends(get_db)
    ) -> dict:
        await RoleCrud.create(session, body)
        raise RuntimeError("boom after write")

    @app.post("/two-roles")
    async def create_two_roles(
        body: RoleCreate, session: AsyncSession = Depends(get_db)
    ) -> dict:
        # First write succeeds, second collides on the unique name and must
        # take the whole request transaction down with it.
        await RoleCrud.create(session, body)
        await RoleCrud.create(session, body)
        return {"ok": True}

    @app.post("/roles-self-commit")
    async def create_then_self_commit(
        body: RoleCreate, session: AsyncSession = Depends(get_db)
    ) -> dict:
        # Endpoint commits explicitly; the middleware must not double-commit or
        # error — it finds no open transaction and no-ops.
        role = await RoleCrud.create(session, body)
        await session.commit()
        return {"id": str(role.id), "name": role.name}

    @app.get("/roles-stream/{name}")
    async def stream_role(
        name: str, session: AsyncSession = Depends(get_db)
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

    app.add_middleware(CommitOnResponseMiddleware)
    return app


async def _row_exists(session_maker, name: str) -> bool:
    async with session_maker() as session:
        row = (
            await session.execute(select(Role).where(Role.name == name))
        ).scalar_one_or_none()
        return row is not None


class TestCommitIntegration:
    @pytest.mark.anyio
    async def test_write_is_committed(self, session_maker):
        app = _build_app(session_maker)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/roles", json={"name": "committed_role"})

        assert resp.status_code == 200
        assert await _row_exists(session_maker, "committed_role")

    @pytest.mark.anyio
    async def test_visible_at_response_start(self, session_maker):
        """The write is visible to a separate session *before* the response is
        sent — the read-after-write guarantee the middleware exists for."""
        app = _build_app(session_maker)
        result: dict = {}
        app.add_middleware(
            _ProbeMiddleware,
            session_maker=session_maker,
            name="probe_role",
            result=result,
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/roles", json={"name": "probe_role"})

        assert resp.status_code == 200
        assert result.get("visible_at_start") is True

    @pytest.mark.anyio
    async def test_error_rolls_back(self, session_maker):
        app = _build_app(session_maker)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/roles-then-boom", json={"name": "ghost_role"})

        assert resp.status_code == 500
        assert not await _row_exists(session_maker, "ghost_role")

    @pytest.mark.anyio
    async def test_explicit_commit_in_endpoint(self, session_maker):
        """An endpoint that commits itself works: the middleware no-ops (no
        double commit / error) and the write is persisted."""
        app = _build_app(session_maker)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/roles-self-commit", json={"name": "self_commit"})

        assert resp.status_code == 200
        assert await _row_exists(session_maker, "self_commit")

    @pytest.mark.anyio
    async def test_streaming_response_coexists(self, session_maker):
        """A read-only streaming endpoint works alongside the middleware: the
        commit fires at stream start, the pre-stream write is committed, and the
        generator can keep reading via the request session."""
        app = _build_app(session_maker)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/roles-stream/streamed_role")

        assert resp.status_code == 200
        assert "data: streamed_role" in resp.text
        # The write made before the stream began is durably committed.
        assert await _row_exists(session_maker, "streamed_role")

    @pytest.mark.anyio
    async def test_multi_write_atomicity(self, session_maker):
        """When the 2nd write fails, the 1st must roll back too (one txn)."""
        app = _build_app(session_maker)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/two-roles", json={"name": "dup_role"})

        assert resp.status_code >= 400
        assert not await _row_exists(session_maker, "dup_role")
