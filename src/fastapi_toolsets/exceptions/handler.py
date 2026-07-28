"""Exception handlers for FastAPI applications."""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import (
    HTTPException,
    RequestValidationError,
    ResponseValidationError,
)
from fastapi.responses import JSONResponse

from ..schemas import ErrorResponse, ResponseStatus
from .exceptions import ApiException

_VALIDATION_LOCATION_PARAMS: frozenset[str] = frozenset(
    {"body", "query", "path", "header", "cookie"}
)


def init_exceptions_handlers(app: FastAPI) -> FastAPI:
    """Register exception handlers and custom OpenAPI schema on a FastAPI app.

    Args:
        app: FastAPI application instance.

    Returns:
        The same FastAPI instance (for chaining).
    """
    _register_exception_handlers(app)
    _original_openapi = app.openapi
    app.openapi = lambda: _patched_openapi(app, _original_openapi)  # type: ignore[method-assign]  # ty:ignore[invalid-assignment]
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on a FastAPI application."""

    @app.exception_handler(ApiException)
    async def api_exception_handler(request: Request, exc: ApiException) -> Response:
        """Handle custom API exceptions with structured response."""
        api_error = exc.api_error
        error_response = ErrorResponse(
            data=api_error.data,
            message=api_error.msg,
            description=api_error.desc,
            error_code=api_error.err_code,
        )
        return JSONResponse(
            status_code=api_error.code,
            content=error_response.model_dump(),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> Response:
        """Handle Starlette/FastAPI HTTPException with a consistent error format."""
        detail = exc.detail if isinstance(exc.detail, str) else "HTTP Error"
        error_response = ErrorResponse(
            message=detail,
            error_code=f"HTTP-{exc.status_code}",
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response.model_dump(),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> Response:
        """Handle Pydantic request validation errors (422)."""
        return _format_validation_error(exc)

    @app.exception_handler(ResponseValidationError)
    async def response_validation_handler(
        request: Request, exc: ResponseValidationError
    ) -> Response:
        """Handle Pydantic response validation errors (422)."""
        return _format_validation_error(exc)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> Response:
        """Handle all unhandled exceptions with a generic 500 response."""
        error_response = ErrorResponse(
            message="Internal Server Error",
            description="An unexpected error occurred. Please try again later.",
            error_code="SERVER-500",
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_response.model_dump(),
        )


def _format_validation_error(
    exc: RequestValidationError | ResponseValidationError,
) -> JSONResponse:
    """Format validation errors into a structured response."""
    errors = exc.errors()
    formatted_errors = []

    for error in errors:
        locs = error["loc"]
        if locs and locs[0] in _VALIDATION_LOCATION_PARAMS:
            locs = locs[1:]
        field_path = ".".join(str(loc) for loc in locs)
        formatted_errors.append(
            {
                "field": field_path or "root",
                "message": error.get("msg", ""),
                "type": error.get("type", ""),
            }
        )

    error_response = ErrorResponse(
        data={"errors": formatted_errors},
        message="Validation Error",
        description=f"{len(formatted_errors)} validation error(s) detected",
        error_code="VAL-422",
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=error_response.model_dump(),
    )


def _patched_openapi(
    app: FastAPI, original_openapi: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """Generate the OpenAPI schema and replace default 422 responses.

    Args:
        app: FastAPI application instance.
        original_openapi: The previous ``app.openapi`` callable to delegate to.

    Returns:
        Patched OpenAPI schema dict.
    """
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = original_openapi()

    for path_data in openapi_schema.get("paths", {}).values():
        for operation in path_data.values():
            if isinstance(operation, dict) and "422" in operation.get("responses", {}):
                operation["responses"]["422"] = {
                    "description": "Validation Error",
                    "content": {
                        "application/json": {
                            "examples": {
                                "VAL-422": {
                                    "summary": "Validation Error",
                                    "value": {
                                        "data": {
                                            "errors": [
                                                {
                                                    "field": "field_name",
                                                    "message": "value is not valid",
                                                    "type": "value_error",
                                                }
                                            ]
                                        },
                                        "status": ResponseStatus.FAIL.value,
                                        "message": "Validation Error",
                                        "description": "1 validation error(s) detected",
                                        "error_code": "VAL-422",
                                    },
                                }
                            }
                        }
                    },
                }

    app.openapi_schema = openapi_schema
    return app.openapi_schema
