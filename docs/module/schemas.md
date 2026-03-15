# Schemas

Standardized Pydantic response models for consistent API responses across your FastAPI application.

## Overview

The `schemas` module provides generic response wrappers that enforce a uniform response structure. All models use `from_attributes=True` for ORM compatibility and `validate_assignment=True` for runtime type safety.

## Response models

### [`Response[T]`](../reference/schemas.md#fastapi_toolsets.schemas.Response)

The most common wrapper for a single resource response.

```python
from fastapi_toolsets.schemas import Response

@router.get("/users/{id}")
async def get_user(user: User = UserDep) -> Response[UserSchema]:
    return Response(data=user, message="User retrieved")
```

### Paginated response models

Three classes wrap paginated list results. Pick the one that matches your endpoint's strategy:

| Class | `pagination` type | `pagination_type` field | Use when |
|---|---|---|---|
| [`OffsetPaginatedResponse[T]`](#offsetpaginatedresponset) | `OffsetPagination` | `"offset"` (fixed) | endpoint always uses offset |
| [`CursorPaginatedResponse[T]`](#cursorpaginatedresponset) | `CursorPagination` | `"cursor"` (fixed) | endpoint always uses cursor |
| [`PaginatedResponse[T]`](#paginatedresponset) | `OffsetPagination \| CursorPagination` | — | unified endpoint supporting both strategies |

#### [`OffsetPaginatedResponse[T]`](../reference/schemas.md#fastapi_toolsets.schemas.OffsetPaginatedResponse)

!!! info "Added in `v2.3.0`"

Use as the return type when the endpoint always uses [`offset_paginate`](crud.md#offset-pagination).  The `pagination` field is guaranteed to be an [`OffsetPagination`](../reference/schemas.md#fastapi_toolsets.schemas.OffsetPagination) object; the response always includes a `pagination_type: "offset"` discriminator.

```python
from fastapi_toolsets.schemas import OffsetPaginatedResponse

@router.get("/users")
async def list_users(
    page: int = 1,
    items_per_page: int = 20,
) -> OffsetPaginatedResponse[UserSchema]:
    return await UserCrud.offset_paginate(
        session, page=page, items_per_page=items_per_page, schema=UserSchema
    )
```

**Response shape:**

```json
{
  "status": "SUCCESS",
  "pagination_type": "offset",
  "data": ["..."],
  "pagination": {
    "total_count": 100,
    "page": 1,
    "items_per_page": 20,
    "has_more": true
  }
}
```

#### [`CursorPaginatedResponse[T]`](../reference/schemas.md#fastapi_toolsets.schemas.CursorPaginatedResponse)

!!! info "Added in `v2.3.0`"

Use as the return type when the endpoint always uses [`cursor_paginate`](crud.md#cursor-pagination).  The `pagination` field is guaranteed to be a [`CursorPagination`](../reference/schemas.md#fastapi_toolsets.schemas.CursorPagination) object; the response always includes a `pagination_type: "cursor"` discriminator.

```python
from fastapi_toolsets.schemas import CursorPaginatedResponse

@router.get("/events")
async def list_events(
    cursor: str | None = None,
    items_per_page: int = 20,
) -> CursorPaginatedResponse[EventSchema]:
    return await EventCrud.cursor_paginate(
        session, cursor=cursor, items_per_page=items_per_page, schema=EventSchema
    )
```

**Response shape:**

```json
{
  "status": "SUCCESS",
  "pagination_type": "cursor",
  "data": ["..."],
  "pagination": {
    "next_cursor": "eyJpZCI6IDQyfQ==",
    "prev_cursor": null,
    "items_per_page": 20,
    "has_more": true
  }
}
```

#### [`PaginatedResponse[T]`](../reference/schemas.md#fastapi_toolsets.schemas.PaginatedResponse)

Return type for endpoints that support **both** pagination strategies via a `pagination_type` query parameter (using [`paginate()`](crud.md#unified-paginate--both-strategies-on-one-endpoint)).

When used as a return annotation, `PaginatedResponse[T]` automatically expands to `Annotated[Union[CursorPaginatedResponse[T], OffsetPaginatedResponse[T]], Field(discriminator="pagination_type")]`, so FastAPI emits a proper `oneOf` + discriminator in the OpenAPI schema with no extra boilerplate:

```python
from fastapi_toolsets.crud import PaginationType
from fastapi_toolsets.schemas import PaginatedResponse

@router.get("/users")
async def list_users(
    pagination_type: PaginationType = PaginationType.OFFSET,
    page: int = 1,
    cursor: str | None = None,
    items_per_page: int = 20,
) -> PaginatedResponse[UserSchema]:
    return await UserCrud.paginate(
        session,
        pagination_type=pagination_type,
        page=page,
        cursor=cursor,
        items_per_page=items_per_page,
        schema=UserSchema,
    )
```

#### Pagination metadata models

The optional `filter_attributes` field is populated when `facet_fields` are configured on the CRUD class (see [Filter attributes](crud.md#filter-attributes-facets)). It is `None` by default and can be hidden from API responses with `response_model_exclude_none=True`.

### [`ErrorResponse`](../reference/schemas.md#fastapi_toolsets.schemas.ErrorResponse)

Returned automatically by the exceptions handler.

---

[:material-api: API Reference](../reference/schemas.md)
