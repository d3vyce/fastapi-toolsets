"""Database admin and test helpers: DDL and truncation."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase


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
        from fastapi_toolsets.db.testing import create_database

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
    across every table in *base*'s metadata.

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
