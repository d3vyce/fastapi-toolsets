# Dependencies

FastAPI dependency factories for automatic model resolution from path and body parameters.

## Overview

The `dependencies` module provides two factory functions that create FastAPI dependencies to fetch a model instance from the database automatically — either from a path parameter or from a request body field — and inject it directly into your route handler.

## `PathDependency`

[`PathDependency`](../reference/dependencies.md#fastapi_toolsets.dependencies.PathDependency) resolves a model from a URL path parameter and injects it into the route handler. Raises [`NotFoundError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.NotFoundError) automatically if the record does not exist.

```python
from fastapi_toolsets.dependencies import PathDependency

UserDep = PathDependency(User, User.id, session_dep=get_db)

@router.get("/users/{user_id}")
async def get_user(user: User = UserDep):
    return user
```

The parameter name is inferred from the field (`user_id` for `User.id`). You can override it:

```python
UserDep = PathDependency(User, User.id, session_dep=get_db, param_name="id")

@router.get("/users/{id}")
async def get_user(user: User = UserDep):
    return user
```

## `BodyDependency`

[`BodyDependency`](../reference/dependencies.md#fastapi_toolsets.dependencies.BodyDependency) resolves a model from a field in the request body. Useful when a body contains a foreign key and you want the full object injected:

```python
from fastapi_toolsets.dependencies import BodyDependency

RoleDep = BodyDependency(Role, Role.id, session_dep=get_db, body_field="role_id")

@router.post("/users")
async def create_user(body: UserCreateSchema, role: Role = RoleDep):
    user = User(username=body.username, role=role)
    ...
```

[:material-api: API Reference](../reference/dependencies.md)
