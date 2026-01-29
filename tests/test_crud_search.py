"""Tests for CRUD search functionality."""

import uuid

import pytest
from pydantic import BaseModel
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_toolsets.crud import CrudFactory, SearchConfig, get_searchable_fields

from .conftest import (
    Base,
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

        assert result["pagination"]["total_count"] == 2

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

        assert result["pagination"]["total_count"] == 2

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

        assert result["pagination"]["total_count"] == 2

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

        assert result["pagination"]["total_count"] == 1

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

        assert result["pagination"]["total_count"] == 1

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
        assert result["pagination"]["total_count"] == 0

        # Should find (case match)
        result = await UserCrud.paginate(
            db_session,
            search=SearchConfig(query="JohnDoe", case_sensitive=True),
            search_fields=[User.username],
        )
        assert result["pagination"]["total_count"] == 1

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
        assert result["pagination"]["total_count"] == 2

        result = await UserCrud.paginate(db_session, search=None)
        assert result["pagination"]["total_count"] == 2

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

        assert result["pagination"]["total_count"] == 1
        assert result["data"][0].username == "active_john"

    @pytest.mark.anyio
    async def test_search_auto_detect_fields(self, db_session: AsyncSession):
        """Auto-detect searchable fields when not specified."""
        await UserCrud.create(
            db_session, UserCreate(username="findme", email="other@test.com")
        )

        result = await UserCrud.paginate(db_session, search="findme")

        assert result["pagination"]["total_count"] == 1

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

        assert result["pagination"]["total_count"] == 0
        assert result["data"] == []

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

        assert result["pagination"]["total_count"] == 15
        assert len(result["data"]) == 5
        assert result["pagination"]["has_more"] is True

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

        assert result["pagination"]["total_count"] == 2

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

        assert result["pagination"]["total_count"] == 3
        usernames = [u.username for u in result["data"]]
        assert usernames == ["alice", "bob", "charlie"]

    @pytest.mark.anyio
    async def test_search_non_string_column(self, db_session: AsyncSession):
        """Search on non-string columns (e.g., UUID) works via cast."""

        class Account(Base):
            __tablename__ = "accounts"
            id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
            name: Mapped[str] = mapped_column(String(100))

        class AccountCreate(BaseModel):
            id: uuid.UUID | None = None
            name: str

        AccountCrud = CrudFactory(Account)

        # Create table for this test
        async with db_session.get_bind().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        account_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        await AccountCrud.create(
            db_session, AccountCreate(id=account_id, name="Test Account")
        )
        await AccountCrud.create(db_session, AccountCreate(name="Other Account"))

        # Search by UUID (partial match)
        result = await AccountCrud.paginate(
            db_session,
            search="12345678",
            search_fields=[Account.id, Account.name],
        )

        assert result["pagination"]["total_count"] == 1
        assert result["data"][0].id == account_id


class TestSearchConfig:
    """Tests for SearchConfig options."""

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

        assert result["pagination"]["total_count"] == 1
        assert result["data"][0].username == "john_test"

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

        assert result["pagination"]["total_count"] == 1


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
