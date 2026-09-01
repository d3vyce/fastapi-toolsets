# CRUD

Generic async CRUD operations for SQLAlchemy models with search, pagination, and many-to-many support.

!!! info
    This module has been coded and tested to be compatible with PostgreSQL only.

## Overview

The `crud` module provides [`AsyncCrud`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud), a base class with a full suite of async database operations, and [`CrudFactory`](../reference/crud.md#fastapi_toolsets.crud.factory.CrudFactory), a convenience function to instantiate it for a given model.

## Creating a CRUD class

### Factory style

```python
from fastapi_toolsets.crud import CrudFactory
from myapp.models import User

UserCrud = CrudFactory(model=User)
```

[`CrudFactory`](../reference/crud.md#fastapi_toolsets.crud.factory.CrudFactory) dynamically creates a class named `AsyncUserCrud` with `User` as its model. This is the most concise option for straightforward CRUD with no custom logic.

### Subclass style

!!! info "Added in `v2.3.0`"

```python
from fastapi_toolsets.crud.factory import AsyncCrud
from myapp.models import User


class UserCrud(AsyncCrud[User]):
    model = User
    searchable_fields = [User.username, User.email]
    default_load_options = [selectinload(User.role)]
```

Subclassing [`AsyncCrud`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud) directly is the preferred style when you need to add custom methods or when the configuration is complex enough to benefit from a named class body.

### Adding custom methods

```python
class UserCrud(AsyncCrud[User]):
    model = User

    @classmethod
    async def get_active(cls, session: AsyncSession) -> list[User]:
        return await cls.get_multi(session, filters=[User.is_active == True])
```

### Sharing a custom base across multiple models

Define a generic base class with the shared methods, then subclass it for each model:

```python
from typing import Generic, TypeVar
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from fastapi_toolsets.crud.factory import AsyncCrud

T = TypeVar("T", bound=DeclarativeBase)


class AuditedCrud(AsyncCrud[T], Generic[T]):
    """Base CRUD with custom function"""

    @classmethod
    async def get_active(cls, session: AsyncSession):
        return await cls.get_multi(session, filters=[cls.model.is_active == True])


class UserCrud(AuditedCrud[User]):
    model = User
    searchable_fields = [User.username, User.email]
```

You can also use the factory shorthand with the same base by passing `base_class`:

```python
UserCrud = CrudFactory(User, base_class=AuditedCrud)
```

## Basic operations

!!! info "`get_or_none` added in `v2.2`"

```python
# Create
user = await UserCrud.create(session=session, obj=UserCreateSchema(username="alice"))

# Get one (raises NotFoundError if not found)
user = await UserCrud.get(session=session, filters=[User.id == user_id])

# Get one or None (never raises)
user = await UserCrud.get_or_none(session=session, filters=[User.id == user_id])

# Get first or None
user = await UserCrud.first(session=session, filters=[User.email == email])

# Get multiple
users = await UserCrud.get_multi(session=session, filters=[User.is_active == True])

# Update
user = await UserCrud.update(
    session=session, obj=UserUpdateSchema(username="bob"), filters=[User.id == user_id]
)

# Delete
await UserCrud.delete(session=session, filters=[User.id == user_id])

# Count / exists
count = await UserCrud.count(session=session, filters=[User.is_active == True])
exists = await UserCrud.exists(session=session, filters=[User.email == email])
```

## Fetching a single record

Three methods fetch a single record — choose based on how you want to handle the "not found" case and whether you need strict uniqueness:

| Method | Not found | Multiple results |
|---|---|---|
| `get` | raises `NotFoundError` | raises `MultipleResultsFound` |
| `get_or_none` | returns `None` | raises `MultipleResultsFound` |
| `first` | returns `None` | returns the first match silently |

Use `get` when the record must exist (e.g. a detail endpoint that should return 404):

```python
user = await UserCrud.get(session=session, filters=[User.id == user_id])
```

Use `get_or_none` when the record may not exist but you still want strict uniqueness enforcement:

```python
user = await UserCrud.get_or_none(session=session, filters=[User.email == email])
if user is None:
    ...  # handle missing case without catching an exception
```

Use `first` when you only care about any one match and don't need uniqueness:

```python
user = await UserCrud.first(session=session, filters=[User.is_active == True])
```

## Row locking

`get`, `get_or_none`, `first`, `get_multi`, and `update` all accept a `with_for_update` parameter that appends a `FOR UPDATE` clause to the underlying `SELECT`, preventing concurrent transactions from modifying the matched rows until the current transaction commits.

| Value | SQL clause |
|---|---|
| `False` (default) | no locking |
| `True` | `FOR UPDATE` |
| `"nowait"` | `FOR UPDATE NOWAIT` |
| `"skip_locked"` | `FOR UPDATE SKIP LOCKED` |

```python
# Lock before reading — typical read-modify-write pattern
user = await UserCrud.get(session, [User.id == user_id], with_for_update=True)

# Raise immediately if another transaction holds the lock
user = await UserCrud.get(session, [User.id == user_id], with_for_update="nowait")

# Skip rows already locked by another transaction (e.g. job queues)
rows = await JobCrud.get_multi(
    session, filters=[Job.status == "pending"], with_for_update="skip_locked"
)

# Lock atomically as part of update (prevents race between SELECT and UPDATE)
user = await UserCrud.update(
    session, UserUpdate(credits=10), [User.id == user_id], with_for_update=True
)
```

!!! warning
    `with_for_update` requires an open transaction. Wrap your call in `async with session.begin()` or use the `transaction` helper if you are not already inside one.

!!! note
    `NOWAIT` raises `sqlalchemy.exc.OperationalError` immediately if the row is locked rather than waiting.

## Pagination

!!! info "Added in `v1.1` (only offset_pagination via `paginate` if `<v1.1`)"

Three pagination methods are available.  All return a typed response whose `pagination_type` field tells clients which strategy was used.

| | `offset_paginate` | `cursor_paginate` | `paginate` |
|---|---|---|---|
| Return type | `OffsetPaginatedResponse` | `CursorPaginatedResponse` | either, based on `pagination_type` param |
| Total count | Yes | No | / |
| Jump to arbitrary page | Yes | No | / |
| Performance on deep pages | Degrades | Constant | / |
| Stable under concurrent inserts | No | Yes | / |
| Use case | Admin panels, numbered pagination | Feeds, APIs, infinite scroll | single endpoint, both strategies |

### Offset pagination

```python
from typing import Annotated
from fastapi import Depends


@router.get("")
async def get_users(
    session: SessionDep,
    params: Annotated[dict, Depends(UserCrud.offset_paginate_params())],
) -> OffsetPaginatedResponse[UserRead]:
    return await UserCrud.offset_paginate(session=session, **params, schema=UserRead)
```

The [`offset_paginate`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.offset_paginate) method returns an [`OffsetPaginatedResponse`](../reference/schemas.md#fastapi_toolsets.schemas.OffsetPaginatedResponse):

```json
{
  "status": "SUCCESS",
  "pagination_type": "offset",
  "data": ["..."],
  "pagination": {
    "total_count": 100,
    "pages": 5,
    "page": 1,
    "items_per_page": 20,
    "has_more": true
  }
}
```

#### Skipping the COUNT query

!!! info "Added in `v2.4.1`"

By default `offset_paginate` runs two queries: one for the page items and one `COUNT(*)` for `total_count`. On large tables the `COUNT` can be expensive. Pass `include_total=False` to `offset_paginate_params()` to skip it:

```python
@router.get("")
async def get_users(
    session: SessionDep,
    params: Annotated[
        dict, Depends(UserCrud.offset_paginate_params(include_total=False))
    ],
) -> OffsetPaginatedResponse[UserRead]:
    return await UserCrud.offset_paginate(session=session, **params, schema=UserRead)
```

### Cursor pagination

```python
@router.get("")
async def list_users(
    session: SessionDep,
    params: Annotated[dict, Depends(UserCrud.cursor_paginate_params())],
) -> CursorPaginatedResponse[UserRead]:
    return await UserCrud.cursor_paginate(session=session, **params, schema=UserRead)
```

The [`cursor_paginate`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.cursor_paginate) method returns a [`CursorPaginatedResponse`](../reference/schemas.md#fastapi_toolsets.schemas.CursorPaginatedResponse):

```json
{
  "status": "SUCCESS",
  "pagination_type": "cursor",
  "data": ["..."],
  "pagination": {
    "next_cursor": "eyJ2YWx1ZSI6ICIzZjQ3YWM2OS0uLi4ifQ==",
    "prev_cursor": null,
    "items_per_page": 20,
    "has_more": true
  }
}
```

Pass `next_cursor` as the `cursor` query parameter on the next request to advance to the next page. `prev_cursor` is set on pages 2+ and points back to the first item of the current page. Both are `null` when there is no adjacent page.

#### Choosing a cursor column

The cursor column is set once on [`CrudFactory`](../reference/crud.md#fastapi_toolsets.crud.factory.CrudFactory) via the `cursor_column` parameter. It must be monotonically ordered for stable results:

- Auto-increment integer PKs
- UUID v7 PKs
- Timestamps

!!! warning
    Random UUID v4 PKs are **not** suitable as cursor columns because their ordering is non-deterministic.

!!! note
    `cursor_column` is required. Calling [`cursor_paginate`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.cursor_paginate) on a CRUD class that has no `cursor_column` configured raises a `ValueError`.

The cursor value is URL-safe base64-encoded (no padding) when returned to the client and decoded back to the correct Python type on the next request. The following SQLAlchemy column types are supported:

| SQLAlchemy type | Python type |
|---|---|
| `Integer`, `BigInteger`, `SmallInteger` | `int` |
| `Uuid` | `uuid.UUID` |
| `DateTime` | `datetime.datetime` |
| `Date` | `datetime.date` |
| `Float`, `Numeric` | `decimal.Decimal` |

```python
# Paginate by the primary key
PostCrud = CrudFactory(model=Post, cursor_column=Post.id)

# Paginate by a timestamp column instead
PostCrud = CrudFactory(model=Post, cursor_column=Post.created_at)
```

### Unified endpoint (both strategies)

!!! info "Added in `v2.3.0`"

[`paginate()`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.paginate) dispatches to `offset_paginate` or `cursor_paginate` based on a `pagination_type` query parameter, letting you expose **one endpoint** that supports both strategies.  The `pagination_type` field in the response tells clients which strategy was used, enabling frontend discriminated-union typing.

```python
from fastapi_toolsets.schemas import PaginatedResponse


@router.get("")
async def list_users(
    session: SessionDep,
    params: Annotated[dict, Depends(UserCrud.paginate_params())],
) -> PaginatedResponse[UserRead]:
    return await UserCrud.paginate(session, **params, schema=UserRead)
```

```
GET /users?pagination_type=offset&page=2&items_per_page=10
GET /users?pagination_type=cursor&cursor=eyJ2YWx1ZSI6...&items_per_page=10
```

## Search

Two search strategies are available, both compatible with [`offset_paginate`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.offset_paginate) and [`cursor_paginate`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.cursor_paginate).

| | Full-text search | Faceted search |
|---|---|---|
| Input | Free-text string | Exact column values |
| Relationship support | Yes | Yes |
| Use case | Search bars | Filter dropdowns |

!!! info "You can use both search strategies in the same endpoint!"

### Full-text search

!!! info "Added in `v2.2.1`"
    The model's primary key is always included in `searchable_fields` automatically, so searching by ID works out of the box without any configuration. When no `searchable_fields` are declared, only the primary key is searched.

Declare `searchable_fields` on the CRUD class. Relationship traversal is supported via tuples:

```python
PostCrud = CrudFactory(
    model=Post,
    searchable_fields=[
        Post.title,
        Post.content,
        (Post.author, User.username),  # search across relationship
    ],
)
```

You can override `searchable_fields` per call with `search_fields`:

```python
result = await UserCrud.offset_paginate(
    session=session,
    search_fields=[User.country],
)
```

Or via the dependency to narrow which fields are exposed as query parameters:

```python
params = UserCrud.offset_paginate_params(search_fields=[Post.title])
```

`search_fields`, `facet_fields` and `order_fields` follow the same override rule
everywhere they are accepted &mdash; `offset_paginate`, `cursor_paginate`,
`paginate` and the matching `*_paginate_params` dependencies:

| Passed | Effect |
| --- | --- |
| omitted or `None` | Use the class-level declaration |
| `[]` | Disable this feature for this call |
| `[...]` | Use exactly these fields (the primary key is **not** prepended &mdash; that only happens for the class-level `searchable_fields`) |

This allows searching with both [`offset_paginate`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.offset_paginate) and [`cursor_paginate`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.cursor_paginate):

```python
@router.get("")
async def get_users(
    session: SessionDep,
    params: Annotated[dict, Depends(UserCrud.offset_paginate_params())],
) -> OffsetPaginatedResponse[UserRead]:
    return await UserCrud.offset_paginate(session=session, **params, schema=UserRead)
```

```python
@router.get("")
async def get_users(
    session: SessionDep,
    params: Annotated[dict, Depends(UserCrud.cursor_paginate_params())],
) -> CursorPaginatedResponse[UserRead]:
    return await UserCrud.cursor_paginate(session=session, **params, schema=UserRead)
```

The dependency adds two query parameters to the endpoint:

| Parameter       | Type          |
| --------------- | ------------- |
| `search`        | `str \| null` |
| `search_column` | `str \| null` |

```
GET /posts?search=hello                        → search all configured columns
GET /posts?search=hello&search_column=title    → search only Post.title
```

The available search column keys are returned in the `search_columns` field of [`PaginatedResponse`](../reference/schemas.md#fastapi_toolsets.schemas.PaginatedResponse). Use them to populate a column picker in the UI, or to validate `search_column` values on the client side:

```json
{
  "status": "SUCCESS",
  "data": ["..."],
  "pagination": { "..." },
  "search_columns": ["content", "author__username", "title"]
}
```

!!! info "Key format uses `__` as a separator for relationship chains."
    A direct column `Post.title` produces `"title"`. A relationship tuple `(Post.author, User.username)` produces `"author__username"`. An unknown `search_column` value raises [`InvalidSearchColumnError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.InvalidSearchColumnError) (HTTP 422).

### Faceted search

!!! info "Added in `v1.2`"

Declare `facet_fields` on the CRUD class to return distinct column values alongside paginated results. This is useful for populating filter dropdowns or building faceted search UIs. Relationship traversal is supported via tuples, using the same syntax as `searchable_fields`:

```python
UserCrud = CrudFactory(
    model=User,
    facet_fields=[
        User.status,
        User.country,
        (User.role, Role.name),  # value from a related model
    ],
)
```

You can override `facet_fields` per call:

```python
result = await UserCrud.offset_paginate(
    session=session,
    facet_fields=[User.country],
)
```

Or via the dependency to narrow which fields are exposed as query parameters:

```python
params = UserCrud.offset_paginate_params(facet_fields=[User.country])
```

Facet filtering is built into the consolidated params dependencies. When `filter=True` (the default), each facet field is exposed as a query parameter and values are collected into `filter_by` automatically:

```python
from typing import Annotated

from fastapi import Depends


@router.get("", response_model_exclude_none=True)
async def list_users(
    session: SessionDep,
    params: Annotated[dict, Depends(UserCrud.offset_paginate_params())],
) -> OffsetPaginatedResponse[UserRead]:
    return await UserCrud.offset_paginate(session=session, **params, schema=UserRead)
```

```python
@router.get("", response_model_exclude_none=True)
async def list_users(
    session: SessionDep,
    params: Annotated[dict, Depends(UserCrud.cursor_paginate_params())],
) -> CursorPaginatedResponse[UserRead]:
    return await UserCrud.cursor_paginate(session=session, **params, schema=UserRead)
```

Both single-value and multi-value query parameters work:

```
GET /users?status=active                      → filter_by={"status": ["active"]}
GET /users?status=active&country=FR           → filter_by={"status": ["active"], "country": ["FR"]}
GET /users?role__name=admin&role__name=editor → filter_by={"role__name": ["admin", "editor"]}  (IN clause)
```

`filter_by` and `filters` can be combined — both are applied with AND logic.

The distinct values for each facet field are returned in the `filter_attributes` field of [`PaginatedResponse`](../reference/schemas.md#fastapi_toolsets.schemas.PaginatedResponse). Use them to populate filter dropdowns in the UI, or to validate `filter_by` keys on the client side:

```json
{
  "status": "SUCCESS",
  "data": ["..."],
  "pagination": { "..." },
  "filter_attributes": {
    "status": ["active", "inactive"],
    "country": ["DE", "FR", "US"],
    "role__name": ["admin", "editor", "viewer"]
  }
}
```

!!! info "Key format uses `__` as a separator for relationship chains."
    A direct column `User.status` produces `"status"`. A relationship tuple `(User.role, Role.name)` produces `"role__name"`. A deeper chain `(User.role, Role.permission, Permission.name)` produces `"role__permission__name"`. An unknown `filter_by` key raises [`InvalidFacetFilterError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.InvalidFacetFilterError) (HTTP 422).

#### Skipping facet queries

!!! info "Added in `v5.1.0`"

Facet values only change with the filters, not with the page. Pass `include_facets=False` to `offset_paginate_params()` / `cursor_paginate_params()` on pages 2..N to skip the facet queries entirely (`filter_attributes` will be `None`):

```python
params: Annotated[dict, Depends(UserCrud.offset_paginate_params(include_facets=False))]
```

## Sorting

!!! info "Added in `v1.3`"

Declare `order_fields` on the CRUD class. Relationship traversal is supported via tuples, using the same syntax as `searchable_fields` and `facet_fields`:

```python
UserCrud = CrudFactory(
    model=User,
    order_fields=[
        User.name,
        User.created_at,
        (User.role, Role.name),  # sort by a related model column
    ],
)
```

You can override `order_fields` per call:

```python
result = await UserCrud.offset_paginate(
    session=session,
    order_fields=[User.name],
)
```

Or via the dependency to narrow which fields are exposed as query parameters:

```python
params = UserCrud.offset_paginate_params(order_fields=[User.name])
```

Sorting is built into the consolidated params dependencies. When `order=True` (the default), `order_by` and `order` query parameters are exposed and resolved into an `OrderByClause` automatically:

```python
from typing import Annotated

from fastapi import Depends


@router.get("")
async def list_users(
    session: SessionDep,
    params: Annotated[dict, Depends(UserCrud.offset_paginate_params())],
) -> OffsetPaginatedResponse[UserRead]:
    return await UserCrud.offset_paginate(session=session, **params, schema=UserRead)
```

```python
@router.get("")
async def list_users(
    session: SessionDep,
    params: Annotated[dict, Depends(UserCrud.cursor_paginate_params())],
) -> CursorPaginatedResponse[UserRead]:
    return await UserCrud.cursor_paginate(session=session, **params, schema=UserRead)
```

The dependency adds two query parameters to the endpoint:

| Parameter  | Type            |
| ---------- | --------------- |
| `order_by` | `str \| null`  |
| `order`    | `asc` or `desc` |

```
GET /users?order_by=name&order=asc         → ORDER BY users.name ASC
GET /users?order_by=role__name&order=desc  → LEFT JOIN roles ON ... ORDER BY roles.name DESC
```

!!! info "Relationship tuples are joined automatically."
    When a relation field is selected, the related table is LEFT OUTER JOINed automatically. An unknown `order_by` value raises [`InvalidOrderFieldError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.InvalidOrderFieldError) (HTTP 422).


The available sort keys are returned in the `order_columns` field of [`PaginatedResponse`](../reference/schemas.md#fastapi_toolsets.schemas.PaginatedResponse). Use them to populate a sort picker in the UI, or to validate `order_by` values on the client side:

```json
{
  "status": "SUCCESS",
  "data": ["..."],
  "pagination": { "..." },
  "order_columns": ["created_at", "name", "role__name"]
}
```

!!! info "Key format uses `__` as a separator for relationship chains."
    A direct column `User.name` produces `"name"`. A relationship tuple `(User.role, Role.name)` produces `"role__name"`.

## Relationship loading

!!! info "Added in `v1.1`"

By default, SQLAlchemy relationships are not loaded unless explicitly requested. Instead of using `lazy="selectin"` on model definitions (which is implicit and applies globally), define a `default_load_options` on the CRUD class to control loading strategy explicitly.

!!! warning
    Avoid using `lazy="selectin"` on model relationships. It fires silently on every query, cannot be disabled per-call, and can cause unexpected cascading loads through deep relationship chains. Use `default_load_options` instead.

```python
from sqlalchemy.orm import selectinload

ArticleCrud = CrudFactory(
    model=Article,
    default_load_options=[
        selectinload(Article.category),
        selectinload(Article.tags),
    ],
)
```

`default_load_options` applies automatically to all read operations (`get`, `first`, `get_multi`, `offset_paginate`, `cursor_paginate`). When `load_options` is passed at call-site, it **fully replaces** `default_load_options` for that query — giving you precise per-call control:

```python
# Only loads category, tags are not loaded
article = await ArticleCrud.get(
    session=session,
    filters=[Article.id == article_id],
    load_options=[selectinload(Article.category)],
)

# Loads nothing — useful for write-then-refresh flows or lightweight checks
articles = await ArticleCrud.get_multi(session=session, load_options=[])
```

## Many-to-many relationships

Use `m2m_fields` to map schema fields containing lists of IDs to SQLAlchemy relationships. The CRUD class resolves and validates all IDs before persisting:

```python
PostCrud = CrudFactory(
    model=Post,
    m2m_fields={"tag_ids": Post.tags},
)

post = await PostCrud.create(
    session=session, obj=PostCreateSchema(title="Hello", tag_ids=[1, 2, 3])
)
```

## Upsert

Atomic `INSERT ... ON CONFLICT DO UPDATE` using PostgreSQL:

```python
await UserCrud.upsert(
    session=session,
    obj=UserCreateSchema(email="alice@example.com", username="alice"),
    index_elements=[User.email],
    set_={"username"},
)
```

## Response serialization

!!! info "Added in `v1.1`"

Pass a Pydantic schema class to `create`, `get`, `update`, or `offset_paginate` to serialize the result directly into that schema and wrap it in a [`Response[schema]`](../reference/schemas.md#fastapi_toolsets.schemas.Response) or [`PaginatedResponse[schema]`](../reference/schemas.md#fastapi_toolsets.schemas.PaginatedResponse):

```python
class UserRead(PydanticBase):
    id: UUID
    username: str


@router.get(
    "/{uuid}",
    responses=generate_error_responses(NotFoundError),
)
async def get_user(session: SessionDep, uuid: UUID) -> Response[UserRead]:
    return await crud.UserCrud.get(
        session=session,
        filters=[User.id == uuid],
        schema=UserRead,
    )


@router.get("")
async def list_users(
    session: SessionDep,
    params: Annotated[dict, Depends(crud.UserCrud.offset_paginate_params())],
) -> OffsetPaginatedResponse[UserRead]:
    return await crud.UserCrud.offset_paginate(
        session=session, **params, schema=UserRead
    )
```

The schema must have `from_attributes=True` (or inherit from [`PydanticBase`](../reference/schemas.md#fastapi_toolsets.schemas.PydanticBase)) so it can be built from SQLAlchemy model instances.

---

[:material-api: API Reference](../reference/crud.md)
