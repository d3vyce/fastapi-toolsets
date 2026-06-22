"""PostgreSQL locking helpers: table locks and advisory locks."""

from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from enum import Enum
from typing import TypeVar

import asyncpg
from sqlalchemy import exc as sa_exc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from ..exceptions import LockTimeoutError, PoolExhaustedError

_SessionT = TypeVar("_SessionT", bound=AsyncSession)


def _is_lock_not_available(e: sa_exc.DBAPIError) -> bool:
    return e.orig is not None and isinstance(
        e.orig.__cause__, asyncpg.exceptions.LockNotAvailableError
    )


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


def lock_tables(
    session_maker: async_sessionmaker[_SessionT],
    tables: list[type[DeclarativeBase]],
    *,
    mode: LockMode = LockMode.SHARE_UPDATE_EXCLUSIVE,
    timeout: str = "5s",
) -> AbstractAsyncContextManager[_SessionT]:
    """Lock PostgreSQL tables for the duration of a transaction.

    Prefer the method on a :class:`Database` instance; use this
    directly only when you manage your own session factory.

    Args:
        session_maker: Async session factory used to create the dedicated
            session.
        tables: List of SQLAlchemy model classes to lock.
        mode: Lock mode (default: SHARE UPDATE EXCLUSIVE).
        timeout: Lock timeout (default: "5s").

    Yields:
        The dedicated session, open within the locked transaction.

    Raises:
        LockTimeoutError: If the lock cannot be acquired within *timeout*.
        PoolExhaustedError: If the connection pool is exhausted.

    Example:
        ```python
        from fastapi_toolsets.db import lock_tables

        async with lock_tables(session_maker, [User, Account]) as session:
            user = await UserCrud.get(session, [User.id == 1])
            user.balance += 100
        ```
    """
    table_names = ",".join(table.__tablename__ for table in tables)

    @asynccontextmanager
    async def _lock() -> AsyncGenerator[_SessionT, None]:
        async with session_maker() as session:
            try:
                await session.execute(text(f"SET LOCAL lock_timeout='{timeout}'"))
                await session.execute(text(f"LOCK {table_names} IN {mode.value} MODE"))
                yield session
                await session.commit()
            except sa_exc.TimeoutError as e:
                await session.rollback()
                raise PoolExhaustedError(
                    f"Connection pool exhausted while locking '{table_names}'. "
                ) from e
            except sa_exc.DBAPIError as e:
                await session.rollback()
                if _is_lock_not_available(e):
                    raise LockTimeoutError(
                        f"Lock on '{table_names}' could not be acquired within {timeout}."
                    ) from e
                raise  # pragma: no cover
            except BaseException:
                await session.rollback()
                raise

    return _lock()


@asynccontextmanager
async def advisory_lock(
    session: AsyncSession,
    key: int | tuple[int, int],
    *,
    shared: bool = False,
    nowait: bool = False,
    timeout: str | None = None,
) -> AsyncGenerator[bool, None]:
    """Acquire a PostgreSQL session-level advisory lock.

    Args:
        session: AsyncSession instance.
        key: Lock key, either a single ``int`` (bigint) or a ``(int, int)`` pair for namespacing.
        shared: Acquire a shared lock (multiple holders allowed). Default is exclusive.
        nowait: Return ``False`` immediately if the lock is unavailable instead of waiting.
        timeout: Maximum wait time (e.g. ``"5s"``, ``"500ms"``). Raises ``DBAPIError``
            if exceeded. Ignored when *nowait* is ``True``.

    Yields:
        ``True`` if the lock was acquired, ``False`` if *nowait* is ``True`` and the lock
        is already held.

    Raises:
        LockTimeoutError: If *timeout* is set and the lock cannot be acquired in time.

    Example:
        ```python
        from fastapi_toolsets.db import advisory_lock

        async with advisory_lock(session, 42):
            ...

        async with advisory_lock(session, 42, nowait=True) as acquired:
            if not acquired:
                raise HTTPException(409, "Resource is locked")

        async with advisory_lock(session, 42, timeout="5s"):
            ...

        async with advisory_lock(session, (1, user_id), shared=True):
            ...
        ```
    """
    suffix = "_shared" if shared else ""
    acquire_fn = f"{'pg_try_advisory_lock' if nowait else 'pg_advisory_lock'}{suffix}"
    release_fn = f"pg_advisory_unlock{suffix}"

    if isinstance(key, tuple):
        k1, k2 = key
        args = "CAST(:k1 AS integer), CAST(:k2 AS integer)"
        params: dict[str, int] = {"k1": k1, "k2": k2}
    else:
        args = ":k"
        params = {"k": key}

    acquire_sql = text(f"SELECT {acquire_fn}({args})")
    release_sql = text(f"SELECT {release_fn}({args})")

    # Lock management runs raw SQL on the caller's session. Guard it with
    # ``no_autoflush`` so acquiring or releasing the lock never flushes the
    # caller's pending ORM changes; SQLAlchemy 2.1 autoflushes on raw
    # ``text()`` too, where 2.0 did not.
    try:
        with session.no_autoflush:
            if timeout is not None and not nowait:
                await session.execute(text(f"SET LOCAL lock_timeout='{timeout}'"))
            result = await session.execute(acquire_sql, params)
    except sa_exc.DBAPIError as e:
        if _is_lock_not_available(e):
            raise LockTimeoutError(
                f"Advisory lock {key!r} could not be acquired within {timeout}."
            ) from e
        raise  # pragma: no cover
    acquired = result.scalar() if nowait else True
    try:
        yield acquired
    finally:
        if acquired:
            with session.no_autoflush:
                await session.execute(release_sql, params)
