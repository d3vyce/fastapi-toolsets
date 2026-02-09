"""Pytest helper utilities for FastAPI testing."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Literal

from httpx import ASGITransport, AsyncClient, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from ..db import create_db_context


@asynccontextmanager
async def create_async_client(
    app: Any,
    base_url: str = "http://test",
) -> AsyncGenerator[AsyncClient, None]:
    """Create an async httpx client for testing FastAPI applications.

    Args:
        app: FastAPI application instance.
        base_url: Base URL for requests. Defaults to "http://test".

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
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url=base_url) as client:
        yield client


@asynccontextmanager
async def create_db_session(
    database_url: str,
    base: type[DeclarativeBase],
    *,
    echo: bool = False,
    expire_on_commit: bool = False,
    drop_tables: bool = True,
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

    Yields:
        An AsyncSession ready for database operations.

    Example:
        ```python
        from fastapi_toolsets.pytest import create_db_session
        from app.models import Base

        DATABASE_URL = "postgresql+asyncpg://user:pass@localhost/test_db"

        @pytest.fixture
        async def db_session():
            async with create_db_session(DATABASE_URL, Base) as session:
                yield session

        async def test_create_user(db_session: AsyncSession):
            user = User(name="test")
            db_session.add(user)
            await db_session.commit()
        ```
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

        if drop_tables:
            async with engine.begin() as conn:
                await conn.run_sync(base.metadata.drop_all)
    finally:
        await engine.dispose()


def _normalize_expected(
    expected: BaseModel | list[BaseModel] | dict | list[dict],
) -> Any:
    """Normalize expected data to a JSON-compatible structure."""
    if isinstance(expected, BaseModel):
        return expected.model_dump(mode="json")
    if isinstance(expected, list):
        return [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in expected
        ]
    return expected


HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


async def assert_endpoint(
    client: AsyncClient,
    method: HttpMethod,
    url: str,
    *,
    expected_status: int = 200,
    expected_data: BaseModel | list[BaseModel] | dict | list[dict] | None = None,
    request_headers: dict[str, str] | None = None,
    request_json: Any | None = None,
    request_params: dict[str, Any] | None = None,
    request_content: bytes | None = None,
) -> Response:
    """Assert an API endpoint returns the expected status and data."""
    kwargs: dict[str, Any] = {}
    if request_headers is not None:
        kwargs["headers"] = request_headers
    if request_json is not None:
        kwargs["json"] = request_json
    if request_params is not None:
        kwargs["params"] = request_params
    if request_content is not None:
        kwargs["content"] = request_content

    response = await client.request(method, url, **kwargs)

    assert response.status_code == expected_status, (
        f"Expected status {expected_status}, got {response.status_code}. "
        f"Response body: {response.text}"
    )

    if expected_data is not None:
        response_json = response.json()
        actual_data = response_json.get("data")
        normalized = _normalize_expected(expected_data)
        assert actual_data == normalized, (
            f"Response data mismatch.\nExpected: {normalized}\nActual:   {actual_data}"
        )

    return response
