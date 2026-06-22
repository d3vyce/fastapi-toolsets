# `db`

Here's the reference for the `Database` facade, the transaction helper, locking
functions, many-to-many helpers, and row-watching utilities.

You can import them directly from `fastapi_toolsets.db`:

```python
from fastapi_toolsets.db import (
    Database,
    LockMode,
    advisory_lock,
    lock_tables,
    m2m_add,
    m2m_remove,
    m2m_set,
    transaction,
    wait_for_row_change,
)
```

## ::: fastapi_toolsets.db.Database

## ::: fastapi_toolsets.db.transaction

## ::: fastapi_toolsets.db.LockMode

## ::: fastapi_toolsets.db.lock_tables

## ::: fastapi_toolsets.db.advisory_lock

## ::: fastapi_toolsets.db.m2m_add

## ::: fastapi_toolsets.db.m2m_remove

## ::: fastapi_toolsets.db.m2m_set

## ::: fastapi_toolsets.db.wait_for_row_change

Admin and test helpers live in `fastapi_toolsets.db.testing`:

```python
from fastapi_toolsets.db.testing import cleanup_tables, create_database
```

## ::: fastapi_toolsets.db.testing.create_database

## ::: fastapi_toolsets.db.testing.cleanup_tables
