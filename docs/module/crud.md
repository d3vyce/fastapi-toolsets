# CRUD

Generic async CRUD operations for SQLAlchemy models with search, pagination, and many-to-many support. This module has features that are only compatible with Postgres. 

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
    return await crud.UserCrud.paginate(
        session=session,
        items_per_page=items_per_page,
        page=page,
    )
```

The [`paginate`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.paginate) function will return a [`PaginatedResponse`](../reference/schemas.md#fastapi_toolsets.schemas.PaginatedResponse).

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

This allow to do a search with the [`paginate`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud.paginate) function:

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
    return await crud.UserCrud.paginate(
        session=session,
        items_per_page=items_per_page,
        page=page,
        search=search,
    )
```

## Relationship loading

!!! info "Added in v1.1"

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

`default_load_options` applies automatically to all read operations (`get`, `first`, `get_multi`, `paginate`). When `load_options` is passed at call-site, it **fully replaces** `default_load_options` for that query — giving you precise per-call control:

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

## `as_response`

Pass `as_response=True` to any write operation to get a [`Response[ModelType]`](../reference/schemas.md#fastapi_toolsets.schemas.Response) back directly for API usage:

```python
@router.get(
    "/{uuid}",
    response_model=Response[User],
    responses=generate_error_responses(NotFoundError),
)
async def get_user(session: SessionDep, uuid: UUID):
    return await crud.UserCrud.get(
        session=session,
        filters=[User.id == uuid],
        as_response=True,
    )
```

---

[:material-api: API Reference](../reference/crud.md)
