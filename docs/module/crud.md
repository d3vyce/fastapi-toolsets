# CRUD

Generic async CRUD operations for SQLAlchemy models with search, pagination, and many-to-many support.

## Overview

The `crud` module provides [`AsyncCrud`](../reference/crud.md#fastapi_toolsets.crud.factory.AsyncCrud), an abstract base class with a full suite of async database operations, and [`CrudFactory`](../reference/crud.md#fastapi_toolsets.crud.factory.CrudFactory), a convenience function to instantiate it for a given model.

## Creating a CRUD class

```python
from fastapi_toolsets.crud import CrudFactory
from myapp.models import User

UserCrud = CrudFactory(
    User,
    searchable_fields=[User.username, User.email],
)
```

[`CrudFactory`](../reference/crud.md#fastapi_toolsets.crud.factory.CrudFactory) dynamically creates a class named `AsyncUserCrud` with `User` as its model.

## Basic operations

```python
# Create
user = await UserCrud.create(session, obj=UserCreateSchema(username="alice"))

# Get one (raises NotFoundError if not found)
user = await UserCrud.get(session, filters=[User.id == user_id])

# Get first or None
user = await UserCrud.first(session, filters=[User.email == email])

# Get multiple
users = await UserCrud.get_multi(session, filters=[User.is_active == True])

# Update
user = await UserCrud.update(session, obj=UserUpdateSchema(username="bob"), filters=[User.id == user_id])

# Delete
await UserCrud.delete(session, filters=[User.id == user_id])

# Count / exists
count = await UserCrud.count(session, filters=[User.is_active == True])
exists = await UserCrud.exists(session, filters=[User.email == email])
```

## Pagination

```python
result = await UserCrud.paginate(
    session,
    filters=[User.is_active == True],
    order_by=[User.created_at.desc()],
    page=1,
    items_per_page=20,
    search="alice",
    search_fields=[User.username, User.email],
)
# result.data: list of users
# result.pagination: Pagination(total_count, items_per_page, page, has_more)
```

## Search

Declare searchable fields on the CRUD class. Relationship traversal is supported via tuples:

```python
PostCrud = CrudFactory(
    Post,
    searchable_fields=[
        Post.title,
        Post.content,
        (Post.author, User.username),  # search across relationship
    ],
)
```

## Many-to-many relationships

Use `m2m_fields` to map schema fields containing lists of IDs to SQLAlchemy relationships. The CRUD class resolves and validates all IDs before persisting:

```python
PostCrud = CrudFactory(
    Post,
    m2m_fields={"tag_ids": Post.tags},
)

# schema: PostCreateSchema(title="Hello", tag_ids=[1, 2, 3])
post = await PostCrud.create(session, obj=PostCreateSchema(...))
```

## Upsert

Atomic `INSERT ... ON CONFLICT DO UPDATE` using PostgreSQL:

```python
await UserCrud.upsert(
    session,
    obj=UserCreateSchema(email="alice@example.com", username="alice"),
    index_elements=[User.email],
    set_={"username"},
)
```

## `as_response`

Pass `as_response=True` to any write operation to get a [`Response[ModelType]`](../reference/schemas.md#fastapi_toolsets.schemas.Response) back directly:

```python
response = await UserCrud.create(session, obj=schema, as_response=True)
# response: Response[User]
```

[:material-api: API Reference](../reference/crud.md)
