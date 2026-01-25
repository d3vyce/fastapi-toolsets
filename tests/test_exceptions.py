"""Tests for fastapi_toolsets.exceptions module."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapi_toolsets.exceptions import (
    ApiException,
    ConflictError,
    ForbiddenError,
    NotFoundError,
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

    def test_custom_detail_message(self):
        """Custom detail overrides default message."""

        class CustomError(ApiException):
            api_error = ApiError(
                code=400,
                msg="Bad Request",
                desc="Request was bad.",
                err_code="BAD-400",
            )

        error = CustomError("Custom message")
        assert str(error) == "Custom message"


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
        """Generates responses for multiple exceptions."""
        responses = generate_error_responses(
            UnauthorizedError,
            ForbiddenError,
            NotFoundError,
        )

        assert 401 in responses
        assert 403 in responses
        assert 404 in responses

    def test_response_has_example(self):
        """Generated response includes example."""
        responses = generate_error_responses(NotFoundError)
        example = responses[404]["content"]["application/json"]["example"]

        assert example["status"] == "FAIL"
        assert example["error_code"] == "RES-404"
        assert example["message"] == "Not Found"


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

    def test_custom_openapi_schema(self):
        """Customizes OpenAPI schema for 422 responses."""
        app = FastAPI()
        init_exceptions_handlers(app)

        from pydantic import BaseModel

        class Item(BaseModel):
            name: str

        @app.post("/items")
        async def create_item(item: Item):
            return item

        openapi = app.openapi()

        post_op = openapi["paths"]["/items"]["post"]
        assert "422" in post_op["responses"]
        resp_422 = post_op["responses"]["422"]
        example = resp_422["content"]["application/json"]["example"]
        assert example["error_code"] == "VAL-422"


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
