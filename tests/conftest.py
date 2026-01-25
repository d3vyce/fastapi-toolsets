"""Shared pytest fixtures for fastapi-utils tests."""

import os

import pytest
from pydantic import BaseModel
from sqlalchemy import ForeignKey, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from fastapi_toolsets.crud import CrudFactory

# PostgreSQL connection URL from environment or default for local development
DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/fastapi_toolsets_test",
)


# =============================================================================
# Test Models
# =============================================================================


class Base(DeclarativeBase):
    """Base class for test models."""

    pass


class Role(Base):
    """Test role model."""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    """Test user model."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    email: Mapped[str] = mapped_column(String(100), unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id"), nullable=True)

    role: Mapped[Role | None] = relationship(back_populates="users")


class Post(Base):
    """Test post model."""

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(String(1000), default="")
    is_published: Mapped[bool] = mapped_column(default=False)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))


# =============================================================================
# Test Schemas
# =============================================================================


class RoleCreate(BaseModel):
    """Schema for creating a role."""

    id: int | None = None
    name: str


class RoleUpdate(BaseModel):
    """Schema for updating a role."""

    name: str | None = None


class UserCreate(BaseModel):
    """Schema for creating a user."""

    id: int | None = None
    username: str
    email: str
    is_active: bool = True
    role_id: int | None = None


class UserUpdate(BaseModel):
    """Schema for updating a user."""

    username: str | None = None
    email: str | None = None
    is_active: bool | None = None
    role_id: int | None = None


class PostCreate(BaseModel):
    """Schema for creating a post."""

    id: int | None = None
    title: str
    content: str = ""
    is_published: bool = False
    author_id: int


class PostUpdate(BaseModel):
    """Schema for updating a post."""

    title: str | None = None
    content: str | None = None
    is_published: bool | None = None


# =============================================================================
# CRUD Classes
# =============================================================================

RoleCrud = CrudFactory(Role)
UserCrud = CrudFactory(User)
PostCrud = CrudFactory(Post)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def anyio_backend():
    """Use asyncio for async tests."""
    return "asyncio"


@pytest.fixture(scope="function")
async def engine():
    """Create a PostgreSQL test database engine."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="function")
async def db_session(engine) -> AsyncSession:
    """Create a test database session with tables.

    Creates all tables before the test and drops them after.
    Each test gets a clean database state.
    """
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create session
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()

    try:
        yield session
    finally:
        await session.close()
        # Drop tables after test
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def sample_role_data() -> RoleCreate:
    """Sample role creation data."""
    return RoleCreate(name="admin")


@pytest.fixture
def sample_user_data() -> UserCreate:
    """Sample user creation data."""
    return UserCreate(
        username="testuser",
        email="test@example.com",
        is_active=True,
    )


@pytest.fixture
def sample_post_data() -> PostCreate:
    """Sample post creation data."""
    return PostCreate(
        title="Test Post",
        content="Test content",
        is_published=True,
        author_id=1,
    )
