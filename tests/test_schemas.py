"""Tests for fastapi_toolsets.schemas module."""

import pytest
from pydantic import ValidationError

from fastapi_toolsets.schemas import (
    ApiError,
    ErrorResponse,
    PaginatedResponse,
    Pagination,
    Response,
    ResponseStatus,
)


class TestResponseStatus:
    """Tests for ResponseStatus enum."""

    def test_success_value(self):
        """SUCCESS has correct value."""
        assert ResponseStatus.SUCCESS.value == "SUCCESS"

    def test_fail_value(self):
        """FAIL has correct value."""
        assert ResponseStatus.FAIL.value == "FAIL"

    def test_is_string_enum(self):
        """ResponseStatus is a string enum."""
        assert isinstance(ResponseStatus.SUCCESS, str)


class TestApiError:
    """Tests for ApiError schema."""

    def test_create_api_error(self):
        """Create ApiError with all fields."""
        error = ApiError(
            code=404,
            msg="Not Found",
            desc="The resource was not found.",
            err_code="RES-404",
        )

        assert error.code == 404
        assert error.msg == "Not Found"
        assert error.desc == "The resource was not found."
        assert error.err_code == "RES-404"

    def test_requires_all_fields(self):
        """ApiError requires all fields."""
        with pytest.raises(ValidationError):
            ApiError(code=404, msg="Not Found")  # type: ignore


class TestResponse:
    """Tests for Response schema."""

    def test_create_with_data(self):
        """Create Response with data."""
        response = Response(data={"id": 1, "name": "test"})

        assert response.data == {"id": 1, "name": "test"}
        assert response.status == ResponseStatus.SUCCESS
        assert response.message == "Success"
        assert response.error_code is None

    def test_create_with_custom_message(self):
        """Create Response with custom message."""
        response = Response(data="result", message="Operation completed")

        assert response.message == "Operation completed"

    def test_create_with_none_data(self):
        """Create Response with None data."""
        response = Response[dict](data=None)

        assert response.data is None
        assert response.status == ResponseStatus.SUCCESS

    def test_generic_type_hint(self):
        """Response supports generic type hints."""
        response: Response[list[str]] = Response(data=["a", "b", "c"])

        assert response.data == ["a", "b", "c"]

    def test_serialization(self):
        """Response serializes correctly."""
        response = Response(data={"key": "value"}, message="Test")
        data = response.model_dump()

        assert data["status"] == "SUCCESS"
        assert data["message"] == "Test"
        assert data["data"] == {"key": "value"}
        assert data["error_code"] is None


class TestErrorResponse:
    """Tests for ErrorResponse schema."""

    def test_default_values(self):
        """ErrorResponse has correct defaults."""
        response = ErrorResponse()

        assert response.status == ResponseStatus.FAIL
        assert response.data is None

    def test_with_description(self):
        """ErrorResponse with description."""
        response = ErrorResponse(
            message="Bad Request",
            description="The request was invalid.",
            error_code="BAD-400",
        )

        assert response.message == "Bad Request"
        assert response.description == "The request was invalid."
        assert response.error_code == "BAD-400"

    def test_serialization(self):
        """ErrorResponse serializes correctly."""
        response = ErrorResponse(
            message="Error",
            description="Details",
            error_code="ERR-500",
        )
        data = response.model_dump()

        assert data["status"] == "FAIL"
        assert data["description"] == "Details"


class TestPagination:
    """Tests for Pagination schema."""

    def test_create_pagination(self):
        """Create Pagination with all fields."""
        pagination = Pagination(
            total_count=100,
            items_per_page=10,
            page=1,
            has_more=True,
        )

        assert pagination.total_count == 100
        assert pagination.items_per_page == 10
        assert pagination.page == 1
        assert pagination.has_more is True

    def test_last_page_has_more_false(self):
        """Last page has has_more=False."""
        pagination = Pagination(
            total_count=25,
            items_per_page=10,
            page=3,
            has_more=False,
        )

        assert pagination.has_more is False

    def test_serialization(self):
        """Pagination serializes correctly."""
        pagination = Pagination(
            total_count=50,
            items_per_page=20,
            page=2,
            has_more=True,
        )
        data = pagination.model_dump()

        assert data["total_count"] == 50
        assert data["items_per_page"] == 20
        assert data["page"] == 2
        assert data["has_more"] is True


class TestPaginatedResponse:
    """Tests for PaginatedResponse schema."""

    def test_create_paginated_response(self):
        """Create PaginatedResponse with data and pagination."""
        pagination = Pagination(
            total_count=30,
            items_per_page=10,
            page=1,
            has_more=True,
        )
        response = PaginatedResponse(
            data=[{"id": 1}, {"id": 2}],
            pagination=pagination,
        )

        assert len(response.data) == 2
        assert response.pagination.total_count == 30
        assert response.status == ResponseStatus.SUCCESS

    def test_with_custom_message(self):
        """PaginatedResponse with custom message."""
        pagination = Pagination(
            total_count=5,
            items_per_page=10,
            page=1,
            has_more=False,
        )
        response = PaginatedResponse(
            data=[1, 2, 3, 4, 5],
            pagination=pagination,
            message="Found 5 items",
        )

        assert response.message == "Found 5 items"

    def test_empty_data(self):
        """PaginatedResponse with empty data."""
        pagination = Pagination(
            total_count=0,
            items_per_page=10,
            page=1,
            has_more=False,
        )
        response = PaginatedResponse[dict](
            data=[],
            pagination=pagination,
        )

        assert response.data == []
        assert response.pagination.total_count == 0

    def test_generic_type_hint(self):
        """PaginatedResponse supports generic type hints."""

        class UserOut:
            id: int
            name: str

        pagination = Pagination(
            total_count=1,
            items_per_page=10,
            page=1,
            has_more=False,
        )
        response: PaginatedResponse[dict] = PaginatedResponse(
            data=[{"id": 1, "name": "test"}],
            pagination=pagination,
        )

        assert response.data[0]["id"] == 1

    def test_serialization(self):
        """PaginatedResponse serializes correctly."""
        pagination = Pagination(
            total_count=100,
            items_per_page=10,
            page=5,
            has_more=True,
        )
        response = PaginatedResponse(
            data=["item1", "item2"],
            pagination=pagination,
            message="Page 5",
        )
        data = response.model_dump()

        assert data["status"] == "SUCCESS"
        assert data["message"] == "Page 5"
        assert data["data"] == ["item1", "item2"]
        assert data["pagination"]["page"] == 5


class TestFromAttributes:
    """Tests for from_attributes config (ORM mode)."""

    def test_response_from_orm_object(self):
        """Response can accept ORM-like objects."""

        class FakeOrmObject:
            def __init__(self):
                self.id = 1
                self.name = "test"

        obj = FakeOrmObject()
        response = Response(data=obj)

        assert response.data.id == 1  # type: ignore
        assert response.data.name == "test"  # type: ignore
