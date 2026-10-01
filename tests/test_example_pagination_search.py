"""Live test for the docs/examples/pagination-search.md example.

Spins up the exact FastAPI app described in the example (sourced from
docs_src/examples/pagination_search/) and exercises it through a real HTTP
client against a real PostgreSQL database.
"""

import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from docs_src.examples.pagination_search.db import get_db
from docs_src.examples.pagination_search.models import Article, Base, Category
from docs_src.examples.pagination_search.routes import router
from fastapi_toolsets.db import Database
from fastapi_toolsets.exceptions import init_exceptions_handlers
from fastapi_toolsets.pytest import create_db_session

from .conftest import DATABASE_URL

# Seeded titles in created_at order.
FASTAPI, SQLALCHEMY, DRAFT = "FastAPI tips", "SQLAlchemy async", "Draft notes"
BY_CREATED_AT = [FASTAPI, SQLALCHEMY, DRAFT]
BY_TITLE = [DRAFT, FASTAPI, SQLALCHEMY]

OFFSET = "/articles/offset?"
CURSOR = "/articles/cursor?"
UNIFIED_OFFSET = "/articles/?pagination_type=offset&"
UNIFIED_CURSOR = "/articles/?pagination_type=cursor&"


def _build_app(session: AsyncSession) -> FastAPI:
    app = FastAPI()
    init_exceptions_handlers(app)

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.include_router(router)
    return app


async def _seed(session: AsyncSession) -> None:
    """Two published articles in categories, one uncategorised draft."""
    python = Category(name="python")
    backend = Category(name="backend")
    session.add_all([python, backend])
    await session.flush()

    now = datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)
    session.add_all(
        [
            Article(
                title=FASTAPI,
                body="Ten useful tips for FastAPI.",
                status="published",
                published=True,
                category_id=python.id,
                created_at=now,
            ),
            Article(
                title=SQLALCHEMY,
                body="How to use async SQLAlchemy.",
                status="published",
                published=True,
                category_id=backend.id,
                created_at=now + datetime.timedelta(seconds=1),
            ),
            Article(
                title=DRAFT,
                body="Work in progress.",
                status="draft",
                published=False,
                category_id=None,
                created_at=now + datetime.timedelta(seconds=2),
            ),
        ]
    )
    await session.commit()


@pytest.fixture
async def client():
    """A client over the example app bound to its own freshly seeded tables."""
    async with create_db_session(DATABASE_URL, Base) as session:
        await _seed(session)
        async with AsyncClient(
            transport=ASGITransport(app=_build_app(session)), base_url="http://test"
        ) as ac:
            yield ac


async def _get(client: AsyncClient, url: str) -> dict[str, Any]:
    resp = await client.get(url)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _titles(body: dict[str, Any]) -> list[str]:
    return [a["title"] for a in body["data"]]


@pytest.mark.anyio
async def test_get_db_yields_async_session():
    """The example's Database dependency yields a real AsyncSession."""
    db = Database(DATABASE_URL)
    try:
        gen = db(Request({"type": "http", "headers": []}))
        assert isinstance(await gen.__anext__(), AsyncSession)
        await gen.aclose()
    finally:
        await db.engine.dispose()


