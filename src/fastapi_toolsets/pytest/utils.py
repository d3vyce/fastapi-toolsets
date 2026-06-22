"""Pytest helper utilities for FastAPI testing."""

import os
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..db.testing import cleanup_tables, create_database
from ..models.watched import EventSession


def _get_xdist_worker(default_test_db: str) -> str:
    """Return the pytest-xdist worker name, or *default_test_db* when not running under xdist.

    Reads the ``PYTEST_XDIST_WORKER`` environment variable that xdist sets
    automatically in each worker process (e.g. ``"gw0"``, ``"gw1"``).
    When xdist is not installed or not active, the variable is absent and
    *default_test_db* is returned instead.

    Args:
        default_test_db: Fallback value returned when ``PYTEST_XDIST_WORKER``
            is not set.
    """
    return os.environ.get("PYTEST_XDIST_WORKER", default_test_db)


def worker_database_url(
    database_url: str,
    default_test_db: str,
    *,
    prefix: str | None = None,
) -> str:
    """Derive a per-worker database URL for pytest-xdist parallel runs.

    Sets the database name to the worker name so each xdist worker operates
    on its own database.  When not running under xdist, *default_test_db* is
    used instead.  When *prefix* is provided, the name becomes
    ``{prefix}_{worker}``.

    The worker name is read from the ``PYTEST_XDIST_WORKER`` environment
    variable (set automatically by xdist in each worker process).

    Args:
        database_url: Original database connection URL.
        default_test_db: Suffix appended to the database name when
            ``PYTEST_XDIST_WORKER`` is not set.
        prefix: Optional prefix prepended to the worker name
            (e.g. ``"test"`` → ``"test_gw0"``).  Without it, the database
            name is just the worker name (e.g. ``"gw0"``).

    Returns:
        A database URL with a worker- or default-specific database name.
    """
    worker = _get_xdist_worker(default_test_db=default_test_db)

    url = make_url(database_url)
    db_name = f"{prefix}_{worker}" if prefix else worker
    url = url.set(database=db_name)
    return url.render_as_string(hide_password=False)


@asynccontextmanager
async def create_worker_database(
    database_url: str,
    default_test_db: str = "test_db",
    *,
    prefix: str | None = None,
    server_url: str | None = None,
) -> AsyncGenerator[str, None]:
    """Create and drop a per-worker database for pytest-xdist isolation.

    Derives a worker-specific database URL using :func:`worker_database_url`,
    then delegates to :func:`~fastapi_toolsets.db.create_database` to create
    and drop it.  Intended for use as a **session-scoped** fixture.

    When running under xdist the database name is suffixed with the worker
    name (e.g. ``_gw0``).  Otherwise it is suffixed with *default_test_db*.

    Args:
        database_url: Original database connection URL (used as the base for
            the worker database name).
        default_test_db: Suffix appended to the database name when
            ``PYTEST_XDIST_WORKER`` is not set. Defaults to ``"test_db"``.
        prefix: Optional prefix prepended to the worker name
            (e.g. ``prefix="test"`` → ``"test_gw0"``).  Without it, the
            database name is just the worker name (e.g. ``"gw0"``).
        server_url: URL used for server-level DDL (must point to an existing
            database on the same server).  Defaults to *database_url* with the
            database omitted, letting asyncpg fall back to the username.

    Yields:
        The worker-specific database URL.

    Example:
        ```python
        from fastapi_toolsets.pytest import create_worker_database, create_db_session

        DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost/myapp"

        @pytest.fixture(scope="session")
        async def worker_db_url():
            async with create_worker_database(DATABASE_URL) as url:
                yield url

        @pytest.fixture
        async def db_session(worker_db_url):
            async with create_db_session(
                worker_db_url, Base, cleanup=True
            ) as session:
                yield session
        ```
    """
    worker_url = worker_database_url(
        database_url=database_url, default_test_db=default_test_db, prefix=prefix
    )
    worker_db_name = make_url(worker_url).database
    assert worker_db_name is not None

    _parsed = make_url(database_url)
    _server_url = server_url or URL.create(
        drivername=_parsed.drivername,
        username=_parsed.username,
        password=_parsed.password,
        host=_parsed.host,
        port=_parsed.port,
        query=_parsed.query,
    ).render_as_string(hide_password=False)

    engine = create_async_engine(_server_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text(f"DROP DATABASE IF EXISTS {worker_db_name} WITH (FORCE)")
            )
        await create_database(db_name=worker_db_name, server_url=_server_url)

        yield worker_url

        async with engine.connect() as conn:
            await conn.execute(
                text(f"DROP DATABASE IF EXISTS {worker_db_name} WITH (FORCE)")
            )
    finally:
        await engine.dispose()


