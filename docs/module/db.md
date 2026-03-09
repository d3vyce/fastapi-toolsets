# DB

SQLAlchemy async session management with transactions, table locking, and row-change polling.

!!! info
    This module has been coded and tested to be compatible with PostgreSQL only.

## Overview

The `db` module provides helpers to create FastAPI dependencies and context managers for `AsyncSession`, along with utilities for nested transactions, table lock and polling for row changes.

## Session dependency

Use [`create_db_dependency`](../reference/db.md#fastapi_toolsets.db.create_db_dependency) to create a FastAPI dependency that yields a session and auto-commits on success:

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fastapi_toolsets.db import create_db_dependency

engine = create_async_engine(url="postgresql+asyncpg://...", future=True)
session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

get_db = create_db_dependency(session_maker=session_maker)

@router.get("/users")
async def list_users(session: AsyncSession = Depends(get_db)):
    ...
```

## Session context manager

Use [`create_db_context`](../reference/db.md#fastapi_toolsets.db.create_db_context) for sessions outside request handlers (e.g. background tasks, CLI commands):

```python
from fastapi_toolsets.db import create_db_context

db_context = create_db_context(session_maker=session_maker)

async def seed():
    async with db_context() as session:
        ...
```

## Nested transactions

[`get_transaction`](../reference/db.md#fastapi_toolsets.db.get_transaction) handles savepoints automatically, allowing safe nesting:

```python
from fastapi_toolsets.db import get_transaction

async def create_user_with_role(session=session):
    async with get_transaction(session=session):
        ...
        async with get_transaction(session=session):  # uses savepoint
            ...
```

## Table locking

[`lock_tables`](../reference/db.md#fastapi_toolsets.db.lock_tables) acquires PostgreSQL table-level locks before executing critical sections:

```python
from fastapi_toolsets.db import lock_tables

async with lock_tables(session=session, tables=[User], mode="EXCLUSIVE"):
    # No other transaction can modify User until this block exits
    ...
```

Available lock modes are defined in [`LockMode`](../reference/db.md#fastapi_toolsets.db.LockMode): `ACCESS_SHARE`, `ROW_SHARE`, `ROW_EXCLUSIVE`, `SHARE_UPDATE_EXCLUSIVE`, `SHARE`, `SHARE_ROW_EXCLUSIVE`, `EXCLUSIVE`, `ACCESS_EXCLUSIVE`.

## Row-change polling

[`wait_for_row_change`](../reference/db.md#fastapi_toolsets.db.wait_for_row_change) polls a row until a specific column changes value, useful for waiting on async side effects:

```python
from fastapi_toolsets.db import wait_for_row_change

# Wait up to 30s for order.status to change
await wait_for_row_change(
    session=session,
    model=Order,
    pk_value=order_id,
    columns=[Order.status],
    interval=1.0,
    timeout=30.0,
)
```

## Creating a database

[`create_database`](../reference/db.md#fastapi_toolsets.db.create_database) creates a database at a given URL. It connects to *server_url* and issues a `CREATE DATABASE` statement:

```python
from fastapi_toolsets.db import create_database

SERVER_URL = "postgresql+asyncpg://postgres:postgres@localhost/postgres"

await create_database(db_name="myapp_test", server_url=SERVER_URL)
```

For test isolation with automatic cleanup, use [`create_worker_database`](../reference/pytest.md#fastapi_toolsets.pytest.utils.create_worker_database) from the `pytest` module instead — it handles drop-before, create, and drop-after automatically.

## Cleaning up tables

[`cleanup_tables`](../reference/db.md#fastapi_toolsets.db.cleanup_tables) truncates all tables:

```python
from fastapi_toolsets.db import cleanup_tables

@pytest.fixture(autouse=True)
async def clean(db_session):
    yield
    await cleanup_tables(session=db_session, base=Base)
```

---

[:material-api: API Reference](../reference/db.md)
