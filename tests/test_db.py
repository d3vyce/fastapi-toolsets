"""Tests for fastapi_toolsets.db module."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fastapi_toolsets.db import (
    LockMode,
    create_db_context,
    create_db_dependency,
    get_transaction,
    lock_tables,
)

from .conftest import DATABASE_URL, Base, Role, RoleCrud, User


class TestCreateDbDependency:
    """Tests for create_db_dependency."""

    @pytest.mark.anyio
    async def test_yields_session(self):
        """Dependency yields a valid session."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db = create_db_dependency(session_factory)

        async for session in get_db():
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

            async for session in get_db():
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
    async def test_lock_single_table(self, db_session: AsyncSession):
        """Lock a single table."""
        async with lock_tables(db_session, [Role]):
            # Inside the lock, we can still perform operations
            role = Role(name="locked_role")
            db_session.add(role)
            await db_session.flush()

        # After lock is released, verify the data was committed
        result = await RoleCrud.first(db_session, [Role.name == "locked_role"])
        assert result is not None

    @pytest.mark.anyio
    async def test_lock_multiple_tables(self, db_session: AsyncSession):
        """Lock multiple tables."""
        async with lock_tables(db_session, [Role, User]):
            role = Role(name="multi_lock_role")
            db_session.add(role)
            await db_session.flush()

        result = await RoleCrud.first(db_session, [Role.name == "multi_lock_role"])
        assert result is not None

    @pytest.mark.anyio
    async def test_lock_with_custom_mode(self, db_session: AsyncSession):
        """Lock with custom lock mode."""
        async with lock_tables(db_session, [Role], mode=LockMode.EXCLUSIVE):
            role = Role(name="exclusive_lock_role")
            db_session.add(role)
            await db_session.flush()

        result = await RoleCrud.first(db_session, [Role.name == "exclusive_lock_role"])
        assert result is not None

    @pytest.mark.anyio
    async def test_lock_rollback_on_exception(self, db_session: AsyncSession):
        """Lock context rolls back on exception."""
        try:
            async with lock_tables(db_session, [Role]):
                role = Role(name="lock_rollback_role")
                db_session.add(role)
                await db_session.flush()
                raise ValueError("Simulated error")
        except ValueError:
            pass

        result = await RoleCrud.first(db_session, [Role.name == "lock_rollback_role"])
        assert result is None
