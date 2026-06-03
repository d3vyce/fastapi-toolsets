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
- `HTTPException` — Starlette/FastAPI HTTP errors
- `RequestValidationError` — Pydantic request validation (422)
- `ResponseValidationError` — Pydantic response validation (422)
- `Exception` — unhandled errors (500)

It also patches `app.openapi()` to replace the default Pydantic 422 schema with a structured example matching the `ErrorResponse` format.

## Built-in exceptions

| Exception | Status | Default message |
|-----------|--------|-----------------|
| [`UnauthorizedError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.UnauthorizedError) | 401 | Unauthorized |
| [`ForbiddenError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.ForbiddenError) | 403 | Forbidden |
| [`NotFoundError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.NotFoundError) | 404 | Not Found |
| [`ConflictError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.ConflictError) | 409 | Conflict |
| [`NoSearchableFieldsError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.NoSearchableFieldsError) | 400 | No Searchable Fields |
| [`InvalidFacetFilterError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.InvalidFacetFilterError) | 400 | Invalid Facet Filter |
| [`InvalidOrderFieldError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.InvalidOrderFieldError) | 422 | Invalid Order Field |
| [`PoolExhaustedError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.PoolExhaustedError) | 503 | Service Unavailable |
| [`LockTimeoutError`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.LockTimeoutError) | 503 | Service Unavailable |

### Per-instance overrides

All built-in exceptions accept optional keyword arguments to customise the response for a specific raise site without changing the class defaults:

| Argument | Effect |
|----------|--------|
| `detail` | Overrides both `str(exc)` (log output) and the `message` field in the response body |
| `desc` | Overrides the `description` field |
| `data` | Overrides the `data` field |

```python
raise NotFoundError(detail="User 42 not found", desc="No user with that ID exists in the database.")
```

## Custom exceptions

Subclass [`ApiException`](../reference/exceptions.md#fastapi_toolsets.exceptions.exceptions.ApiException) and define an `api_error` class variable:

```python
from fastapi_toolsets.exceptions import ApiException
from fastapi_toolsets.schemas import ApiError

class PaymentRequiredError(ApiException):
    api_error = ApiError(
        code=402,
        msg="Payment Required",
        desc="Your subscription has expired.",
        err_code="BILLING-402",
    )
```

!!! warning
    Subclasses that do not define `api_error` raise a `TypeError` at **class creation time**, not at raise time.

### Custom `__init__`

Override `__init__` to compute `detail`, `desc`, or `data` dynamically, then delegate to `super().__init__()`:

```python
class OrderValidationError(ApiException):
    api_error = ApiError(
        code=422,
        msg="Order Validation Failed",
        desc="One or more order fields are invalid.",
        err_code="ORDER-422",
    )

    def __init__(self, *field_errors: str) -> None:
        super().__init__(
            f"{len(field_errors)} validation error(s)",
            desc=", ".join(field_errors),
            data={"errors": [{"message": e} for e in field_errors]},
        )
```

### Intermediate base classes

Use `abstract=True` when creating a shared base that is not meant to be raised directly:

```python
class BillingError(ApiException, abstract=True):
    """Base for all billing-related errors."""

class PaymentRequiredError(BillingError):
    api_error = ApiError(code=402, msg="Payment Required", desc="...", err_code="BILLING-402")

class SubscriptionExpiredError(BillingError):
    api_error = ApiError(code=402, msg="Subscription Expired", desc="...", err_code="BILLING-402-EXP")
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

Multiple exceptions sharing the same HTTP status code are grouped under one entry, each appearing as a named example keyed by its `err_code`. This keeps the OpenAPI UI readable when several error variants map to the same status.

---

[:material-api: API Reference](../reference/exceptions.md)
