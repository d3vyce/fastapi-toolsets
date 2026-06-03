"""Tests for fastapi_toolsets.exceptions module."""

import pytest
from fastapi import FastAPI
from fastapi.exceptions import HTTPException
from fastapi.testclient import TestClient

from fastapi_toolsets.exceptions import (
    ApiException,
    ConflictError,
    ForbiddenError,
    InvalidOrderFieldError,
    LockTimeoutError,
    NotFoundError,
    PoolExhaustedError,
    UnauthorizedError,
    generate_error_responses,
    init_exceptions_handlers,
)
from fastapi_toolsets.schemas import ApiError


class TestApiException:
    """Tests for ApiException base class."""

    def test_subclass_with_api_error(self):
        """Subclasses can define api_error."""

        class CustomError(ApiException):
            api_error = ApiError(
                code=418,
                msg="I'm a teapot",
                desc="The server is a teapot.",
                err_code="TEA-418",
            )

        error = CustomError()
        assert error.api_error.code == 418
        assert error.api_error.msg == "I'm a teapot"
        assert str(error) == "I'm a teapot"

    def test_detail_overrides_msg_and_str(self):
        """detail sets both str(exc) and api_error.msg; class-level msg is unchanged."""

        class CustomError(ApiException):
            api_error = ApiError(
                code=400,
                msg="Bad Request",
                desc="Request was bad.",
                err_code="BAD-400",
            )

        error = CustomError("Widget not found")
        assert str(error) == "Widget not found"
        assert error.api_error.msg == "Widget not found"
        assert CustomError.api_error.msg == "Bad Request"  # class unchanged

    def test_desc_override(self):
        """desc kwarg overrides api_error.desc on the instance only."""

        class MyError(ApiException):
            api_error = ApiError(
                code=400, msg="Error", desc="Default.", err_code="ERR-400"
            )

        err = MyError(desc="Custom desc.")
        assert err.api_error.desc == "Custom desc."
        assert MyError.api_error.desc == "Default."  # class unchanged

    def test_data_override(self):
        """data kwarg sets api_error.data on the instance only."""

        class MyError(ApiException):
            api_error = ApiError(
                code=400, msg="Error", desc="Default.", err_code="ERR-400"
            )

        err = MyError(data={"key": "value"})
        assert err.api_error.data == {"key": "value"}
        assert MyError.api_error.data is None  # class unchanged

    def test_desc_and_data_override(self):
        """detail, desc and data can all be overridden together."""

        class MyError(ApiException):
            api_error = ApiError(
                code=400, msg="Error", desc="Default.", err_code="ERR-400"
            )

        err = MyError("custom msg", desc="New desc.", data={"x": 1})
        assert str(err) == "custom msg"
        assert err.api_error.msg == "custom msg"  # detail also updates msg
        assert err.api_error.desc == "New desc."
        assert err.api_error.data == {"x": 1}
        assert err.api_error.code == 400  # other fields unchanged

    def test_class_api_error_not_mutated_after_instance_override(self):
        """Raising with desc/data does not mutate the class-level api_error."""

        class MyError(ApiException):
            api_error = ApiError(
                code=400, msg="Error", desc="Default.", err_code="ERR-400"
            )

        MyError(desc="Changed", data={"x": 1})
        assert MyError.api_error.desc == "Default."
        assert MyError.api_error.data is None

    def test_subclass_uses_super_with_desc_and_data(self):
        """Subclasses can delegate detail/desc/data to super().__init__()."""

        class BuildValidationError(ApiException):
            api_error = ApiError(
                code=422,
                msg="Build Validation Error",
                desc="The build configuration is invalid.",
                err_code="BUILD-422",
            )

            def __init__(self, *errors: str) -> None:
                super().__init__(
                    f"{len(errors)} validation error(s)",
                    desc=", ".join(errors),
                    data={"errors": [{"message": e} for e in errors]},
                )

        err = BuildValidationError("Field A is required", "Field B is invalid")
        assert str(err) == "2 validation error(s)"
        assert err.api_error.msg == "2 validation error(s)"  # detail set msg
        assert err.api_error.desc == "Field A is required, Field B is invalid"
        assert err.api_error.data == {
            "errors": [
                {"message": "Field A is required"},
                {"message": "Field B is invalid"},
            ]
        }
        assert err.api_error.code == 422  # other fields unchanged

    def test_detail_desc_data_in_http_response(self):
        """detail/desc/data overrides all appear correctly in the FastAPI HTTP response."""

        class DynamicError(ApiException):
            api_error = ApiError(
                code=400, msg="Error", desc="Default.", err_code="ERR-400"
            )

            def __init__(self, message: str) -> None:
                super().__init__(
                    message,
                    desc=f"Detail: {message}",
                    data={"reason": message},
                )

        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/error")
        async def raise_error():
            raise DynamicError("something went wrong")

        client = TestClient(app)
        response = client.get("/error")

        assert response.status_code == 400
        body = response.json()
        assert body["message"] == "something went wrong"
        assert body["description"] == "Detail: something went wrong"
        assert body["data"] == {"reason": "something went wrong"}