class TestSearchFilterAndSort:
    """Query parameters narrow and order the listing on every endpoint."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("query", "titles"),
        [
            ("", BY_CREATED_AT),
            ("search=fastapi", [FASTAPI]),
            ("search=python", [FASTAPI]),
            ("status=published", [FASTAPI, SQLALCHEMY]),
            ("status=published&status=draft", BY_CREATED_AT),
            ("search=async&status=published", [SQLALCHEMY]),
            ("order_by=title&order=asc", BY_TITLE),
            ("order_by=title&order=desc", BY_TITLE[::-1]),
            ("order_by=created_at&order=desc", BY_CREATED_AT[::-1]),
        ],
        ids=[
            "all/default_order",
            "search/field",
            "search/relationship",
            "filter/scalar",
            "filter/multi_value",
            "search+filter",
            "sort/title_asc",
            "sort/title_desc",
            "sort/created_at_desc",
        ],
    )
    @pytest.mark.parametrize(
        "prefix", [OFFSET, UNIFIED_OFFSET], ids=["offset", "unified"]
    )
    async def test_offset_results(
        self, client: AsyncClient, prefix: str, query: str, titles: list[str]
    ):
        """Search (including through ``category``), facets and ordering compose."""
        body = await _get(client, prefix + query)

        assert body["pagination_type"] == "offset"
        assert _titles(body) == titles
        assert body["pagination"]["total_count"] == len(titles)

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("query", "titles"),
        [
            ("", BY_CREATED_AT),
            ("status=draft", [DRAFT]),
            ("search=sqlalchemy", [SQLALCHEMY]),
            ("search=fastapi", [FASTAPI]),
            ("order_by=title&order=asc", BY_CREATED_AT),
            ("order_by=title&order=desc", BY_CREATED_AT),
        ],
        ids=[
            "all/default_order",
            "filter",
            "search",
            "search/fastapi",
            "sort/title_asc",
            "sort/title_desc",
        ],
    )
    @pytest.mark.parametrize(
        "prefix", [CURSOR, UNIFIED_CURSOR], ids=["cursor", "unified"]
    )
    async def test_cursor_results(
        self, client: AsyncClient, prefix: str, query: str, titles: list[str]
    ):
        """The cursor column stays the primary sort; order_by is only a tiebreaker."""
        body = await _get(client, prefix + query)

        assert body["pagination_type"] == "cursor"
        assert "total_count" not in body["pagination"]
        assert _titles(body) == titles

    @pytest.mark.anyio
    async def test_filter_attributes_list_facets_ignoring_their_own_filter(
        self, client: AsyncClient
    ):
        """Facets list every value, and ``status=`` does not hide the other statuses."""
        facets = (await _get(client, OFFSET))["filter_attributes"]
        assert set(facets["status"]) == {"draft", "published"}
        assert set(facets["category__name"]) == {"backend", "python"}

        scoped = (await _get(client, OFFSET + "status=published"))["filter_attributes"]
        assert "draft" in scoped["status"]

    @pytest.mark.anyio
    @pytest.mark.parametrize("prefix", [OFFSET, CURSOR], ids=["offset", "cursor"])
    async def test_invalid_order_by_returns_422(self, client: AsyncClient, prefix: str):
        resp = await client.get(prefix + "order_by=nonexistent_field")

        assert resp.status_code == 422
        assert resp.json()["error_code"] == "SORT-422"
        assert resp.json()["status"] == "FAIL"


class TestPaging:
    """Pages walk the seeded articles two at a time."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "prefix", [OFFSET, UNIFIED_OFFSET], ids=["offset", "unified"]
    )
    async def test_offset_page(self, client: AsyncClient, prefix: str):
        body = await _get(client, prefix + "items_per_page=2&page=1")

        assert body["pagination_type"] == "offset"
        assert _titles(body) == BY_CREATED_AT[:2]
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["total_count"] == 3
        assert body["pagination"]["has_more"] is True

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "prefix", [CURSOR, UNIFIED_CURSOR], ids=["cursor", "unified"]
    )
    async def test_cursor_pages(self, client: AsyncClient, prefix: str):
        """The first page's ``next_cursor`` fetches the remaining article."""
        first = await _get(client, prefix + "items_per_page=2")

        assert first["pagination_type"] == "cursor"
        assert _titles(first) == BY_CREATED_AT[:2]
        assert first["pagination"]["has_more"] is True
        assert first["pagination"]["prev_cursor"] is None
        next_cursor = first["pagination"]["next_cursor"]
        assert next_cursor is not None

        second = await _get(client, prefix + f"items_per_page=2&cursor={next_cursor}")

        assert _titles(second) == BY_CREATED_AT[2:]
        assert second["pagination"]["has_more"] is False

    @pytest.mark.anyio
    async def test_unified_defaults_to_offset(self, client: AsyncClient):
        body = await _get(client, "/articles/")

        assert body["pagination_type"] == "offset"
        assert body["pagination"]["total_count"] == 3
