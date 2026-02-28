"""Tests for CRUD search functionality."""

import inspect
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement, UnaryExpression

from fastapi_toolsets.crud import (
    CrudFactory,
    InvalidFacetFilterError,
    SearchConfig,
    get_searchable_fields,
)
from fastapi_toolsets.exceptions import InvalidSortFieldError
from fastapi_toolsets.schemas import OffsetPagination

from .conftest import (
    Role,
    RoleCreate,
    RoleCrud,
    User,
    UserCreate,
    UserCrud,
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

        result = await UserCrud.paginate(
            db_session,
            search="doe",
            search_fields=[User.username],
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

        result = await UserCrud.paginate(
            db_session,
            search="company",
            search_fields=[User.username, User.email],
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

        result = await UserCrud.paginate(
            db_session,
            search="admin",
            search_fields=[(User.role, Role.name)],
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
        result = await UserCrud.paginate(
            db_session,
            search="admin",
            search_fields=[User.username, (User.role, Role.name)],
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1

    @pytest.mark.anyio
    async def test_search_case_insensitive(self, db_session: AsyncSession):
        """Search is case-insensitive by default."""
        await UserCrud.create(
            db_session, UserCreate(username="JohnDoe", email="j@test.com")
        )

        result = await UserCrud.paginate(
            db_session,
            search="johndoe",
            search_fields=[User.username],
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
        result = await UserCrud.paginate(
            db_session,
            search=SearchConfig(query="johndoe", case_sensitive=True),
            search_fields=[User.username],
        )
        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 0

        # Should find (case match)
        result = await UserCrud.paginate(
            db_session,
            search=SearchConfig(query="JohnDoe", case_sensitive=True),
            search_fields=[User.username],
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

        result = await UserCrud.paginate(db_session, search="")
        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2

        result = await UserCrud.paginate(db_session, search=None)
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

        result = await UserCrud.paginate(
            db_session,
            filters=[User.is_active == True],  # noqa: E712
            search="john",
            search_fields=[User.username],
        )

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1
        assert result.data[0].username == "active_john"

    @pytest.mark.anyio
    async def test_search_auto_detect_fields(self, db_session: AsyncSession):
        """Auto-detect searchable fields when not specified."""
        await UserCrud.create(
            db_session, UserCreate(username="findme", email="other@test.com")
        )

        result = await UserCrud.paginate(db_session, search="findme")

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1

    @pytest.mark.anyio
    async def test_search_no_results(self, db_session: AsyncSession):
        """Search with no matching results."""
        await UserCrud.create(
            db_session, UserCreate(username="john", email="j@test.com")
        )

        result = await UserCrud.paginate(
            db_session,
            search="nonexistent",
            search_fields=[User.username],
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

        result = await UserCrud.paginate(
            db_session,
            search="user_",
            search_fields=[User.username],
            page=1,
            items_per_page=5,
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
        result = await UserCrud.paginate(
            db_session,
            search="role",
            search_fields=[User.username],
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

        result = await UserCrud.paginate(
            db_session,
            search="@test.com",
            search_fields=[User.email],
            order_by=User.username,
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
        result = await UserCrud.paginate(
            db_session,
            search="12345678",
            search_fields=[User.id, User.username],
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
        result = await UserCrud.paginate(
            db_session,
            search=SearchConfig(query="john", match_mode="all"),
            search_fields=[User.username, User.email],
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

        result = await UserCrud.paginate(
            db_session,
            search=SearchConfig(query="findme", fields=[User.email]),
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
        assert "User" in str(error)
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
        assert "NoStringModel" in str(exc_info.value)


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

        result = await UserCrud.offset_paginate(db_session)

        assert result.filter_attributes is None

    @pytest.mark.anyio
    async def test_cursor_paginate_no_facets(self, db_session: AsyncSession):
        """filter_attributes is None for cursor_paginate when facet_fields not set."""
        UserCursorCrud = CrudFactory(User, cursor_column=User.id)
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )

        result = await UserCursorCrud.cursor_paginate(db_session)

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

        result = await UserFacetCrud.offset_paginate(db_session)

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

        result = await UserFacetCursorCrud.cursor_paginate(db_session)

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

        result = await UserFacetCrud.offset_paginate(db_session)

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
            db_session, facet_fields=[User.email]
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

        result = await UserRelFacetCrud.offset_paginate(db_session)

        assert result.filter_attributes is not None
        assert set(result.filter_attributes["name"]) == {"admin", "editor"}

    @pytest.mark.anyio
    async def test_relationship_facet_none_excluded(self, db_session: AsyncSession):
        """None values (e.g. NULL role) are excluded from facet results."""
        UserRelFacetCrud = CrudFactory(User, facet_fields=[(User.role, Role.name)])

        # Only user with no role
        await UserCrud.create(
            db_session, UserCreate(username="norole", email="n@test.com")
        )

        result = await UserRelFacetCrud.offset_paginate(db_session)

        assert result.filter_attributes is not None
        assert result.filter_attributes["name"] == []

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
            db_session, search="admin", search_fields=[(User.role, Role.name)]
        )

        assert result.filter_attributes is not None
        assert result.filter_attributes["name"] == ["admin"]


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
            db_session, filter_by={"username": "alice"}
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
            db_session, filter_by={"username": ["alice", "bob"]}
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
            db_session, filter_by={"name": "admin"}
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
                db_session, filter_by={"nonexistent": "value"}
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
            filter_by={"name": "admin", "id": str(admin.id)},
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
            db_session, filter_by={"username": "alice"}
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
            db_session, filter_by=UserFilter(username="alice")
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
            db_session, filter_by=UserFilter(username="alice")
        )

        assert len(result.data) == 1
        assert result.data[0].username == "alice"


class TestFilterParamsSchema:
    """Tests for AsyncCrud.filter_params()."""

    def test_generates_fields_from_facet_fields(self):
        """Returned dependency has one keyword param per facet field."""
        import inspect

        UserFacetCrud = CrudFactory(User, facet_fields=[User.username, User.email])
        dep = UserFacetCrud.filter_params()

        param_names = set(inspect.signature(dep).parameters)
        assert param_names == {"username", "email"}

    def test_relationship_facet_uses_column_key(self):
        """Relationship tuple uses the terminal column's key."""
        import inspect

        UserRoleCrud = CrudFactory(User, facet_fields=[(User.role, Role.name)])
        dep = UserRoleCrud.filter_params()

        param_names = set(inspect.signature(dep).parameters)
        assert param_names == {"name"}

    def test_raises_when_no_facet_fields(self):
        """ValueError raised when no facet_fields are configured or provided."""
        with pytest.raises(ValueError, match="no facet_fields"):
            UserCrud.filter_params()

    def test_facet_fields_override(self):
        """facet_fields= parameter overrides the class-level default."""
        import inspect

        UserFacetCrud = CrudFactory(User, facet_fields=[User.username, User.email])
        dep = UserFacetCrud.filter_params(facet_fields=[User.email])

        param_names = set(inspect.signature(dep).parameters)
        assert param_names == {"email"}

    @pytest.mark.anyio
    async def test_awaiting_dep_returns_dict_with_values(self):
        """Awaiting the dependency returns a dict with only the supplied keys."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username, User.email])
        dep = UserFacetCrud.filter_params()

        result = await dep(username=["alice"])
        assert result == {"username": ["alice"]}

    @pytest.mark.anyio
    async def test_multi_value_list_field(self):
        """Multiple values are accepted as a list."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        dep = UserFacetCrud.filter_params()

        result = await dep(username=["alice", "bob"])
        assert result == {"username": ["alice", "bob"]}

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

    def test_unique_column_keys_kept_plain(self):
        """Fields with unique column keys are not prefixed."""
        from fastapi_toolsets.crud.search import facet_keys

        keys = facet_keys([User.username, User.email])
        assert keys == ["username", "email"]

    def test_dependency_name_includes_model_name(self):
        """Returned dependency is named {Model}FilterParams."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        dep = UserFacetCrud.filter_params()

        assert dep.__name__ == "UserFilterParams"  # type: ignore[union-attr]

    @pytest.mark.anyio
    async def test_integration_with_offset_paginate(self, db_session: AsyncSession):
        """Dependency result can be passed directly to offset_paginate via filter_by."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        dep = UserFacetCrud.filter_params()
        f = await dep(username=["alice"])
        result = await UserFacetCrud.offset_paginate(db_session, filter_by=f)

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 1
        assert result.data[0].username == "alice"

    @pytest.mark.anyio
    async def test_dep_result_passed_to_cursor_paginate(self, db_session: AsyncSession):
        """Dependency result can be passed directly to cursor_paginate via filter_by."""
        UserFacetCursorCrud = CrudFactory(
            User, cursor_column=User.id, facet_fields=[User.username]
        )
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        dep = UserFacetCursorCrud.filter_params()
        f = await dep(username=["alice"])
        result = await UserFacetCursorCrud.cursor_paginate(db_session, filter_by=f)

        assert len(result.data) == 1
        assert result.data[0].username == "alice"

    @pytest.mark.anyio
    async def test_all_none_dep_result_passes_no_filter(self, db_session: AsyncSession):
        """All-None dependency result results in no filter (returns all rows)."""
        UserFacetCrud = CrudFactory(User, facet_fields=[User.username])
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com")
        )

        dep = UserFacetCrud.filter_params()
        f = await dep()  # all fields None
        result = await UserFacetCrud.offset_paginate(db_session, filter_by=f)

        assert isinstance(result.pagination, OffsetPagination)
        assert result.pagination.total_count == 2


class TestSortParamsSchema:
    """Tests for AsyncCrud.sort_params()."""

    def test_generates_sort_by_and_sort_order_params(self):
        """Returned dependency has sort_by and sort_order query params."""
        UserSortCrud = CrudFactory(User, sort_fields=[User.username, User.email])
        dep = UserSortCrud.sort_params()

        param_names = set(inspect.signature(dep).parameters)
        assert param_names == {"sort_by", "sort_order"}

    def test_dependency_name_includes_model_name(self):
        """Dependency function is named after the model."""
        UserSortCrud = CrudFactory(User, sort_fields=[User.username])
        dep = UserSortCrud.sort_params()
        assert getattr(dep, "__name__") == "UserSortParams"

    def test_raises_when_no_sort_fields(self):
        """ValueError raised when no sort_fields are configured or provided."""
        with pytest.raises(ValueError, match="no sort_fields"):
            UserCrud.sort_params()

    def test_sort_fields_override(self):
        """sort_fields= parameter overrides the class-level default."""
        UserSortCrud = CrudFactory(User, sort_fields=[User.username, User.email])
        dep = UserSortCrud.sort_params(sort_fields=[User.email])

        param_names = set(inspect.signature(dep).parameters)
        assert "sort_by" in param_names
        # description should only mention email, not username
        sig = inspect.signature(dep)
        description = sig.parameters["sort_by"].default.description
        assert "email" in description
        assert "username" not in description

    def test_sort_by_description_lists_valid_fields(self):
        """sort_by query param description mentions each allowed field."""
        UserSortCrud = CrudFactory(User, sort_fields=[User.username, User.email])
        dep = UserSortCrud.sort_params()

        sig = inspect.signature(dep)
        description = sig.parameters["sort_by"].default.description
        assert "username" in description
        assert "email" in description

    def test_default_order_reflected_in_sort_order_default(self):
        """default_order is used as the default value for sort_order."""
        UserSortCrud = CrudFactory(User, sort_fields=[User.username])
        dep_asc = UserSortCrud.sort_params(default_order="asc")
        dep_desc = UserSortCrud.sort_params(default_order="desc")

        sig_asc = inspect.signature(dep_asc)
        sig_desc = inspect.signature(dep_desc)
        assert sig_asc.parameters["sort_order"].default.default == "asc"
        assert sig_desc.parameters["sort_order"].default.default == "desc"

    @pytest.mark.anyio
    async def test_no_sort_by_no_default_returns_none(self):
        """Returns None when sort_by is absent and no default_field is set."""
        UserSortCrud = CrudFactory(User, sort_fields=[User.username])
        dep = UserSortCrud.sort_params()
        result = await dep(sort_by=None, sort_order="asc")
        assert result is None

    @pytest.mark.anyio
    async def test_no_sort_by_with_default_field_returns_asc_expression(self):
        """Returns default_field.asc() when sort_by absent and sort_order=asc."""
        UserSortCrud = CrudFactory(User, sort_fields=[User.username])
        dep = UserSortCrud.sort_params(default_field=User.username)
        result = await dep(sort_by=None, sort_order="asc")
        assert isinstance(result, UnaryExpression)
        assert "ASC" in str(result)

    @pytest.mark.anyio
    async def test_no_sort_by_with_default_field_returns_desc_expression(self):
        """Returns default_field.desc() when sort_by absent and sort_order=desc."""
        UserSortCrud = CrudFactory(User, sort_fields=[User.username])
        dep = UserSortCrud.sort_params(default_field=User.username)
        result = await dep(sort_by=None, sort_order="desc")
        assert isinstance(result, UnaryExpression)
        assert "DESC" in str(result)

    @pytest.mark.anyio
    async def test_valid_sort_by_asc(self):
        """Returns field.asc() for a valid sort_by with sort_order=asc."""
        UserSortCrud = CrudFactory(User, sort_fields=[User.username])
        dep = UserSortCrud.sort_params()
        result = await dep(sort_by="username", sort_order="asc")
        assert isinstance(result, UnaryExpression)
        assert "ASC" in str(result)

    @pytest.mark.anyio
    async def test_valid_sort_by_desc(self):
        """Returns field.desc() for a valid sort_by with sort_order=desc."""
        UserSortCrud = CrudFactory(User, sort_fields=[User.username])
        dep = UserSortCrud.sort_params()
        result = await dep(sort_by="username", sort_order="desc")
        assert isinstance(result, UnaryExpression)
        assert "DESC" in str(result)

    @pytest.mark.anyio
    async def test_invalid_sort_by_raises_invalid_sort_field_error(self):
        """Raises InvalidSortFieldError for an unknown sort_by value."""
        UserSortCrud = CrudFactory(User, sort_fields=[User.username])
        dep = UserSortCrud.sort_params()
        with pytest.raises(InvalidSortFieldError) as exc_info:
            await dep(sort_by="nonexistent", sort_order="asc")
        assert exc_info.value.field == "nonexistent"
        assert "username" in exc_info.value.valid_fields

    @pytest.mark.anyio
    async def test_multiple_fields_all_resolve(self):
        """All configured fields resolve correctly via sort_by."""
        UserSortCrud = CrudFactory(User, sort_fields=[User.username, User.email])
        dep = UserSortCrud.sort_params()
        result_username = await dep(sort_by="username", sort_order="asc")
        result_email = await dep(sort_by="email", sort_order="desc")
        assert isinstance(result_username, ColumnElement)
        assert isinstance(result_email, ColumnElement)

    @pytest.mark.anyio
    async def test_sort_params_integrates_with_get_multi(
        self, db_session: AsyncSession
    ):
        """sort_params output is accepted by get_multi(order_by=...)."""
        UserSortCrud = CrudFactory(User, sort_fields=[User.username])
        await UserCrud.create(
            db_session, UserCreate(username="charlie", email="c@test.com")
        )
        await UserCrud.create(
            db_session, UserCreate(username="alice", email="a@test.com")
        )

        dep = UserSortCrud.sort_params()
        order_by = await dep(sort_by="username", sort_order="asc")
        results = await UserSortCrud.get_multi(db_session, order_by=order_by)

        assert results[0].username == "alice"
        assert results[1].username == "charlie"
