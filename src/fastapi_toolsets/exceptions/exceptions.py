"""Custom exceptions with standardized API error responses."""

from typing import Any, ClassVar

from ..schemas import ApiError, ErrorResponse, ResponseStatus


class ApiException(Exception):
    """Base exception for API errors with structured response.

    Subclass this to create custom API exceptions with consistent error format.
    The exception handler will use api_error to generate the response.

    Example:
        class CustomError(ApiException):
            api_error = ApiError(
                code=400,
                msg="Bad Request",
                desc="The request was invalid.",
                err_code="CUSTOM-400",
            )
    """

    api_error: ClassVar[ApiError]

    def __init__(self, detail: str | None = None):
        """Initialize the exception.

        Args:
            detail: Optional override for the error message
        """
        super().__init__(detail or self.api_error.msg)


class UnauthorizedError(ApiException):
    """HTTP 401 - User is not authenticated."""

    api_error = ApiError(
        code=401,
        msg="Unauthorized",
        desc="Authentication credentials were missing or invalid.",
        err_code="AUTH-401",
    )


class ForbiddenError(ApiException):
    """HTTP 403 - User lacks required permissions."""

    api_error = ApiError(
        code=403,
        msg="Forbidden",
        desc="You do not have permission to access this resource.",
        err_code="AUTH-403",
    )


class NotFoundError(ApiException):
    """HTTP 404 - Resource not found."""

    api_error = ApiError(
        code=404,
        msg="Not Found",
        desc="The requested resource was not found.",
        err_code="RES-404",
    )


class ConflictError(ApiException):
    """HTTP 409 - Resource conflict."""

    api_error = ApiError(
        code=409,
        msg="Conflict",
        desc="The request conflicts with the current state of the resource.",
        err_code="RES-409",
    )


class InsufficientRolesError(ForbiddenError):
    """User does not have the required roles."""

    api_error = ApiError(
        code=403,
        msg="Insufficient Roles",
        desc="You do not have the required roles to access this resource.",
        err_code="RBAC-403",
    )

    def __init__(self, required_roles: list[str], user_roles: set[str] | None = None):
        self.required_roles = required_roles
        self.user_roles = user_roles

        desc = f"Required roles: {', '.join(required_roles)}"
        if user_roles is not None:
            desc += f". User has: {', '.join(user_roles) if user_roles else 'no roles'}"

        super().__init__(desc)


class UserNotFoundError(NotFoundError):
    """User was not found."""

    api_error = ApiError(
        code=404,
        msg="User Not Found",
        desc="The requested user was not found.",
        err_code="USER-404",
    )


class RoleNotFoundError(NotFoundError):
    """Role was not found."""

    api_error = ApiError(
        code=404,
        msg="Role Not Found",
        desc="The requested role was not found.",
        err_code="ROLE-404",
    )


class NoSearchableFieldsError(ApiException):
    """Raised when search is requested but no searchable fields are available."""

    api_error = ApiError(
        code=400,
        msg="No Searchable Fields",
        desc="No searchable fields configured for this resource.",
        err_code="SEARCH-400",
    )

    def __init__(self, model: type) -> None:
        self.model = model
        detail = (
            f"No searchable fields found for model '{model.__name__}'. "
            "Provide 'search_fields' parameter or set 'searchable_fields' on the CRUD class."
        )
        super().__init__(detail)


def generate_error_responses(
    *errors: type[ApiException],
) -> dict[int | str, dict[str, Any]]:
    """Generate OpenAPI response documentation for exceptions.

    Use this to document possible error responses for an endpoint.

    Args:
        *errors: Exception classes that inherit from ApiException

    Returns:
        Dict suitable for FastAPI's responses parameter

    Example:
        from fastapi_toolsets.exceptions import generate_error_responses, UnauthorizedError, ForbiddenError

        @app.get(
            "/admin",
            responses=generate_error_responses(UnauthorizedError, ForbiddenError)
        )
        async def admin_endpoint():
            ...
    """
    responses: dict[int | str, dict[str, Any]] = {}

    for error in errors:
        api_error = error.api_error

        responses[api_error.code] = {
            "model": ErrorResponse,
            "description": api_error.msg,
            "content": {
                "application/json": {
                    "example": {
                        "data": None,
                        "status": ResponseStatus.FAIL.value,
                        "message": api_error.msg,
                        "description": api_error.desc,
                        "error_code": api_error.err_code,
                    }
                }
            },
        }

    return responses
