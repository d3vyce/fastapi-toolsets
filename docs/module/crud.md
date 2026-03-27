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
@router.get("")
async def get_users(
    session: SessionDep,
    items_per_page: int = 50,
    page: int = 1,
) -> OffsetPaginatedResponse[UserRead]:
    return await UserCrud.offset_paginate(
        session=session,
        items_per_page=items_per_page,
        page=page,
        schema=UserRead,
    )
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

By default `offset_paginate` runs two queries: one for the page items and one `COUNT(*)` for `total_count`. On large tables the `COUNT` can be expensive. Pass `include_total=False` to skip it:

```python
result = await UserCrud.offset_paginate(
    session=session,
    page=page,
    items_per_page=items_per_page,
    include_total=False,
    schema=UserRead,
)
```

#### Pagination params dependency

!!! info "Added in `v2.4.1`"

Use [`offset_params()`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.offset_params) to generate a FastAPI dependency that injects `page` and `items_per_page` from query parameters with configurable defaults and a `max_page_size` cap:

```python
from typing import Annotated
from fastapi import Depends

@router.get("")
async def list_users(
    session: SessionDep,
    params: Annotated[dict, Depends(UserCrud.offset_params(default_page_size=20, max_page_size=100))],
) -> OffsetPaginatedResponse[UserRead]:
    return await UserCrud.offset_paginate(session=session, **params, schema=UserRead)
```

### Cursor pagination

```python
@router.get("")
async def list_users(
    session: SessionDep,
    cursor: str | None = None,
    items_per_page: int = 20,
) -> CursorPaginatedResponse[UserRead]:
    return await UserCrud.cursor_paginate(
        session=session,
        cursor=cursor,
        items_per_page=items_per_page,
        schema=UserRead,
    )
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

#### Pagination params dependency

!!! info "Added in `v2.4.1`"

Use [`cursor_params()`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.cursor_params) to inject `cursor` and `items_per_page` from query parameters with a `max_page_size` cap:

```python
from typing import Annotated
from fastapi import Depends

@router.get("")
async def list_users(
    session: SessionDep,
    params: Annotated[dict, Depends(UserCrud.cursor_params(default_page_size=20, max_page_size=100))],
) -> CursorPaginatedResponse[UserRead]:
    return await UserCrud.cursor_paginate(session=session, **params, schema=UserRead)
```

### Unified endpoint (both strategies)

!!! info "Added in `v2.3.0`"

[`paginate()`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.paginate) dispatches to `offset_paginate` or `cursor_paginate` based on a `pagination_type` query parameter, letting you expose **one endpoint** that supports both strategies.  The `pagination_type` field in the response tells clients which strategy was used, enabling frontend discriminated-union typing.

```python
from fastapi_toolsets.crud import PaginationType
from fastapi_toolsets.schemas import PaginatedResponse

@router.get("")
async def list_users(
    session: SessionDep,
    pagination_type: PaginationType = PaginationType.OFFSET,
    page: int = Query(1, ge=1, description="Current page (offset only)"),
    cursor: str | None = Query(None, description="Cursor token (cursor only)"),
    items_per_page: int = Query(20, ge=1, le=100),
) -> PaginatedResponse[UserRead]:
    return await UserCrud.paginate(
        session,
        pagination_type=pagination_type,
        page=page,
        cursor=cursor,
        items_per_page=items_per_page,
        schema=UserRead,
    )
```

```
GET /users?pagination_type=offset&page=2&items_per_page=10
GET /users?pagination_type=cursor&cursor=eyJ2YWx1ZSI6...&items_per_page=10
```

#### Pagination params dependency

!!! info "Added in `v2.4.1`"

Use [`paginate_params()`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.paginate_params) to inject all parameters at once with configurable defaults and a `max_page_size` cap:

```python
from typing import Annotated
from fastapi import Depends
from fastapi_toolsets.schemas import PaginatedResponse

@router.get("")
async def list_users(
    session: SessionDep,
    params: Annotated[dict, Depends(UserCrud.paginate_params(default_page_size=20, max_page_size=100))],
) -> PaginatedResponse[UserRead]:
    return await UserCrud.paginate(session, **params, schema=UserRead)
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
@router.get("")
async def get_users(
    session: SessionDep,
    items_per_page: int = 50,
    page: int = 1,
    search: str | None = None,
) -> OffsetPaginatedResponse[UserRead]:
    return await UserCrud.offset_paginate(
        session=session,
        items_per_page=items_per_page,
        page=page,
        search=search,
        schema=UserRead,
    )
```

```python
@router.get("")
async def get_users(
    session: SessionDep,
    cursor: str | None = None,
    items_per_page: int = 50,
    search: str | None = None,
) -> CursorPaginatedResponse[UserRead]:
    return await UserCrud.cursor_paginate(
        session=session,
        items_per_page=items_per_page,
        cursor=cursor,
        search=search,
        schema=UserRead,
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
    "role__name": ["admin", "editor", "viewer"]
  }
}
```

Use `filter_by` to pass the client's chosen filter values directly — no need to build SQLAlchemy conditions by hand. Any unknown key raises [`InvalidFacetFilterError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.InvalidFacetFilterError).

!!! info "The keys in `filter_by` are the same keys the client received in `filter_attributes`."
    Keys use `__` as a separator for the full relationship chain. A direct column `User.status` produces `"status"`. A relationship tuple `(User.role, Role.name)` produces `"role__name"`. A deeper chain `(User.role, Role.permission, Permission.name)` produces `"role__permission__name"`.

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
) -> OffsetPaginatedResponse[UserRead]:
    return await UserCrud.offset_paginate(
        session=session,
        page=page,
        filter_by=filter_by,
        schema=UserRead,
    )
```

Both single-value and multi-value query parameters work:

```
GET /users?status=active                      → filter_by={"status": ["active"]}
GET /users?status=active&country=FR           → filter_by={"status": ["active"], "country": ["FR"]}
GET /users?role__name=admin&role__name=editor → filter_by={"role__name": ["admin", "editor"]}  (IN clause)
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
) -> OffsetPaginatedResponse[UserRead]:
    return await UserCrud.offset_paginate(session=session, order_by=order_by, schema=UserRead)
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
async def list_users(session: SessionDep, page: int = 1) -> OffsetPaginatedResponse[UserRead]:
    return await crud.UserCrud.offset_paginate(
        session=session,
        page=page,
        schema=UserRead,
    )
```

The schema must have `from_attributes=True` (or inherit from [`PydanticBase`](../reference/schemas.md#fastapi_toolsets.schemas.PydanticBase)) so it can be built from SQLAlchemy model instances.

---

[:material-api: API Reference](../reference/crud.md)