@asynccontextmanager
async def create_async_client(
    app: Any,
    base_url: str = "http://test",
    dependency_overrides: dict[Callable[..., Any], Callable[..., Any]] | None = None,
    **kwargs: Any,
) -> AsyncGenerator[AsyncClient, None]:
    """Create an async httpx client for testing FastAPI applications.

    Args:
        app: FastAPI application instance.
        base_url: Base URL for requests. Defaults to "http://test".
        dependency_overrides: Optional mapping of original dependencies to
            their test replacements. Applied via ``app.dependency_overrides``
            before yielding and cleaned up after.
        **kwargs: Additional keyword arguments forwarded to
            :class:`httpx.AsyncClient` (e.g. ``headers``, ``cookies``,
            ``auth``, ``timeout``).

    Yields:
        An AsyncClient configured for the app.

    Example:
        ```python
        from fastapi import FastAPI
        from fastapi_toolsets.pytest import create_async_client

        app = FastAPI()

        @pytest.fixture
        async def client():
            async with create_async_client(app) as c:
                yield c

        async def test_endpoint(client: AsyncClient):
            response = await client.get("/health")
            assert response.status_code == 200
        ```

    Example with dependency overrides:
        ```python
        from fastapi_toolsets.pytest import create_async_client, create_db_session
        from app.db import get_db

        @pytest.fixture
        async def db_session():
            async with create_db_session(DATABASE_URL, Base, cleanup=True) as session:
                yield session

        @pytest.fixture
        async def client(db_session):
            async def override():
                yield db_session

            async with create_async_client(
                app, dependency_overrides={get_db: override}
            ) as c:
                yield c
        ```
    """
    if dependency_overrides:
        app.dependency_overrides.update(dependency_overrides)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url=base_url, **kwargs
        ) as client:
            yield client
    finally:
        if dependency_overrides:
            for key in dependency_overrides:
                app.dependency_overrides.pop(key, None)


@asynccontextmanager
async def create_db_session(
    database_url: str,
    base: type[DeclarativeBase],
    *,
    echo: bool = False,
    expire_on_commit: bool = False,
    drop_tables: bool = True,
    cleanup: bool = False,
    engine_kwargs: dict[str, Any] | None = None,
    session_kwargs: dict[str, Any] | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Create a database session for testing.

    Creates tables before yielding the session and optionally drops them after.
    Each call creates a fresh engine and session for test isolation.

    Args:
        database_url: Database connection URL (e.g., "postgresql+asyncpg://...").
        base: SQLAlchemy DeclarativeBase class containing model metadata.
        echo: Enable SQLAlchemy query logging. Defaults to False.
        expire_on_commit: Expire objects after commit. Defaults to False.
        drop_tables: Drop tables after test. Defaults to True.
        cleanup: Truncate all tables after test using
            :func:`cleanup_tables`. Defaults to False.
        engine_kwargs: Additional keyword arguments forwarded to
            :func:`sqlalchemy.ext.asyncio.create_async_engine`
            (e.g. ``pool_size``, ``connect_args``).
        session_kwargs: Additional keyword arguments forwarded to
            :class:`sqlalchemy.ext.asyncio.async_sessionmaker`
            (e.g. ``autoflush``, ``class_``).

    Yields:
        An AsyncSession ready for database operations.

    Example:
        ```python
        from fastapi_toolsets.pytest import create_db_session
        from app.models import Base

        DATABASE_URL = "postgresql+asyncpg://user:pass@localhost/test_db"

        @pytest.fixture
        async def db_session():
            async with create_db_session(
                DATABASE_URL, Base, cleanup=True
            ) as session:
                yield session

        async def test_create_user(db_session: AsyncSession):
            user = User(name="test")
            db_session.add(user)
            await db_session.commit()
        ```
    """
    engine = create_async_engine(database_url, echo=echo, **(engine_kwargs or {}))

    try:
        async with engine.begin() as conn:
            await conn.run_sync(base.metadata.create_all)

        session_maker = async_sessionmaker(
            engine,
            expire_on_commit=expire_on_commit,
            class_=EventSession,
            **(session_kwargs or {}),
        )
        async with session_maker() as session:
            yield session

            if cleanup:
                await cleanup_tables(session=session, base=base)

        if drop_tables:
            async with engine.begin() as conn:
                await conn.run_sync(base.metadata.drop_all)
    finally:
        await engine.dispose()
