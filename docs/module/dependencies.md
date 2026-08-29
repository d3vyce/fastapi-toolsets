# Dependencies

FastAPI dependency factories for automatic model resolution from path and body parameters.

## Overview

The `dependencies` module provides two factory functions that create FastAPI dependencies to fetch a model instance from the database automatically — either from a path parameter or from a request body field — and inject it directly into your route handler.

## `PathDependency`

[`PathDependency`](../reference/dependencies.md#fastapi_toolsets.dependencies.PathDependency) resolves a model from a URL path parameter and injects it into the route handler. Raises [`NotFoundError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.NotFoundError) automatically if the record does not exist.

```python
from fastapi_toolsets.dependencies import PathDependency

# Plain callable
UserDep = PathDependency(model=User, field=User.id, session_dep=get_db)

# Annotated
SessionDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = PathDependency(model=User, field=User.id, session_dep=SessionDep)


@router.get("/users/{user_id}")
async def get_user(user: User = UserDep):
    return user
```

By default the parameter name is inferred from the field (`user_id` for `User.id`). You can override it:

```python
UserDep = PathDependency(model=User, field=User.id, session_dep=get_db, param_name="id")


@router.get("/users/{id}")
async def get_user(user: User = UserDep):
    return user
```

## `BodyDependency`

[`BodyDependency`](../reference/dependencies.md#fastapi_toolsets.dependencies.BodyDependency) resolves a model from a field in the request body. Useful when a body contains a foreign key and you want the full object injected:

```python
from fastapi_toolsets.dependencies import BodyDependency

# Plain callable
RoleDep = BodyDependency(
    model=Role, field=Role.id, session_dep=get_db, body_field="role_id"
)

# Annotated
SessionDep = Annotated[AsyncSession, Depends(get_db)]
RoleDep = BodyDependency(
    model=Role, field=Role.id, session_dep=SessionDep, body_field="role_id"
)


@router.post("/users")
async def create_user(body: UserCreateSchema, role: Role = RoleDep):
    user = User(username=body.username, role=role)
    ...
```

## Eager loading

By default both factories fetch through a bare `CrudFactory(model)`, so relationships are not loaded. Pass `load_options` for a one-off, or `crud` to reuse a CRUD class you already configured:

```python
from sqlalchemy.orm import selectinload

from fastapi_toolsets.crud import CrudFactory
from fastapi_toolsets.dependencies import PathDependency

UserDep = PathDependency(
    model=User,
    field=User.id,
    session_dep=get_db,
    load_options=[selectinload(User.role)],
)

# Or reuse the app's configured CRUD and its default_load_options
UserCrud = CrudFactory(User, default_load_options=[selectinload(User.role)])
UserDep = PathDependency(model=User, field=User.id, session_dep=get_db, crud=UserCrud)


@router.get("/users/{user_id}")
async def get_user(user: User = UserDep):
    return user.role.name  # already loaded, no extra query
```

Both parameters work the same way on `BodyDependency`. When given together, the
usual [relationship loading](crud.md#relationship-loading) precedence applies.

---

[:material-api: API Reference](../reference/dependencies.md)
