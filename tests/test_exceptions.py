"""Tests for the API exceptions, their OpenAPI documentation and the handlers."""

import copy

import pytest
from fastapi import FastAPI
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel

from fastapi_toolsets.exceptions import (
    ApiException,
    ConflictError,
    ForbiddenError,
    InvalidFacetFilterError,
    InvalidOrderFieldError,
    InvalidSearchColumnError,
    LockTimeoutError,
    NoSearchableFieldsError,
    NotFoundError,
    PoolExhaustedError,
    UnauthorizedError,
    UnsupportedFacetTypeError,
    generate_error_responses,
    init_exceptions_handlers,
)
from fastapi_toolsets.exceptions.handler import _patched_openapi
from fastapi_toolsets.schemas import ApiError, ErrorResponse


class _PlainError(ApiException):
    api_error = ApiError(code=400, msg="Error", desc="Default.", err_code="ERR-400")


class _AbstractBase(ApiException, abstract=True):
    pass


class _BadRequestA(ApiException):
    api_error = ApiError(code=400, msg="Bad A", desc="Reason A.", err_code="ERR-A")


class _BadRequestB(ApiException):
    api_error = ApiError(code=400, msg="Bad B", desc="Reason B.", err_code="ERR-B")


class _ErrorWithData(ApiException):
    api_error = ApiError(
        code=422,
        msg="Validation Error",
        desc="1 validation error(s) detected",
        err_code="CUSTOM-422",
        data={"errors": [{"field": "email", "message": "invalid format"}]},
    )


class _DynamicError(ApiException):
    """Subclass forwarding detail, desc and data to ``super().__init__``."""

    api_error = ApiError(code=400, msg="Error", desc="Default.", err_code="DYN-400")

    def __init__(self, message: str) -> None:
        super().__init__(message, desc=f"Detail: {message}", data={"reason": message})


class _Widget:
    """Stand-in model for ``NoSearchableFieldsError``."""


class _Item(BaseModel):
    name: str
    price: float


class _Count(BaseModel):
    count: int


_BUILTIN_ERRORS = [
    pytest.param(
        UnauthorizedError(), 401, "AUTH-401", "Unauthorized", "credentials", id="401"
    ),
    pytest.param(
        ForbiddenError(), 403, "AUTH-403", "Forbidden", "permission", id="403"
    ),
    pytest.param(
        NotFoundError(), 404, "RES-404", "Not Found", "was not found", id="404"
    ),
    pytest.param(ConflictError(), 409, "RES-409", "Conflict", "conflicts", id="409"),
    pytest.param(
        PoolExhaustedError(),
        503,
        "DB-503-POOL",
        "Service Unavailable",
        "pool is exhausted",
        id="pool-exhausted",
    ),
    pytest.param(
        LockTimeoutError(),
        503,
        "DB-503-LOCK",
        "Service Unavailable",
        "lock could not be acquired",
        id="lock-timeout",
    ),
    pytest.param(
        NoSearchableFieldsError(_Widget),
        400,
        "SEARCH-400",
        "No Searchable Fields",
        "model '_Widget'",
        id="no-searchable-fields",
    ),
    pytest.param(
        InvalidFacetFilterError("color", {"size", "shape"}),
        400,
        "FACET-400",
        "Invalid Facet Filter",
        "Valid keys: ['shape', 'size']",
        id="invalid-facet-filter",
    ),
    pytest.param(
        UnsupportedFacetTypeError("blob", "LargeBinary"),
        400,
        "FACET-TYPE-400",
        "Unsupported Facet Type",
        "'blob' has unsupported column type 'LargeBinary'",
        id="unsupported-facet-type",
    ),
    pytest.param(
        InvalidSearchColumnError("ssn", ["name"]),
        400,
        "SEARCH-COL-400",
        "Invalid Search Column",
        "Valid columns: ['name']",
        id="invalid-search-column",
    ),
    pytest.param(
        InvalidOrderFieldError("bad", ["name"]),
        422,
        "SORT-422",
        "Invalid Order Field",
        "'bad' is not an allowed order field. Valid fields: ['name']",
        id="invalid-order-field",
    ),
]


def _app() -> FastAPI:
    return init_exceptions_handlers(FastAPI())


def _raising_client(error: BaseException) -> TestClient:
    """Client for an app whose ``/raise`` route raises *error*."""
    app = _app()

    @app.get("/raise")
    async def raise_error() -> None:
        raise error

    return TestClient(app, raise_server_exceptions=False)