class TestApiExceptionGuard:
    """Tests for the __init_subclass__ api_error guard."""

    def test_missing_api_error_raises_type_error(self):
        """Defining a subclass without api_error raises TypeError at class creation time."""
        with pytest.raises(
            TypeError, match="must define an 'api_error' class attribute"
        ):

            class BrokenError(ApiException):
                pass

    def test_abstract_subclass_skips_guard(self):
        """abstract=True allows intermediate base classes without api_error."""

        class BaseGroupError(ApiException, abstract=True):
            pass

        # Concrete child must still define it
        class ConcreteError(BaseGroupError):
            api_error = ApiError(
                code=400, msg="Error", desc="Desc.", err_code="ERR-400"
            )

        err = ConcreteError()
        assert err.api_error.code == 400

    def test_abstract_child_still_requires_api_error_on_concrete(self):
        """Concrete subclass of an abstract class must define api_error."""

        class Base(ApiException, abstract=True):
            pass

        with pytest.raises(
            TypeError, match="must define an 'api_error' class attribute"
        ):

            class Concrete(Base):
                pass

    def test_inherited_api_error_satisfies_guard(self):
        """Subclass that inherits api_error from a parent does not need its own."""

        class ConcreteError(NotFoundError):
            pass

        err = ConcreteError()
        assert err.api_error.code == 404


class TestDbExceptions:
    """Tests for database-related exception classes."""

    def test_pool_exhausted_error_attributes(self):
        """PoolExhaustedError has 503 status and DB-503-POOL error code."""
        error = PoolExhaustedError()
        assert error.api_error.code == 503
        assert error.api_error.err_code == "DB-503-POOL"
        assert error.api_error.msg == "Service Unavailable"

    def test_pool_exhausted_error_with_detail(self):
        """PoolExhaustedError accepts a detail string that overrides msg."""
        error = PoolExhaustedError("pool full")
        assert error.api_error.msg == "pool full"
        assert PoolExhaustedError.api_error.msg == "Service Unavailable"

    def test_lock_timeout_error_attributes(self):
        """LockTimeoutError has 503 status and DB-503-LOCK error code."""
        error = LockTimeoutError()
        assert error.api_error.code == 503
        assert error.api_error.err_code == "DB-503-LOCK"
        assert error.api_error.msg == "Service Unavailable"

    def test_lock_timeout_error_with_detail(self):
        """LockTimeoutError accepts a detail string that overrides msg."""
        error = LockTimeoutError("contended")
        assert error.api_error.msg == "contended"
        assert LockTimeoutError.api_error.msg == "Service Unavailable"

    def test_pool_exhausted_handled_as_503(self):
        """init_exceptions_handlers turns PoolExhaustedError into a 503 response."""
        from fastapi import FastAPI
        from fastapi_toolsets.exceptions import init_exceptions_handlers

        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/db")
        async def endpoint():
            raise PoolExhaustedError()

        client = TestClient(app)
        response = client.get("/db")
        assert response.status_code == 503
        assert response.json()["error_code"] == "DB-503-POOL"

    def test_lock_timeout_handled_as_503(self):
        """init_exceptions_handlers turns LockTimeoutError into a 503 response."""
        from fastapi import FastAPI
        from fastapi_toolsets.exceptions import init_exceptions_handlers

        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/lock")
        async def endpoint():
            raise LockTimeoutError()

        client = TestClient(app)
        response = client.get("/lock")
        assert response.status_code == 503
        assert response.json()["error_code"] == "DB-503-LOCK"


