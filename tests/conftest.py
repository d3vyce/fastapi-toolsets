"""Shared pytest fixtures for fastapi-utils tests."""

import os
import uuid

import pytest
from pydantic import BaseModel
import datetime
import decimal

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Uuid,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from fastapi_toolsets.crud import CrudFactory
from fastapi_toolsets.schemas import PydanticBase

DATABASE_URL = os.getenv(
    key="DATABASE_URL",
    default="postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
)


class Base(DeclarativeBase):
    """Base class for test models."""

    pass


class Role(Base):
    """Test role model."""

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), unique=True)

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    """Test user model."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    email: Mapped[str] = mapped_column(String(100), unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("roles.id"), nullable=True
    )

    role: Mapped[Role | None] = relationship(back_populates="users")


class Tag(Base):
    """Test tag model."""

    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), unique=True)


post_tags = Table(
    "post_tags",
    Base.metadata,
    Column(
        "post_id", Uuid, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("tag_id", Uuid, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class IntRole(Base):
    """Test role model with auto-increment integer PK."""

    __tablename__ = "int_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)


class Event(Base):
    """Test model with DateTime and Date cursor columns."""

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime)
    scheduled_date: Mapped[datetime.date] = mapped_column(Date)


class Product(Base):
    """Test model with Numeric cursor column."""

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    price: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 2))


class Post(Base):
    """Test post model."""

    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(String(1000), default="")
    is_published: Mapped[bool] = mapped_column(default=False)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))

    tags: Mapped[list[Tag]] = relationship(secondary=post_tags)


class RoleCreate(BaseModel):
    """Schema for creating a role."""

    id: uuid.UUID | None = None
    name: str


class RoleRead(PydanticBase):
    """Schema for reading a role."""

    id: uuid.UUID
    name: str


class RoleUpdate(BaseModel):
    """Schema for updating a role."""

    name: str | None = None


class UserCreate(BaseModel):
    """Schema for creating a user."""

    id: uuid.UUID | None = None
    username: str
    email: str
    is_active: bool = True
    role_id: uuid.UUID | None = None


class UserRead(PydanticBase):
    """Schema for reading a user (subset of fields — no email)."""

    id: uuid.UUID
    username: str


class UserUpdate(BaseModel):
    """Schema for updating a user."""

    username: str | None = None
    email: str | None = None
    is_active: bool | None = None
    role_id: uuid.UUID | None = None


class TagCreate(BaseModel):
    """Schema for creating a tag."""

    id: uuid.UUID | None = None
    name: str


class PostCreate(BaseModel):
    """Schema for creating a post."""

    id: uuid.UUID | None = None
    title: str
    content: str = ""
    is_published: bool = False
    author_id: uuid.UUID


class PostUpdate(BaseModel):
    """Schema for updating a post."""

    title: str | None = None
    content: str | None = None
    is_published: bool | None = None


class PostM2MCreate(BaseModel):
    """Schema for creating a post with M2M tag IDs."""

    id: uuid.UUID | None = None
    title: str
    content: str = ""
    is_published: bool = False
    author_id: uuid.UUID
    tag_ids: list[uuid.UUID] = []


class PostM2MUpdate(BaseModel):
    """Schema for updating a post with M2M tag IDs."""

    title: str | None = None
    content: str | None = None
    is_published: bool | None = None
    tag_ids: list[uuid.UUID] | None = None


class IntRoleCreate(BaseModel):
    """Schema for creating an IntRole."""

    name: str


class EventCreate(BaseModel):
    """Schema for creating an Event."""

    name: str
    occurred_at: datetime.datetime
    scheduled_date: datetime.date


class ProductCreate(BaseModel):
    """Schema for creating a Product."""

    name: str
    price: decimal.Decimal


RoleCrud = CrudFactory(Role)
RoleCursorCrud = CrudFactory(Role, cursor_column=Role.id)
IntRoleCursorCrud = CrudFactory(IntRole, cursor_column=IntRole.id)
UserCrud = CrudFactory(User)
UserCursorCrud = CrudFactory(User, cursor_column=User.id)
PostCrud = CrudFactory(Post)
TagCrud = CrudFactory(Tag)
PostM2MCrud = CrudFactory(Post, m2m_fields={"tag_ids": Post.tags})
EventCrud = CrudFactory(Event)
EventDateTimeCursorCrud = CrudFactory(Event, cursor_column=Event.occurred_at)
EventDateCursorCrud = CrudFactory(Event, cursor_column=Event.scheduled_date)
ProductCrud = CrudFactory(Product)
ProductNumericCursorCrud = CrudFactory(Product, cursor_column=Product.price)


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
async def db_session(engine):
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
        author_id=uuid.uuid4(),
    )
