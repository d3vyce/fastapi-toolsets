"""Tests for the response envelopes and the pagination schemas."""

from typing import TypeVar, get_args

import pytest
from pydantic import ValidationError

from fastapi_toolsets.schemas import (
    ApiError,
    CursorPaginatedResponse,
    CursorPagination,
    ErrorResponse,
    OffsetPaginatedResponse,
    OffsetPagination,
    PaginatedResponse,
    PaginationType,
    PydanticBase,
    Response,
    ResponseStatus,
)

T = TypeVar("T")

_OFFSET = OffsetPagination(total_count=30, items_per_page=10, page=1, has_more=True)
_CURSOR = CursorPagination(
    next_cursor="tok_next", prev_cursor="tok_prev", items_per_page=10, has_more=True
)


class _OrmObject:
    """Attribute-only object, as an ORM row would be."""

    id = 1
    name = "test"


class TestEnvelopes:
    """``ApiError``, ``Response`` and ``ErrorResponse``."""

    def test_status_and_pagination_type_are_string_enums(self):
        assert [m.value for m in ResponseStatus] == ["SUCCESS", "FAIL"]
        assert [m.value for m in PaginationType] == ["offset", "cursor"]
        members = (*ResponseStatus, *PaginationType)
        assert all(isinstance(m, str) and m == m.value for m in members)

    def test_api_error_requires_the_core_fields_and_defaults_data(self):
        error = ApiError(
            code=404,
            msg="Not Found",
            desc="The resource was not found.",
            err_code="RES-404",
        )

        assert error.model_dump() == {
            "code": 404,
            "msg": "Not Found",
            "desc": "The resource was not found.",
            "err_code": "RES-404",
            "data": None,
        }
        assert error.model_copy(update={"data": {"errors": []}}).data == {"errors": []}
        with pytest.raises(ValidationError):
            ApiError(code=404, msg="Not Found")  # type: ignore[call-arg]  # ty:ignore[missing-argument]

    @pytest.mark.parametrize(
        "data",
        [None, {"id": 1, "name": "test"}, ["a", "b", "c"], _OrmObject()],
        ids=["none", "dict", "list", "orm-object"],
    )
    def test_response_wraps_any_payload_with_success_defaults(self, data):
        response = Response(data=data)

        assert response.data == data
        assert response.status is ResponseStatus.SUCCESS
        assert response.model_dump() == {
            "status": "SUCCESS",
            "message": "Success",
            "error_code": None,
            "data": data,
        }
        assert Response(data=data, message="Operation completed").message == (
            "Operation completed"
        )

    def test_error_response_defaults_to_fail(self):
        empty = ErrorResponse()
        assert (empty.status, empty.description, empty.data) == (
            ResponseStatus.FAIL,
            None,
            None,
        )

        response = ErrorResponse(
            message="Bad Request",
            description="The request was invalid.",
            error_code="BAD-400",
            data={"field": "id"},
        )
        assert response.model_dump() == {
            "status": "FAIL",
            "message": "Bad Request",
            "error_code": "BAD-400",
            "description": "The request was invalid.",
            "data": {"field": "id"},
        }


class TestPaginationMetadata:
    """The offset and cursor pagination blocks."""

    @pytest.mark.parametrize(
        ("total_count", "items_per_page", "pages"),
        [(42, 10, 5), (40, 10, 4), (0, 10, 0), (100, 0, 0), (None, 20, None)],
        ids=["rounded-up", "exact", "no-items", "zero-per-page", "unknown-total"],
    )
    def test_offset_pagination_computes_pages(self, total_count, items_per_page, pages):
        pagination = OffsetPagination(
            total_count=total_count,
            items_per_page=items_per_page,
            page=1,
            has_more=False,
        )

        assert pagination.pages == pages
        assert pagination.model_dump() == {
            "total_count": total_count,
            "items_per_page": items_per_page,
            "page": 1,
            "has_more": False,
            "pages": pages,
        }

    def test_cursor_pagination_defaults_prev_cursor_to_none(self):
        first = CursorPagination(next_cursor="n1", items_per_page=20, has_more=True)
        last = CursorPagination(
            next_cursor=None, prev_cursor="p1", items_per_page=20, has_more=False
        )

        assert first.model_dump() == {
            "next_cursor": "n1",
            "prev_cursor": None,
            "items_per_page": 20,
            "has_more": True,
        }
        assert last.model_dump() == {
            "next_cursor": None,
            "prev_cursor": "p1",
            "items_per_page": 20,
            "has_more": False,
        }


class TestPaginatedResponse:
    """The generic list envelope and its offset / cursor specialisations."""

    @pytest.mark.parametrize("pagination", [_OFFSET, _CURSOR], ids=["offset", "cursor"])
    def test_accepts_either_pagination_kind(self, pagination):
        response = PaginatedResponse(
            data=[{"id": 1}, {"id": 2}], pagination=pagination, message="Page 1"
        )

        assert isinstance(response.pagination, type(pagination))
        assert response.model_dump() == {
            "status": "SUCCESS",
            "message": "Page 1",
            "error_code": None,
            "data": [{"id": 1}, {"id": 2}],
            "pagination": pagination.model_dump(),
            "pagination_type": None,
            "filter_attributes": None,
            "search_columns": None,
            "order_columns": None,
        }

    def test_subscripting_builds_a_cached_discriminated_union(self):
        class Row(PydanticBase):
            id: int

        alias = PaginatedResponse[Row]

        union, discriminator = get_args(alias)
        assert set(get_args(union)) == {
            CursorPaginatedResponse[Row],
            OffsetPaginatedResponse[Row],
        }
        assert discriminator.discriminator == "pagination_type"
        assert PaginatedResponse[Row] is alias
        assert not hasattr(PaginatedResponse[T], "__metadata__")

    @pytest.mark.parametrize(
        ("cls", "pagination", "kind"),
        [
            (OffsetPaginatedResponse, _OFFSET, PaginationType.OFFSET),
            (CursorPaginatedResponse, _CURSOR, PaginationType.CURSOR),
        ],
        ids=["offset", "cursor"],
    )
    def test_typed_subclasses_pin_the_discriminator(self, cls, pagination, kind):
        response = cls(
            data=[{"id": 1}],
            pagination=pagination,
            filter_attributes={"status": ["active"]},
        )

        assert isinstance(response, PaginatedResponse)
        assert response.pagination == pagination
        assert response.pagination_type is kind
        dumped = response.model_dump(mode="json")
        assert dumped["pagination_type"] == kind.value
        assert dumped["pagination"] == pagination.model_dump(mode="json")
        assert (dumped["status"], dumped["filter_attributes"]) == (
            "SUCCESS",
            {"status": ["active"]},
        )

        other = (
            PaginationType.CURSOR
            if kind is PaginationType.OFFSET
            else PaginationType.OFFSET
        )
        with pytest.raises(ValidationError):
            cls(data=[], pagination=pagination, pagination_type=other)
