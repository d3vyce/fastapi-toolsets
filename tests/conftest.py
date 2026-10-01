"""Shared pytest fixtures for fastapi-utils tests."""

import contextlib
import datetime
import decimal
import os
import uuid
from contextlib import asynccontextmanager
from enum import Enum

import pytest
from pydantic import BaseModel
from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Uuid,
    event,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    defer,
    mapped_column,
    relationship,
    selectinload,
)

from fastapi_toolsets.crud import CrudFactory
from fastapi_toolsets.schemas import PydanticBase

DATABASE_URL = os.getenv(
    key="DATABASE_URL",
    default="postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
)


class Base(DeclarativeBase):
    """Base class for test models."""


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
    notes: Mapped[str | None]
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


class Permission(Base):
    """Test model with composite primary key."""

    __tablename__ = "permissions"

    subject: Mapped[str] = mapped_column(String(50), primary_key=True)
    action: Mapped[str] = mapped_column(String(50), primary_key=True)


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


class OrderStatus(int, Enum):
    """Integer-backed enum for order status."""

    PENDING = 1
    PROCESSING = 2
    SHIPPED = 3
    CANCELLED = 4


class Color(str, Enum):
    """String-backed enum for color."""

    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class Order(Base):
    """Test model with an IntEnum column (Enum(int, Enum)) and a raw Integer column."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[OrderStatus] = mapped_column(SAEnum(OrderStatus))
    priority: Mapped[int] = mapped_column(Integer)
    color: Mapped[Color] = mapped_column(SAEnum(Color))


class Transfer(Base):
    """Test model with two FKs to the same table (users)."""

    __tablename__ = "transfers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    amount: Mapped[str] = mapped_column(String(50))
    sender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    receiver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))


class Article(Base):
    """Test article model with ARRAY and JSON columns."""

    __tablename__ = "articles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    labels: Mapped[list[str]] = mapped_column(ARRAY(String))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


class Challenge(Base):
    """Base challenge model (root of joined-table inheritance hierarchy)."""

    __tablename__ = "challenges"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    challenge_type: Mapped[str] = mapped_column(String(50))
    points: Mapped[int] = mapped_column(Integer, default=0)

    __mapper_args__ = {
        "polymorphic_on": "challenge_type",
        "polymorphic_identity": "challenge",
    }


class ChallengeStandard(Challenge):
    """Standard challenge — child table in joined-table inheritance."""

    __tablename__ = "challenge_standard"

    id: Mapped[uuid.UUID] = mapped_column(ForeignKey("challenges.id"), primary_key=True)
    difficulty: Mapped[str] = mapped_column(String(50))

    __mapper_args__ = {
        "polymorphic_identity": "standard",
    }


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
    is_active: bool = True


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


class IntRoleRead(PydanticBase):
    """Schema for reading an IntRole."""

    id: int
    name: str


class IntRoleCreate(BaseModel):
    """Schema for creating an IntRole."""

    name: str


class EventRead(PydanticBase):
    """Schema for reading an Event."""

    id: uuid.UUID
    name: str


class EventCreate(BaseModel):
    """Schema for creating an Event."""

    name: str
    occurred_at: datetime.datetime
    scheduled_date: datetime.date


class ProductRead(PydanticBase):
    """Schema for reading a Product."""

    id: uuid.UUID
    name: str


class ProductCreate(BaseModel):
    """Schema for creating a Product."""

    name: str
    price: decimal.Decimal


class ArticleCreate(BaseModel):
    """Schema for creating an article."""

    id: uuid.UUID | None = None
    title: str
    labels: list[str] = []


class ArticleRead(PydanticBase):
    """Schema for reading an article."""

    id: uuid.UUID
    title: str
    labels: list[str]


class OrderCreate(BaseModel):
    """Schema for creating an order."""

    id: uuid.UUID | None = None
    name: str
    status: OrderStatus
    priority: int = 0
    color: Color = Color.RED


class OrderRead(PydanticBase):
    """Schema for reading an order."""

    id: uuid.UUID
    name: str
    status: OrderStatus
    priority: int
    color: Color


class TransferCreate(BaseModel):
    """Schema for creating a transfer."""

    id: uuid.UUID | None = None
    amount: str
    sender_id: uuid.UUID
    receiver_id: uuid.UUID


OrderCrud = CrudFactory(Order)
TransferCrud = CrudFactory(Transfer)
ArticleCrud = CrudFactory(Article)
RoleCrud = CrudFactory(Role)
RoleCursorCrud = CrudFactory(Role, cursor_column=Role.id)
IntRoleCursorCrud = CrudFactory(IntRole, cursor_column=IntRole.id)
UserCrud = CrudFactory(User)
UserCursorCrud = CrudFactory(User, cursor_column=User.id)
PostCrud = CrudFactory(Post)
TagCrud = CrudFactory(Tag)
PostM2MCrud = CrudFactory(Post, m2m_fields={"tag_ids": Post.tags})
# Default options that leave the M2M collection out.
PostDeferredCrud = CrudFactory(
    Post, default_load_options=[defer(Post.content)], m2m_fields={"tag_ids": Post.tags}
)
PostTagsLoadCrud = CrudFactory(
    Post,
    default_load_options=[selectinload(Post.tags)],
    m2m_fields={"tag_ids": Post.tags},
)
UserRoleLoadCrud = CrudFactory(User, default_load_options=[selectinload(User.role)])
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


@asynccontextmanager
async def _tables(engine):
    """Create every table for the block, and drop them after it."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(scope="function")
async def session_maker(engine):
    """Provide a session factory with tables created and dropped around the test."""
    async with _tables(engine):
        yield async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def _session_with_tables(engine, *, expire_on_commit: bool):
    """A session over freshly created tables, dropped afterwards."""
    async with _tables(engine):
        session = async_sessionmaker(engine, expire_on_commit=expire_on_commit)()
        try:
            yield session
        finally:
            await session.close()


@pytest.fixture(scope="function")
async def db_session(engine):
    """A session with ``expire_on_commit=False``, as the ``Database`` facade builds."""
    async with _session_with_tables(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture(scope="function", params=[False, True], ids=["keep", "expire"])
async def db_session_any(engine, request):
    """A session under both ``expire_on_commit`` settings."""
    async with _session_with_tables(engine, expire_on_commit=request.param) as session:
        yield session


@contextlib.contextmanager
def capture_sql(engine):
    """Collect every SQL statement sent through *engine*."""
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record)


def selects(statements: list[str]) -> list[str]:
    """The SELECT statements among *statements*."""
    return [sql for sql in statements if sql.startswith("SELECT")]


def following(statements: list[str], marker: str) -> list[str]:
    """The statements sent after the first one containing *marker*."""
    first = next(i for i, sql in enumerate(statements) if marker in sql)
    return statements[first + 1 :]