class TestBuiltInExceptions:
    """Tests for built-in exception classes."""

    def test_unauthorized_error(self):
        """UnauthorizedError has correct attributes."""
        error = UnauthorizedError()
        assert error.api_error.code == 401
        assert error.api_error.err_code == "AUTH-401"

    def test_forbidden_error(self):
        """ForbiddenError has correct attributes."""
        error = ForbiddenError()
        assert error.api_error.code == 403
        assert error.api_error.err_code == "AUTH-403"

    def test_not_found_error(self):
        """NotFoundError has correct attributes."""
        error = NotFoundError()
        assert error.api_error.code == 404
        assert error.api_error.err_code == "RES-404"

    def test_conflict_error(self):
        """ConflictError has correct attributes."""
        error = ConflictError()
        assert error.api_error.code == 409
        assert error.api_error.err_code == "RES-409"


class TestGenerateErrorResponses:
    """Tests for generate_error_responses function."""

    def test_generates_single_response(self):
        """Generates response for single exception."""
        responses = generate_error_responses(NotFoundError)

        assert 404 in responses
        assert responses[404]["description"] == "Not Found"

    def test_generates_multiple_responses(self):
        """Generates responses for multiple exceptions with distinct status codes."""
        responses = generate_error_responses(
            UnauthorizedError,
            ForbiddenError,
            NotFoundError,
        )

        assert 401 in responses
        assert 403 in responses
        assert 404 in responses

    def test_response_has_named_example(self):
        """Generated response uses named examples keyed by err_code."""
        responses = generate_error_responses(NotFoundError)
        examples = responses[404]["content"]["application/json"]["examples"]

        assert "RES-404" in examples
        value = examples["RES-404"]["value"]
        assert value["status"] == "FAIL"
        assert value["error_code"] == "RES-404"
        assert value["message"] == "Not Found"
        assert value["data"] is None

    def test_response_example_has_summary(self):
        """Each named example carries a summary equal to api_error.msg."""
        responses = generate_error_responses(NotFoundError)
        example = responses[404]["content"]["application/json"]["examples"]["RES-404"]

        assert example["summary"] == "Not Found"

    def test_response_example_with_data(self):
        """Generated response includes data when set on ApiError."""

        class ErrorWithData(ApiException):
            api_error = ApiError(
                code=400,
                msg="Bad Request",
                desc="Invalid input.",
                err_code="BAD-400",
                data={"details": "some context"},
            )

        responses = generate_error_responses(ErrorWithData)
        value = responses[400]["content"]["application/json"]["examples"]["BAD-400"][
            "value"
        ]

        assert value["data"] == {"details": "some context"}

    def test_two_errors_same_code_both_present(self):
        """Two exceptions with the same HTTP code produce two named examples."""

        class BadRequestA(ApiException):
            api_error = ApiError(
                code=400, msg="Bad A", desc="Reason A.", err_code="ERR-A"
            )

        class BadRequestB(ApiException):
            api_error = ApiError(
                code=400, msg="Bad B", desc="Reason B.", err_code="ERR-B"
            )

        responses = generate_error_responses(BadRequestA, BadRequestB)

        assert 400 in responses
        examples = responses[400]["content"]["application/json"]["examples"]
        assert "ERR-A" in examples
        assert "ERR-B" in examples
        assert examples["ERR-A"]["value"]["message"] == "Bad A"
        assert examples["ERR-B"]["value"]["message"] == "Bad B"

    def test_two_errors_same_code_single_top_level_entry(self):
        """Two exceptions with the same HTTP code produce exactly one top-level entry."""

        class BadRequestA(ApiException):
            api_error = ApiError(
                code=400, msg="Bad A", desc="Reason A.", err_code="ERR-A"
            )

        class BadRequestB(ApiException):
            api_error = ApiError(
                code=400, msg="Bad B", desc="Reason B.", err_code="ERR-B"
            )

        responses = generate_error_responses(BadRequestA, BadRequestB)
        assert len([k for k in responses if k == 400]) == 1


