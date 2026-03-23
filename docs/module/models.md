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

### [`WatchedFieldsMixin`](../reference/models.md#fastapi_toolsets.models.WatchedFieldsMixin)

!!! info "Added in `v2.4`"

`WatchedFieldsMixin` provides lifecycle callbacks that fire **after commit** — meaning the row is durably persisted when your callback runs. If the transaction rolls back, no callback fires.

Three callbacks are available, each corresponding to a [`ModelEvent`](../reference/models.md#fastapi_toolsets.models.ModelEvent) value:

| Callback | Event | Trigger |
|---|---|---|
| `on_create()` | `ModelEvent.CREATE` | After `INSERT` |
| `on_delete()` | `ModelEvent.DELETE` | After `DELETE` |
| `on_update(changes)` | `ModelEvent.UPDATE` | After `UPDATE` on a watched field |

Server-side defaults (e.g. `id`, `created_at`) are fully populated in all callbacks. All callbacks support both `async def` and plain `def`. Use `@watch` to restrict which fields trigger `on_update`:

| Decorator | `on_update` behaviour |
|---|---|
| `@watch("status", "role")` | Only fires when `status` or `role` changes |
| *(no decorator)* | Fires when **any** mapped field changes |

`@watch` is inherited through the class hierarchy. If a subclass does not declare its own `@watch`, it uses the filter from the nearest decorated parent. Applying `@watch` on the subclass overrides the parent's filter:

```python
@watch("status")
class Order(Base, UUIDMixin, WatchedFieldsMixin):
    ...

class UrgentOrder(Order):
    # inherits @watch("status") — on_update fires only for status changes
    ...

@watch("priority")
class PriorityOrder(Order):
    # overrides parent — on_update fires only for priority changes
    ...
```

#### Option 1 — catch-all with `on_event`

Override `on_event` to handle all event types in one place. The specific methods delegate here by default:

```python
from fastapi_toolsets.models import ModelEvent, UUIDMixin, WatchedFieldsMixin, watch

@watch("status")
class Order(Base, UUIDMixin, WatchedFieldsMixin):
    __tablename__ = "orders"

    status: Mapped[str]

    async def on_event(self, event: ModelEvent, changes: dict | None = None) -> None:
        if event == ModelEvent.CREATE:
            await notify_new_order(self.id)
        elif event == ModelEvent.DELETE:
            await notify_order_cancelled(self.id)
        elif event == ModelEvent.UPDATE:
            await notify_status_change(self.id, changes["status"])
```

#### Option 2 — targeted overrides

Override individual methods for more focused logic:

```python
@watch("status")
class Order(Base, UUIDMixin, WatchedFieldsMixin):
    __tablename__ = "orders"

    status: Mapped[str]

    async def on_create(self) -> None:
        await notify_new_order(self.id)

    async def on_delete(self) -> None:
        await notify_order_cancelled(self.id)

    async def on_update(self, changes: dict) -> None:
        if "status" in changes:
            old = changes["status"]["old"]
            new = changes["status"]["new"]
            await notify_status_change(self.id, old, new)
```

#### Field changes format

The `changes` dict maps each watched field that changed to `{"old": ..., "new": ...}`. Only fields that actually changed are included:

```python
# status changed   → {"status": {"old": "pending", "new": "shipped"}}
# two fields changed → {"status": {...}, "assigned_to": {...}}
```

!!! info "Multiple flushes in one transaction are merged: the earliest `old` and latest `new` are preserved, and `on_update` fires only once per commit."

!!! warning "Callbacks fire only for ORM-level changes. Rows updated via raw SQL (`UPDATE ... SET ...`) are not detected."

!!! warning "Callbacks fire after the **outermost** transaction commits."
    If you create several related objects using `CrudFactory.create` and need
    callbacks to see all of them (including associations), wrap the whole
    operation in a single [`get_transaction`](db.md) block. Without it, each
    `create` call commits independently and `on_create` fires before the
    remaining objects exist.

    ```python
    from fastapi_toolsets.db import get_transaction

    async with get_transaction(session):
        order = await OrderCrud.create(session, order_data)
        item  = await ItemCrud.create(session, item_data)
        await session.refresh(order, attribute_names=["items"])
        order.items.append(item)
    # on_create fires here for both order and item,
    # with the full association already committed.
    ```

## Composing mixins

All mixins can be combined in any order. The only constraint is that exactly one primary key must be defined — either via `UUIDMixin` or directly on the model.

```python
from fastapi_toolsets.models import UUIDMixin, TimestampMixin

class Event(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "events"
    name: Mapped[str]

class Counter(Base, UpdatedAtMixin):
    __tablename__ = "counters"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    value: Mapped[int]
```

---

[:material-api: API Reference](../reference/models.md)
