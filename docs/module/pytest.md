# Pytest

Testing helpers for FastAPI applications: async HTTP client, database sessions, and parallel worker support.

## Installation

=== "uv"
    ``` bash
    uv add "fastapi-toolsets[pytest]"
    ```

=== "pip"
    ``` bash
    pip install "fastapi-toolsets[pytest]"
    ```

## Async client

Use [`create_async_client`](../reference/pytest.md#fastapi_toolsets.pytest.utils.create_async_client) to get an `httpx.AsyncClient` bound to your FastAPI app:

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

Any extra keyword arguments are forwarded to `httpx.AsyncClient`, so you can set default headers, authentication, timeouts, and more:

```python
async with create_async_client(
    app=app,
    headers={"X-Api-Key": "secret"},
    timeout=10,
) as c:
    ...
```

## Database sessions

Use [`create_worker_database`](../reference/pytest.md#fastapi_toolsets.pytest.utils.create_worker_database) + [`create_db_session`](../reference/pytest.md#fastapi_toolsets.pytest.utils.create_db_session) to get a fully isolated `AsyncSession` for each test:

```python
from fastapi_toolsets.pytest import create_worker_database, create_db_session

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

`create_worker_database` connects without specifying a database (asyncpg falls back to the username), so the target test database does not need to exist beforehand.

!!! info
    `cleanup=True` truncates all tables between tests via `TRUNCATE … RESTART IDENTITY CASCADE`, which is faster than dropping and recreating tables.

### Engine and session options

Pass `engine_kwargs` or `session_kwargs` to forward options to the underlying SQLAlchemy primitives:

```python
async with create_db_session(
    database_url=worker_db_url,
    base=Base,
    engine_kwargs={"pool_size": 5, "connect_args": {"timeout": 10}},
    session_kwargs={"autoflush": False},
) as session:
    ...
```

## Parallel testing with pytest-xdist

The fixtures above work with `pytest-xdist` out of the box. Each worker gets its own database suffixed with the worker name (e.g. `myapp_gw0`, `myapp_gw1`).

Use [`worker_database_url`](../reference/pytest.md#fastapi_toolsets.pytest.utils.worker_database_url) to derive the per-worker URL manually if needed:

```python
from fastapi_toolsets.pytest import worker_database_url

url = worker_database_url("postgresql+asyncpg://user:pass@localhost/myapp", default_test_db="test")
# → "postgresql+asyncpg://user:pass@localhost/myapp_gw0" under xdist
# → "postgresql+asyncpg://user:pass@localhost/myapp_test" otherwise
```

## Manual table cleanup

[`cleanup_tables`](../reference/db.md#fastapi_toolsets.db.cleanup_tables) truncates all tables in a single statement and can be called directly when you need more control:

```python
from fastapi_toolsets.db import cleanup_tables

@pytest.fixture(autouse=True)
async def clean(db_session):
    yield
    await cleanup_tables(session=db_session, base=Base)
```

---

[:material-api: API Reference](../reference/pytest.md)
