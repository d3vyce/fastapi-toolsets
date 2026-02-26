# Exceptions

Structured API exceptions with consistent error responses and automatic OpenAPI documentation.

## Overview

The `exceptions` module provides a set of pre-built HTTP exceptions and a FastAPI exception handler that formats all errors — including validation errors — into a uniform [`ErrorResponse`](../reference/schemas.md#fastapi_toolsets.schemas.ErrorResponse).

## Setup

Register the exception handlers on your FastAPI app at startup:

```python
from fastapi import FastAPI
from fastapi_toolsets.exceptions import init_exceptions_handlers

app = FastAPI()
init_exceptions_handlers(app=app)
```

This registers handlers for:

- [`ApiException`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.ApiException) — all custom exceptions below
- `RequestValidationError` — Pydantic request validation (422)
- `ResponseValidationError` — Pydantic response validation (422)
- `Exception` — unhandled errors (500)

## Built-in exceptions

| Exception | Status | Default message |
|-----------|--------|-----------------|
| [`UnauthorizedError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.UnauthorizedError) | 401 | Unauthorized |
| [`ForbiddenError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.ForbiddenError) | 403 | Forbidden |
| [`NotFoundError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.NotFoundError) | 404 | Not found |
| [`ConflictError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.ConflictError) | 409 | Conflict |
| [`NoSearchableFieldsError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.NoSearchableFieldsError) | 400 | No searchable fields |
| [`InvalidFacetFilterError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.InvalidFacetFilterError) | 400 | Invalid facet filter |

```python
from fastapi_toolsets.exceptions import NotFoundError

@router.get("/users/{id}")
async def get_user(id: int, session: AsyncSession = Depends(get_db)):
    user = await UserCrud.first(session=session, filters=[User.id == id])
    if not user:
        raise NotFoundError
    return user
```

## Custom exceptions

Subclass [`ApiException`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.ApiException) and define an `api_error` class variable:

```python
from fastapi_toolsets.exceptions import ApiException
from fastapi_toolsets.schemas import ApiError

class PaymentRequiredError(ApiException):
    api_error = ApiError(
        code=402,
        msg="Payment required",
        desc="Your subscription has expired.",
        err_code="PAYMENT_REQUIRED",
    )
```

## OpenAPI response documentation

Use [`generate_error_responses`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.generate_error_responses) to add error schemas to your endpoint's OpenAPI spec:

```python
from fastapi_toolsets.exceptions import generate_error_responses, NotFoundError, ForbiddenError

@router.get(
    "/users/{id}",
    responses=generate_error_responses(NotFoundError, ForbiddenError),
)
async def get_user(...): ...
```

!!! info
    The pydantic validation error is automatically added by FastAPI.

---

[:material-api: API Reference](../reference/exceptions.md)
