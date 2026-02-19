# Schemas

Standardized Pydantic response models for consistent API responses across your FastAPI application.

## Overview

The `schemas` module provides generic response wrappers that enforce a uniform response structure. All models use `from_attributes=True` for ORM compatibility and `validate_assignment=True` for runtime type safety.

## Response models

### `Response[T]`

The most common wrapper for a single resource response.

```python
from fastapi_toolsets.schemas import Response

@router.get("/users/{id}")
async def get_user(user: User = UserDep) -> Response[UserSchema]:
    return Response(data=user, message="User retrieved")
```

### `PaginatedResponse[T]`

Wraps a list of items with pagination metadata.

```python
from fastapi_toolsets.schemas import PaginatedResponse, Pagination

@router.get("/users")
async def list_users() -> PaginatedResponse[UserSchema]:
    return PaginatedResponse(
        data=users,
        pagination=Pagination(
            total_count=100,
            items_per_page=10,
            page=1,
            has_more=True,
        ),
    )
```

### `ErrorResponse`

Returned automatically by the exceptions handler. Can also be used as a response model for OpenAPI docs.

```python
from fastapi_toolsets.schemas import ErrorResponse

@router.delete("/users/{id}", responses={404: {"model": ErrorResponse}})
async def delete_user(...): ...
```

[:material-api: API Reference](../reference/schemas.md)
