"""Custom exceptions with standardized API error responses."""

from typing import Any, ClassVar

from sqlalchemy.exc import IntegrityError

from ..schemas import ApiError, ErrorResponse, ResponseStatus


class ApiException(Exception):
    """Base exception for API errors with structured response."""

    api_error: ClassVar[ApiError]

    def __init_subclass__(cls, abstract: bool = False, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not abstract and not hasattr(cls, "api_error"):
            raise TypeError(
                f"{cls.__name__} must define an 'api_error' class attribute. "
                "Pass abstract=True when creating intermediate base classes."
            )

    def __init__(
        self,
        detail: str | None = None,
        *,
        desc: str | None = None,
        data: Any = None,
    ) -> None:
        """Initialize the exception.

        Args:
            detail: Optional human-readable message
            desc: Optional per-instance override for the ``description`` field
                in the HTTP response body.
            data: Optional per-instance override for the ``data`` field in the
                HTTP response body.
        """
        updates: dict[str, Any] = {}
        if detail is not None:
            updates["msg"] = detail
        if desc is not None:
            updates["desc"] = desc
        if data is not None:
            updates["data"] = data
        if updates:
            object.__setattr__(
                self, "api_error", self.__class__.api_error.model_copy(update=updates)
            )
        super().__init__(self.api_error.msg)


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


class NoSearchableFieldsError(ApiException):
    """Raised when search is requested but no searchable fields are available."""

    api_error = ApiError(
        code=400,
        msg="No Searchable Fields",
        desc="No searchable fields configured for this resource.",
        err_code="SEARCH-400",
    )

    def __init__(self, model: type) -> None:
        """Initialize the exception.

        Args:
            model: The model class that has no searchable fields configured.
        """
        self.model = model
        super().__init__(
            desc=(
                f"No searchable fields found for model '{model.__name__}'. "
                "Provide 'search_fields' parameter or set 'searchable_fields' on the CRUD class."
            )
        )


class InvalidFacetFilterError(ApiException):
    """Raised when filter_by contains a key not declared in facet_fields."""

    api_error = ApiError(
        code=400,
        msg="Invalid Facet Filter",
        desc="One or more filter_by keys are not declared as facet fields.",
        err_code="FACET-400",
    )

    def __init__(self, key: str, valid_keys: set[str]) -> None:
        """Initialize the exception.

        Args:
            key: The unknown filter key provided by the caller.
            valid_keys: Set of valid keys derived from the declared facet_fields.
        """
        self.key = key
        self.valid_keys = valid_keys
        super().__init__(
            desc=(
                f"'{key}' is not a declared facet field. "
                f"Valid keys: {sorted(valid_keys) or 'none — set facet_fields on the CRUD class'}."
            )
        )


class UnsupportedFacetTypeError(ApiException):
    """Raised when a facet field has a column type not supported by filter_by."""

    api_error = ApiError(
        code=400,
        msg="Unsupported Facet Type",
        desc="The column type is not supported for facet filtering.",
        err_code="FACET-TYPE-400",
    )

    def __init__(self, key: str, col_type: str) -> None:
        """Initialize the exception.

        Args:
            key: The facet field key.
            col_type: The unsupported column type name.
        """
        self.key = key
        self.col_type = col_type
        super().__init__(
            desc=(
                f"Facet field '{key}' has unsupported column type '{col_type}'. "
                f"Supported types: String, Integer, Numeric, Boolean, "
                f"Date, DateTime, Time, Enum, Uuid, ARRAY."
            )
        )


class InvalidSearchColumnError(ApiException):
    """Raised when search_column is not one of the configured searchable fields."""

    api_error = ApiError(
        code=400,
        msg="Invalid Search Column",
        desc="The requested search column is not a configured searchable field.",
        err_code="SEARCH-COL-400",
    )

    def __init__(self, column: str, valid_columns: list[str]) -> None:
        """Initialize the exception.

        Args:
            column: The unknown search column provided by the caller.
            valid_columns: List of valid search column keys.
        """
        self.column = column
        self.valid_columns = valid_columns
        super().__init__(
            desc=(
                f"'{column}' is not a searchable column. "
                f"Valid columns: {valid_columns}."
            )
        )


class InvalidOrderFieldError(ApiException):
    """Raised when order_by contains a field not in the allowed order fields."""

    api_error = ApiError(
        code=422,
        msg="Invalid Order Field",
        desc="The requested order field is not allowed for this resource.",
        err_code="SORT-422",
    )

    def __init__(self, field: str, valid_fields: list[str]) -> None:
        """Initialize the exception.

        Args:
            field: The unknown order field provided by the caller.
            valid_fields: List of valid field names.
        """
        self.field = field
        self.valid_fields = valid_fields
        super().__init__(
            desc=f"'{field}' is not an allowed order field. Valid fields: {valid_fields}."
        )


class PoolExhaustedError(ApiException):
    """HTTP 503 - Database connection pool is exhausted."""

    api_error = ApiError(
        code=503,
        msg="Service Unavailable",
        desc=(
            "The database connection pool is exhausted. "
            "Too many concurrent requests are holding connections. "
            "Retry shortly or contact support if the issue persists."
        ),
        err_code="DB-503-POOL",
    )


class LockTimeoutError(ApiException):
    """HTTP 503 - A database lock could not be acquired within the timeout."""

    api_error = ApiError(
        code=503,
        msg="Service Unavailable",
        desc=(
            "A database lock could not be acquired within the allowed timeout. "
            "The resource is under heavy contention. Retry shortly."
        ),
        err_code="DB-503-LOCK",
    )


class IntegrityViolationError(ApiException, abstract=True):
    """Base for errors raised when a write breaks a database constraint."""

    def __init__(
        self,
        *,
        constraint: str | None = None,
        table: str | None = None,
        column: str | None = None,
    ) -> None:
        """Initialize the exception.

        Args:
            constraint: Name of the violated constraint, if known.
            table: Table the constraint belongs to, if known.
            column: Column involved, if known.
        """
        self.constraint = constraint
        self.table = table
        self.column = column
        super().__init__()


class UniqueViolationError(IntegrityViolationError):
    """HTTP 409 - A unique constraint was violated."""

    api_error = ApiError(
        code=409,
        msg="Conflict",
        desc="A resource with the same unique value already exists.",
        err_code="DB-409-UNIQUE",
    )


class ForeignKeyViolationError(IntegrityViolationError):
    """HTTP 409 - A foreign key constraint was violated."""

    api_error = ApiError(
        code=409,
        msg="Conflict",
        desc="The resource references, or is referenced by, another resource.",
        err_code="DB-409-FK",
    )


class ExclusionViolationError(IntegrityViolationError):
    """HTTP 409 - An exclusion constraint was violated."""

    api_error = ApiError(
        code=409,
        msg="Conflict",
        desc="The resource overlaps with an existing resource.",
        err_code="DB-409-EXCLUSION",
    )


class NotNullViolationError(IntegrityViolationError):
    """HTTP 422 - A required value is missing."""

    api_error = ApiError(
        code=422,
        msg="Unprocessable Content",
        desc="A required value is missing.",
        err_code="DB-422-NOTNULL",
    )


class CheckViolationError(IntegrityViolationError):
    """HTTP 422 - A check constraint was violated."""

    api_error = ApiError(
        code=422,
        msg="Unprocessable Content",
        desc="A value does not satisfy a database constraint.",
        err_code="DB-422-CHECK",
    )


_INTEGRITY_ERRORS: dict[str, type[IntegrityViolationError]] = {
    "23505": UniqueViolationError,
    "23503": ForeignKeyViolationError,
    "23001": ForeignKeyViolationError,  # restrict_violation
    "23P01": ExclusionViolationError,
    "23502": NotNullViolationError,
    "23514": CheckViolationError,
}


def from_integrity_error(exc: IntegrityError) -> IntegrityViolationError | None:
    """Translate a SQLAlchemy ``IntegrityError`` into an API error.

    Args:
        exc: The error raised by the flush or commit.

    Returns:
        The matching :class:`IntegrityViolationError`, or ``None`` when the
        violation is not one of the known PostgreSQL classes.
    """
    if exc.orig is None:
        return None
    driver_exc = exc.driver_exception
    error_class = _INTEGRITY_ERRORS.get(getattr(driver_exc, "sqlstate", None) or "")
    if error_class is None:
        return None
    return error_class(
        constraint=getattr(driver_exc, "constraint_name", None),
        table=getattr(driver_exc, "table_name", None),
        column=getattr(driver_exc, "column_name", None),
    )


def generate_error_responses(
    *errors: type[ApiException],
) -> dict[int | str, dict[str, Any]]:
    """Generate OpenAPI response documentation for exceptions.

    Args:
        *errors: Exception classes that inherit from ApiException.

    Returns:
        Dict suitable for FastAPI's ``responses`` parameter.
    """
    responses: dict[int | str, dict[str, Any]] = {}

    for error in errors:
        api_error = error.api_error
        code = api_error.code

        if code not in responses:
            responses[code] = {
                "model": ErrorResponse,
                "description": api_error.msg,
                "content": {
                    "application/json": {
                        "examples": {},
                    }
                },
            }

        responses[code]["content"]["application/json"]["examples"][
            api_error.err_code
        ] = {
            "summary": api_error.msg,
            "value": {
                "data": api_error.data,
                "status": ResponseStatus.FAIL.value,
                "message": api_error.msg,
                "description": api_error.desc,
                "error_code": api_error.err_code,
            },
        }

    return responses
