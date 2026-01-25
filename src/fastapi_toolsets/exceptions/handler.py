"""Exception handlers for FastAPI applications."""

from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from ..schemas import ResponseStatus
from .exceptions import ApiException


def init_exceptions_handlers(app: FastAPI) -> FastAPI:
    _register_exception_handlers(app)
    app.openapi = lambda: _custom_openapi(app)  # type: ignore[method-assign]
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on a FastAPI application.

    Args:
        app: FastAPI application instance

    Example:
        from fastapi import FastAPI
        from fastapi_toolsets.exceptions import init_exceptions_handlers

        app = FastAPI()
        init_exceptions_handlers(app)
    """

    @app.exception_handler(ApiException)
    async def api_exception_handler(request: Request, exc: ApiException) -> Response:
        """Handle custom API exceptions with structured response."""
        api_error = exc.api_error

        return JSONResponse(
            status_code=api_error.code,
            content={
                "data": None,
                "status": ResponseStatus.FAIL.value,
                "message": api_error.msg,
                "description": api_error.desc,
                "error_code": api_error.err_code,
            },
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
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "data": None,
                "status": ResponseStatus.FAIL.value,
                "message": "Internal Server Error",
                "description": "An unexpected error occurred. Please try again later.",
                "error_code": "SERVER-500",
            },
        )


def _format_validation_error(
    exc: RequestValidationError | ResponseValidationError,
) -> JSONResponse:
    """Format validation errors into a structured response."""
    errors = exc.errors()
    formatted_errors = []

    for error in errors:
        field_path = ".".join(
            str(loc)
            for loc in error["loc"]
            if loc not in ("body", "query", "path", "header", "cookie")
        )
        formatted_errors.append(
            {
                "field": field_path or "root",
                "message": error.get("msg", ""),
                "type": error.get("type", ""),
            }
        )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "data": {"errors": formatted_errors},
            "status": ResponseStatus.FAIL.value,
            "message": "Validation Error",
            "description": f"{len(formatted_errors)} validation error(s) detected",
            "error_code": "VAL-422",
        },
    )


def _custom_openapi(app: FastAPI) -> dict[str, Any]:
    """Generate custom OpenAPI schema with standardized error format.

    Replaces default 422 validation error responses with the custom format.

    Args:
        app: FastAPI application instance

    Returns:
        OpenAPI schema dict

    Example:
        from fastapi import FastAPI
        from fastapi_toolsets.exceptions import init_exceptions_handlers

        app = FastAPI()
        init_exceptions_handlers(app)  # Automatically sets custom OpenAPI
    """
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )

    for path_data in openapi_schema.get("paths", {}).values():
        for operation in path_data.values():
            if isinstance(operation, dict) and "responses" in operation:
                if "422" in operation["responses"]:
                    operation["responses"]["422"] = {
                        "description": "Validation Error",
                        "content": {
                            "application/json": {
                                "example": {
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
                                }
                            }
                        },
                    }

    app.openapi_schema = openapi_schema
    return app.openapi_schema
