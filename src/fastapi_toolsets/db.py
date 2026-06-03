"""Database utilities: sessions, transactions, and locks."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from enum import Enum
from typing import Any, TypeVar, cast

import asyncpg
from sqlalchemy import Table, delete, text, tuple_
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, QueryableAttribute
from sqlalchemy.orm.relationships import RelationshipProperty

from .exceptions import LockTimeoutError, NotFoundError, PoolExhaustedError


def _is_lock_not_available(e: sa_exc.DBAPIError) -> bool:
    return e.orig is not None and isinstance(
        e.orig.__cause__, asyncpg.exceptions.LockNotAvailableError
    )


__all__ = [
    "LockMode",
    "advisory_lock",
    "cleanup_tables",
    "create_database",
    "create_db_context",
    "create_db_dependency",
    "get_transaction",
    "lock_tables",
    "m2m_add",
    "m2m_remove",
    "m2m_set",
    "wait_for_row_change",
]


_SessionT = TypeVar("_SessionT", bound=AsyncSession)


def create_db_dependency(
    session_maker: async_sessionmaker[_SessionT],
) -> Callable[[], AsyncGenerator[_SessionT, None]]:
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

    async def get_db() -> AsyncGenerator[_SessionT, None]:
        async with session_maker() as session:
            try:
                await session.connection()
            except sa_exc.TimeoutError as e:
                raise PoolExhaustedError() from e
            yield session
            if session.in_transaction():
                await session.commit()

    return get_db


def create_db_context(
    session_maker: async_sessionmaker[_SessionT],
) -> Callable[[], AbstractAsyncContextManager[_SessionT]]:
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


def lock_tables(
    session_maker: async_sessionmaker[_SessionT],
    tables: list[type[DeclarativeBase]],
    *,
    mode: LockMode = LockMode.SHARE_UPDATE_EXCLUSIVE,
    timeout: str = "5s",
) -> AbstractAsyncContextManager[_SessionT]:
    """Lock PostgreSQL tables for the duration of a transaction.

    Args:
        session_maker: Async session factory used to create the dedicated
            session.
        tables: List of SQLAlchemy model classes to lock.
        mode: Lock mode (default: SHARE UPDATE EXCLUSIVE).
        timeout: Lock timeout (default: "5s").

    Yields:
        The dedicated session, open within the locked transaction.

    Raises:
        SQLAlchemyError: If the lock cannot be acquired within *timeout*.

    Example:
        ```python
        from fastapi_toolsets.db import lock_tables, LockMode

        async with lock_tables(session_maker, [User, Account]) as session:
            # Tables are locked; changes are committed when the context exits.
            user = await UserCrud.get(session, [User.id == 1])
            user.balance += 100

        # With custom lock mode
        async with lock_tables(session_maker, [Order], mode=LockMode.EXCLUSIVE) as session:
            await process_order(session, order_id)
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
        key: Lock key — a single ``int`` (bigint) or a ``(int, int)`` pair for namespacing.
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

    if timeout is not None and not nowait:
        await session.execute(text(f"SET LOCAL lock_timeout='{timeout}'"))

    try:
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
            await session.execute(release_sql, params)


async def create_database(
    db_name: str,
    *,
    server_url: str,
) -> None:
    """Create a database.

    Connects to *server_url* using ``AUTOCOMMIT`` isolation and issues a
    ``CREATE DATABASE`` statement for *db_name*.

    Args:
        db_name: Name of the database to create.
        server_url: URL used for server-level DDL (must point to an existing
            database on the same server).

    Example:
        ```python
        from fastapi_toolsets.db import create_database

        SERVER_URL = "postgresql+asyncpg://postgres:postgres@localhost/postgres"
        await create_database("myapp_test", server_url=SERVER_URL)
        ```
    """
    engine = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f"CREATE DATABASE {db_name}"))
    finally:
        await engine.dispose()


async def cleanup_tables(
    session: AsyncSession,
    base: type[DeclarativeBase],
) -> None:
    """Truncate all tables for fast between-test cleanup.

    Executes a single ``TRUNCATE … RESTART IDENTITY CASCADE`` statement
    across every table in *base*'s metadata, which is significantly faster
    than dropping and re-creating tables between tests.

    This is a no-op when the metadata contains no tables.

    Args:
        session: An active async database session.
        base: SQLAlchemy DeclarativeBase class containing model metadata.

    Example:
        ```python
        @pytest.fixture
        async def db_session(worker_db_url):
            async with create_db_session(worker_db_url, Base) as session:
                yield session
                await cleanup_tables(session, Base)
        ```
    """
    tables = base.metadata.sorted_tables
    if not tables:
        return

    table_names = ", ".join(f'"{t.name}"' for t in tables)
    await session.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    await session.commit()


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
        NotFoundError: If the row does not exist or is deleted during polling
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
        raise NotFoundError(f"{model.__name__} with pk={pk_value!r} not found")

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
            raise NotFoundError(f"{model.__name__} with pk={pk_value!r} was deleted")

        current = {col: getattr(instance, col) for col in watch_cols}
        if current != initial:
            return instance


def _m2m_prop(rel_attr: QueryableAttribute) -> RelationshipProperty:  # type: ignore[type-arg]
    """Return the validated M2M RelationshipProperty for *rel_attr*.

    Raises TypeError if *rel_attr* is not a Many-to-Many relationship.
    """
    prop = rel_attr.property
    if not isinstance(prop, RelationshipProperty) or prop.secondary is None:
        raise TypeError(
            f"m2m helpers require a Many-to-Many relationship attribute, "
            f"got {rel_attr!r}. Use a relationship with a secondary table."
        )
    return prop


async def m2m_add(
    session: AsyncSession,
    instance: DeclarativeBase,
    rel_attr: QueryableAttribute,
    *related: DeclarativeBase,
    ignore_conflicts: bool = False,
) -> None:
    """Insert rows into a Many-to-Many association table without loading the ORM collection.

    Args:
        session: DB async session.
        instance: The "owner" side model instance (e.g. the ``A`` in ``A.b_list``).
        rel_attr: The M2M relationship attribute on the model class (e.g. ``A.b_list``).
        *related: One or more related instances to associate with ``instance``.
        ignore_conflicts: When ``True``, silently skip rows that already exist
            in the association table (``ON CONFLICT DO NOTHING``).

    Raises:
        TypeError: If ``rel_attr`` is not a Many-to-Many relationship.
    """
    prop = _m2m_prop(rel_attr)
    if not related:
        return

    secondary = cast(Table, prop.secondary)
    assert secondary is not None  # guaranteed by _m2m_prop
    sync_pairs = prop.secondary_synchronize_pairs
    assert sync_pairs is not None  # set whenever secondary is set

    # synchronize_pairs:           [(parent_col, assoc_col), ...]
    # secondary_synchronize_pairs: [(related_col, assoc_col), ...]
    rows: list[dict[str, Any]] = []
    for rel_instance in related:
        row: dict[str, Any] = {}
        for parent_col, assoc_col in prop.synchronize_pairs:
            row[assoc_col.name] = getattr(instance, cast(str, parent_col.key))
        for related_col, assoc_col in sync_pairs:
            row[assoc_col.name] = getattr(rel_instance, cast(str, related_col.key))
        rows.append(row)

    stmt = pg_insert(secondary).values(rows)
    if ignore_conflicts:
        stmt = stmt.on_conflict_do_nothing()
    await session.execute(stmt)


async def m2m_remove(
    session: AsyncSession,
    instance: DeclarativeBase,
    rel_attr: QueryableAttribute,
    *related: DeclarativeBase,
) -> None:
    """Remove rows from a Many-to-Many association table without loading the ORM collection.

    Args:
        session: DB async session.
        instance: The "owner" side model instance (e.g. the ``A`` in ``A.b_list``).
        rel_attr: The M2M relationship attribute on the model class (e.g. ``A.b_list``).
        *related: One or more related instances to disassociate from ``instance``.

    Raises:
        TypeError: If ``rel_attr`` is not a Many-to-Many relationship.
    """
    prop = _m2m_prop(rel_attr)
    if not related:
        return

    secondary = cast(Table, prop.secondary)
    assert secondary is not None  # guaranteed by _m2m_prop
    related_pairs = prop.secondary_synchronize_pairs
    assert related_pairs is not None  # set whenever secondary is set

    parent_where = [
        assoc_col == getattr(instance, cast(str, parent_col.key))
        for parent_col, assoc_col in prop.synchronize_pairs
    ]

    if len(related_pairs) == 1:
        related_col, assoc_col = related_pairs[0]
        related_values = [getattr(r, cast(str, related_col.key)) for r in related]
        related_where = assoc_col.in_(related_values)
    else:
        assoc_cols = [ac for _, ac in related_pairs]
        rel_cols = [rc for rc, _ in related_pairs]
        related_values_t = [
            tuple(getattr(r, cast(str, rc.key)) for rc in rel_cols) for r in related
        ]
        related_where = tuple_(*assoc_cols).in_(related_values_t)

    await session.execute(delete(secondary).where(*parent_where, related_where))


async def m2m_set(
    session: AsyncSession,
    instance: DeclarativeBase,
    rel_attr: QueryableAttribute,
    *related: DeclarativeBase,
) -> None:
    """Replace the entire Many-to-Many association set atomically.

    Args:
        session: DB async session.
        instance: The "owner" side model instance (e.g. the ``A`` in ``A.b_list``).
        rel_attr: The M2M relationship attribute on the model class (e.g. ``A.b_list``).
        *related: The new complete set of related instances.

    Raises:
        TypeError: If ``rel_attr`` is not a Many-to-Many relationship.
    """
    prop = _m2m_prop(rel_attr)
    secondary = cast(Table, prop.secondary)
    assert secondary is not None  # guaranteed by _m2m_prop

    parent_where = [
        assoc_col == getattr(instance, cast(str, parent_col.key))
        for parent_col, assoc_col in prop.synchronize_pairs
    ]
    await session.execute(delete(secondary).where(*parent_where))

    if related:
        await m2m_add(session, instance, rel_attr, *related)
