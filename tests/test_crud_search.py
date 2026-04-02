"""Tests for CRUD search functionality."""

import inspect
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement, UnaryExpression

from fastapi_toolsets.crud import (
    CrudFactory,
    InvalidFacetFilterError,
    InvalidSearchColumnError,
    SearchConfig,
    UnsupportedFacetTypeError,
    get_searchable_fields,
)
from fastapi_toolsets.exceptions import InvalidOrderFieldError
from fastapi_toolsets.schemas import OffsetPagination, PaginationType

from .conftest import (
    Article,
    ArticleCreate,
    ArticleCrud,
    ArticleRead,
    Role,
    RoleCreate,
    RoleCrud,
    RoleCursorCrud,
    RoleRead,
    User,
    UserCreate,
    UserCrud,
    UserRead,
)


class TestPaginateSearch:
    """Tests for paginate() with search."""

    @pytest.mark.anyio
    async def test_search_single_column(self, db_session: AsyncSession):
        """Search on a single direct column."""
        await UserCrud.create(
            db_session, UserCreate(username="john_doe", email="john@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="jane_doe", email="jane@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob_smith", email="bob@test.com")
        )

        result = await UserCrud.offset_paginate(
            db_session,
            search="doe",
            search_fields=[User.username],
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2

    @pytest.mark.anyio
    async def test_search_multiple_columns(self, db_session: AsyncSession):
        """Search across multiple columns (OR logic)."""
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="alice@company.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="company_bob", email="bob@other.com")
        )

        result = await UserCrud.offset_paginate(
            db_session,
            search="company",
            search_fields=[User.username, User.email],
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2

    @pytest.mark.anyio
    async def test_search_relationship_depth1(self, db_session: AsyncSession):
        """Search through a relationship (depth 1)."""
        admin_role = await RoleCrud.create(db_session, RoleCreate(name="administrator"))
        user_role = await RoleCrud.create(db_session, RoleCreate(name="basic_user"))

        await UserCrud.create(
            db_session,
            UserCreate(username="admin1", email="a1@test.com", role_id=admin_role.id),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="admin2", email="a2@test.com", role_id=admin_role.id),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="user1", email="u1@test.com", role_id=user_role.id),
        )

        result = await UserCrud.offset_paginate(
            db_session,
            search="admin",
            search_fields=[(User.role, Role.name)],
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2

    @pytest.mark.anyio
    async def test_search_mixed_direct_and_relation(self, db_session: AsyncSession):
        """Search combining direct columns and relationships."""
        role = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        await UserCrud.create(
            db_session,
            UserCreate(username="john", email="john@test.com", role_id=role.id),
        )

        # Search "admin" in username OR role.name
        result = await UserCrud.offset_paginate(
            db_session,
            search="admin",
            search_fields=[User.username, (User.role, Role.name)],
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1

    @pytest.mark.anyio
    async def test_search_case_insensitive(self, db_session: AsyncSession):
        """Search is case-insensitive by default."""
        await UserCrud.create(
            db_session, UserCreate(username="JohnDoe", email="j@test.com")
        )

        result = await UserCrud.offset_paginate(
            db_session,
            search="johndoe",
            search_fields=[User.username],
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1

    @pytest.mark.anyio
    async def test_search_case_sensitive(self, db_session: AsyncSession):
        """Case-sensitive search with SearchConfig."""
        await UserCrud.create(
            db_session, UserCreate(username="JohnDoe", email="j@test.com")
        )

        # Should not find (case mismatch)
        result = await UserCrud.offset_paginate(
            db_session,
            search=SearchConfig(query="johndoe", case_sensitive=True),
            search_fields=[User.username],
            schema=UserRead,
        )
        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 0

        # Should find (case match)
        result = await UserCrud.offset_paginate(
            db_session,
            search=SearchConfig(query="JohnDoe", case_sensitive=True),
            search_fields=[User.username],
            schema=UserRead,
        )
        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1

    @pytest.mark.anyio
    async def test_search_empty_query(self, db_session: AsyncSession):
        """Empty search returns all results."""
        await UserCrud.create(
            db_session, UserCreate(username="user1", email="u1@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="user2", email="u2@test.com")
        )

        result = await UserCrud.offset_paginate(db_session, search="", schema=UserRead)
        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2

        result = await UserCrud.offset_paginate(
            db_session, search=None, schema=UserRead
        )
        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2

    @pytest.mark.anyio
    async def test_search_with_existing_filters(self, db_session: AsyncSession):
        """Search combines with existing filters (AND)."""
        await UserCrud.create(
            db_session,
            UserCreate(username="active_john", email="aj@test.com", is_active=True),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="inactive_john", email="ij@test.com", is_active=False),
        )

        result = await UserCrud.offset_paginate(
            db_session,
            filters=[User.is_active == True],  # noqa: E712
            search="john",
            search_fields=[User.username],
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1
        assert result.data[0].username == "active_john"

    @pytest.mark.anyio
    async def test_search_explicit_fields(self, db_session: AsyncSession):
        """Search works when search_fields are passed per call."""
        await UserCrud.create(
            db_session, UserCreate(username="findme", email="other@test.com")
        )

        result = await UserCrud.offset_paginate(
            db_session,
            search="findme",
            search_fields=[User.username],
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1

    @pytest.mark.anyio
    async def test_search_no_results(self, db_session: AsyncSession):
        """Search with no matching results."""
        await UserCrud.create(
            db_session, UserCreate(username="john", email="j@test.com")
        )

        result = await UserCrud.offset_paginate(
            db_session,
            search="nonexistent",
            search_fields=[User.username],
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 0
        assert result.data == []

    @pytest.mark.anyio
    async def test_search_with_pagination(self, db_session: AsyncSession):
        """Search respects pagination parameters."""
        for i in range(15):
            await UserCrud.create(
                db_session,
                UserCreate(username=f"user_{i}", email=f"user{i}@test.com"),
            )

        result = await UserCrud.offset_paginate(
            db_session,
            search="user_",
            search_fields=[User.username],
            page=1,
            items_per_page=5,
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 15
        assert len(result.data) == 5
        assert result.pagination.has_more is True

    @pytest.mark.anyio
    async def test_search_null_relationship(self, db_session: AsyncSession):
        """Users without relationship are included (outerjoin)."""
        role = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        await UserCrud.create(
            db_session,
            UserCreate(username="with_role", email="wr@test.com", role_id=role.id),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="no_role", email="nr@test.com", role_id=None),
        )

        # Search in username, not in role
        result = await UserCrud.offset_paginate(
            db_session,
            search="role",
            search_fields=[User.username],
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2

    @pytest.mark.anyio
    async def test_search_with_order_by(self, db_session: AsyncSession):
        """Search works with order_by parameter."""
        await UserCrud.create(
            db_session, UserCreate(username="charlie", email="c@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        result = await UserCrud.offset_paginate(
            db_session,
            search="@test.com",
            search_fields=[User.email],
            order_by=User.username,
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 3
        usernames = [u.username for u in result.data]
        assert usernames == ["alice", "bob", "charlie"]

    @pytest.mark.anyio
    async def test_search_non_string_column(self, db_session: AsyncSession):
        """Search on non-string columns (e.g., UUID) works via cast."""
        user_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        await UserCrud.create(
            db_session, UserCreate(id=user_id, username="john", email="john@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="jane", email="jane@test.com")
        )

        # Search by UUID (partial match)
        result = await UserCrud.offset_paginate(
            db_session,
            search="12345678",
            search_fields=[User.id, User.username],
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1
        assert result.data[0].id == user_id


class TestBuildSearchFilters:
    """Unit tests for build_search_filters."""

    def test_deduplicates_relationship_join(self):
        """Two tuple fields sharing the same relationship do not add the join twice."""
        from fastapi_toolsets.crud.search import build_search_filters

        # Both fields traverse User.role — the second must not re-add the join.
        filters, joins = build_search_filters(
            User,
            "admin",
            search_fields=[(User.role, Role.name), (User.role, Role.id)],
        )

        assert len(joins) == 1


class TestSearchConfig:
    """Tests for SearchConfig options."""

    def test_search_config_empty_query_returns_empty(self):
        """SearchConfig with an empty/blank query returns empty filters without hitting the DB."""
        from fastapi_toolsets.crud.search import build_search_filters

        filters, joins = build_search_filters(User, SearchConfig(query="   "))

        assert filters == []
        assert joins == []

    @pytest.mark.anyio
    async def test_match_mode_all(self, db_session: AsyncSession):
        """match_mode='all' requires all fields to match (AND)."""
        await UserCrud.create(
            db_session,
            UserCreate(username="john_test", email="john_test@company.com"),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="john_other", email="other@example.com"),
        )

        # 'john' must be in username AND email
        result = await UserCrud.offset_paginate(
            db_session,
            search=SearchConfig(query="john", match_mode="all"),
            search_fields=[User.username, User.email],
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1
        assert result.data[0].username == "john_test"

    @pytest.mark.anyio
    async def test_search_config_with_fields(self, db_session: AsyncSession):
        """SearchConfig can specify fields directly."""
        await UserCrud.create(
            db_session, UserCreate(username="test", email="findme@test.com")
        )

        result = await UserCrud.offset_paginate(
            db_session,
            search=SearchConfig(query="findme", fields=[User.email]),
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1


class TestNoSearchableFieldsError:
    """Tests for NoSearchableFieldsError exception."""

    def test_error_is_api_exception(self):
        """NoSearchableFieldsError inherits from ApiException."""
        from fastapi_toolsets.exceptions import ApiException, NoSearchableFieldsError

        assert issubclass(NoSearchableFieldsError, ApiException)

    def test_error_has_api_error_fields(self):
        """NoSearchableFieldsError has proper api_error configuration."""
        from fastapi_toolsets.exceptions import NoSearchableFieldsError

        assert NoSearchableFieldsError.api_error.code == 400
        assert NoSearchableFieldsError.api_error.err_code == "SEARCH-400"

    def test_error_message_contains_model_name(self):
        """Error message includes the model name."""
        from fastapi_toolsets.exceptions import NoSearchableFieldsError

        error = NoSearchableFieldsError(User)
        assert "User" in error.api_error.desc
        assert error.model is User

    def test_error_raised_when_no_fields(self):
        """Error is raised when search has no searchable fields."""
        from sqlalchemy import Integer
        from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

        from fastapi_toolsets.crud.search import build_search_filters
        from fastapi_toolsets.exceptions import NoSearchableFieldsError

        # Model with no String columns
        class NoStringBase(DeclarativeBase):
            pass

        class NoStringModel(NoStringBase):
            __tablename__ = "no_strings"
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            count: Mapped[int] = mapped_column(Integer, default=0)

        with pytest.raises(NoSearchableFieldsError) as exc_info:
            build_search_filters(NoStringModel, "test")

        assert exc_info.value.model is NoStringModel
        assert "NoStringModel" in exc_info.value.api_error.desc


class TestGetSearchableFields:
    """Tests for auto-detection of searchable fields."""

    def test_detects_string_columns(self):
        """Detects String columns on the model."""
        fields = get_searchable_fields(User, include_relationships=False)

        # Should include username and email (String), not id or is_active
        field_names = [str(f) for f in fields]
        assert any("username" in f for f in field_names)
        assert any("email" in f for f in field_names)
        assert not any("id" in f and "role_id" not in f for f in field_names)
        assert not any("is_active" in f for f in field_names)

    def test_detects_relationship_fields(self):
        """Detects String fields on related models."""
        fields = get_searchable_fields(User, include_relationships=True)

        # Should include (User.role, Role.name)
        has_role_name = any(isinstance(f, tuple) and len(f) == 2 for f in fields)
        assert has_role_name

    def test_skips_collection_relationships(self):
        """Skips one-to-many relationships."""
        fields = get_searchable_fields(Role, include_relationships=True)

        # Role.users is a collection, should not be included
        field_strs = [str(f) for f in fields]
        assert not any("users" in f for f in field_strs)


class TestFacetsNotSet:
    """filter_attributes is None when no facet_fields are configured."""

    @pytest.mark.anyio
    async def test_offset_paginate_no_facets(self, db_session: AsyncSession):
        """filter_attributes is None when facet_fields not set on factory or call."""
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )

        result = await UserCrud.offset_paginate(db_session, schema=UserRead)

        assert result.filter_attributes is None

    @pytest.mark.anyio
    async def test_cursor_paginate_no_facets(self, db_session: AsyncSession):
        """filter_attributes is None for cursor_paginate when facet_fields not set."""
        UserCursorCrud = CrudFactory(User, cursor_column=User.id)
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )

        result = await UserCursorCrud.cursor_paginate(db_session, schema=UserRead)

        assert result.filter_attributes is None


class TestFacetsDirectColumn:
    """Facets on direct model columns."""

    @pytest.mark.anyio
    async def test_offset_paginate_direct_column(self, db_session: AsyncSession):
        """Returns distinct values for a direct column via factory default."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        result = await UserFacetCrud.offset_paginate(db_session, schema=UserRead)

        assert result.filter_attributes is not None
        # Distinct usernames, sorted
        assert result.filter_attributes["username"] == ["alice", "bob"]

    @pytest.mark.anyio
    async def test_cursor_paginate_direct_column(self, db_session: AsyncSession):
        """Returns distinct values for a direct column in cursor_paginate."""
        UserFacetCursorCrud = CrudFactory(
            User, cursor_column=User.id, facet_fields=[User.email]
        )
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        result = await UserFacetCursorCrud.cursor_paginate(db_session, schema=UserRead)

        assert result.filter_attributes is not None
        assert set(result.filter_attributes["email"]) == {"a@test.com", "b@test.com"}

    @pytest.mark.anyio
    async def test_multiple_facet_columns(self, db_session: AsyncSession):
        """Returns distinct values for multiple columns."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username, User.email])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        result = await UserFacetCrud.offset_paginate(db_session, schema=UserRead)

        assert result.filter_attributes is not None
        assert "username" in result.filter_attributes
        assert "email" in result.filter_attributes
        assert set(result.filter_attributes["username"]) == {"alice", "bob"}

    @pytest.mark.anyio
    async def test_per_call_override(self, db_session: AsyncSession):
        """Per-call facet_fields overrides the factory default."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )

        # Override: ask for email instead of username
        result = await UserFacetCrud.offset_paginate(
            db_session, facet_fields=[User.email], schema=UserRead
        )

        assert result.filter_attributes is not None
        assert "email" in result.filter_attributes
        assert "username" not in result.filter_attributes


class TestFacetsRespectFilters:
    """Facets reflect the active filter conditions."""

    @pytest.mark.anyio
    async def test_facets_respect_base_filters(self, db_session: AsyncSession):
        """Facet values are scoped to the applied filters."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com", is_active=True)
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com", is_active=False)
        )

        # Filter to active users only — facets should only see "alice"
        result = await UserFacetCrud.offset_paginate(
            db_session,
            filters=[User.is_active == True],  # noqa: E712
            schema=UserRead,
        )

        assert result.filter_attributes is not None
        assert result.filter_attributes["username"] == ["alice"]


class TestFacetsRelationship:
    """Facets on relationship columns via tuple syntax."""

    @pytest.mark.anyio
    async def test_relationship_facet(self, db_session: AsyncSession):
        """Returns distinct values from a related model column."""
        UserRelFacetCrud = CrudFactory(User, facet_fields=[(User.role, Role.name)])

        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        editor = await RoleCrud.create(db_session, RoleCreate(name="editor"))

        await UserCrud.create(
            db_session,
            UserCreate(username="alice", email="a@test.com", role_id=admin.id),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="bob", email="b@test.com", role_id=editor.id),
        )
        # User without a role — their role.name should be excluded (None filtered out)
        await UserCrud.create(
            db_session, UserCreate(username="charlie", email="c@test.com")
        )

        result = await UserRelFacetCrud.offset_paginate(db_session, schema=UserRead)

        assert result.filter_attributes is not None
        assert set(result.filter_attributes["role__name"]) == {"admin", "editor"}

    @pytest.mark.anyio
    async def test_relationship_facet_none_excluded(self, db_session: AsyncSession):
        """None values (e.g. NULL role) are excluded from facet results."""
        UserRelFacetCrud = CrudFactory(User, facet_fields=[(User.role, Role.name)])

        # Only user with no role
        await UserCrud.create(
            db_session, UserCreate(username="norole", email="n@test.com")
        )

        result = await UserRelFacetCrud.offset_paginate(db_session, schema=UserRead)

        assert result.filter_attributes is not None
        assert result.filter_attributes["role__name"] == []

    @pytest.mark.anyio
    async def test_relationship_facet_deduplicates_join_with_search(
        self, db_session: AsyncSession
    ):
        """Facet join is not duplicated when search already added the same relationship join."""
        # Both search and facet use (User.role, Role.name) — join should not be doubled
        UserSearchFacetCrud = CrudFactory(
            User,
            searchable_fields=[(User.role, Role.name)],
            facet_fields=[(User.role, Role.name)],
        )

        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        await UserCrud.create(
            db_session,
            UserCreate(username="alice", email="a@test.com", role_id=admin.id),
        )

        result = await UserSearchFacetCrud.offset_paginate(
            db_session,
            search="admin",
            search_fields=[(User.role, Role.name)],
            schema=UserRead,
        )

        assert result.filter_attributes is not None
        assert result.filter_attributes["role__name"] == ["admin"]

    @pytest.mark.anyio
    async def test_relationship_search_and_filter_by_same_join(
        self, db_session: AsyncSession
    ):
        """Search + filter_by on the same relationship must not duplicate the JOIN."""
        UserSearchFacetCrud = CrudFactory(
            User,
            searchable_fields=[(User.role, Role.name)],
            facet_fields=[(User.role, Role.name)],
        )

        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        editor = await RoleCrud.create(db_session, RoleCreate(name="editor"))
        await UserCrud.create(
            db_session,
            UserCreate(username="alice", email="a@test.com", role_id=admin.id),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="bob", email="b@test.com", role_id=editor.id),
        )

        # Search by role name AND filter by role name — both need the same join
        result = await UserSearchFacetCrud.offset_paginate(
            db_session,
            search="admin",
            filter_by={"role__name": "admin"},
            schema=UserRead,
        )

        assert len(result.data) == 1
        assert result.data[0].username == "alice"
        assert result.filter_attributes is not None
        assert result.filter_attributes["role__name"] == ["admin"]

    @pytest.mark.anyio
    async def test_cursor_paginate_duplicate_join(self, db_session: AsyncSession):
        """cursor_paginate with overlapping search + facet joins must not fail."""
        UserSearchFacetCursorCrud = CrudFactory(
            User,
            searchable_fields=[(User.role, Role.name)],
            facet_fields=[(User.role, Role.name)],
            cursor_column=User.id,
        )

        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        await UserCrud.create(
            db_session,
            UserCreate(username="alice", email="a@test.com", role_id=admin.id),
        )

        result = await UserSearchFacetCursorCrud.cursor_paginate(
            db_session,
            search="admin",
            filter_by={"role__name": "admin"},
            schema=UserRead,
        )

        assert len(result.data) == 1
        assert result.data[0].username == "alice"


class TestFilterBy:
    """Tests for the filter_by parameter on offset_paginate and cursor_paginate."""

    @pytest.mark.anyio
    async def test_scalar_filter(self, db_session: AsyncSession):
        """filter_by with a scalar value produces an equality filter."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        result = await UserFacetCrud.offset_paginate(
            db_session, filter_by={"username": "alice"}, schema=UserRead
        )

        assert len(result.data) == 1
        assert result.data[0].username == "alice"
        # facet also scoped to the filter
        assert result.filter_attributes == {"username": ["alice"]}

    @pytest.mark.anyio
    async def test_list_filter_produces_in_clause(self, db_session: AsyncSession):
        """filter_by with a list value produces an IN filter."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="charlie", email="c@test.com")
        )

        result = await UserFacetCrud.offset_paginate(
            db_session, filter_by={"username": ["alice", "bob"]}, schema=UserRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2
        returned_names = {u.username for u in result.data}
        assert returned_names == {"alice", "bob"}

    @pytest.mark.anyio
    async def test_relationship_filter_by(self, db_session: AsyncSession):
        """filter_by works with relationship tuple facet fields."""
        UserRelFacetCrud = CrudFactory(User, facet_fields=[(User.role, Role.name)])

        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        editor = await RoleCrud.create(db_session, RoleCreate(name="editor"))
        await UserCrud.create(
            db_session,
            UserCreate(username="alice", email="a@test.com", role_id=admin.id),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="bob", email="b@test.com", role_id=editor.id),
        )

        result = await UserRelFacetCrud.offset_paginate(
            db_session, filter_by={"role__name": "admin"}, schema=UserRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1
        assert result.data[0].username == "alice"

    @pytest.mark.anyio
    async def test_filter_by_combined_with_filters(self, db_session: AsyncSession):
        """filter_by and filters= are combined (AND logic)."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com", is_active=True)
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="alice2", email="a2@test.com", is_active=False),
        )

        result = await UserFacetCrud.offset_paginate(
            db_session,
            filters=[User.is_active == True],  # noqa: E712
            filter_by={"username": ["alice", "alice2"]},
            schema=UserRead,
        )

        # Only alice passes both: is_active=True AND username IN [alice, alice2]
        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1
        assert result.data[0].username == "alice"

    @pytest.mark.anyio
    async def test_invalid_key_raises(self, db_session: AsyncSession):
        """filter_by with an undeclared key raises InvalidFacetFilterError."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])

        with pytest.raises(InvalidFacetFilterError) as exc_info:
            await UserFacetCrud.offset_paginate(
                db_session, filter_by={"nonexistent": "value"}, schema=UserRead
            )

        assert exc_info.value.key == "nonexistent"
        assert "username" in exc_info.value.valid_keys

    @pytest.mark.anyio
    async def test_filter_by_deduplicates_relationship_join(
        self, db_session: AsyncSession
    ):
        """Two filter_by keys through the same relationship do not duplicate the join."""
        # Both (User.role, Role.name) and (User.role, Role.id) traverse User.role —
        # the second key must not re-add the join (exercises the dedup branch in build_filter_by).
        UserRoleFacetCrud = CrudFactory(
            User,
            facet_fields=[(User.role, Role.name), (User.role, Role.id)],
        )

        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        editor = await RoleCrud.create(db_session, RoleCreate(name="editor"))
        await UserCrud.create(
            db_session,
            UserCreate(username="alice", email="a@test.com", role_id=admin.id),
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="bob", email="b@test.com", role_id=editor.id),
        )

        result = await UserRoleFacetCrud.offset_paginate(
            db_session,
            filter_by={"role__name": "admin", "role__id": str(admin.id)},
            schema=UserRead,
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1
        assert result.data[0].username == "alice"

    @pytest.mark.anyio
    async def test_cursor_paginate_filter_by(self, db_session: AsyncSession):
        """filter_by works with cursor_paginate."""
        UserFacetCursorCrud = CrudFactory(
            User, cursor_column=User.id, facet_fields=[User.username]
        )
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        result = await UserFacetCursorCrud.cursor_paginate(
            db_session, filter_by={"username": "alice"}, schema=UserRead
        )

        assert len(result.data) == 1
        assert result.data[0].username == "alice"
        assert result.filter_attributes == {"username": ["alice"]}

    @pytest.mark.anyio
    async def test_basemodel_filter_by_offset_paginate(self, db_session: AsyncSession):
        """filter_by accepts a BaseModel instance (model_dump path) in offset_paginate."""
        from pydantic import BaseModel as PydanticBaseModel

        class UserFilter(PydanticBaseModel):
            username: str | None = None

        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        result = await UserFacetCrud.offset_paginate(
            db_session, filter_by=UserFilter(username="alice"), schema=UserRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1
        assert result.data[0].username == "alice"

    @pytest.mark.anyio
    async def test_basemodel_filter_by_cursor_paginate(self, db_session: AsyncSession):
        """filter_by accepts a BaseModel instance (model_dump path) in cursor_paginate."""
        from pydantic import BaseModel as PydanticBaseModel

        class UserFilter(PydanticBaseModel):
            username: str | None = None

        UserFacetCursorCrud = CrudFactory(
            User, cursor_column=User.id, facet_fields=[User.username]
        )
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        result = await UserFacetCursorCrud.cursor_paginate(
            db_session, filter_by=UserFilter(username="alice"), schema=UserRead
        )

        assert len(result.data) == 1
        assert result.data[0].username == "alice"

    @pytest.mark.anyio
    async def test_bool_filter_false(self, db_session: AsyncSession):
        """filter_by with a string 'false' value correctly filters rows."""
        UserBoolCrud = CrudFactory(User, facet_fields=[User.is_active])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com", is_active=True)
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="bob", email="b@test.com", is_active=False),
        )

        result = await UserBoolCrud.offset_paginate(
            db_session, filter_by={"is_active": "false"}, schema=UserRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1
        assert result.data[0].username == "bob"

    @pytest.mark.anyio
    async def test_bool_filter_true(self, db_session: AsyncSession):
        """filter_by with a string 'true' value correctly filters rows."""
        UserBoolCrud = CrudFactory(User, facet_fields=[User.is_active])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com", is_active=True)
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="bob", email="b@test.com", is_active=False),
        )

        result = await UserBoolCrud.offset_paginate(
            db_session, filter_by={"is_active": "true"}, schema=UserRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1
        assert result.data[0].username == "alice"

    @pytest.mark.anyio
    async def test_bool_filter_list(self, db_session: AsyncSession):
        """filter_by with a list of string booleans produces an IN clause."""
        UserBoolCrud = CrudFactory(User, facet_fields=[User.is_active])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com", is_active=True)
        )
        await UserCrud.create(
            db_session,
            UserCreate(username="bob", email="b@test.com", is_active=False),
        )

        result = await UserBoolCrud.offset_paginate(
            db_session, filter_by={"is_active": ["true", "false"]}, schema=UserRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2

    @pytest.mark.anyio
    async def test_bool_filter_native_bool(self, db_session: AsyncSession):
        """filter_by with a native Python bool passes through coercion."""
        UserBoolCrud = CrudFactory(User, facet_fields=[User.is_active])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com", is_active=True)
        )

        result = await UserBoolCrud.offset_paginate(
            db_session, filter_by={"is_active": True}, schema=UserRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1

    def test_bool_coerce_invalid_value(self):
        """_coerce_bool raises ValueError for non-bool, non-string values."""
        from fastapi_toolsets.crud.search import _coerce_bool

        with pytest.raises(ValueError, match="Cannot coerce"):
            _coerce_bool(42)

    def test_bool_coerce_invalid_string(self):
        """_coerce_bool raises ValueError for unrecognized string values."""
        from fastapi_toolsets.crud.search import _coerce_bool

        with pytest.raises(ValueError, match="Cannot coerce"):
            _coerce_bool("maybe")

    @pytest.mark.anyio
    async def test_array_contains_single_value(self, db_session: AsyncSession):
        """filter_by on an ARRAY column with a scalar checks containment."""
        ArticleFacetCrud = CrudFactory(Article, facet_fields=[Article.labels])
        await ArticleCrud.create(
            db_session, ArticleCreate(title="Post 1", labels=["python", "fastapi"])
        )
        await ArticleCrud.create(
            db_session, ArticleCreate(title="Post 2", labels=["rust", "axum"])
        )
        await ArticleCrud.create(
            db_session, ArticleCreate(title="Post 3", labels=["python", "django"])
        )

        result = await ArticleFacetCrud.offset_paginate(
            db_session, filter_by={"labels": "python"}, schema=ArticleRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2
        titles = {a.title for a in result.data}
        assert titles == {"Post 1", "Post 3"}
        # facet returns individual unnested values, not whole arrays
        assert result.filter_attributes == {"labels": ["django", "fastapi", "python"]}

    @pytest.mark.anyio
    async def test_array_overlap_list_value(self, db_session: AsyncSession):
        """filter_by on an ARRAY column with a list checks overlap."""
        ArticleFacetCrud = CrudFactory(Article, facet_fields=[Article.labels])
        await ArticleCrud.create(
            db_session, ArticleCreate(title="Post 1", labels=["python", "fastapi"])
        )
        await ArticleCrud.create(
            db_session, ArticleCreate(title="Post 2", labels=["rust", "axum"])
        )
        await ArticleCrud.create(
            db_session, ArticleCreate(title="Post 3", labels=["python", "django"])
        )

        result = await ArticleFacetCrud.offset_paginate(
            db_session, filter_by={"labels": ["rust", "django"]}, schema=ArticleRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2
        titles = {a.title for a in result.data}
        assert titles == {"Post 2", "Post 3"}

    @pytest.mark.anyio
    async def test_unsupported_column_type_raises(self, db_session: AsyncSession):
        """filter_by on a JSON column raises UnsupportedFacetTypeError."""
        ArticleJsonCrud = CrudFactory(Article, facet_fields=[Article.metadata_])

        with pytest.raises(UnsupportedFacetTypeError) as exc_info:
            await ArticleJsonCrud.offset_paginate(
                db_session,
                filter_by={"metadata_": {"key": "value"}},
                schema=ArticleRead,
            )

        assert exc_info.value.key == "metadata_"
        assert "JSON" in exc_info.value.col_type


class TestFilterParamsViaConsolidated:
    """Tests for filter params via consolidated offset_paginate_params()."""

    def test_generates_fields_from_facet_fields(self):
        """Returned dependency has one keyword param per facet field."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username, User.email])
        dep = UserFacetCrud.offset_paginate_params(search=False, order=False)

        param_names = set(inspect.signature(dep).parameters)
        assert "username" in param_names
        assert "email" in param_names

    def test_relationship_facet_uses_full_chain_key(self):
        """Relationship tuple uses the full chain joined by __ as the key."""
        UserRoleCrud = CrudFactory(User, facet_fields=[(User.role, Role.name)])
        dep = UserRoleCrud.offset_paginate_params(search=False, order=False)

        param_names = set(inspect.signature(dep).parameters)
        assert "role__name" in param_names

    def test_filter_disabled_no_facet_params(self):
        """When filter=False, no facet params are generated."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username, User.email])
        dep = UserFacetCrud.offset_paginate_params(
            search=False, filter=False, order=False
        )

        param_names = set(inspect.signature(dep).parameters)
        assert param_names == {"page", "items_per_page"}

    def test_facet_fields_override(self):
        """facet_fields= parameter overrides the class-level default."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username, User.email])
        dep = UserFacetCrud.offset_paginate_params(
            search=False, order=False, facet_fields=[User.email]
        )

        param_names = set(inspect.signature(dep).parameters)
        assert "email" in param_names
        assert "username" not in param_names

    @pytest.mark.anyio
    async def test_awaiting_dep_returns_filter_by_with_values(self):
        """Awaiting the dependency returns filter_by dict with only supplied keys."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username, User.email])
        dep = UserFacetCrud.offset_paginate_params(search=False, order=False)

        result = await dep(page=1, items_per_page=20, username=["alice"], email=None)
        assert result["filter_by"] == {"username": ["alice"]}

    @pytest.mark.anyio
    async def test_multi_value_list_field(self):
        """Multiple values are accepted as a list."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        dep = UserFacetCrud.offset_paginate_params(search=False, order=False)

        result = await dep(page=1, items_per_page=20, username=["alice", "bob"])
        assert result["filter_by"] == {"username": ["alice", "bob"]}

    def test_disambiguates_duplicate_column_keys(self):
        """Two relationship tuples sharing a terminal column key get prefixed names."""
        from unittest.mock import MagicMock

        from fastapi_toolsets.crud.search import facet_keys

        col_a = MagicMock()
        col_a.key = "name"
        rel_a = MagicMock()
        rel_a.key = "project"

        col_b = MagicMock()
        col_b.key = "name"
        rel_b = MagicMock()
        rel_b.key = "os"

        keys = facet_keys([(rel_a, col_a), (rel_b, col_b)])
        assert keys == ["project__name", "os__name"]

    def test_deep_chain_joins_all_segments(self):
        """Three-element tuple produces all relation segments joined by __."""
        from unittest.mock import MagicMock

        from fastapi_toolsets.crud.search import facet_keys

        rel_a = MagicMock()
        rel_a.key = "role"
        rel_b = MagicMock()
        rel_b.key = "permission"
        col = MagicMock()
        col.key = "name"

        keys = facet_keys([(rel_a, rel_b, col)])
        assert keys == ["role__permission__name"]

    def test_unique_column_keys_kept_plain(self):
        """Fields with unique column keys are not prefixed."""
        from fastapi_toolsets.crud.search import facet_keys

        keys = facet_keys([User.username, User.email])
        assert keys == ["username", "email"]

    def test_dependency_name_includes_model_name(self):
        """Returned dependency is named {Model}OffsetPaginateParams."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        dep = UserFacetCrud.offset_paginate_params(search=False, order=False)

        assert dep.__name__ == "UserOffsetPaginateParams"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

    @pytest.mark.anyio
    async def test_integration_with_offset_paginate(self, db_session: AsyncSession):
        """Dependency result can be unpacked directly into offset_paginate."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        dep = UserFacetCrud.offset_paginate_params(search=False, order=False)
        params = await dep(page=1, items_per_page=20, username=["alice"])
        result = await UserFacetCrud.offset_paginate(
            db_session, **params, schema=UserRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1
        assert result.data[0].username == "alice"

    @pytest.mark.anyio
    async def test_dep_result_passed_to_cursor_paginate(self, db_session: AsyncSession):
        """Dependency result can be unpacked directly into cursor_paginate."""
        UserFacetCursorCrud = CrudFactory(
            User, cursor_column=User.id, facet_fields=[User.username]
        )
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        dep = UserFacetCursorCrud.cursor_paginate_params(search=False, order=False)
        params = await dep(cursor=None, items_per_page=20, username=["alice"])
        result = await UserFacetCursorCrud.cursor_paginate(
            db_session, **params, schema=UserRead
        )

        assert len(result.data) == 1
        assert result.data[0].username == "alice"

    @pytest.mark.anyio
    async def test_all_none_dep_result_passes_no_filter(self, db_session: AsyncSession):
        """All-None dependency result results in filter_by=None (returns all rows)."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        dep = UserFacetCrud.offset_paginate_params(search=False, order=False)
        params = await dep(page=1, items_per_page=20)  # all facet fields None
        assert params["filter_by"] is None
        result = await UserFacetCrud.offset_paginate(
            db_session, **params, schema=UserRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2

    def test_facet_key_collision_raises(self):
        """ValueError raised when a facet key clashes with a reserved param name."""
        # Create a mock facet field whose key would be "search"
        from unittest.mock import MagicMock

        mock_field = MagicMock()
        mock_field.key = "search"
        mock_field.property.columns = [MagicMock()]

        UserFacetCrud = CrudFactory(User, facet_fields=[mock_field])
        with pytest.raises(ValueError, match="conflicts with a reserved"):
            UserFacetCrud.offset_paginate_params(search=True, order=False)


class TestSearchParamsViaConsolidated:
    """Tests for search params via consolidated offset_paginate_params()."""

    def test_generates_search_and_search_column_params(self):
        """Returned dependency has search and search_column query params."""
        UserSearchCrud = CrudFactory(
            User, searchable_fields=[User.username, User.email]
        )
        dep = UserSearchCrud.offset_paginate_params(filter=False, order=False)

        param_names = set(inspect.signature(dep).parameters)
        assert "search" in param_names
        assert "search_column" in param_names

    def test_search_disabled_no_search_params(self):
        """When search=False, no search params are generated."""
        UserSearchCrud = CrudFactory(
            User, searchable_fields=[User.username, User.email]
        )
        dep = UserSearchCrud.offset_paginate_params(
            search=False, filter=False, order=False
        )

        param_names = set(inspect.signature(dep).parameters)
        assert "search" not in param_names
        assert "search_column" not in param_names

    @pytest.mark.anyio
    async def test_awaiting_dep_with_search_only(self):
        """Awaiting the dependency with only search returns search in dict."""
        UserSearchCrud = CrudFactory(
            User, searchable_fields=[User.username, User.email]
        )
        dep = UserSearchCrud.offset_paginate_params(filter=False, order=False)

        result = await dep(
            page=1, items_per_page=20, search="alice", search_column=None
        )
        assert result["search"] == "alice"
        assert "search_column" not in result

    @pytest.mark.anyio
    async def test_awaiting_dep_with_search_and_column(self):
        """Awaiting the dependency with both params returns both keys."""
        UserSearchCrud = CrudFactory(
            User, searchable_fields=[User.username, User.email]
        )
        dep = UserSearchCrud.offset_paginate_params(filter=False, order=False)

        result = await dep(
            page=1, items_per_page=20, search="alice", search_column="username"
        )
        assert result["search"] == "alice"
        assert result["search_column"] == "username"

    @pytest.mark.anyio
    async def test_awaiting_dep_with_no_search_values(self):
        """Awaiting the dependency with no search values omits search keys."""
        UserSearchCrud = CrudFactory(
            User, searchable_fields=[User.username, User.email]
        )
        dep = UserSearchCrud.offset_paginate_params(filter=False, order=False)

        result = await dep(page=1, items_per_page=20, search=None, search_column=None)
        assert "search" not in result
        assert "search_column" not in result

    def test_relationship_search_field_key(self):
        """Relationship tuple search fields use __ joined keys."""
        UserRelSearchCrud = CrudFactory(
            User, searchable_fields=[User.username, (User.role, Role.name)]
        )
        dep = UserRelSearchCrud.offset_paginate_params(filter=False, order=False)

        params = inspect.signature(dep).parameters
        search_column_param = params["search_column"]
        assert search_column_param.default.json_schema_extra.get("enum") == [
            "id",
            "username",
            "role__name",
        ]


class TestSearchColumns:
    """Tests for search_columns in paginated responses."""

    @pytest.mark.anyio
    async def test_search_columns_returned_in_offset_paginate(
        self, db_session: AsyncSession
    ):
        """offset_paginate response includes search_columns."""
        UserSearchCrud = CrudFactory(
            User, searchable_fields=[User.username, User.email]
        )
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )

        result = await UserSearchCrud.offset_paginate(db_session, schema=UserRead)

        assert result.search_columns is not None
        assert "username" in result.search_columns
        assert "email" in result.search_columns

    @pytest.mark.anyio
    async def test_search_columns_returned_in_cursor_paginate(
        self, db_session: AsyncSession
    ):
        """cursor_paginate response includes search_columns."""
        UserSearchCursorCrud = CrudFactory(
            User,
            cursor_column=User.id,
            searchable_fields=[User.username, User.email],
        )
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )

        result = await UserSearchCursorCrud.cursor_paginate(db_session, schema=UserRead)

        assert result.search_columns is not None
        assert "username" in result.search_columns
        assert "email" in result.search_columns

    @pytest.mark.anyio
    async def test_search_column_narrows_search(self, db_session: AsyncSession):
        """search_column restricts search to a single field."""
        UserSearchCrud = CrudFactory(
            User, searchable_fields=[User.username, User.email]
        )
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="bob@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="alice@test.com")
        )

        # Search "alice" in username only — should return only alice
        result = await UserSearchCrud.offset_paginate(
            db_session, search="alice", search_column="username", schema=UserRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1
        assert result.data[0].username == "alice"

    @pytest.mark.anyio
    async def test_search_column_invalid_raises(self, db_session: AsyncSession):
        """search_column with an invalid key raises InvalidSearchColumnError."""
        UserSearchCrud = CrudFactory(
            User, searchable_fields=[User.username, User.email]
        )

        with pytest.raises(InvalidSearchColumnError) as exc_info:
            await UserSearchCrud.offset_paginate(
                db_session,
                search="alice",
                search_column="nonexistent",
                schema=UserRead,
            )

        assert exc_info.value.column == "nonexistent"

    @pytest.mark.anyio
    async def test_search_without_search_column_searches_all(
        self, db_session: AsyncSession
    ):
        """search without search_column searches across all configured fields."""
        UserSearchCrud = CrudFactory(
            User, searchable_fields=[User.username, User.email]
        )
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="bob@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="alice@test.com")
        )

        # Search "alice" across all fields — should return both
        result = await UserSearchCrud.offset_paginate(
            db_session, search="alice", schema=UserRead
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2

    @pytest.mark.anyio
    async def test_search_column_with_cursor_paginate(self, db_session: AsyncSession):
        """search_column works with cursor_paginate."""
        UserSearchCursorCrud = CrudFactory(
            User,
            cursor_column=User.id,
            searchable_fields=[User.username, User.email],
        )
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="bob@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="alice@test.com")
        )

        result = await UserSearchCursorCrud.cursor_paginate(
            db_session, search="alice", search_column="email", schema=UserRead
        )

        assert len(result.data) == 1
        assert result.data[0].username == "bob"


class TestOrderParamsViaConsolidated:
    """Tests for order params via consolidated offset_paginate_params()."""

    def test_generates_order_by_and_order_params(self):
        """Returned dependency has order_by and order query params."""
        UserOrderCrud = CrudFactory(User, order_fields=[User.username, User.email])
        dep = UserOrderCrud.offset_paginate_params(search=False, filter=False)

        param_names = set(inspect.signature(dep).parameters)
        assert "order_by" in param_names
        assert "order" in param_names

    def test_order_disabled_no_order_params(self):
        """When order=False, no order params are generated."""
        UserOrderCrud = CrudFactory(User, order_fields=[User.username])
        dep = UserOrderCrud.offset_paginate_params(
            search=False, filter=False, order=False
        )

        param_names = set(inspect.signature(dep).parameters)
        assert "order_by" not in param_names
        assert "order" not in param_names

    def test_order_fields_override(self):
        """order_fields= parameter overrides the class-level default."""
        UserOrderCrud = CrudFactory(User, order_fields=[User.username, User.email])
        dep = UserOrderCrud.offset_paginate_params(
            search=False, filter=False, order_fields=[User.email]
        )

        sig = inspect.signature(dep)
        description = sig.parameters["order_by"].default.description
        assert "email" in description
        assert "username" not in description

    def test_order_by_description_lists_valid_fields(self):
        """order_by query param description mentions each allowed field."""
        UserOrderCrud = CrudFactory(User, order_fields=[User.username, User.email])
        dep = UserOrderCrud.offset_paginate_params(search=False, filter=False)

        sig = inspect.signature(dep)
        description = sig.parameters["order_by"].default.description
        assert "username" in description
        assert "email" in description

    def test_default_order_reflected_in_order_default(self):
        """default_order is used as the default value for order."""
        UserOrderCrud = CrudFactory(User, order_fields=[User.username])
        dep_asc = UserOrderCrud.offset_paginate_params(
            search=False, filter=False, default_order="asc"
        )
        dep_desc = UserOrderCrud.offset_paginate_params(
            search=False, filter=False, default_order="desc"
        )

        sig_asc = inspect.signature(dep_asc)
        sig_desc = inspect.signature(dep_desc)
        assert sig_asc.parameters["order"].default.default == "asc"
        assert sig_desc.parameters["order"].default.default == "desc"

    @pytest.mark.anyio
    async def test_no_order_by_no_default_returns_none(self):
        """Returns order_by=None when order_by is absent and no default_order_field is set."""
        UserOrderCrud = CrudFactory(User, order_fields=[User.username])
        dep = UserOrderCrud.offset_paginate_params(search=False, filter=False)
        result = await dep(page=1, items_per_page=20, order_by=None, order="asc")
        assert result["order_by"] is None

    @pytest.mark.anyio
    async def test_no_order_by_with_default_field_returns_asc_expression(self):
        """Returns default_order_field.asc() when order_by absent and order=asc."""
        UserOrderCrud = CrudFactory(User, order_fields=[User.username])
        dep = UserOrderCrud.offset_paginate_params(
            search=False, filter=False, default_order_field=User.username
        )
        result = await dep(page=1, items_per_page=20, order_by=None, order="asc")
        assert isinstance(result["order_by"], UnaryExpression)
        assert "ASC" in str(result["order_by"])

    @pytest.mark.anyio
    async def test_no_order_by_with_default_field_returns_desc_expression(self):
        """Returns default_order_field.desc() when order_by absent and order=desc."""
        UserOrderCrud = CrudFactory(User, order_fields=[User.username])
        dep = UserOrderCrud.offset_paginate_params(
            search=False, filter=False, default_order_field=User.username
        )
        result = await dep(page=1, items_per_page=20, order_by=None, order="desc")
        assert isinstance(result["order_by"], UnaryExpression)
        assert "DESC" in str(result["order_by"])

    @pytest.mark.anyio
    async def test_valid_order_by_asc(self):
        """Returns field.asc() for a valid order_by with order=asc."""
        UserOrderCrud = CrudFactory(User, order_fields=[User.username])
        dep = UserOrderCrud.offset_paginate_params(search=False, filter=False)
        result = await dep(page=1, items_per_page=20, order_by="username", order="asc")
        assert isinstance(result["order_by"], UnaryExpression)
        assert "ASC" in str(result["order_by"])

    @pytest.mark.anyio
    async def test_valid_order_by_desc(self):
        """Returns field.desc() for a valid order_by with order=desc."""
        UserOrderCrud = CrudFactory(User, order_fields=[User.username])
        dep = UserOrderCrud.offset_paginate_params(search=False, filter=False)
        result = await dep(page=1, items_per_page=20, order_by="username", order="desc")
        assert isinstance(result["order_by"], UnaryExpression)
        assert "DESC" in str(result["order_by"])

    @pytest.mark.anyio
    async def test_invalid_order_by_raises_invalid_order_field_error(self):
        """Raises InvalidOrderFieldError for an unknown order_by value."""
        UserOrderCrud = CrudFactory(User, order_fields=[User.username])
        dep = UserOrderCrud.offset_paginate_params(search=False, filter=False)
        with pytest.raises(InvalidOrderFieldError) as exc_info:
            await dep(page=1, items_per_page=20, order_by="nonexistent", order="asc")
        assert exc_info.value.field == "nonexistent"
        assert "username" in exc_info.value.valid_fields

    @pytest.mark.anyio
    async def test_multiple_fields_all_resolve(self):
        """All configured fields resolve correctly via order_by."""
        UserOrderCrud = CrudFactory(User, order_fields=[User.username, User.email])
        dep = UserOrderCrud.offset_paginate_params(search=False, filter=False)
        result_username = await dep(
            page=1, items_per_page=20, order_by="username", order="asc"
        )
        result_email = await dep(
            page=1, items_per_page=20, order_by="email", order="desc"
        )
        assert isinstance(result_username["order_by"], ColumnElement)
        assert isinstance(result_email["order_by"], ColumnElement)

    @pytest.mark.anyio
    async def test_order_integrates_with_offset_paginate(
        self, db_session: AsyncSession
    ):
        """order in consolidated params is accepted by offset_paginate(order_by=...)."""
        UserOrderCrud = CrudFactory(User, order_fields=[User.username])
        await UserCrud.create(
            db_session, UserCreate(username="charlie", email="c@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )

        dep = UserOrderCrud.offset_paginate_params(search=False, filter=False)
        params = await dep(page=1, items_per_page=20, order_by="username", order="asc")
        result = await UserOrderCrud.offset_paginate(
            db_session, **params, schema=UserRead
        )

        assert result.data[0].username == "alice"
        assert result.data[1].username == "charlie"


class TestOffsetPaginateParamsSchema:
    """Tests for AsyncCrud.offset_paginate_params()."""

    def test_returns_page_and_items_per_page_params(self):
        """Returned dependency has page and items_per_page params."""
        dep = RoleCrud.offset_paginate_params(search=False, filter=False, order=False)
        param_names = set(inspect.signature(dep).parameters)
        assert param_names == {"page", "items_per_page"}

    def test_dependency_name_includes_model_name(self):
        """Dependency function is named after the model."""
        dep = RoleCrud.offset_paginate_params(search=False, filter=False, order=False)
        assert getattr(dep, "__name__") == "RoleOffsetPaginateParams"

    def test_default_page_size_reflected_in_items_per_page_default(self):
        """default_page_size is used as the default for items_per_page."""
        dep = RoleCrud.offset_paginate_params(
            default_page_size=42, search=False, filter=False, order=False
        )
        sig = inspect.signature(dep)
        assert sig.parameters["items_per_page"].default.default == 42

    def test_max_page_size_reflected_in_items_per_page_le(self):
        """max_page_size is used as le constraint on items_per_page."""
        dep = RoleCrud.offset_paginate_params(
            max_page_size=50, search=False, filter=False, order=False
        )
        sig = inspect.signature(dep)
        le = next(
            m.le
            for m in sig.parameters["items_per_page"].default.metadata
            if hasattr(m, "le")
        )
        assert le == 50

    def test_include_total_not_a_query_param(self):
        """include_total is not exposed as a query parameter."""
        dep = RoleCrud.offset_paginate_params(search=False, filter=False, order=False)
        param_names = set(inspect.signature(dep).parameters)
        assert "include_total" not in param_names

    @pytest.mark.anyio
    async def test_include_total_true_forwarded_in_result(self):
        """include_total=True factory arg appears in the resolved dict."""
        result = await RoleCrud.offset_paginate_params(
            include_total=True, search=False, filter=False, order=False
        )(page=1, items_per_page=10)
        assert result["include_total"] is True

    @pytest.mark.anyio
    async def test_include_total_false_forwarded_in_result(self):
        """include_total=False factory arg appears in the resolved dict."""
        result = await RoleCrud.offset_paginate_params(
            include_total=False, search=False, filter=False, order=False
        )(page=1, items_per_page=10)
        assert result["include_total"] is False

    @pytest.mark.anyio
    async def test_awaiting_dep_returns_dict(self):
        """Awaiting the dependency returns a dict with page, items_per_page, include_total."""
        dep = RoleCrud.offset_paginate_params(
            include_total=False, search=False, filter=False, order=False
        )
        result = await dep(page=2, items_per_page=10)
        assert result == {"page": 2, "items_per_page": 10, "include_total": False}

    @pytest.mark.anyio
    async def test_integrates_with_offset_paginate(self, db_session: AsyncSession):
        """offset_paginate_params output can be unpacked directly into offset_paginate."""
        await RoleCrud.create(db_session, RoleCreate(name="admin"))
        dep = RoleCrud.offset_paginate_params(search=False, filter=False, order=False)
        params = await dep(page=1, items_per_page=10)
        result = await RoleCrud.offset_paginate(db_session, **params, schema=RoleRead)
        assert result.pagination.page == 1
        assert result.pagination.items_per_page == 10

    def test_all_features_enabled(self):
        """With all features enabled, params include search, filter, and order."""
        FullCrud = CrudFactory(
            User,
            searchable_fields=[User.username],
            facet_fields=[User.email],
            order_fields=[User.username],
        )
        dep = FullCrud.offset_paginate_params()
        param_names = set(inspect.signature(dep).parameters)
        assert param_names == {
            "page",
            "items_per_page",
            "search",
            "search_column",
            "email",
            "order_by",
            "order",
        }

    def test_search_enabled_but_no_searchable_fields(self):
        """search=True with no searchable_fields silently skips search params."""
        NoCrud = CrudFactory(Role)
        NoCrud.searchable_fields = None
        dep = NoCrud.offset_paginate_params(
            search=True, filter=False, order=False, search_fields=None
        )
        param_names = set(inspect.signature(dep).parameters)
        assert "search" not in param_names
        assert "search_column" not in param_names

    def test_filter_enabled_but_no_facet_fields(self):
        """filter=True with no facet_fields silently skips filter params."""
        dep = RoleCrud.offset_paginate_params(search=False, filter=True, order=False)
        param_names = set(inspect.signature(dep).parameters)
        assert param_names == {"page", "items_per_page"}

    def test_order_enabled_but_no_order_fields(self):
        """order=True with no order_fields silently skips order params."""
        dep = RoleCrud.offset_paginate_params(search=False, filter=False, order=True)
        param_names = set(inspect.signature(dep).parameters)
        assert "order_by" not in param_names
        assert "order" not in param_names


class TestCursorPaginateParamsSchema:
    """Tests for AsyncCrud.cursor_paginate_params()."""

    def test_returns_cursor_and_items_per_page_params(self):
        """Returned dependency has cursor and items_per_page params."""
        dep = RoleCursorCrud.cursor_paginate_params(
            search=False, filter=False, order=False
        )
        param_names = set(inspect.signature(dep).parameters)
        assert param_names == {"cursor", "items_per_page"}

    def test_dependency_name_includes_model_name(self):
        """Dependency function is named after the model."""
        dep = RoleCursorCrud.cursor_paginate_params(
            search=False, filter=False, order=False
        )
        assert getattr(dep, "__name__") == "RoleCursorPaginateParams"

    def test_default_page_size_reflected_in_items_per_page_default(self):
        """default_page_size is used as the default for items_per_page."""
        dep = RoleCursorCrud.cursor_paginate_params(
            default_page_size=15, search=False, filter=False, order=False
        )
        sig = inspect.signature(dep)
        assert sig.parameters["items_per_page"].default.default == 15

    def test_max_page_size_reflected_in_items_per_page_le(self):
        """max_page_size is used as le constraint on items_per_page."""
        dep = RoleCursorCrud.cursor_paginate_params(
            max_page_size=75, search=False, filter=False, order=False
        )
        sig = inspect.signature(dep)
        le = next(
            m.le
            for m in sig.parameters["items_per_page"].default.metadata
            if hasattr(m, "le")
        )
        assert le == 75

    def test_cursor_defaults_to_none(self):
        """cursor defaults to None."""
        dep = RoleCursorCrud.cursor_paginate_params(
            search=False, filter=False, order=False
        )
        sig = inspect.signature(dep)
        assert sig.parameters["cursor"].default.default is None

    @pytest.mark.anyio
    async def test_awaiting_dep_returns_dict(self):
        """Awaiting the dependency returns a dict with cursor and items_per_page."""
        dep = RoleCursorCrud.cursor_paginate_params(
            search=False, filter=False, order=False
        )
        result = await dep(cursor=None, items_per_page=5)
        assert result == {"cursor": None, "items_per_page": 5}

    @pytest.mark.anyio
    async def test_integrates_with_cursor_paginate(self, db_session: AsyncSession):
        """cursor_paginate_params output can be unpacked directly into cursor_paginate."""
        await RoleCrud.create(db_session, RoleCreate(name="admin"))
        dep = RoleCursorCrud.cursor_paginate_params(
            search=False, filter=False, order=False
        )
        params = await dep(cursor=None, items_per_page=10)
        result = await RoleCursorCrud.cursor_paginate(
            db_session, **params, schema=RoleRead
        )
        assert result.pagination.items_per_page == 10


class TestPaginateParamsSchema:
    """Tests for AsyncCrud.paginate_params()."""

    def test_returns_all_params(self):
        """Returned dependency has pagination_type, page, cursor, items_per_page (no include_total)."""
        dep = RoleCursorCrud.paginate_params(search=False, filter=False, order=False)
        param_names = set(inspect.signature(dep).parameters)
        assert param_names == {"pagination_type", "page", "cursor", "items_per_page"}

    def test_dependency_name_includes_model_name(self):
        """Dependency function is named after the model."""
        dep = RoleCursorCrud.paginate_params(search=False, filter=False, order=False)
        assert getattr(dep, "__name__") == "RolePaginateParams"

    def test_default_pagination_type(self):
        """default_pagination_type is reflected in pagination_type default."""
        from fastapi_toolsets.schemas import PaginationType

        dep = RoleCursorCrud.paginate_params(
            default_pagination_type=PaginationType.CURSOR,
            search=False,
            filter=False,
            order=False,
        )
        sig = inspect.signature(dep)
        assert (
            sig.parameters["pagination_type"].default.default == PaginationType.CURSOR
        )

    def test_default_page_size(self):
        """default_page_size is reflected in items_per_page default."""
        dep = RoleCursorCrud.paginate_params(
            default_page_size=15, search=False, filter=False, order=False
        )
        sig = inspect.signature(dep)
        assert sig.parameters["items_per_page"].default.default == 15

    def test_max_page_size_le_constraint(self):
        """max_page_size is used as le constraint on items_per_page."""
        dep = RoleCursorCrud.paginate_params(
            max_page_size=60, search=False, filter=False, order=False
        )
        sig = inspect.signature(dep)
        le = next(
            m.le
            for m in sig.parameters["items_per_page"].default.metadata
            if hasattr(m, "le")
        )
        assert le == 60

    def test_include_total_not_a_query_param(self):
        """include_total is not exposed as a query parameter."""
        dep = RoleCursorCrud.paginate_params(search=False, filter=False, order=False)
        assert "include_total" not in set(inspect.signature(dep).parameters)

    @pytest.mark.anyio
    async def test_include_total_forwarded_in_result(self):
        """include_total factory arg appears in the resolved dict."""
        result_true = await RoleCursorCrud.paginate_params(
            include_total=True, search=False, filter=False, order=False
        )(
            pagination_type=PaginationType.OFFSET,
            page=1,
            cursor=None,
            items_per_page=10,
        )
        result_false = await RoleCursorCrud.paginate_params(
            include_total=False, search=False, filter=False, order=False
        )(
            pagination_type=PaginationType.OFFSET,
            page=1,
            cursor=None,
            items_per_page=10,
        )
        assert result_true["include_total"] is True
        assert result_false["include_total"] is False

    @pytest.mark.anyio
    async def test_awaiting_dep_returns_dict(self):
        """Awaiting the dependency returns a dict with all pagination keys."""
        dep = RoleCursorCrud.paginate_params(search=False, filter=False, order=False)
        result = await dep(
            pagination_type=PaginationType.OFFSET,
            page=2,
            cursor=None,
            items_per_page=10,
        )
        assert result == {
            "pagination_type": PaginationType.OFFSET,
            "page": 2,
            "cursor": None,
            "items_per_page": 10,
            "include_total": True,
        }

    @pytest.mark.anyio
    async def test_integrates_with_paginate_offset(self, db_session: AsyncSession):
        """paginate_params output unpacks into paginate() for offset strategy."""
        from fastapi_toolsets.schemas import OffsetPagination

        await RoleCrud.create(db_session, RoleCreate(name="admin"))
        params = await RoleCursorCrud.paginate_params(
            search=False, filter=False, order=False
        )(
            pagination_type=PaginationType.OFFSET,
            page=1,
            cursor=None,
            items_per_page=10,
        )
        result = await RoleCursorCrud.paginate(db_session, **params, schema=RoleRead)
        assert isinstance(result.pagination, OffsetPagination)

    @pytest.mark.anyio
    async def test_integrates_with_paginate_cursor(self, db_session: AsyncSession):
        """paginate_params output unpacks into paginate() for cursor strategy."""
        from fastapi_toolsets.schemas import CursorPagination

        await RoleCrud.create(db_session, RoleCreate(name="admin"))
        params = await RoleCursorCrud.paginate_params(
            search=False, filter=False, order=False
        )(
            pagination_type=PaginationType.CURSOR,
            page=1,
            cursor=None,
            items_per_page=10,
        )
        result = await RoleCursorCrud.paginate(db_session, **params, schema=RoleRead)
        assert isinstance(result.pagination, CursorPagination)
