"""Pytest helper utilities for FastAPI testing."""

import os
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..db import create_db_context


@asynccontextmanager
async def create_async_client(
    app: Any,
    base_url: str = "http://test",
    dependency_overrides: dict[Callable[..., Any], Callable[..., Any]] | None = None,
) -> AsyncGenerator[AsyncClient, None]:
    """Create an async httpx client for testing FastAPI applications.

    Args:
        app: FastAPI application instance.
        base_url: Base URL for requests. Defaults to "http://test".
        dependency_overrides: Optional mapping of original dependencies to
            their test replacements. Applied via ``app.dependency_overrides``
            before yielding and cleaned up after.

    Yields:
        An AsyncClient configured for the app.

    Example:
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

    Example with dependency overrides:
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
    """
    if dependency_overrides:
        app.dependency_overrides.update(dependency_overrides)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url=base_url) as client:
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

    Yields:
        An AsyncSession ready for database operations.

    Example:
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
    """
    engine = create_async_engine(database_url, echo=echo)

    try:
        # Create tables
        async with engine.begin() as conn:
            await conn.run_sync(base.metadata.create_all)

        # Create session using existing db context utility
        session_maker = async_sessionmaker(engine, expire_on_commit=expire_on_commit)
        get_session = create_db_context(session_maker)

        async with get_session() as session:
            yield session

            if cleanup:
                await cleanup_tables(session, base)

        if drop_tables:
            async with engine.begin() as conn:
                await conn.run_sync(base.metadata.drop_all)
    finally:
        await engine.dispose()


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


def worker_database_url(database_url: str, default_test_db: str) -> str:
    """Derive a per-worker database URL for pytest-xdist parallel runs.

    Appends ``_{worker_name}`` to the database name so each xdist worker
    operates on its own database.  When not running under xdist,
    ``_{default_test_db}`` is appended instead.

    The worker name is read from the ``PYTEST_XDIST_WORKER`` environment
    variable (set automatically by xdist in each worker process).

    Args:
        database_url: Original database connection URL.
        default_test_db: Suffix appended to the database name when
            ``PYTEST_XDIST_WORKER`` is not set.

    Returns:
        A database URL with a worker- or default-specific database name.

    Example:
        # With PYTEST_XDIST_WORKER="gw0":
        url = worker_database_url(
            "postgresql+asyncpg://user:pass@localhost/test_db",
            default_test_db="test",
        )
        # "postgresql+asyncpg://user:pass@localhost/test_db_gw0"

        # Without PYTEST_XDIST_WORKER:
        url = worker_database_url(
            "postgresql+asyncpg://user:pass@localhost/test_db",
            default_test_db="test",
        )
        # "postgresql+asyncpg://user:pass@localhost/test_db_test"
    """
    worker = _get_xdist_worker(default_test_db=default_test_db)

    url = make_url(database_url)
    url = url.set(database=f"{url.database}_{worker}")
    return url.render_as_string(hide_password=False)


@asynccontextmanager
async def create_worker_database(
    database_url: str,
    default_test_db: str = "test_db",
) -> AsyncGenerator[str, None]:
    """Create and drop a per-worker database for pytest-xdist isolation.

    Intended for use as a **session-scoped** fixture.  Connects to the server
    using the original *database_url* (with ``AUTOCOMMIT`` isolation for DDL),
    creates a dedicated database for the worker, and yields the worker-specific
    URL.  On cleanup the worker database is dropped.

    When running under xdist the database name is suffixed with the worker
    name (e.g. ``_gw0``).  Otherwise it is suffixed with *default_test_db*.

    Args:
        database_url: Original database connection URL.
        default_test_db: Suffix appended to the database name when
            ``PYTEST_XDIST_WORKER`` is not set. Defaults to ``"test_db"``.

    Yields:
        The worker-specific database URL.

    Example:
        from fastapi_toolsets.pytest import (
            create_worker_database, create_db_session,
        )

        DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost/test_db"

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
    """
    worker_url = worker_database_url(
        database_url=database_url, default_test_db=default_test_db
    )
    worker_db_name = make_url(worker_url).database

    engine = create_async_engine(
        database_url,
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {worker_db_name}"))
            await conn.execute(text(f"CREATE DATABASE {worker_db_name}"))

        yield worker_url

        async with engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {worker_db_name}"))
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
        @pytest.fixture
        async def db_session(worker_db_url):
            async with create_db_session(worker_db_url, Base) as session:
                yield session
                await cleanup_tables(session, Base)
    """
    tables = base.metadata.sorted_tables
    if not tables:
        return

    table_names = ", ".join(f'"{t.name}"' for t in tables)
    await session.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    await session.commit()
