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

The `watch_fields` decorator combined with `WatchedFieldsMixin` lets you react to field changes (including on row creation).


Apply `@watch_fields` to declare which fields to monitor, and override `on_field_changes` to handle the event:

```python
from fastapi_toolsets.models import UUIDMixin, WatchedFieldsMixin, watch_fields

@watch_fields("status")
class Order(Base, UUIDMixin, WatchedFieldsMixin):
    __tablename__ = "orders"

    status: Mapped[str]

    async def on_field_changes(self, changes: dict) -> None:
        # Called after every commit that touches a watched field,
        # including the initial INSERT.
        if "status" in changes:
            old = changes["status"]["old"]  # None on creation
            new = changes["status"]["new"]
            await notify(self.id, old, new)
```

The `changes` dict maps each watched field that changed to `{"old": ..., "new": ...}`. On row creation, `old` is always `None`:

```python
# INSERT  → {"status": {"old": None,       "new": "pending"}}
# UPDATE  → {"status": {"old": "pending",  "new": "shipped"}}
```

Server-side defaults (e.g. `id`, `created_at`) are fully populated when `on_field_changes` is called, so `self.id` is safe to use inside the callback.

!!! info "If you flush several times before committing, the changes are merged: the earliest `old` and the latest `new` are preserved, and `on_field_changes` fires only once per commit."

!!! warning "The callback fires only for changes made through the ORM. Rows updated via raw SQL (`UPDATE ... SET ...`) are not detected."

You can also watch multiple fields:

```python
@watch_fields("status", "assigned_to")
class Ticket(Base, UUIDMixin, WatchedFieldsMixin):
    __tablename__ = "tickets"

    status: Mapped[str]
    assigned_to: Mapped[str | None]

    async def on_field_changes(self, changes: dict) -> None:
        if "status" in changes:
            await send_status_email(self.id, changes["status"])
        if "assigned_to" in changes:
            await send_assignment_notification(self.id, changes["assigned_to"])
```

Only fields that actually changed are included in `changes` — if only `status` changed, `assigned_to` will not appear.

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
