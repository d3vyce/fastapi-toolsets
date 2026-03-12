# CRUD

Generic async CRUD operations for SQLAlchemy models with search, pagination, and many-to-many support.

!!! info
    This module has been coded and tested to be compatible with PostgreSQL only.

## Overview

The `crud` module provides [`AsyncCrud`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud), an abstract base class with a full suite of async database operations, and [`CrudFactory`](../reference/crud.md#fastapi_toolsets.crud.factory.CrudFactory), a convenience function to instantiate it for a given model.

## Creating a CRUD class

```python
from fastapi_toolsets.crud import CrudFactory
from myapp.models import User

UserCrud = CrudFactory(model=User)
```

[`CrudFactory`](../reference/crud.md#fastapi_toolsets.crud.factory.CrudFactory) dynamically creates a class named `AsyncUserCrud` with `User` as its model.

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
user = await UserCrud.update(session=session, obj=UserUpdateSchema(username="bob"), filters=[User.id == user_id])

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

## Pagination

!!! info "Added in `v1.1` (only offset_pagination via `paginate` if `<v1.1`)"

Two pagination strategies are available. Both return a [`PaginatedResponse`](../reference/schemas.md#fastapi_toolsets.schemas.PaginatedResponse) but differ in how they navigate through results.

| | `offset_paginate` | `cursor_paginate` |
|---|---|---|
| Total count | Yes | No |
| Jump to arbitrary page | Yes | No |
| Performance on deep pages | Degrades | Constant |
| Stable under concurrent inserts | No | Yes |
| Search compatible | Yes | Yes |
| Use case | Admin panels, numbered pagination | Feeds, APIs, infinite scroll |

### Offset pagination

```python
@router.get(
    "",
    response_model=PaginatedResponse[User],
)
async def get_users(
    session: SessionDep,
    items_per_page: int = 50,
    page: int = 1,
):
    return await crud.UserCrud.offset_paginate(
        session=session,
        items_per_page=items_per_page,
        page=page,
    )
```

The [`offset_paginate`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.offset_paginate) method returns a [`PaginatedResponse`](../reference/schemas.md#fastapi_toolsets.schemas.PaginatedResponse) whose `pagination` field is an [`OffsetPagination`](../reference/schemas.md#fastapi_toolsets.schemas.OffsetPagination) object:

```json
{
  "status": "SUCCESS",
  "data": ["..."],
  "pagination": {
    "total_count": 100,
    "page": 1,
    "items_per_page": 20,
    "has_more": true
  }
}
```

### Cursor pagination

```python
@router.get(
    "",
    response_model=PaginatedResponse[UserRead],
)
async def list_users(
    session: SessionDep,
    cursor: str | None = None,
    items_per_page: int = 20,
):
    return await UserCrud.cursor_paginate(
        session=session,
        cursor=cursor,
        items_per_page=items_per_page,
    )
```

The [`cursor_paginate`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.cursor_paginate) method returns a [`PaginatedResponse`](../reference/schemas.md#fastapi_toolsets.schemas.PaginatedResponse) whose `pagination` field is a [`CursorPagination`](../reference/schemas.md#fastapi_toolsets.schemas.CursorPagination) object:

```json
{
  "status": "SUCCESS",
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

The cursor value is base64-encoded when returned to the client and decoded back to the correct Python type on the next request. The following SQLAlchemy column types are supported:

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

This allows searching with both [`offset_paginate`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.offset_paginate) and [`cursor_paginate`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.cursor_paginate):

```python
@router.get(
    "",
    response_model=PaginatedResponse[User],
)
async def get_users(
    session: SessionDep,
    items_per_page: int = 50,
    page: int = 1,
    search: str | None = None,
):
    return await crud.UserCrud.offset_paginate(
        session=session,
        items_per_page=items_per_page,
        page=page,
        search=search,
    )
```

```python
@router.get(
    "",
    response_model=PaginatedResponse[User],
)
async def get_users(
    session: SessionDep,
    cursor: str | None = None,
    items_per_page: int = 50,
    search: str | None = None,
):
    return await crud.UserCrud.cursor_paginate(
        session=session,
        items_per_page=items_per_page,
        cursor=cursor,
        search=search,
    )
```

### Faceted search

!!! info "Added in `v1.2`"

Declare `facet_fields` on the CRUD class to return distinct column values alongside paginated results. This is useful for populating filter dropdowns or building faceted search UIs.

Facet fields use the same syntax as `searchable_fields` — direct columns or relationship tuples:

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

The distinct values are returned in the `filter_attributes` field of [`PaginatedResponse`](../reference/schemas.md#fastapi_toolsets.schemas.PaginatedResponse):

```json
{
  "status": "SUCCESS",
  "data": ["..."],
  "pagination": { "..." },
  "filter_attributes": {
    "status": ["active", "inactive"],
    "country": ["DE", "FR", "US"],
    "name": ["admin", "editor", "viewer"]
  }
}
```

Use `filter_by` to pass the client's chosen filter values directly — no need to build SQLAlchemy conditions by hand. Any unknown key raises [`InvalidFacetFilterError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.InvalidFacetFilterError).

!!! info "The keys in `filter_by` are the same keys the client received in `filter_attributes`."
    Keys are normally the terminal `column.key` (e.g. `"name"` for `Role.name`). When two facet fields share the same column key (e.g. `(Build.project, Project.name)` and `(Build.os, Os.name)`), the relationship name is prepended automatically: `"project__name"` and `"os__name"`.

`filter_by` and `filters` can be combined — both are applied with AND logic.

Use [`filter_params()`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.filter_params) to generate a dict with the facet filter values from the query parameters:

```python
from typing import Annotated

from fastapi import Depends

UserCrud = CrudFactory(
    model=User,
    facet_fields=[User.status, User.country, (User.role, Role.name)],
)

@router.get("", response_model_exclude_none=True)
async def list_users(
    session: SessionDep,
    page: int = 1,
    filter_by: Annotated[dict[str, list[str]], Depends(UserCrud.filter_params())],
) -> PaginatedResponse[UserRead]:
    return await UserCrud.offset_paginate(
        session=session,
        page=page,
        filter_by=filter_by,
    )
```

Both single-value and multi-value query parameters work:

```
GET /users?status=active              → filter_by={"status": ["active"]}
GET /users?status=active&country=FR   → filter_by={"status": ["active"], "country": ["FR"]}
GET /users?role=admin&role=editor     → filter_by={"role": ["admin", "editor"]}  (IN clause)
```

## Sorting

!!! info "Added in `v1.3`"

Declare `order_fields` on the CRUD class to expose client-driven column ordering via `order_by` and `order` query parameters.

```python
UserCrud = CrudFactory(
    model=User,
    order_fields=[
        User.name,
        User.created_at,
    ],
)
```

Call [`order_params()`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.order_params) to generate a FastAPI dependency that maps the query parameters to an [`OrderByClause`](../reference/crud.md#fastapi_toolsets.crud.factory.OrderByClause) expression:

```python
from typing import Annotated

from fastapi import Depends
from fastapi_toolsets.crud import OrderByClause

@router.get("")
async def list_users(
    session: SessionDep,
    order_by: Annotated[OrderByClause | None, Depends(UserCrud.order_params())],
) -> PaginatedResponse[UserRead]:
    return await UserCrud.offset_paginate(session=session, order_by=order_by)
```

The dependency adds two query parameters to the endpoint:

| Parameter  | Type            |
| ---------- | --------------- |
| `order_by` | `str | null`   |
| `order`    | `asc` or `desc` |

```
GET /users?order_by=name&order=asc   → ORDER BY users.name ASC
GET /users?order_by=name&order=desc  → ORDER BY users.name DESC
```

An unknown `order_by` value raises [`InvalidOrderFieldError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.InvalidOrderFieldError) (HTTP 422).

You can also pass `order_fields` directly to `order_params()` to override the class-level defaults without modifying them:

```python
UserOrderParams = UserCrud.order_params(order_fields=[User.name])
```

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

post = await PostCrud.create(session=session, obj=PostCreateSchema(title="Hello", tag_ids=[1, 2, 3]))
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
async def list_users(session: SessionDep, page: int = 1) -> PaginatedResponse[UserRead]:
    return await crud.UserCrud.offset_paginate(
        session=session,
        page=page,
        schema=UserRead,
    )
```

The schema must have `from_attributes=True` (or inherit from [`PydanticBase`](../reference/schemas.md#fastapi_toolsets.schemas.PydanticBase)) so it can be built from SQLAlchemy model instances.

---

[:material-api: API Reference](../reference/crud.md)
