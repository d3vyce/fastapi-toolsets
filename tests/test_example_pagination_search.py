"""Live test for the docs/examples/pagination-search.md example.

Spins up the exact FastAPI app described in the example (sourced from
docs_src/examples/pagination_search/) and exercises it through a real HTTP
client against a real PostgreSQL database.
"""

import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from docs_src.examples.pagination_search.db import get_db
from docs_src.examples.pagination_search.models import Article, Base, Category
from docs_src.examples.pagination_search.routes import router
from fastapi_toolsets.exceptions import init_exceptions_handlers
from fastapi_toolsets.pytest import create_db_session

from .conftest import DATABASE_URL


def build_app(session: AsyncSession) -> FastAPI:
    app = FastAPI()
    init_exceptions_handlers(app)

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.include_router(router)
    return app


@pytest.fixture(scope="function")
async def ex_db_session():
    """Isolated session for the example models (separate tables from conftest)."""
    async with create_db_session(DATABASE_URL, Base) as session:
        yield session


@pytest.fixture
async def client(ex_db_session: AsyncSession):
    app = build_app(ex_db_session)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


async def seed(session: AsyncSession):
    """Insert representative fixture data."""
    python = Category(name="python")
    backend = Category(name="backend")
    session.add_all([python, backend])
    await session.flush()

    now = datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)
    session.add_all(
        [
            Article(
                title="FastAPI tips",
                body="Ten useful tips for FastAPI.",
                status="published",
                published=True,
                category_id=python.id,
                created_at=now,
            ),
            Article(
                title="SQLAlchemy async",
                body="How to use async SQLAlchemy.",
                status="published",
                published=True,
                category_id=backend.id,
                created_at=now + datetime.timedelta(seconds=1),
            ),
            Article(
                title="Draft notes",
                body="Work in progress.",
                status="draft",
                published=False,
                category_id=None,
                created_at=now + datetime.timedelta(seconds=2),
            ),
        ]
    )
    await session.commit()


class TestAppSessionDep:
    @pytest.mark.anyio
    async def test_get_db_yields_async_session(self):
        """The Database dependency yields a real AsyncSession when called directly."""
        from starlette.requests import Request

        from fastapi_toolsets.db import Database

        db = Database(DATABASE_URL)
        try:
            gen = db(Request({"type": "http", "headers": []}))
            session = await gen.__anext__()
            assert isinstance(session, AsyncSession)
            await gen.aclose()
        finally:
            await db.engine.dispose()


class TestOffsetPagination:
    @pytest.mark.anyio
    async def test_returns_all_articles(self, client: AsyncClient, ex_db_session):
        await seed(ex_db_session)

        resp = await client.get("/articles/offset")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total_count"] == 3
        assert len(body["data"]) == 3

    @pytest.mark.anyio
    async def test_pagination_page_size(self, client: AsyncClient, ex_db_session):
        await seed(ex_db_session)

        resp = await client.get("/articles/offset?items_per_page=2&page=1")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["pagination"]["total_count"] == 3
        assert body["pagination"]["has_more"] is True

    @pytest.mark.anyio
    async def test_full_text_search(self, client: AsyncClient, ex_db_session):
        await seed(ex_db_session)

        resp = await client.get("/articles/offset?search=fastapi")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total_count"] == 1
        assert body["data"][0]["title"] == "FastAPI tips"

    @pytest.mark.anyio
    async def test_search_traverses_relationship(
        self, client: AsyncClient, ex_db_session
    ):
        await seed(ex_db_session)

        # "python" matches Category.name, not Article.title or body
        resp = await client.get("/articles/offset?search=python")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total_count"] == 1
        assert body["data"][0]["title"] == "FastAPI tips"

    @pytest.mark.anyio
    async def test_facet_filter_scalar(self, client: AsyncClient, ex_db_session):
        await seed(ex_db_session)

        resp = await client.get("/articles/offset?status=published")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total_count"] == 2
        assert all(a["status"] == "published" for a in body["data"])

    @pytest.mark.anyio
    async def test_facet_filter_multi_value(self, client: AsyncClient, ex_db_session):
        await seed(ex_db_session)

        resp = await client.get("/articles/offset?status=published&status=draft")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total_count"] == 3

    @pytest.mark.anyio
    async def test_filter_attributes_in_response(
        self, client: AsyncClient, ex_db_session
    ):
        await seed(ex_db_session)

        resp = await client.get("/articles/offset")

        assert resp.status_code == 200
        body = resp.json()
        fa = body["filter_attributes"]
        assert set(fa["status"]) == {"draft", "published"}
        assert set(fa["category__name"]) == {"backend", "python"}

    @pytest.mark.anyio
    async def test_filter_attributes_scoped_to_filter(
        self, client: AsyncClient, ex_db_session
    ):
        await seed(ex_db_session)

        resp = await client.get("/articles/offset?status=published")

        body = resp.json()
        # a facet excludes its own filter_by condition, so filtering by
        # status=published still shows every status the facet offers
        assert "draft" in body["filter_attributes"]["status"]

    @pytest.mark.anyio
    async def test_search_and_filter_combined(self, client: AsyncClient, ex_db_session):
        await seed(ex_db_session)

        resp = await client.get("/articles/offset?search=async&status=published")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total_count"] == 1
        assert body["data"][0]["title"] == "SQLAlchemy async"


