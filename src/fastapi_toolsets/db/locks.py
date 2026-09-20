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
    session_maker: async_sessionmaker[_SessionT] | None,
    tables: list[type[DeclarativeBase]],
    *,
    session: _SessionT | None = None,
    mode: LockMode = LockMode.SHARE_UPDATE_EXCLUSIVE,
    timeout: str = "5s",
) -> AbstractAsyncContextManager[_SessionT]:
    """Lock PostgreSQL tables for the duration of a transaction.

    Prefer the method on a :class:`Database` instance; use this
    directly only when you manage your own session factory.

    Pass exactly one of *session_maker*, which opens a dedicated session and
    commits it at block exit, or *session*, which locks on a transaction you
    already own and so needs only one connection.

    Args:
        session_maker: Async session factory for the dedicated session.
            ``None`` when *session* is given.
        tables: List of SQLAlchemy model classes to lock.
        session: Existing session whose transaction takes the lock.
        mode: Lock mode (default: SHARE UPDATE EXCLUSIVE).
        timeout: Lock timeout (default: "5s").

    Yields:
        The session holding the lock: the dedicated one, or *session* itself.

    Raises:
        TypeError: If neither or both of *session_maker* and *session* are given.
        LockTimeoutError: If the lock cannot be acquired within *timeout*.
        PoolExhaustedError: If the connection pool is exhausted.

    Note:
        With *session*, nothing is committed or rolled back for you: the lock
        is held until the caller's transaction ends, pending ORM changes flush
        as it is taken, and ``lock_timeout`` stays set on that transaction.

    Example:
        ```python
        from fastapi_toolsets.db import lock_tables

        async with lock_tables(session_maker, [User, Account]) as session:
            user = await UserCrud.get(session, [User.id == 1])
            user.balance += 100
        ```
    """
    table_names = ",".join(table.__tablename__ for table in tables)
    set_timeout = text(f"SET LOCAL lock_timeout='{timeout}'")
    acquire = text(f"LOCK {table_names} IN {mode.value} MODE")

    def _translate(e: BaseException) -> None:
        if isinstance(e, sa_exc.TimeoutError):
            raise PoolExhaustedError(
                f"Connection pool exhausted while locking '{table_names}'. "
            ) from e
        if isinstance(e, sa_exc.DBAPIError) and _is_lock_not_available(e):
            raise LockTimeoutError(
                f"Lock on '{table_names}' could not be acquired within {timeout}."
            ) from e

    @asynccontextmanager
    async def _lock_dedicated(
        maker: async_sessionmaker[_SessionT],
    ) -> AsyncGenerator[_SessionT, None]:
        async with maker() as dedicated:
            try:
                await dedicated.execute(set_timeout)
                await dedicated.execute(acquire)
                yield dedicated
                await dedicated.commit()
            except BaseException as e:
                await dedicated.rollback()
                _translate(e)
                raise

    @asynccontextmanager
    async def _lock_caller(caller: _SessionT) -> AsyncGenerator[_SessionT, None]:
        try:
            with caller.no_autoflush:
                await caller.execute(set_timeout)
            async with caller.begin_nested():
                await caller.execute(acquire)
        except BaseException as e:
            _translate(e)
            raise
        yield caller

    if session_maker is not None and session is None:
        return _lock_dedicated(session_maker)
    if session is not None and session_maker is None:
        return _lock_caller(session)
    raise TypeError(
        "lock_tables() requires exactly one of 'session_maker' or 'session'."
    )


@asynccontextmanager
async def advisory_lock(
    session: AsyncSession,
    key: int | tuple[int, int],
    *,
    shared: bool = False,
    nowait: bool = False,
    xact: bool = False,
    timeout: str | None = None,
) -> AsyncGenerator[bool, None]:
    """Acquire a PostgreSQL advisory lock.

    Args:
        session: AsyncSession instance.
        key: Lock key, either a single ``int`` (bigint) or a ``(int, int)`` pair for namespacing.
        shared: Acquire a shared lock (multiple holders allowed). Default is exclusive.
        nowait: Return ``False`` immediately if the lock is unavailable instead of waiting.
        xact: Hold the lock until the caller's transaction ends.
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

        async with advisory_lock(session, (team_id, question_id), xact=True):
            ...  # held until the request's transaction commits
        ```
    """
    suffix = "_shared" if shared else ""
    scope = "_xact" if xact else ""
    acquire_fn = f"pg_{'try_' if nowait else ''}advisory{scope}_lock{suffix}"
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
        if acquired and not xact:
            with session.no_autoflush:
                await session.execute(release_sql, params)