def _validation_app() -> FastAPI:
    """App whose routes fail request, response and root-level validation."""
    app = _app()

    @app.post("/items")
    async def create_item(item: _Item) -> _Item:
        return item

    @app.get("/count", response_model=_Count)
    async def count() -> dict[str, str]:
        return {"count": "not-a-number"}

    @app.get("/root")
    async def root() -> None:
        raise RequestValidationError(
            [{"type": "custom", "loc": (), "msg": "root level error", "input": None}]
        )

    return app


class TestApiException:
    """Per-instance overrides and the ``api_error`` guard on subclasses."""

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({}, {"msg": "Error", "desc": "Default.", "data": None}),
            (
                {"detail": "Widget missing"},
                {"msg": "Widget missing", "desc": "Default.", "data": None},
            ),
            (
                {"desc": "Custom desc."},
                {"msg": "Error", "desc": "Custom desc.", "data": None},
            ),
            (
                {"data": {"key": "value"}},
                {"msg": "Error", "desc": "Default.", "data": {"key": "value"}},
            ),
            (
                {"detail": "custom msg", "desc": "New desc.", "data": {"x": 1}},
                {"msg": "custom msg", "desc": "New desc.", "data": {"x": 1}},
            ),
        ],
        ids=["none", "detail", "desc", "data", "all"],
    )
    def test_instance_overrides_leave_the_class_definition_alone(
        self, kwargs, expected
    ):
        error = _PlainError(**kwargs)

        assert str(error) == expected["msg"]
        assert error.api_error.model_dump(include={"msg", "desc", "data"}) == expected
        assert (error.api_error.code, error.api_error.err_code) == (400, "ERR-400")
        assert _PlainError.api_error == ApiError(
            code=400, msg="Error", desc="Default.", err_code="ERR-400"
        )

    @pytest.mark.parametrize(
        "base", [ApiException, _AbstractBase], ids=["direct", "under-abstract-base"]
    )
    def test_subclass_without_api_error_is_rejected_at_class_creation(self, base):
        with pytest.raises(
            TypeError, match="must define an 'api_error' class attribute"
        ):
            type("BrokenError", (base,), {})

    def test_abstract_bases_and_inherited_definitions_pass_the_guard(self):
        class Concrete(_AbstractBase):
            api_error = _PlainError.api_error

        class Inherited(NotFoundError):
            pass

        assert Concrete().api_error.code == 400
        assert Inherited().api_error.err_code == "RES-404"


class TestBuiltinErrors:
    """Every shipped exception carries its status, codes and description."""

    @pytest.mark.parametrize(
        ("error", "code", "err_code", "msg", "desc_part"), _BUILTIN_ERRORS
    )
    def test_definition_and_http_response(self, error, code, err_code, msg, desc_part):
        api_error = error.api_error
        assert (api_error.code, api_error.err_code, api_error.msg) == (
            code,
            err_code,
            msg,
        )
        assert desc_part in api_error.desc
        assert str(error) == msg

        response = _raising_client(error).get("/raise")

        assert response.status_code == code
        body = response.json()
        assert (body["status"], body["error_code"]) == ("FAIL", err_code)
        assert (body["message"], body["description"]) == (msg, api_error.desc)


class TestGenerateErrorResponses:
    """``generate_error_responses`` builds FastAPI ``responses`` entries."""

    def test_one_entry_per_status_code_with_an_example_per_error(self):
        responses = generate_error_responses(
            UnauthorizedError, _BadRequestA, NotFoundError, _BadRequestB
        )

        assert list(responses) == [401, 400, 404]
        entry = responses[400]
        assert entry["model"] is ErrorResponse and entry["description"] == "Bad A"
        examples = entry["content"]["application/json"]["examples"]
        assert [(key, ex["summary"]) for key, ex in examples.items()] == [
            ("ERR-A", "Bad A"),
            ("ERR-B", "Bad B"),
        ]

    @pytest.mark.parametrize(
        "error", [NotFoundError, _ErrorWithData], ids=["without-data", "with-data"]
    )
    def test_example_value_mirrors_the_api_error(self, error):
        api_error = error.api_error

        responses = generate_error_responses(error)

        content = responses[api_error.code]["content"]["application/json"]
        assert content["examples"][api_error.err_code] == {
            "summary": api_error.msg,
            "value": {
                "data": api_error.data,
                "status": "FAIL",
                "message": api_error.msg,
                "description": api_error.desc,
                "error_code": api_error.err_code,
            },
        }