class TestCursorPagination:
    @pytest.mark.anyio
    async def test_first_page(self, client: AsyncClient, ex_db_session):
        await seed(ex_db_session)

        resp = await client.get("/articles/cursor?items_per_page=2")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["pagination"]["has_more"] is True
        assert body["pagination"]["next_cursor"] is not None
        assert body["pagination"]["prev_cursor"] is None

    @pytest.mark.anyio
    async def test_second_page(self, client: AsyncClient, ex_db_session):
        await seed(ex_db_session)

        first = await client.get("/articles/cursor?items_per_page=2")
        next_cursor = first.json()["pagination"]["next_cursor"]

        resp = await client.get(
            f"/articles/cursor?items_per_page=2&cursor={next_cursor}"
        )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["pagination"]["has_more"] is False

    @pytest.mark.anyio
    async def test_facet_filter(self, client: AsyncClient, ex_db_session):
        await seed(ex_db_session)

        resp = await client.get("/articles/cursor?status=draft")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["status"] == "draft"

    @pytest.mark.anyio
    async def test_full_text_search(self, client: AsyncClient, ex_db_session):
        await seed(ex_db_session)

        resp = await client.get("/articles/cursor?search=sqlalchemy")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["title"] == "SQLAlchemy async"


class TestOffsetSorting:
    """Tests for order_by / order query parameters on the offset endpoint."""

    @pytest.mark.anyio
    async def test_default_order_uses_created_at_asc(
        self, client: AsyncClient, ex_db_session
    ):
        """No order_by → default field (created_at) ASC."""
        await seed(ex_db_session)

        resp = await client.get("/articles/offset")

        assert resp.status_code == 200
        titles = [a["title"] for a in resp.json()["data"]]
        assert titles == ["FastAPI tips", "SQLAlchemy async", "Draft notes"]

    @pytest.mark.anyio
    async def test_order_by_title_asc(self, client: AsyncClient, ex_db_session):
        """order_by=title&order=asc returns alphabetical order."""
        await seed(ex_db_session)

        resp = await client.get("/articles/offset?order_by=title&order=asc")

        assert resp.status_code == 200
        titles = [a["title"] for a in resp.json()["data"]]
        assert titles == ["Draft notes", "FastAPI tips", "SQLAlchemy async"]

    @pytest.mark.anyio
    async def test_order_by_title_desc(self, client: AsyncClient, ex_db_session):
        """order_by=title&order=desc returns reverse alphabetical order."""
        await seed(ex_db_session)

        resp = await client.get("/articles/offset?order_by=title&order=desc")

        assert resp.status_code == 200
        titles = [a["title"] for a in resp.json()["data"]]
        assert titles == ["SQLAlchemy async", "FastAPI tips", "Draft notes"]

    @pytest.mark.anyio
    async def test_order_by_created_at_desc(self, client: AsyncClient, ex_db_session):
        """order_by=created_at&order=desc returns newest-first."""
        await seed(ex_db_session)

        resp = await client.get("/articles/offset?order_by=created_at&order=desc")

        assert resp.status_code == 200
        titles = [a["title"] for a in resp.json()["data"]]
        assert titles == ["Draft notes", "SQLAlchemy async", "FastAPI tips"]

    @pytest.mark.anyio
    async def test_invalid_order_by_returns_422(
        self, client: AsyncClient, ex_db_session
    ):
        """Unknown order_by field returns 422 with SORT-422 error code."""
        resp = await client.get("/articles/offset?order_by=nonexistent_field")

        assert resp.status_code == 422
        body = resp.json()
        assert body["error_code"] == "SORT-422"
        assert body["status"] == "FAIL"


