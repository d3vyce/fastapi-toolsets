# Models

!!! info "Added in `v2.0`"

Reusable SQLAlchemy 2.0 mixins for common column patterns, designed to be composed freely on any `DeclarativeBase` model.

## Overview

The `models` module provides mixins that each add a single, well-defined column behaviour. They work with standard SQLAlchemy 2.0 declarative syntax and are fully compatible with `AsyncSession`.

```python
from fastapi_toolsets.models import UUIDMixin, TimestampMixin


class Article(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "articles"

    title: Mapped[str]
    content: Mapped[str]
```

All timestamp columns are timezone-aware (`TIMESTAMPTZ`). All defaults are server-side (`clock_timestamp()`), so they are also applied when inserting rows via raw SQL outside the ORM.

## Mixins

### [`UUIDMixin`](../reference/models.md#fastapi_toolsets.models.UUIDMixin)

Adds a `id: UUID` primary key generated server-side by PostgreSQL using `gen_random_uuid()`. The value is retrieved via `RETURNING` after insert, so it is available on the Python object immediately after `flush()`.

!!! warning "Requires PostgreSQL 13+"

```python
from fastapi_toolsets.models import UUIDMixin


class User(Base, UUIDMixin):
    __tablename__ = "users"

    username: Mapped[str]


# id is None before flush
user = User(username="alice")
session.add(user)
await session.flush()
print(user.id)  # UUID('...')
```

### [`UUIDv7Mixin`](../reference/models.md#fastapi_toolsets.models.UUIDv7Mixin)

!!! info "Added in `v2.3`"

Adds a `id: UUID` primary key generated server-side by PostgreSQL using `uuidv7()`. It's a time-ordered UUID format that encodes a millisecond-precision timestamp in the most significant bits, making it naturally sortable and index-friendly.

!!! warning "Requires PostgreSQL 18+"

```python
from fastapi_toolsets.models import UUIDv7Mixin


class Event(Base, UUIDv7Mixin):
    __tablename__ = "events"

    name: Mapped[str]


# id is None before flush
event = Event(name="user.signup")
session.add(event)
await session.flush()
print(event.id)  # UUID('019...')
```

### [`CreatedAtMixin`](../reference/models.md#fastapi_toolsets.models.CreatedAtMixin)

Adds a `created_at: datetime` column set to `clock_timestamp()` on insert. The column has no `onupdate` hook — it is intentionally immutable after the row is created.

```python
from fastapi_toolsets.models import UUIDMixin, CreatedAtMixin


class Order(Base, UUIDMixin, CreatedAtMixin):
    __tablename__ = "orders"

    total: Mapped[float]
```

### [`UpdatedAtMixin`](../reference/models.md#fastapi_toolsets.models.UpdatedAtMixin)

