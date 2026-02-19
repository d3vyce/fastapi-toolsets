"""Database utilities: sessions, transactions, and locks."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from enum import Enum
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

__all__ = [
    "LockMode",
    "create_db_context",
    "create_db_dependency",
    "lock_tables",
    "get_transaction",
    "wait_for_row_change",
]


def create_db_dependency(
    session_maker: async_sessionmaker[AsyncSession],
) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
    """Create a FastAPI dependency for database sessions.

    Creates a dependency function that yields a session and auto-commits
    if a transaction is active when the request completes.

    Args:
        session_maker: Async session factory from create_session_factory()

    Returns:
        An async generator function usable with FastAPI's Depends()

    Example:
        ```python
        from fastapi import Depends
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
        from fastapi_toolsets.db import create_db_dependency

        engine = create_async_engine("postgresql+asyncpg://...")
        SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
        get_db = create_db_dependency(SessionLocal)

        @app.get("/users")
        async def list_users(session: AsyncSession = Depends(get_db)):
            ...
        ```
    """

    async def get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_maker() as session:
            yield session
            if session.in_transaction():
                await session.commit()

    return get_db


def create_db_context(
    session_maker: async_sessionmaker[AsyncSession],
) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    """Create a context manager for database sessions.

    Creates a context manager for use outside of FastAPI request handlers,
    such as in background tasks, CLI commands, or tests.

    Args:
        session_maker: Async session factory from create_session_factory()

    Returns:
        An async context manager function

    Example:
        ```python
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from fastapi_toolsets.db import create_db_context

        engine = create_async_engine("postgresql+asyncpg://...")
        SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
        get_db_context = create_db_context(SessionLocal)

        async def background_task():
            async with get_db_context() as session:
                user = await UserCrud.get(session, [User.id == 1])
                ...
        ```
    """
    get_db = create_db_dependency(session_maker)
    return asynccontextmanager(get_db)


@asynccontextmanager
async def get_transaction(
    session: AsyncSession,
) -> AsyncGenerator[AsyncSession, None]:
    """Get a transaction context, handling nested transactions.

    If already in a transaction, creates a savepoint (nested transaction).
    Otherwise, starts a new transaction.

    Args:
        session: AsyncSession instance

    Yields:
        The session within the transaction context

    Example:
        ```python
        async with get_transaction(session):
            session.add(model)
            # Auto-commits on exit, rolls back on exception
        ```
    """
    if session.in_transaction():
        async with session.begin_nested():
            yield session
    else:
        async with session.begin():
            yield session


class LockMode(str, Enum):
    """PostgreSQL table lock modes.

    See: https://www.postgresql.org/docs/current/explicit-locking.html
    """

    ACCESS_SHARE = "ACCESS SHARE"
    ROW_SHARE = "ROW SHARE"
    ROW_EXCLUSIVE = "ROW EXCLUSIVE"
    SHARE_UPDATE_EXCLUSIVE = "SHARE UPDATE EXCLUSIVE"
    SHARE = "SHARE"
    SHARE_ROW_EXCLUSIVE = "SHARE ROW EXCLUSIVE"
    EXCLUSIVE = "EXCLUSIVE"
    ACCESS_EXCLUSIVE = "ACCESS EXCLUSIVE"


@asynccontextmanager
async def lock_tables(
    session: AsyncSession,
    tables: list[type[DeclarativeBase]],
    *,
    mode: LockMode = LockMode.SHARE_UPDATE_EXCLUSIVE,
    timeout: str = "5s",
) -> AsyncGenerator[AsyncSession, None]:
    """Lock PostgreSQL tables for the duration of a transaction.

    Acquires table-level locks that are held until the transaction ends.
    Useful for preventing concurrent modifications during critical operations.

    Args:
        session: AsyncSession instance
        tables: List of SQLAlchemy model classes to lock
        mode: Lock mode (default: SHARE UPDATE EXCLUSIVE)
        timeout: Lock timeout (default: "5s")

    Yields:
        The session with locked tables

    Raises:
        SQLAlchemyError: If lock cannot be acquired within timeout

    Example:
        ```python
        from fastapi_toolsets.db import lock_tables, LockMode

        async with lock_tables(session, [User, Account]):
            # Tables are locked with SHARE UPDATE EXCLUSIVE mode
            user = await UserCrud.get(session, [User.id == 1])
            user.balance += 100

        # With custom lock mode
        async with lock_tables(session, [Order], mode=LockMode.EXCLUSIVE):
            # Exclusive lock - no other transactions can access
            await process_order(session, order_id)
        ```
    """
    table_names = ",".join(table.__tablename__ for table in tables)

    async with get_transaction(session):
        await session.execute(text(f"SET LOCAL lock_timeout='{timeout}'"))
        await session.execute(text(f"LOCK {table_names} IN {mode.value} MODE"))
        yield session


_M = TypeVar("_M", bound=DeclarativeBase)


async def wait_for_row_change(
    session: AsyncSession,
    model: type[_M],
    pk_value: Any,
    *,
    columns: list[str] | None = None,
    interval: float = 0.5,
    timeout: float | None = None,
) -> _M:
    """Poll a database row until a change is detected.

    Queries the row every ``interval`` seconds and returns the model instance
    once a change is detected in any column (or only the specified ``columns``).

    Args:
        session: AsyncSession instance
        model: SQLAlchemy model class
        pk_value: Primary key value of the row to watch
        columns: Optional list of column names to watch. If None, all columns
                 are watched.
        interval: Polling interval in seconds (default: 0.5)
        timeout: Maximum time to wait in seconds. None means wait forever.

    Returns:
        The refreshed model instance with updated values

    Raises:
        LookupError: If the row does not exist or is deleted during polling
        TimeoutError: If timeout expires before a change is detected

    Example:
        ```python
        from fastapi_toolsets.db import wait_for_row_change

        # Wait for any column to change
        updated = await wait_for_row_change(session, User, user_id)

        # Watch specific columns with a timeout
        updated = await wait_for_row_change(
            session, User, user_id,
            columns=["status", "email"],
            interval=1.0,
            timeout=30.0,
        )
        ```
    """
    instance = await session.get(model, pk_value)
    if instance is None:
        raise LookupError(f"{model.__name__} with pk={pk_value!r} not found")

    if columns is not None:
        watch_cols = columns
    else:
        watch_cols = [attr.key for attr in model.__mapper__.column_attrs]

    initial = {col: getattr(instance, col) for col in watch_cols}

    elapsed = 0.0
    while True:
        await asyncio.sleep(interval)
        elapsed += interval

        if timeout is not None and elapsed >= timeout:
            raise TimeoutError(
                f"No change detected on {model.__name__} "
                f"with pk={pk_value!r} within {timeout}s"
            )

        session.expunge(instance)
        instance = await session.get(model, pk_value)

        if instance is None:
            raise LookupError(f"{model.__name__} with pk={pk_value!r} was deleted")

        current = {col: getattr(instance, col) for col in watch_cols}
        if current != initial:
            return instance