class TestCursorSorting:
    """Tests for order_by / order query parameters on the cursor endpoint.

    In cursor_paginate the cursor_column is always the primary sort; order_by
    acts as a secondary tiebreaker. With the seeded articles (all having unique
    created_at values) the overall ordering is always created_at ASC regardless
    of the order_by value — only the valid/invalid field check and the response
    shape are meaningful here.
    """

    @pytest.mark.anyio
    async def test_default_order_uses_created_at_asc(
        self, client: AsyncClient, ex_db_session
    ):
        """No order_by → default field (created_at) ASC."""
        await seed(ex_db_session)

        resp = await client.get("/articles/cursor")

        assert resp.status_code == 200
        titles = [a["title"] for a in resp.json()["data"]]
        assert titles == ["FastAPI tips", "SQLAlchemy async", "Draft notes"]

    @pytest.mark.anyio
    async def test_order_by_title_asc_accepted(
        self, client: AsyncClient, ex_db_session
    ):
        """order_by=title is a valid field — request succeeds and returns all articles."""
        await seed(ex_db_session)

        resp = await client.get("/articles/cursor?order_by=title&order=asc")

        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 3

    @pytest.mark.anyio
    async def test_order_by_title_desc_accepted(
        self, client: AsyncClient, ex_db_session
    ):
        """order_by=title&order=desc is valid — request succeeds and returns all articles."""
        await seed(ex_db_session)

        resp = await client.get("/articles/cursor?order_by=title&order=desc")

        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 3

    @pytest.mark.anyio
    async def test_invalid_order_by_returns_422(
        self, client: AsyncClient, ex_db_session
    ):
        """Unknown order_by field returns 422 with SORT-422 error code."""
        resp = await client.get("/articles/cursor?order_by=nonexistent_field")

        assert resp.status_code == 422
        body = resp.json()
        assert body["error_code"] == "SORT-422"
        assert body["status"] == "FAIL"


class TestPaginateUnified:
    """Tests for the unified GET /articles/ endpoint using paginate()."""

    @pytest.mark.anyio
    async def test_defaults_to_offset_pagination(
        self, client: AsyncClient, ex_db_session
    ):
        """Without pagination_type, defaults to offset pagination."""
        await seed(ex_db_session)

        resp = await client.get("/articles/")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination_type"] == "offset"
        assert "total_count" in body["pagination"]
        assert body["pagination"]["total_count"] == 3

    @pytest.mark.anyio
    async def test_explicit_offset_pagination(self, client: AsyncClient, ex_db_session):
        """pagination_type=offset returns OffsetPagination metadata."""
        await seed(ex_db_session)

        resp = await client.get(
            "/articles/?pagination_type=offset&page=1&items_per_page=2"
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination_type"] == "offset"
        assert body["pagination"]["total_count"] == 3
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["has_more"] is True
        assert len(body["data"]) == 2

    @pytest.mark.anyio
    async def test_cursor_pagination_type(self, client: AsyncClient, ex_db_session):
        """pagination_type=cursor returns CursorPagination metadata."""
        await seed(ex_db_session)

        resp = await client.get("/articles/?pagination_type=cursor&items_per_page=2")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination_type"] == "cursor"
        assert "next_cursor" in body["pagination"]
        assert "total_count" not in body["pagination"]
        assert body["pagination"]["has_more"] is True
        assert len(body["data"]) == 2

    @pytest.mark.anyio
    async def test_cursor_pagination_navigate_pages(
        self, client: AsyncClient, ex_db_session
    ):
        """Cursor from first page can be used to fetch the next page."""
        await seed(ex_db_session)

        first = await client.get("/articles/?pagination_type=cursor&items_per_page=2")
        assert first.status_code == 200
        first_body = first.json()
        next_cursor = first_body["pagination"]["next_cursor"]
        assert next_cursor is not None

        second = await client.get(
            f"/articles/?pagination_type=cursor&items_per_page=2&cursor={next_cursor}"
        )
        assert second.status_code == 200
        second_body = second.json()
        assert second_body["pagination_type"] == "cursor"
        assert second_body["pagination"]["has_more"] is False
        assert len(second_body["data"]) == 1

    @pytest.mark.anyio
    async def test_cursor_pagination_with_search(
        self, client: AsyncClient, ex_db_session
    ):
        """paginate() with cursor type respects search parameter."""
        await seed(ex_db_session)

        resp = await client.get("/articles/?pagination_type=cursor&search=fastapi")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination_type"] == "cursor"
        assert len(body["data"]) == 1
        assert body["data"][0]["title"] == "FastAPI tips"

    @pytest.mark.anyio
    async def test_offset_pagination_with_filter(
        self, client: AsyncClient, ex_db_session
    ):
        """paginate() with offset type respects filter_by parameter."""
        await seed(ex_db_session)

        resp = await client.get("/articles/?pagination_type=offset&status=published")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination_type"] == "offset"
        assert body["pagination"]["total_count"] == 2