class TestInitExceptionsHandlers:
    """Tests for init_exceptions_handlers function."""

    def test_returns_app(self):
        """Returns the FastAPI app."""
        app = FastAPI()
        result = init_exceptions_handlers(app)
        assert result is app

    def test_handles_api_exception(self):
        """Handles ApiException with structured response."""
        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/error")
        async def raise_error():
            raise NotFoundError()

        client = TestClient(app)
        response = client.get("/error")

        assert response.status_code == 404
        data = response.json()
        assert data["status"] == "FAIL"
        assert data["error_code"] == "RES-404"
        assert data["message"] == "Not Found"

    def test_handles_api_exception_without_data(self):
        """ApiException without data returns null data field."""
        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/error")
        async def raise_error():
            raise NotFoundError()

        client = TestClient(app)
        response = client.get("/error")

        assert response.status_code == 404
        assert response.json()["data"] is None

    def test_handles_api_exception_with_data(self):
        """ApiException with data returns the data payload."""
        app = FastAPI()
        init_exceptions_handlers(app)

        class CustomValidationError(ApiException):
            api_error = ApiError(
                code=422,
                msg="Validation Error",
                desc="1 validation error(s) detected",
                err_code="CUSTOM-422",
                data={
                    "errors": [
                        {
                            "field": "email",
                            "message": "invalid format",
                            "type": "value_error",
                        }
                    ]
                },
            )

        @app.get("/error")
        async def raise_error():
            raise CustomValidationError()

        client = TestClient(app)
        response = client.get("/error")

        assert response.status_code == 422
        data = response.json()
        assert data["data"] == {
            "errors": [
                {"field": "email", "message": "invalid format", "type": "value_error"}
            ]
        }
        assert data["error_code"] == "CUSTOM-422"

    def test_handles_validation_error(self):
        """Handles validation errors with structured response."""
        from pydantic import BaseModel

        app = FastAPI()
        init_exceptions_handlers(app)

        class Item(BaseModel):
            name: str
            price: float

        @app.post("/items")
        async def create_item(item: Item):
            return item

        client = TestClient(app)
        response = client.post("/items", json={"name": 123})

        assert response.status_code == 422
        data = response.json()
        assert data["status"] == "FAIL"
        assert data["error_code"] == "VAL-422"
        assert "errors" in data["data"]

    def test_handles_generic_exception(self):
        """Handles unhandled exceptions with 500 response."""
        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/crash")
        async def crash():
            raise RuntimeError("Something went wrong")

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/crash")

        assert response.status_code == 500
        data = response.json()
        assert data["status"] == "FAIL"
        assert data["error_code"] == "SERVER-500"

    def test_handles_http_exception(self):
        """Handles starlette HTTPException with consistent ErrorResponse envelope."""
        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/protected")
        async def protected():
            raise HTTPException(status_code=403, detail="Forbidden")

        client = TestClient(app)
        response = client.get("/protected")

        assert response.status_code == 403
        data = response.json()
        assert data["status"] == "FAIL"
        assert data["error_code"] == "HTTP-403"
        assert data["message"] == "Forbidden"

    def test_handles_http_exception_404_from_route(self):
        """HTTPException(404) raised inside a route uses the consistent ErrorResponse envelope."""
        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/items/{item_id}")
        async def get_item(item_id: int):
            raise HTTPException(status_code=404, detail="Item not found")

        client = TestClient(app)
        response = client.get("/items/99")

        assert response.status_code == 404
        data = response.json()
        assert data["status"] == "FAIL"
        assert data["error_code"] == "HTTP-404"
        assert data["message"] == "Item not found"

    def test_handles_http_exception_forwards_headers(self):
        """HTTPException with WWW-Authenticate header forwards it in the response."""
        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/secure")
        async def secure():
            raise HTTPException(
                status_code=401,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )

        client = TestClient(app)
        response = client.get("/secure")

        assert response.status_code == 401
        assert response.headers.get("www-authenticate") == "Bearer"

    def test_custom_openapi_schema(self):
        """Customises OpenAPI schema for 422 responses using named examples."""
        from pydantic import BaseModel

        app = FastAPI()
        init_exceptions_handlers(app)

        class Item(BaseModel):
            name: str

        @app.post("/items")
        async def create_item(item: Item):
            return item

        openapi = app.openapi()

        post_op = openapi["paths"]["/items"]["post"]
        assert "422" in post_op["responses"]
        resp_422 = post_op["responses"]["422"]
        examples = resp_422["content"]["application/json"]["examples"]
        assert "VAL-422" in examples
        assert examples["VAL-422"]["value"]["error_code"] == "VAL-422"

    def test_custom_openapi_preserves_app_metadata(self):
        """_patched_openapi preserves custom FastAPI app-level metadata."""
        app = FastAPI(
            title="My API",
            version="2.0.0",
            description="Custom description",
        )
        init_exceptions_handlers(app)

        schema = app.openapi()
        assert schema["info"]["title"] == "My API"
        assert schema["info"]["version"] == "2.0.0"

    def test_handles_response_validation_error(self):
        """Handles ResponseValidationError with a structured 422 response."""
        from pydantic import BaseModel

        class CountResponse(BaseModel):
            count: int

        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/broken", response_model=CountResponse)
        async def broken():
            return {"count": "not-a-number"}  # triggers ResponseValidationError

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/broken")

        assert response.status_code == 422
        data = response.json()
        assert data["status"] == "FAIL"
        assert data["error_code"] == "VAL-422"
        assert "errors" in data["data"]

    def test_handles_validation_error_with_non_standard_loc(self):
        """Validation error with empty loc tuple maps the field to 'root'."""
        from fastapi.exceptions import RequestValidationError

        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/root-error")
        async def root_error():
            raise RequestValidationError(
                [
                    {
                        "type": "custom",
                        "loc": (),
                        "msg": "root level error",
                        "input": None,
                        "url": "",
                    }
                ]
            )

        client = TestClient(app)
        response = client.get("/root-error")

        assert response.status_code == 422
        data = response.json()
        assert data["data"]["errors"][0]["field"] == "root"

    def test_openapi_schema_cached_after_first_call(self):
        """app.openapi() returns the cached schema on subsequent calls."""
        from pydantic import BaseModel

        app = FastAPI()
        init_exceptions_handlers(app)

        class Item(BaseModel):
            name: str

        @app.post("/items")
        async def create_item(item: Item):
            return item

        schema_first = app.openapi()
        schema_second = app.openapi()
        assert schema_first is schema_second

    def test_openapi_skips_operations_without_422(self):
        """_patched_openapi leaves operations that have no 422 response unchanged."""
        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        schema = app.openapi()
        get_op = schema["paths"]["/ping"]["get"]
        assert "422" not in get_op["responses"]
        assert "200" in get_op["responses"]

    def test_openapi_skips_non_dict_path_item_values(self):
        """_patched_openapi ignores non-dict values in path items (e.g. path-level parameters)."""
        from fastapi_toolsets.exceptions.handler import _patched_openapi

        app = FastAPI()

        def fake_openapi() -> dict:
            return {
                "paths": {
                    "/items": {
                        "parameters": [
                            {"name": "q", "in": "query"}
                        ],  # list, not a dict
                        "get": {"responses": {"200": {"description": "OK"}}},
                    }
                }
            }

        schema = _patched_openapi(app, fake_openapi)
        # The list value was skipped without error; the GET operation is intact
        assert schema["paths"]["/items"]["parameters"] == [{"name": "q", "in": "query"}]
        assert "422" not in schema["paths"]["/items"]["get"]["responses"]