Adds an `updated_at: datetime` column set to `clock_timestamp()` on insert and automatically updated to `clock_timestamp()` on every ORM-level update (via SQLAlchemy's `onupdate` hook).

```python
from fastapi_toolsets.models import UUIDMixin, UpdatedAtMixin


class Post(Base, UUIDMixin, UpdatedAtMixin):
    __tablename__ = "posts"

    title: Mapped[str]


post = Post(title="Hello")
await session.flush()
await session.refresh(post)

post.title = "Hello World"
await session.flush()
await session.refresh(post)
print(post.updated_at)
```

!!! note
    `updated_at` is updated by SQLAlchemy at ORM flush time. If you update rows via raw SQL (e.g. `UPDATE posts SET ...`), the column will **not** be updated automatically — use a database trigger if you need that guarantee.

### [`TimestampMixin`](../reference/models.md#fastapi_toolsets.models.TimestampMixin)

Convenience mixin that combines [`CreatedAtMixin`](../reference/models.md#fastapi_toolsets.models.CreatedAtMixin) and [`UpdatedAtMixin`](../reference/models.md#fastapi_toolsets.models.UpdatedAtMixin). Equivalent to inheriting both.

```python
from fastapi_toolsets.models import UUIDMixin, TimestampMixin


class Article(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "articles"

    title: Mapped[str]
```

## Lifecycle events

The event system provides lifecycle callbacks that fire **after commit**. If the transaction rolls back, no callback fires.

### Setup

Event dispatch requires [`EventSession`](../reference/models.md#fastapi_toolsets.models.EventSession). Pass it as the session class when creating your session factory:

```python
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from fastapi_toolsets.models import EventSession

engine = create_async_engine("postgresql+asyncpg://...")
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=EventSession)
```

!!! info "Callbacks fire on `session.commit()` only — not on savepoints."
    Savepoints created by [`transaction`](db.md) or `begin_nested()` do **not**
    trigger callbacks. All events accumulated across flushes are dispatched once
    when the outermost `commit()` is called.

### Events

Three event types are available, each corresponding to a [`ModelEvent`](../reference/models.md#fastapi_toolsets.models.ModelEvent) value:

| Event | Trigger |
|---|---|
| `ModelEvent.CREATE` | After `INSERT` commit |
| `ModelEvent.DELETE` | After `DELETE` commit |
| `ModelEvent.UPDATE` | After `UPDATE` commit on a watched field |

!!! warning "Callbacks fire only for ORM-level changes. Rows updated via raw SQL (`UPDATE ... SET ...`) are not detected."

### Watched fields

Set `__watched_fields__` on the model to restrict which field changes trigger `UPDATE` events. It must be a `tuple[str, ...]` — any other type raises `TypeError`:

| Class attribute | `UPDATE` behaviour |
|---|---|
| `__watched_fields__ = ("status", "role")` | Only fires when `status` or `role` changes |
| *(not set)* | Fires when **any** mapped field changes |

`__watched_fields__` is inherited through the class hierarchy via normal Python MRO. A subclass can override it:

```python
class Order(Base, UUIDMixin):
    __watched_fields__ = ("status",)
    ...


class UrgentOrder(Order):
    # inherits __watched_fields__ = ("status",)
    ...


class PriorityOrder(Order):
    __watched_fields__ = ("priority",)
    # overrides parent — UPDATE fires only for priority changes
    ...
```

### Registering handlers

Register handlers with the [`listens_for`](../reference/models.md#fastapi_toolsets.models.listens_for) decorator. Every callback receives three arguments: the model instance, the [`ModelEvent`](../reference/models.md#fastapi_toolsets.models.ModelEvent) that triggered it, and a `changes` dict (`None` for `CREATE` and `DELETE`):

```python
from fastapi_toolsets.models import ModelEvent, UUIDMixin, listens_for


class Order(Base, UUIDMixin):
    __tablename__ = "orders"
    __watched_fields__ = ("status",)

    status: Mapped[str]


@listens_for(Order, [ModelEvent.CREATE])
async def on_order_created(order: Order, event_type: ModelEvent, changes: None):
    await notify_new_order(order.id)


@listens_for(Order, [ModelEvent.DELETE])
async def on_order_deleted(order: Order, event_type: ModelEvent, changes: None):
    await notify_order_cancelled(order.id)


@listens_for(Order, [ModelEvent.UPDATE])
async def on_order_updated(order: Order, event_type: ModelEvent, changes: dict):
    if "status" in changes:
        await notify_status_change(order.id, changes["status"])
```

Multiple handlers can be registered for the same model and event. Handlers registered on a parent class also fire for subclass instances.

A single handler can listen for multiple events at once. When `event_types` is omitted, the handler fires for all events:

```python
@listens_for(Order, [ModelEvent.CREATE, ModelEvent.UPDATE])
async def on_order_changed(order: Order, event_type: ModelEvent, changes: dict | None):
    await invalidate_cache(order.id)


@listens_for(Order)  # all events
async def on_any_order_event(
    order: Order, event_type: ModelEvent, changes: dict | None
):
    await audit_log(order.id, event_type)
```

### Field changes format

The `changes` dict maps each watched field that changed to `{"old": ..., "new": ...}`. Only fields that actually changed are included. For `CREATE` and `DELETE` events, `changes` is `None`:

```python
# CREATE / DELETE → changes is None
# status changed   → {"status": {"old": "pending", "new": "shipped"}}
# two fields changed → {"status": {...}, "assigned_to": {...}}
```

!!! info "Multiple flushes in one transaction are merged: the earliest `old` and latest `new` are preserved, and `on_update` fires only once per commit."

---

[:material-api: API Reference](../reference/models.md)
