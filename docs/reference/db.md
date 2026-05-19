# `db`

Here's the reference for all database session utilities, transaction helpers, and locking functions.

You can import them directly from `fastapi_toolsets.db`:

```python
from fastapi_toolsets.db import (
    LockMode,
    advisory_lock,
    cleanup_tables,
    create_database,
    create_db_dependency,
    create_db_context,
    get_transaction,
    lock_tables,
    m2m_add,
    m2m_remove,
    m2m_set,
    wait_for_row_change,
)
```

## ::: fastapi_toolsets.db.LockMode

## ::: fastapi_toolsets.db.create_db_dependency

## ::: fastapi_toolsets.db.create_db_context

## ::: fastapi_toolsets.db.get_transaction

## ::: fastapi_toolsets.db.lock_tables

## ::: fastapi_toolsets.db.advisory_lock

## ::: fastapi_toolsets.db.wait_for_row_change

## ::: fastapi_toolsets.db.create_database

## ::: fastapi_toolsets.db.cleanup_tables

## ::: fastapi_toolsets.db.m2m_add

## ::: fastapi_toolsets.db.m2m_remove

## ::: fastapi_toolsets.db.m2m_set