class TestExceptionIntegration:
    """Integration tests for exception handling."""

    @pytest.fixture
    def app_with_routes(self):
        """Create app with test routes."""
        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/users/{user_id}")
        async def get_user(user_id: int):
            if user_id == 404:
                raise NotFoundError()
            if user_id == 401:
                raise UnauthorizedError()
            if user_id == 403:
                raise ForbiddenError()
            if user_id == 409:
                raise ConflictError()
            return {"id": user_id}

        return app

    def test_not_found_response(self, app_with_routes):
        """NotFoundError returns 404."""
        client = TestClient(app_with_routes)
        response = client.get("/users/404")

        assert response.status_code == 404
        assert response.json()["error_code"] == "RES-404"

    def test_unauthorized_response(self, app_with_routes):
        """UnauthorizedError returns 401."""
        client = TestClient(app_with_routes)
        response = client.get("/users/401")

        assert response.status_code == 401
        assert response.json()["error_code"] == "AUTH-401"

    def test_forbidden_response(self, app_with_routes):
        """ForbiddenError returns 403."""
        client = TestClient(app_with_routes)
        response = client.get("/users/403")

        assert response.status_code == 403
        assert response.json()["error_code"] == "AUTH-403"

    def test_conflict_response(self, app_with_routes):
        """ConflictError returns 409."""
        client = TestClient(app_with_routes)
        response = client.get("/users/409")

        assert response.status_code == 409
        assert response.json()["error_code"] == "RES-409"

    def test_success_response(self, app_with_routes):
        """Successful requests return normally."""
        client = TestClient(app_with_routes)
        response = client.get("/users/1")

        assert response.status_code == 200
        assert response.json() == {"id": 1}


