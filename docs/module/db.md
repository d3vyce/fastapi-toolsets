# DB

SQLAlchemy async session management with transactions, table locking, advisory locking, and row-change polling.

!!! info
    This module has been coded and tested to be compatible with PostgreSQL only.

## Overview

The `db` module provides helpers to create FastAPI dependencies and context managers for `AsyncSession`, along with utilities for nested transactions, table locks, advisory locks, and polling for row changes.

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

[`lock_tables`](../reference/db.md#fastapi_toolsets.db.lock_tables) acquires PostgreSQL table-level locks before executing critical sections. It opens a **dedicated session** internally and yields it to the caller, so the lock is guaranteed to be released when the context exits:

```python
from fastapi_toolsets.db import lock_tables, LockMode

async with lock_tables(session_maker=session_maker, tables=[User], mode=LockMode.EXCLUSIVE) as session:
    # No other transaction can modify User until this block exits
    ...
```

Available lock modes are defined in [`LockMode`](../reference/db.md#fastapi_toolsets.db.LockMode): `ACCESS_SHARE`, `ROW_SHARE`, `ROW_EXCLUSIVE`, `SHARE_UPDATE_EXCLUSIVE`, `SHARE`, `SHARE_ROW_EXCLUSIVE`, `EXCLUSIVE`, `ACCESS_EXCLUSIVE`.

Pass `timeout` to limit how long the lock waits before giving up. On timeout, a [`LockTimeoutError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.LockTimeoutError) is raised instead of a raw database error:

```python
async with lock_tables(session_maker, [Order], timeout="2s") as session:
    ...
```

## Advisory locking

[`advisory_lock`](../reference/db.md#fastapi_toolsets.db.advisory_lock) acquires a PostgreSQL session-level advisory lock. The lock is released explicitly when the context exits, regardless of whether the transaction has committed.

```python
from fastapi_toolsets.db import advisory_lock

# Blocking exclusive lock — waits until the lock is free
async with advisory_lock(session=session, key=42):
    ...

# Non-blocking — yields False immediately if already held
async with advisory_lock(session=session, key=42, nowait=True) as acquired:
    if not acquired:
        raise HTTPException(409, "Resource is locked")

# Blocking with a timeout — raises LockTimeoutError if not acquired in time
async with advisory_lock(session=session, key=42, timeout="5s"):
    ...

# Shared — multiple readers allowed simultaneously, blocks exclusive writers
async with advisory_lock(session=session, key=42, shared=True):
    ...

# Two-integer key for namespacing (e.g. lock_type + resource_id)
async with advisory_lock(session=session, key=(1, user_id)):
    ...
```

!!! note
    Advisory locks use PostgreSQL session-level functions (`pg_advisory_lock` / `pg_advisory_unlock`). The lock is tied to the database connection, not the SQLAlchemy transaction — it is released when the context exits, even if the surrounding transaction is still open.

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

!!! info "Added in `v2.1`"

[`create_database`](../reference/db.md#fastapi_toolsets.db.create_database) creates a database at a given URL. It connects to *server_url* and issues a `CREATE DATABASE` statement:

```python
from fastapi_toolsets.db import create_database

SERVER_URL = "postgresql+asyncpg://postgres:postgres@localhost/postgres"

await create_database(db_name="myapp_test", server_url=SERVER_URL)
```

For test isolation with automatic cleanup, use [`create_worker_database`](../reference/pytest.md#fastapi_toolsets.pytest.utils.create_worker_database) from the `pytest` module instead — it handles drop-before, create, and drop-after automatically.

## Cleaning up tables

!!! info "Added in `v2.1`"

[`cleanup_tables`](../reference/db.md#fastapi_toolsets.db.cleanup_tables) truncates all tables:

```python
from fastapi_toolsets.db import cleanup_tables

@pytest.fixture(autouse=True)
async def clean(db_session):
    yield
    await cleanup_tables(session=db_session, base=Base)
```

## Many-to-Many helpers

SQLAlchemy's ORM collection API triggers lazy-loads when you append to a relationship inside a savepoint (e.g. inside `lock_tables` or a nested `get_transaction`). The three `m2m_*` helpers bypass the ORM collection entirely and issue direct SQL against the association table.

### `m2m_add` — insert associations

[`m2m_add`](../reference/db.md#fastapi_toolsets.db.m2m_add) inserts one or more rows into a secondary table without touching the ORM collection:

```python
from fastapi_toolsets.db import lock_tables, m2m_add

async with lock_tables(session_maker, [Tag]) as session:
    tag = await TagCrud.create(session, TagCreate(name="python"))
    await m2m_add(session, post, Post.tags, tag)
```

Pass `ignore_conflicts=True` to silently skip associations that already exist:

```python
await m2m_add(session, post, Post.tags, tag, ignore_conflicts=True)
```

### `m2m_remove` — delete associations

[`m2m_remove`](../reference/db.md#fastapi_toolsets.db.m2m_remove) deletes specific association rows. Removing a non-existent association is a no-op:

```python
from fastapi_toolsets.db import get_transaction, m2m_remove

async with get_transaction(session):
    await m2m_remove(session, post, Post.tags, tag1, tag2)
```

### `m2m_set` — replace the full set

[`m2m_set`](../reference/db.md#fastapi_toolsets.db.m2m_set) atomically replaces all associations: it deletes every existing row for the owner instance then inserts the new set. Passing no related instances clears the association entirely:

```python
from fastapi_toolsets.db import get_transaction, m2m_set

# Replace all tags
async with get_transaction(session):
    await m2m_set(session, post, Post.tags, tag_a, tag_b)

# Clear all tags
async with get_transaction(session):
    await m2m_set(session, post, Post.tags)
```

All three helpers raise `TypeError` if the relationship attribute is not a Many-to-Many (i.e. has no secondary table).

---

[:material-api: API Reference](../reference/db.md)
