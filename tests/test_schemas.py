"""Tests for fastapi_toolsets.schemas module."""

import pytest
from pydantic import ValidationError

from fastapi_toolsets.schemas import (
    ApiError,
    CursorPagination,
    CursorPaginatedResponse,
    ErrorResponse,
    OffsetPagination,
    OffsetPaginatedResponse,
    PaginatedResponse,
    PaginationType,
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

    def test_data_defaults_to_none(self):
        """ApiError data field defaults to None."""
        error = ApiError(
            code=404,
            msg="Not Found",
            desc="The resource was not found.",
            err_code="RES-404",
        )
        assert error.data is None

    def test_create_with_data(self):
        """ApiError can be created with a data payload."""
        error = ApiError(
            code=422,
            msg="Validation Error",
            desc="2 validation error(s) detected",
            err_code="VAL-422",
            data={
                "errors": [{"field": "name", "message": "required", "type": "missing"}]
            },
        )
        assert error.data == {
            "errors": [{"field": "name", "message": "required", "type": "missing"}]
        }

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


class TestOffsetPagination:
    """Tests for OffsetPagination schema (canonical name for offset-based pagination)."""

    def test_create_pagination(self):
        """Create OffsetPagination with all fields."""
        pagination = OffsetPagination(
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
        pagination = OffsetPagination(
            total_count=25,
            items_per_page=10,
            page=3,
            has_more=False,
        )

        assert pagination.has_more is False

    def test_serialization(self):
        """OffsetPagination serializes correctly."""
        pagination = OffsetPagination(
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


class TestCursorPagination:
    """Tests for CursorPagination schema."""

    def test_create_with_next_cursor(self):
        """CursorPagination with a next cursor indicates more pages."""
        pagination = CursorPagination(
            next_cursor="eyJ2YWx1ZSI6ICIxMjMifQ==",
            items_per_page=20,
            has_more=True,
        )
        assert pagination.next_cursor == "eyJ2YWx1ZSI6ICIxMjMifQ=="
        assert pagination.prev_cursor is None
        assert pagination.items_per_page == 20
        assert pagination.has_more is True

    def test_create_last_page(self):
        """CursorPagination for the last page has next_cursor=None and has_more=False."""
        pagination = CursorPagination(
            next_cursor=None,
            items_per_page=20,
            has_more=False,
        )
        assert pagination.next_cursor is None
        assert pagination.has_more is False

    def test_prev_cursor_defaults_to_none(self):
        """prev_cursor defaults to None."""
        pagination = CursorPagination(
            next_cursor=None, items_per_page=10, has_more=False
        )
        assert pagination.prev_cursor is None

    def test_prev_cursor_can_be_set(self):
        """prev_cursor can be explicitly set."""
        pagination = CursorPagination(
            next_cursor="next123",
            prev_cursor="prev456",
            items_per_page=10,
            has_more=True,
        )
        assert pagination.prev_cursor == "prev456"

    def test_serialization(self):
        """CursorPagination serializes correctly."""
        pagination = CursorPagination(
            next_cursor="abc123",
            prev_cursor="xyz789",
            items_per_page=20,
            has_more=True,
        )
        data = pagination.model_dump()
        assert data["next_cursor"] == "abc123"
        assert data["prev_cursor"] == "xyz789"
        assert data["items_per_page"] == 20
        assert data["has_more"] is True


class TestPaginatedResponse:
    """Tests for PaginatedResponse schema."""

    def test_create_paginated_response(self):
        """Create PaginatedResponse with data and pagination."""
        pagination = OffsetPagination(
            total_count=30,
            items_per_page=10,
            page=1,
            has_more=True,
        )
        response = PaginatedResponse(
            data=[{"id": 1}, {"id": 2}],
            pagination=pagination,
        )

        assert isinstance(response.pagination, OffsetPagination)
        assert len(response.data) == 2
        assert response.pagination.total_count == 30
        assert response.status == ResponseStatus.SUCCESS

    def test_with_custom_message(self):
        """PaginatedResponse with custom message."""
        pagination = OffsetPagination(
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
        pagination = OffsetPagination(
            total_count=0,
            items_per_page=10,
            page=1,
            has_more=False,
        )
        response = PaginatedResponse(
            data=[],
            pagination=pagination,
        )

        assert isinstance(response.pagination, OffsetPagination)
        assert response.data == []
        assert response.pagination.total_count == 0

    def test_class_getitem_with_concrete_type_returns_discriminated_union(self):
        """PaginatedResponse[T] with a concrete type returns a discriminated Annotated union."""
        import typing

        alias = PaginatedResponse[dict]
        args = typing.get_args(alias)
        # args[0] is the Union, args[1] is the FieldInfo discriminator
        union_args = typing.get_args(args[0])
        assert CursorPaginatedResponse[dict] in union_args
        assert OffsetPaginatedResponse[dict] in union_args

    def test_class_getitem_is_cached(self):
        """Repeated subscripting with the same type returns the identical cached object."""
        assert PaginatedResponse[dict] is PaginatedResponse[dict]

    def test_class_getitem_with_typevar_returns_generic(self):
        """PaginatedResponse[TypeVar] falls through to Pydantic generic parametrisation."""
        from typing import TypeVar

        T = TypeVar("T")
        alias = PaginatedResponse[T]
        # Should be a generic alias, not an Annotated union
        assert not hasattr(alias, "__metadata__")

    def test_generic_type_hint(self):
        """PaginatedResponse supports generic type hints."""
        pagination = OffsetPagination(
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
        pagination = OffsetPagination(
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

    def test_pagination_field_accepts_offset_pagination(self):
        """PaginatedResponse.pagination accepts OffsetPagination."""
        response = PaginatedResponse(
            data=[1, 2],
            pagination=OffsetPagination(
                total_count=2, items_per_page=10, page=1, has_more=False
            ),
        )
        assert isinstance(response.pagination, OffsetPagination)

    def test_pagination_field_accepts_cursor_pagination(self):
        """PaginatedResponse.pagination accepts CursorPagination."""
        response = PaginatedResponse(
            data=[1, 2],
            pagination=CursorPagination(
                next_cursor=None, items_per_page=10, has_more=False
            ),
        )
        assert isinstance(response.pagination, CursorPagination)


class TestPaginationType:
    """Tests for PaginationType enum."""

    def test_offset_value(self):
        """OFFSET has string value 'offset'."""
        assert PaginationType.OFFSET == "offset"
        assert PaginationType.OFFSET.value == "offset"

    def test_cursor_value(self):
        """CURSOR has string value 'cursor'."""
        assert PaginationType.CURSOR == "cursor"
        assert PaginationType.CURSOR.value == "cursor"

    def test_is_string_enum(self):
        """PaginationType is a string enum."""
        assert isinstance(PaginationType.OFFSET, str)
        assert isinstance(PaginationType.CURSOR, str)

    def test_members(self):
        """PaginationType has exactly two members."""
        assert set(PaginationType) == {PaginationType.OFFSET, PaginationType.CURSOR}


class TestOffsetPaginatedResponse:
    """Tests for OffsetPaginatedResponse schema."""

    def test_pagination_type_is_offset(self):
        """pagination_type is always PaginationType.OFFSET."""
        response = OffsetPaginatedResponse(
            data=[],
            pagination=OffsetPagination(
                total_count=0, items_per_page=10, page=1, has_more=False
            ),
        )
        assert response.pagination_type is PaginationType.OFFSET

    def test_pagination_type_serializes_to_string(self):
        """pagination_type serializes to 'offset' in JSON mode."""
        response = OffsetPaginatedResponse(
            data=[],
            pagination=OffsetPagination(
                total_count=0, items_per_page=10, page=1, has_more=False
            ),
        )
        assert response.model_dump(mode="json")["pagination_type"] == "offset"

    def test_pagination_field_is_typed(self):
        """pagination field is OffsetPagination, not the union."""
        response = OffsetPaginatedResponse(
            data=[{"id": 1}],
            pagination=OffsetPagination(
                total_count=10, items_per_page=5, page=2, has_more=True
            ),
        )
        assert isinstance(response.pagination, OffsetPagination)
        assert response.pagination.total_count == 10
        assert response.pagination.page == 2

    def test_is_subclass_of_paginated_response(self):
        """OffsetPaginatedResponse IS a PaginatedResponse."""
        response = OffsetPaginatedResponse(
            data=[],
            pagination=OffsetPagination(
                total_count=0, items_per_page=10, page=1, has_more=False
            ),
        )
        assert isinstance(response, PaginatedResponse)

    def test_pagination_type_default_cannot_be_overridden_to_cursor(self):
        """pagination_type rejects values other than OFFSET."""
        with pytest.raises(ValidationError):
            OffsetPaginatedResponse(
                data=[],
                pagination=OffsetPagination(
                    total_count=0, items_per_page=10, page=1, has_more=False
                ),
                pagination_type=PaginationType.CURSOR,  # type: ignore[arg-type]
            )

    def test_filter_attributes_defaults_to_none(self):
        """filter_attributes defaults to None."""
        response = OffsetPaginatedResponse(
            data=[],
            pagination=OffsetPagination(
                total_count=0, items_per_page=10, page=1, has_more=False
            ),
        )
        assert response.filter_attributes is None

    def test_full_serialization(self):
        """Full JSON serialization includes all expected fields."""
        response = OffsetPaginatedResponse(
            data=[{"id": 1}],
            pagination=OffsetPagination(
                total_count=1, items_per_page=10, page=1, has_more=False
            ),
            filter_attributes={"status": ["active"]},
        )
        data = response.model_dump(mode="json")

        assert data["pagination_type"] == "offset"
        assert data["status"] == "SUCCESS"
        assert data["data"] == [{"id": 1}]
        assert data["pagination"]["total_count"] == 1
        assert data["filter_attributes"] == {"status": ["active"]}


class TestCursorPaginatedResponse:
    """Tests for CursorPaginatedResponse schema."""

    def test_pagination_type_is_cursor(self):
        """pagination_type is always PaginationType.CURSOR."""
        response = CursorPaginatedResponse(
            data=[],
            pagination=CursorPagination(
                next_cursor=None, items_per_page=10, has_more=False
            ),
        )
        assert response.pagination_type is PaginationType.CURSOR

    def test_pagination_type_serializes_to_string(self):
        """pagination_type serializes to 'cursor' in JSON mode."""
        response = CursorPaginatedResponse(
            data=[],
            pagination=CursorPagination(
                next_cursor=None, items_per_page=10, has_more=False
            ),
        )
        assert response.model_dump(mode="json")["pagination_type"] == "cursor"

    def test_pagination_field_is_typed(self):
        """pagination field is CursorPagination, not the union."""
        response = CursorPaginatedResponse(
            data=[{"id": 1}],
            pagination=CursorPagination(
                next_cursor="abc123",
                prev_cursor=None,
                items_per_page=20,
                has_more=True,
            ),
        )
        assert isinstance(response.pagination, CursorPagination)
        assert response.pagination.next_cursor == "abc123"
        assert response.pagination.has_more is True

    def test_is_subclass_of_paginated_response(self):
        """CursorPaginatedResponse IS a PaginatedResponse."""
        response = CursorPaginatedResponse(
            data=[],
            pagination=CursorPagination(
                next_cursor=None, items_per_page=10, has_more=False
            ),
        )
        assert isinstance(response, PaginatedResponse)

    def test_pagination_type_default_cannot_be_overridden_to_offset(self):
        """pagination_type rejects values other than CURSOR."""
        with pytest.raises(ValidationError):
            CursorPaginatedResponse(
                data=[],
                pagination=CursorPagination(
                    next_cursor=None, items_per_page=10, has_more=False
                ),
                pagination_type=PaginationType.OFFSET,  # type: ignore[arg-type]
            )

    def test_full_serialization(self):
        """Full JSON serialization includes all expected fields."""
        response = CursorPaginatedResponse(
            data=[{"id": 1}],
            pagination=CursorPagination(
                next_cursor="tok_next",
                prev_cursor="tok_prev",
                items_per_page=10,
                has_more=True,
            ),
        )
        data = response.model_dump(mode="json")

        assert data["pagination_type"] == "cursor"
        assert data["status"] == "SUCCESS"
        assert data["pagination"]["next_cursor"] == "tok_next"
        assert data["pagination"]["prev_cursor"] == "tok_prev"


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