class TestInvalidOrderFieldError:
    """Tests for InvalidOrderFieldError exception."""

    def test_api_error_attributes(self):
        """InvalidOrderFieldError has correct api_error metadata."""
        assert InvalidOrderFieldError.api_error.code == 422
        assert InvalidOrderFieldError.api_error.err_code == "SORT-422"
        assert InvalidOrderFieldError.api_error.msg == "Invalid Order Field"

    def test_stores_field_and_valid_fields(self):
        """InvalidOrderFieldError stores field and valid_fields on the instance."""
        error = InvalidOrderFieldError("unknown", ["name", "created_at"])
        assert error.field == "unknown"
        assert error.valid_fields == ["name", "created_at"]

    def test_description_contains_field_and_valid_fields(self):
        """api_error.desc mentions the bad field and valid options."""
        error = InvalidOrderFieldError("bad_field", ["name", "email"])
        assert "bad_field" in error.api_error.desc
        assert "name" in error.api_error.desc
        assert "email" in error.api_error.desc

    def test_handled_as_422_by_exception_handler(self):
        """init_exceptions_handlers turns InvalidOrderFieldError into a 422 response."""
        app = FastAPI()
        init_exceptions_handlers(app)

        @app.get("/items")
        async def list_items():
            raise InvalidOrderFieldError("bad", ["name"])

        client = TestClient(app)
        response = client.get("/items")

        assert response.status_code == 422
        data = response.json()
        assert data["error_code"] == "SORT-422"
        assert data["status"] == "FAIL"
