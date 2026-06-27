# DB

SQLAlchemy async session management with transactions, table locking, advisory locking, and row-change polling.

!!! info
    This module has been coded and tested to be compatible with PostgreSQL only.

## Overview

The `db` module is built around one object, [`Database`](../reference/db.md#fastapi_toolsets.db.Database), which owns the engine and sessionmaker and exposes the FastAPI dependency, a commit-before-response middleware, session/transaction context managers, and table locking. Free helpers cover savepoint-aware transactions, advisory locks, many-to-many association tables, and row-change polling.

## Setup

Create one `Database` for your app. Provide a **URL** (the facade builds and disposes the engine) or pass an existing **`engine=`** you own (e.g. for Alembic or `event.listen`). The session factory is built internally with `expire_on_commit=False`.

```python
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_toolsets.db import Database

db = Database("postgresql+asyncpg://postgres:postgres@localhost/app")

app = FastAPI()
db.install(app)  # commit middleware + engine disposal on shutdown

@app.get("/users")
async def list_users(session: AsyncSession = Depends(db)):
    ...
```

The `Database` instance **is** the dependency: use it directly as `Depends(db)`. The whole request runs as a single transaction (CRUD writes use savepoints under it).

The **URL** may be a plain string or a Pydantic [`PostgresDsn`](https://docs.pydantic.dev/latest/api/networks/#pydantic.networks.PostgresDsn). In URL mode you can tune the engine: pass `connect_args` for DBAPI-level options and any other keyword for `create_async_engine` (e.g. `pool_size`, `echo`, `pool_pre_ping`):

```python
from pydantic_settings import BaseSettings
from pydantic import PostgresDsn

class Settings(BaseSettings):
    database_url: PostgresDsn

settings = Settings()

db = Database(
    settings.database_url,
    pool_size=20,
    pool_pre_ping=True,
    connect_args={"server_settings": {"application_name": "myapp"}},
)
```

## Committing before the response

[`db.install(app)`](../reference/db.md#fastapi_toolsets.db.Database) adds a middleware that commits the request's session when the response starts, after the endpoint returns and before the body is sent. With the middleware installed, the dependency does not commit again.

The request is committed as a single transaction:

- **Read-after-write**: a follow-up request sees the write.
- **Atomicity**: multi-write endpoints roll back as a unit on failure.
- **Errors roll back**: on a raised exception the session rolls back and nothing is committed.

Without `install`, the session commits in the dependency teardown, which runs after the response has been sent.

!!! warning "Streaming / SSE endpoints"
    For a `StreamingResponse` / `EventSourceResponse`, the commit fires at the **start** of the stream. A stream that **writes** must open a short-lived session per write with [`db.session()`](#session-context-manager); the start-time commit will not flush writes made later during the stream.

## Lifespan

`db.install(app)` disposes the engine on shutdown, composing around your own lifespan:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    await warm_cache()   # your startup
    yield
    await flush_metrics()  # your shutdown

app = FastAPI(lifespan=lifespan)
db.install(app)  # your shutdown runs first, then the engine is disposed
```

If you have no lifespan of your own, [`db.lifespan`](../reference/db.md#fastapi_toolsets.db.Database) works standalone as `FastAPI(lifespan=db.lifespan)`. Engine disposal is idempotent and is a no-op when you passed your own `engine=`.

## Session context manager

Use [`db.session()`](../reference/db.md#fastapi_toolsets.db.Database) for sessions outside request handlers (e.g. background tasks, CLI commands). It commits on clean exit and rolls back on exception:

```python
async def seed():
    async with db.session() as session:
        ...
```

## Transactions

[`transaction`](../reference/db.md#fastapi_toolsets.db.transaction) opens a transaction on a session, using a savepoint when one is already open so it nests safely:

```python
from fastapi_toolsets.db import transaction

async def create_user_with_role(session):
    async with transaction(session):
        ...
        async with transaction(session):  # uses a savepoint
            ...
```

When you have a `Database`, [`db.begin()`](../reference/db.md#fastapi_toolsets.db.Database) opens a session already inside a transaction:

```python
async with db.begin() as session:
    session.add(User(name="ada"))  # commits on exit, rolls back on exception
```

## Table locking

[`db.lock_tables`](../reference/db.md#fastapi_toolsets.db.Database) acquires PostgreSQL table-level locks for a critical section. It opens a dedicated session internally and releases the lock when the context exits:

```python
from fastapi_toolsets.db import LockMode

async with db.lock_tables([User], mode=LockMode.EXCLUSIVE) as session:
    # No other transaction can modify User until this block exits
    ...
```

Available lock modes are defined in [`LockMode`](../reference/db.md#fastapi_toolsets.db.LockMode): `ACCESS_SHARE`, `ROW_SHARE`, `ROW_EXCLUSIVE`, `SHARE_UPDATE_EXCLUSIVE`, `SHARE`, `SHARE_ROW_EXCLUSIVE`, `EXCLUSIVE`, `ACCESS_EXCLUSIVE`.

Pass `timeout` to limit how long the lock waits. On timeout, a [`LockTimeoutError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.LockTimeoutError) is raised instead of a raw database error:

```python
async with db.lock_tables([Order], timeout="2s") as session:
    ...
```

## Advisory locking

[`advisory_lock`](../reference/db.md#fastapi_toolsets.db.advisory_lock) acquires a PostgreSQL session-level advisory lock on a session you provide. The lock is released when the context exits:

```python
from fastapi_toolsets.db import advisory_lock

# Blocking exclusive lock: waits until the lock is free
async with advisory_lock(session=session, key=42):
    ...

# Non-blocking: yields False immediately if already held
async with advisory_lock(session=session, key=42, nowait=True) as acquired:
    if not acquired:
        raise HTTPException(409, "Resource is locked")

# Blocking with a timeout: raises LockTimeoutError if not acquired in time
async with advisory_lock(session=session, key=42, timeout="5s"):
    ...

# Shared lock: multiple readers allowed simultaneously, blocks exclusive writers
async with advisory_lock(session=session, key=42, shared=True):
    ...

# Two-integer key for namespacing (e.g. lock_type + resource_id)
async with advisory_lock(session=session, key=(1, user_id)):
    ...
```

!!! note
    Advisory locks use PostgreSQL session-level functions (`pg_advisory_lock` / `pg_advisory_unlock`). The lock is tied to the database connection, not the SQLAlchemy transaction, so it is released when the context exits even if the surrounding transaction is still open.

## Row-change polling

[`wait_for_row_change`](../reference/db.md#fastapi_toolsets.db.wait_for_row_change) polls a row until a specific column changes value:

```python
from fastapi_toolsets.db import wait_for_row_change

# Wait up to 30s for order.status to change
await wait_for_row_change(
    session=session,
    model=Order,
    pk_value=order_id,
    columns=["status"],
    interval=1.0,
    timeout=30.0,
)
```

## Creating a database

[`create_database`](../reference/db.md#fastapi_toolsets.db.testing.create_database) (in `fastapi_toolsets.db.testing`) connects to *server_url* and issues a `CREATE DATABASE` statement:

```python
from fastapi_toolsets.db.testing import create_database

SERVER_URL = "postgresql+asyncpg://postgres:postgres@localhost/postgres"

await create_database(db_name="myapp_test", server_url=SERVER_URL)
```

For test isolation with automatic cleanup, use [`create_worker_database`](../reference/pytest.md#fastapi_toolsets.pytest.utils.create_worker_database) from the `pytest` module, which handles drop-before, create, and drop-after.

## Cleaning up tables

[`cleanup_tables`](../reference/db.md#fastapi_toolsets.db.testing.cleanup_tables) (in `fastapi_toolsets.db.testing`) truncates all tables:

```python
from fastapi_toolsets.db.testing import cleanup_tables

@pytest.fixture(autouse=True)
async def clean(db_session):
    yield
    await cleanup_tables(session=db_session, base=Base)
```

## Many-to-Many helpers

The three `m2m_*` helpers modify a many-to-many association table with direct SQL, without loading the ORM collection.

### `m2m_add`: insert associations

[`m2m_add`](../reference/db.md#fastapi_toolsets.db.m2m_add) inserts one or more rows into a secondary table:

```python
from fastapi_toolsets.db import m2m_add

async with db.lock_tables([Tag]) as session:
    tag = await TagCrud.create(session, TagCreate(name="python"))
    await m2m_add(session, post, Post.tags, tag)
```

Pass `ignore_conflicts=True` to skip associations that already exist:

```python
await m2m_add(session, post, Post.tags, tag, ignore_conflicts=True)
```

### `m2m_remove`: delete associations

[`m2m_remove`](../reference/db.md#fastapi_toolsets.db.m2m_remove) deletes specific association rows. Removing a non-existent association is a no-op:

```python
from fastapi_toolsets.db import m2m_remove, transaction

async with transaction(session):
    await m2m_remove(session, post, Post.tags, tag1, tag2)
```

### `m2m_set`: replace the full set

[`m2m_set`](../reference/db.md#fastapi_toolsets.db.m2m_set) replaces all associations: it deletes every existing row for the owner instance then inserts the new set. Passing no related instances clears the association:

```python
from fastapi_toolsets.db import m2m_set, transaction

# Replace all tags
async with transaction(session):
    await m2m_set(session, post, Post.tags, tag_a, tag_b)

# Clear all tags
async with transaction(session):
    await m2m_set(session, post, Post.tags)
```

All three helpers raise `TypeError` if the relationship attribute is not a Many-to-Many (i.e. has no secondary table).

---

[:material-api: API Reference](../reference/db.md)
