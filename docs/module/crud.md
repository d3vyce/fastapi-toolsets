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

```python
# Create
user = await UserCrud.create(session=session, obj=UserCreateSchema(username="alice"))

# Get one (raises NotFoundError if not found)
user = await UserCrud.get(session=session, filters=[User.id == user_id])

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

!!! warning "Deprecated: `paginate`"
    The `paginate` function is a backward-compatible alias for `offset_paginate`. This function is **deprecated** and will be removed in **v2.0**.

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

```python
# Paginate by the primary key
PostCrud = CrudFactory(model=Post, cursor_column=Post.id)

# Paginate by a timestamp column instead
PostCrud = CrudFactory(model=Post, cursor_column=Post.created_at)
```

## Search

Declare searchable fields on the CRUD class. Relationship traversal is supported via tuples:

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

## `schema` — typed response serialization

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

!!! warning "Deprecated: `as_response`"
    The `as_response=True` parameter is **deprecated** and will be removed in **v2.0**. Replace it with `schema=YourSchema`.

---

[:material-api: API Reference](../reference/crud.md)