class TestExceptionHandlers:
    """``init_exceptions_handlers`` wraps every failure in the error envelope."""

    @pytest.mark.parametrize(
        "error",
        [_ErrorWithData(), _DynamicError("something went wrong")],
        ids=["class-level-data", "instance-overrides"],
    )
    def test_api_exception_body_mirrors_its_api_error(self, error):
        api_error = error.api_error

        response = _raising_client(error).get("/raise")

        assert response.status_code == api_error.code
        assert response.json() == {
            "status": "FAIL",
            "message": api_error.msg,
            "description": api_error.desc,
            "error_code": api_error.err_code,
            "data": api_error.data,
        }

    @pytest.mark.parametrize(
        ("status_code", "detail", "headers", "message"),
        [
            (403, "Forbidden", None, "Forbidden"),
            (404, "Item not found", None, "Item not found"),
            (
                401,
                "Not authenticated",
                {"WWW-Authenticate": "Bearer"},
                "Not authenticated",
            ),
            (400, {"reason": "structured"}, None, "HTTP Error"),
        ],
        ids=["forbidden", "not-found", "with-headers", "non-string-detail"],
    )
    def test_http_exception_is_wrapped_in_the_envelope(
        self, status_code, detail, headers, message
    ):
        error = HTTPException(status_code=status_code, detail=detail, headers=headers)

        response = _raising_client(error).get("/raise")

        assert response.status_code == status_code
        assert response.json() == {
            "status": "FAIL",
            "message": message,
            "description": None,
            "error_code": f"HTTP-{status_code}",
            "data": None,
        }
        for key, value in (headers or {}).items():
            assert response.headers[key] == value

    @pytest.mark.parametrize(
        ("method", "path", "body", "fields"),
        [
            ("post", "/items", {"name": 123}, ["name", "price"]),
            ("get", "/count", None, ["response.count"]),
            ("get", "/root", None, ["root"]),
        ],
        ids=["request-body", "response-model", "empty-location"],
    )
    def test_validation_errors_become_a_422_listing_field_paths(
        self, method, path, body, fields
    ):
        client = TestClient(_validation_app(), raise_server_exceptions=False)

        response = client.request(method, path, json=body)

        assert response.status_code == 422
        data = response.json()
        assert (data["status"], data["error_code"]) == ("FAIL", "VAL-422")
        assert data["description"] == f"{len(fields)} validation error(s) detected"
        assert [e["field"] for e in data["data"]["errors"]] == fields
        assert all(e["message"] and e["type"] for e in data["data"]["errors"])

    def test_unhandled_exceptions_become_a_500_and_successes_pass_through(self):
        app = _app()

        @app.get("/crash")
        async def crash() -> None:
            raise RuntimeError("Something went wrong")

        @app.get("/ok")
        async def ok() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)

        assert client.get("/ok").json() == {"ok": True}
        response = client.get("/crash")
        assert response.status_code == 500
        assert response.json()["error_code"] == "SERVER-500"
        assert response.json()["status"] == "FAIL"

    def test_openapi_replaces_422_responses_and_caches_the_schema(self):
        app = FastAPI(title="My API", version="2.0.0")
        assert init_exceptions_handlers(app) is app

        @app.post("/items")
        async def create_item(item: _Item) -> _Item:
            return item

        @app.get("/ping")
        async def ping() -> dict[str, bool]:
            return {"ok": True}

        schema = app.openapi()

        assert (schema["info"]["title"], schema["info"]["version"]) == (
            "My API",
            "2.0.0",
        )
        post_422 = schema["paths"]["/items"]["post"]["responses"]["422"]
        example = post_422["content"]["application/json"]["examples"]["VAL-422"]
        assert example["value"]["error_code"] == "VAL-422"
        assert set(schema["paths"]["/ping"]["get"]["responses"]) == {"200"}
        assert app.openapi() is schema

    def test_patched_openapi_skips_non_dict_path_items(self):
        paths = {
            "/items": {
                "parameters": [{"name": "q", "in": "query"}],
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        }
        original = copy.deepcopy(paths)

        schema = _patched_openapi(FastAPI(), lambda: {"paths": paths})

        assert schema["paths"] == original
