# Pytest

Testing helpers for FastAPI applications with async client, database sessions, and parallel worker support.

## Installation

=== "uv"
    ``` bash
    uv add "fastapi-toolsets[pytest]"
    ```

=== "pip"
    ``` bash
    pip install "fastapi-toolsets[pytest]"
    ```

## Overview

The `pytest` module provides utilities for setting up async test clients, managing test database sessions, and supporting parallel test execution with `pytest-xdist`.

## Creating an async client

Use [`create_async_client`](../reference/pytest.md#fastapi_toolsets.pytest.utils.create_async_client) to get an `httpx.AsyncClient` configured for your FastAPI app:

```python
from fastapi_toolsets.pytest import create_async_client

@pytest.fixture
async def http_client(db_session):
    async def _override_get_db():
        yield db_session

    async with create_async_client(
        app=app,
        base_url="http://127.0.0.1/api/v1",
        dependency_overrides={get_db: _override_get_db},
    ) as c:
        yield c
```

## Database sessions in tests

Use [`create_db_session`](../reference/pytest.md#fastapi_toolsets.pytest.utils.create_db_session) to create an isolated `AsyncSession` for a test:

```python
from fastapi_toolsets.pytest import create_db_session, create_worker_database

@pytest.fixture(scope="session")
async def worker_db_url():
    async with create_worker_database(
        database_url=str(settings.SQLALCHEMY_DATABASE_URI)
    ) as url:
        yield url


@pytest.fixture
async def db_session(worker_db_url):
    async with create_db_session(
        database_url=worker_db_url, base=Base, cleanup=True
    ) as session:
        yield session
```

!!! info
    In this example, the database is reset between each test using the argument `cleanup=True`.

## Parallel testing with pytest-xdist

The examples above are already compatible with parallel test execution with `pytest-xdist`.

## Cleaning up tables

If you want to manually clean up a database you can use [`cleanup_tables`](../reference/pytest.md#fastapi_toolsets.pytest.utils.cleanup_tables), this will truncates all tables between tests for fast isolation:

```python
from fastapi_toolsets.pytest import cleanup_tables

@pytest.fixture(autouse=True)
async def clean(db_session):
    yield
    await cleanup_tables(session=db_session, base=Base)
```

---

[:material-api: API Reference](../reference/pytest.md)
