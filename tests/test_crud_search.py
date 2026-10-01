"""Tests for pagination, search, facets, filter_by, ordering and the params dependencies."""

import datetime
import decimal
import inspect
import uuid
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import ForeignKey, ForeignKeyConstraint, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    aliased,
    mapped_column,
    relationship,
    selectinload,
)
from sqlalchemy.sql.elements import UnaryExpression

from fastapi_toolsets.crud import (
    CrudFactory,
    InvalidFacetFilterError,
    InvalidSearchColumnError,
    SearchConfig,
    UnsupportedFacetTypeError,
    get_searchable_fields,
)
from fastapi_toolsets.crud.factory import _CursorDirection, _encode_cursor
from fastapi_toolsets.crud.search import (
    _coerce_bool,
    _facet_rows,
    build_search_filters,
    facet_keys,
)
from fastapi_toolsets.exceptions import (
    ApiException,
    InvalidOrderFieldError,
    NoSearchableFieldsError,
)
from fastapi_toolsets.schemas import (
    CursorPagination,
    OffsetPagination,
    PaginationType,
    PydanticBase,
)

from .conftest import (
    Article,
    ArticleCreate,
    ArticleCrud,
    ArticleRead,
    Color,
    EventCreate,
    EventCrud,
    EventDateCursorCrud,
    EventDateTimeCursorCrud,
    EventRead,
    IntRoleCreate,
    IntRoleCursorCrud,
    IntRoleRead,
    Order,
    OrderCreate,
    OrderCrud,
    OrderRead,
    OrderStatus,
    Permission,
    Post,
    PostCrud,
    PostWithTagsRead,
    ProductCreate,
    ProductCrud,
    ProductNumericCursorCrud,
    ProductRead,
    Role,
    RoleCreate,
    RoleCrud,
    RoleCursorCrud,
    RoleRead,
    Tag,
    User,
    UserCreate,
    UserCrud,
    UserCursorCrud,
    UserRead,
    UserWithRoleRead,
    capture_sql,
    create_role,
    create_user,
    post_tags,
    raises_if,
)

# Models only compiled to SQL, never created in the database.


class _LocalBase(DeclarativeBase):
    pass


class _Node(_LocalBase):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id"))

    children: Mapped[list["_Node"]] = relationship()


class _Shelf(_LocalBase):
    __tablename__ = "shelves"

    id: Mapped[int] = mapped_column(primary_key=True)

    active_books: Mapped[list["_Book"]] = relationship(
        primaryjoin=lambda: and_(_Shelf.id == _Book.shelf_id, _Book.title != "old"),
        viewonly=True,
    )


class _Book(_LocalBase):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    shelf_id: Mapped[int] = mapped_column(ForeignKey("shelves.id"))


class _CompositeOrder(_LocalBase):
    __tablename__ = "composite_orders"

    a: Mapped[int] = mapped_column(primary_key=True)
    b: Mapped[int] = mapped_column(primary_key=True)

    lines: Mapped[list["_Line"]] = relationship()


class _Line(_LocalBase):
    __tablename__ = "order_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["order_a", "order_b"], ["composite_orders.a", "composite_orders.b"]
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str]
    order_a: Mapped[int]
    order_b: Mapped[int]


class _Counter(_LocalBase):
    __tablename__ = "counters"

    id: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[int]


class _Reserved(_LocalBase):
    __tablename__ = "reserved"

    id: Mapped[int] = mapped_column(primary_key=True)
    search: Mapped[str]


def _sql(model, filters) -> str:
    return str(select(*model.__mapper__.primary_key).where(*filters))


# Schemas and crud classes


class _PostTitle(PydanticBase):
    id: uuid.UUID
    title: str


class _PermissionRead(PydanticBase):
    subject: str
    action: str


class _UserFilter(BaseModel):
    username: str | None = None
    is_active: bool | None = None


UserSearchCrud = CrudFactory(
    User,
    searchable_fields=[User.username, (User.role, Role.name)],
    facet_fields=[User.is_active, (User.role, Role.name), (User.role, Role.id)],
    order_fields=[User.username, (User.role, Role.name)],
    cursor_column=User.id,
)
PostTagSearchCrud = CrudFactory(
    Post, searchable_fields=[Post.title, (Post.tags, Tag.name)], cursor_column=Post.id
)
PostFacetCrud = CrudFactory(
    Post,
    searchable_fields=[Post.title],
    facet_fields=[Post.is_published, (Post.tags, Tag.name), (Post.tags, Tag.id)],
    cursor_column=Post.id,
)
PermissionFacetCrud = CrudFactory(Permission, facet_fields=[Permission.action])
OrderFacetCrud = CrudFactory(
    Order,
    facet_fields=[Order.status, Order.priority, Order.color],
    cursor_column=Order.id,
)
ArticleFacetCrud = CrudFactory(Article, facet_fields=[Article.labels])
ArticleJsonCrud = CrudFactory(Article, facet_fields=[Article.metadata_])

_POST_COUNT = 10


async def _users(session: AsyncSession, *names: str, role: Role | None = None):
    role_id = role.id if role else None
    return [await create_user(session, name, role_id=role_id) for name in names]


async def _roles(session: AsyncSession, count: int) -> list[Role]:
    return [await create_role(session, f"role{i:02d}") for i in range(count)]


async def _posts_with_tags(session: AsyncSession) -> None:
    """10 posts, each with 3 tags whose names all contain "shared"."""
    author, *_ = await _users(session, "fanout")
    for i in range(_POST_COUNT):
        tags = [Tag(name=f"shared-{i}-{j}") for j in range(3)]
        session.add_all(tags)
        session.add(Post(title=f"post{i:02d}", author_id=author.id, tags=tags))
    await session.flush()


async def _orders(session: AsyncSession) -> None:
    for name, status, priority, color in (
        ("order-1", OrderStatus.PENDING, 1, Color.RED),
        ("order-2", OrderStatus.SHIPPED, 3, Color.BLUE),
        ("order-3", OrderStatus.CANCELLED, 1, Color.RED),
    ):
        await OrderCrud.create(
            session,
            OrderCreate(name=name, status=status, priority=priority, color=color),
        )


def _aggregates(statements: list[str]) -> list[str]:
    return [sql for sql in statements if "array_agg" in sql]


async def _paginate(crud, method: str, session: AsyncSession, **kwargs):
    return await getattr(crud, f"{method}_paginate")(session, **kwargs)


_methods = pytest.mark.parametrize("method", ["offset", "cursor"])


def _paging(method: str) -> dict[str, Any]:
    """The first-page argument of the *method* params dependency."""
    return {"page": 1} if method == "offset" else {"cursor": None}


class TestOffsetPaginate:
    """Pages, totals and has_more."""

    @pytest.mark.anyio
    async def test_pages_carry_the_total_and_has_more(self, db_session):
        roles = await _roles(db_session, 12)

        first = await RoleCrud.offset_paginate(
            db_session, items_per_page=5, order_by=Role.name, schema=RoleRead
        )
        last = await RoleCrud.offset_paginate(
            db_session, page=3, items_per_page=5, order_by=Role.name, schema=RoleRead
        )
        beyond = await RoleCrud.offset_paginate(
            db_session, page=9, items_per_page=5, schema=RoleRead
        )
        filtered = await RoleCrud.offset_paginate(
            db_session, filters=[Role.name < "role03"], schema=RoleRead
        )

        assert isinstance(first.pagination, OffsetPagination)
        assert [r.id for r in first.data] == [r.id for r in roles[:5]]
        assert first.pagination.total_count == 12 and first.pagination.has_more
        assert first.pagination.page == 1 and first.pagination.items_per_page == 5
        assert [r.id for r in last.data] == [roles[10].id, roles[11].id]
        assert not last.pagination.has_more and last.pagination.total_count == 12
        assert beyond.data == [] and beyond.pagination.total_count == 12
        assert filtered.pagination.total_count == 3

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("count", "has_more"), [(5, False), (10, False), (15, True)]
    )
    async def test_without_a_total_an_extra_row_sets_has_more(
        self, db_session, count, has_more
    ):
        await _roles(db_session, count)

        result = await RoleCrud.offset_paginate(
            db_session, items_per_page=10, include_total=False, schema=RoleRead
        )

        assert result.pagination.total_count is None
        assert result.pagination.has_more is has_more
        assert len(result.data) == min(count, 10)

    @pytest.mark.anyio
    async def test_a_short_page_gives_the_total_without_a_count(
        self, engine, db_session
    ):
        await _roles(db_session, 3)

        with capture_sql(engine) as statements:
            short = await RoleCrud.offset_paginate(db_session, schema=RoleRead)
        with capture_sql(engine) as counted:
            beyond = await RoleCrud.offset_paginate(db_session, page=5, schema=RoleRead)

        assert short.pagination.total_count == 3 and len(statements) == 1
        assert beyond.pagination.total_count == 3
        assert any("count(" in sql for sql in counted)

    @pytest.mark.anyio
    async def test_schema_filters_the_fields(self, db_session):
        await _users(db_session, "alice")

        result = await UserCrud.offset_paginate(db_session, schema=UserRead)

        assert result.data[0].username == "alice"
        assert not hasattr(result.data[0], "email")


class TestCursorPaginate:
    """Forward and backward traversal, and the cursor column types."""

    @pytest.mark.anyio
    async def test_traversal_visits_every_row_once(self, db_session):
        roles = await _roles(db_session, 9)
        seen: list[uuid.UUID] = []
        cursor = None

        for _ in range(9):
            page = await RoleCursorCrud.cursor_paginate(
                db_session, cursor=cursor, items_per_page=4, schema=RoleRead
            )
            seen += [r.id for r in page.data]
            cursor = page.pagination.next_cursor
            if cursor is None:
                break

        assert isinstance(page.pagination, CursorPagination)
        assert sorted(seen) == sorted(r.id for r in roles)
        assert not page.pagination.has_more and len(page.data) == 1

    @pytest.mark.anyio
    async def test_prev_cursor_walks_back_to_the_same_pages(self, db_session):
        await _roles(db_session, 9)

        page1 = await RoleCursorCrud.cursor_paginate(
            db_session, items_per_page=3, schema=RoleRead
        )
        page2 = await RoleCursorCrud.cursor_paginate(
            db_session,
            cursor=page1.pagination.next_cursor,
            items_per_page=3,
            schema=RoleRead,
        )
        page3 = await RoleCursorCrud.cursor_paginate(
            db_session,
            cursor=page2.pagination.next_cursor,
            items_per_page=3,
            schema=RoleRead,
        )
        back2 = await RoleCursorCrud.cursor_paginate(
            db_session,
            cursor=page3.pagination.prev_cursor,
            items_per_page=3,
            schema=RoleRead,
        )
        back1 = await RoleCursorCrud.cursor_paginate(
            db_session,
            cursor=back2.pagination.prev_cursor,
            items_per_page=3,
            schema=RoleRead,
        )

        assert page1.pagination.prev_cursor is None
        assert page3.pagination.next_cursor is None
        assert [r.id for r in back2.data] == [r.id for r in page2.data]
        assert back2.pagination.prev_cursor is not None
        assert back2.pagination.next_cursor is not None
        assert [r.id for r in back1.data] == [r.id for r in page1.data]
        assert back1.pagination.prev_cursor is None

    @pytest.mark.anyio
    async def test_going_back_before_the_first_row_is_empty(self, db_session):
        await IntRoleCursorCrud.create(db_session, IntRoleCreate(name="only"))
        before_all = _encode_cursor(0, direction=_CursorDirection.PREV)

        empty = await IntRoleCursorCrud.cursor_paginate(
            db_session, cursor=before_all, schema=IntRoleRead
        )
        nothing = await RoleCursorCrud.cursor_paginate(db_session, schema=RoleRead)

        assert empty.data == []
        assert empty.pagination.next_cursor is None
        assert empty.pagination.prev_cursor is None
        assert nothing.data == [] and not nothing.pagination.has_more

    @pytest.mark.anyio
    async def test_order_by_and_load_options_apply(self, db_session):
        role = await RoleCrud.create(db_session, RoleCreate(name="manager"))
        await _users(db_session, "c", "a", "b", role=role)

        result = await UserCursorCrud.cursor_paginate(
            db_session,
            order_by=User.username.desc(),
            load_options=[selectinload(User.role)],
            schema=UserWithRoleRead,
        )

        assert all(u.role is not None for u in result.data)
        assert len(result.data) == 3

    @pytest.mark.anyio
    @pytest.mark.parametrize("kind", ["integer", "datetime", "date", "numeric"])
    async def test_cursor_values_round_trip_for_each_column_type(
        self, db_session, kind
    ):
        base = datetime.datetime(2024, 1, 1)
        for i in range(5):
            if kind == "integer":
                await IntRoleCursorCrud.create(db_session, IntRoleCreate(name=f"r{i}"))
            elif kind == "numeric":
                await ProductCrud.create(
                    db_session,
                    ProductCreate(name=f"p{i}", price=decimal.Decimal(f"{i + 1}.99")),
                )
            else:
                await EventCrud.create(
                    db_session,
                    EventCreate(
                        name=f"e{i}",
                        occurred_at=base + datetime.timedelta(hours=i),
                        scheduled_date=datetime.date(2024, 1, i + 1),
                    ),
                )
        crud, schema = {
            "integer": (IntRoleCursorCrud, IntRoleRead),
            "datetime": (EventDateTimeCursorCrud, EventRead),
            "date": (EventDateCursorCrud, EventRead),
            "numeric": (ProductNumericCursorCrud, ProductRead),
        }[kind]

        page1 = await crud.cursor_paginate(db_session, items_per_page=3, schema=schema)
        page2 = await crud.cursor_paginate(
            db_session,
            cursor=page1.pagination.next_cursor,
            items_per_page=3,
            schema=schema,
        )

        assert len(page1.data) == 3 and page1.pagination.has_more
        assert len(page2.data) == 2 and not page2.pagination.has_more
        assert {e.id for e in page1.data}.isdisjoint({e.id for e in page2.data})

    @pytest.mark.anyio
    async def test_unsupported_cursor_column_and_missing_column_raise(self, db_session):
        by_name = CrudFactory(Role, cursor_column=Role.name)
        await _roles(db_session, 2)

        page1 = await by_name.cursor_paginate(
            db_session, items_per_page=1, schema=RoleRead
        )

        with pytest.raises(ValueError, match="Unsupported cursor column type"):
            await by_name.cursor_paginate(
                db_session, cursor=page1.pagination.next_cursor, schema=RoleRead
            )
        with pytest.raises(ValueError, match="cursor_column is not set"):
            await RoleCrud.cursor_paginate(db_session, schema=RoleRead)


class TestPaginate:
    """The unified paginate() entry point."""

    @pytest.mark.anyio
    async def test_dispatches_on_the_pagination_type(self, db_session):
        await _roles(db_session, 3)

        offset = await RoleCursorCrud.paginate(
            db_session,
            pagination_type=PaginationType.OFFSET,
            include_total=False,
            schema=RoleRead,
        )
        cursor = await RoleCursorCrud.paginate(
            db_session, pagination_type=PaginationType.CURSOR, schema=RoleRead
        )

        assert isinstance(offset.pagination, OffsetPagination)
        assert offset.pagination.total_count is None
        assert isinstance(cursor.pagination, CursorPagination)
        assert len(offset.data) == len(cursor.data) == 3

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"items_per_page": 0}, "items_per_page must be >= 1"),
            ({"page": 0}, "page must be >= 1"),
            ({"pagination_type": "neither"}, "Unknown pagination_type"),
        ],
    )
    async def test_invalid_arguments_raise(self, db_session, kwargs, message):
        with pytest.raises(ValueError, match=message):
            await RoleCursorCrud.paginate(db_session, schema=RoleRead, **kwargs)


class TestSearch:
    """Search over direct, related, enum and non-string columns."""

    @pytest.mark.anyio
    async def test_search_matches_direct_and_related_columns(self, db_session):
        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        await _users(db_session, "alice", role=admin)
        await _users(db_session, "bob", "carol")

        by_name = await UserSearchCrud.offset_paginate(
            db_session, search="ALI", schema=UserRead
        )
        by_role = await UserSearchCrud.offset_paginate(
            db_session, search="adm", order_by=User.username, schema=UserRead
        )
        explicit = await UserCrud.offset_paginate(
            db_session, search="test.com", search_fields=[User.email], schema=UserRead
        )
        filtered = await UserCrud.offset_paginate(
            db_session,
            search="o",
            search_fields=[User.username],
            filters=[User.username != "bob"],
            schema=UserRead,
        )
        nothing = await UserSearchCrud.offset_paginate(
            db_session, search="zzz", schema=UserRead
        )

        assert [u.username for u in by_name.data] == ["alice"]
        assert [u.username for u in by_role.data] == ["alice"]
        assert explicit.pagination.total_count == 3
        assert [u.username for u in filtered.data] == ["carol"]
        assert nothing.data == [] and nothing.pagination.total_count == 0

    @pytest.mark.anyio
    async def test_search_config_controls_case_fields_and_match_mode(self, db_session):
        await _users(db_session, "Alice", "alice2", "bob")

        sensitive = await UserCrud.offset_paginate(
            db_session,
            search=SearchConfig(query="Alice", case_sensitive=True),
            search_fields=[User.username],
            schema=UserRead,
        )
        both = await UserCrud.offset_paginate(
            db_session,
            search=SearchConfig(query="alice", fields=[User.username, User.email]),
            schema=UserRead,
        )
        every = await UserCrud.offset_paginate(
            db_session,
            search=SearchConfig(query="alice2", match_mode="all"),
            search_fields=[User.username, User.email],
            schema=UserRead,
        )
        blank = await UserCrud.offset_paginate(
            db_session, search="   ", search_fields=[User.username], schema=UserRead
        )

        assert [u.username for u in sensitive.data] == ["Alice"]
        assert both.pagination.total_count == 2
        assert [u.username for u in every.data] == ["alice2"]
        assert blank.pagination.total_count == 3

    @pytest.mark.anyio
    async def test_search_casts_non_string_and_enum_columns(self, db_session):
        user_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        await UserCrud.create(
            db_session, UserCreate(id=user_id, username="john", email="j@test.com")
        )
        await _orders(db_session)

        by_id = await UserCrud.offset_paginate(
            db_session, search="12345678", schema=UserRead
        )
        by_status = await OrderCrud.offset_paginate(
            db_session,
            search="SHIPPED",
            search_fields=[Order.status, Order.color],
            schema=OrderRead,
        )
        by_color = await OrderCrud.offset_paginate(
            db_session,
            search="blue",
            search_fields=[Order.name, Order.color],
            schema=OrderRead,
        )

        assert [u.id for u in by_id.data] == [user_id]
        assert [o.name for o in by_status.data] == ["order-2"]
        assert [o.name for o in by_color.data] == ["order-2"]

    @pytest.mark.anyio
    async def test_a_missing_relationship_does_not_hide_the_row(self, db_session):
        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        await _users(db_session, "with_role", role=admin)
        await _users(db_session, "no_role")

        result = await UserSearchCrud.offset_paginate(
            db_session, search="role", schema=UserRead
        )

        assert result.pagination.total_count == 2

    @pytest.mark.anyio
    @_methods
    async def test_search_column_narrows_and_is_validated(self, db_session, method):
        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        await _users(db_session, "admin-user")
        await _users(db_session, "alice", role=admin)

        narrowed = await _paginate(
            UserSearchCrud,
            method,
            db_session,
            search="admin",
            search_column="username",
            schema=UserRead,
        )
        everywhere = await _paginate(
            UserSearchCrud, method, db_session, search="admin", schema=UserRead
        )

        assert [u.username for u in narrowed.data] == ["admin-user"]
        assert len(everywhere.data) == 2
        assert narrowed.search_columns == ["id", "username", "role__name"]
        with pytest.raises(InvalidSearchColumnError) as exc:
            await _paginate(
                UserSearchCrud,
                method,
                db_session,
                search="x",
                search_column="email",
                schema=UserRead,
            )
        assert exc.value.column == "email"
        assert exc.value.valid_columns == ["id", "role__name", "username"]

    @pytest.mark.anyio
    @_methods
    async def test_response_metadata_follows_the_configured_fields(
        self, db_session, method
    ):
        await _users(db_session, "alice")

        configured = await _paginate(
            UserSearchCrud, method, db_session, schema=UserRead
        )
        overridden = await _paginate(
            UserSearchCrud,
            method,
            db_session,
            search_fields=[User.email],
            order_fields=[User.email, User.username],
            schema=UserRead,
        )
        plain = await _paginate(
            CrudFactory(User, cursor_column=User.id),
            method,
            db_session,
            schema=UserRead,
        )

        assert configured.search_columns == ["id", "username", "role__name"]
        assert configured.order_columns == ["role__name", "username"]
        assert overridden.search_columns == ["email"]
        assert overridden.order_columns == ["email", "username"]
        assert plain.search_columns == ["id"] and plain.order_columns is None

    def test_searchable_fields_are_detected_from_the_mapper(self):
        user_fields = get_searchable_fields(User)
        role_fields = get_searchable_fields(Role)

        assert User.username in user_fields and User.email in user_fields
        assert (User.role, Role.name) in user_fields
        assert role_fields == [Role.name]
        assert get_searchable_fields(User, include_relationships=False) == [
            f for f in user_fields if not isinstance(f, tuple)
        ]
        with pytest.raises(NoSearchableFieldsError) as exc:
            build_search_filters(_Counter, "x")
        assert isinstance(exc.value, ApiException)
        assert exc.value.model is _Counter


class TestSearchFilters:
    """build_search_filters: the page form and the aggregate form."""

    def test_to_many_fields_are_joined_for_the_page_and_subqueried_for_aggregates(
        self,
    ):
        fields = [Post.title, (Post.tags, Tag.name)]

        page_filters, page_joins = build_search_filters(Post, "x", search_fields=fields)
        agg_filters, agg_joins = build_search_filters(
            Post, "x", search_fields=fields, to_many_subqueries=True
        )
        one_filters, one_joins = build_search_filters(
            User,
            "admin",
            search_fields=[(User.role, Role.name), (User.role, Role.id)],
            to_many_subqueries=True,
        )

        assert page_joins == [Post.tags] and "JOIN" not in _sql(Post, page_filters)
        assert agg_joins == []
        assert "posts.id IN (SELECT post_tags.post_id" in _sql(Post, agg_filters)
        assert one_joins == [User.role]
        assert "IN (SELECT" not in _sql(User, one_filters)

    def test_fields_on_one_relationship_share_a_subquery(self):
        filters, _ = build_search_filters(
            Post,
            SearchConfig(query="x", match_mode="all"),
            search_fields=[(Post.tags, Tag.name), (Post.tags, Tag.id)],
            to_many_subqueries=True,
        )

        sql = _sql(Post, filters)
        assert len(filters) == 1 and sql.count("IN (SELECT") == 1
        assert "tags.name" in sql and "CAST(tags.id AS VARCHAR)" in sql

    def test_subquery_keys_match_the_relationship_shape(self):
        self_filters, _ = build_search_filters(
            _Node,
            "x",
            search_fields=[(_Node.children, _Node.name)],
            to_many_subqueries=True,
        )
        composite_filters, _ = build_search_filters(
            _CompositeOrder,
            "x",
            search_fields=[(_CompositeOrder.lines, _Line.sku)],
            to_many_subqueries=True,
        )

        assert "FROM nodes" in str(self_filters[0].right)
        assert "(composite_orders.a, composite_orders.b) IN" in _sql(
            _CompositeOrder, composite_filters
        )

    @pytest.mark.parametrize(
        ("model", "field"),
        [
            (_Shelf, (_Shelf.active_books, _Book.title)),
            (Post, (aliased(Post).tags, Tag.name)),
        ],
        ids=["custom-join", "alias"],
    )
    def test_relationships_the_subquery_cannot_express_stay_joined(self, model, field):
        _, joins = build_search_filters(
            model, "x", search_fields=[field], to_many_subqueries=True
        )

        assert len(joins) == 1

    def test_casts_only_non_string_columns(self):
        plain, _ = build_search_filters(User, "john", search_fields=[User.username])
        cast, _ = build_search_filters(User, "john", search_fields=[User.id])
        enum, _ = build_search_filters(Order, "x", search_fields=[Order.status])

        assert "CAST" not in str(plain[0])
        assert "CAST" in str(cast[0]) and "CAST" in str(enum[0])


_fan_out = pytest.mark.parametrize(
    "fan_out",
    [{}, {"order_by": Tag.name, "order_joins": [Post.tags]}],
    ids=["search", "order_join"],
)


class TestToManyJoins:
    """A to-many or raw join must not shrink, repeat or miscount pages."""

    @pytest.mark.anyio
    @_fan_out
    async def test_offset_pages_are_full_and_complete(self, db_session, fan_out):
        await _posts_with_tags(db_session)
        seen: list[str] = []

        for page in (1, 2):
            result = await PostTagSearchCrud.offset_paginate(
                db_session,
                page=page,
                items_per_page=5,
                search="shared",
                **fan_out,
                load_options=[selectinload(Post.tags)],
                schema=PostWithTagsRead,
            )
            assert result.pagination.total_count == _POST_COUNT
            assert len(result.data) == 5
            assert all(len(p.tags) == 3 for p in result.data)
            seen += [p.title for p in result.data]
        beyond = await PostTagSearchCrud.offset_paginate(
            db_session,
            page=99,
            items_per_page=5,
            search="shared",
            **fan_out,
            schema=_PostTitle,
        )

        assert len(set(seen)) == _POST_COUNT
        assert beyond.data == [] and beyond.pagination.total_count == _POST_COUNT

    @pytest.mark.anyio
    @_fan_out
    async def test_has_more_counts_entities_not_joined_rows(self, db_session, fan_out):
        await _posts_with_tags(db_session)

        pages = [
            await PostTagSearchCrud.offset_paginate(
                db_session,
                page=page,
                items_per_page=5,
                search="shared",
                **fan_out,
                include_total=False,
                schema=_PostTitle,
            )
            for page in (1, 2)
        ]

        assert [len(p.data) for p in pages] == [5, 5]
        assert [p.pagination.has_more for p in pages] == [True, False]

    @pytest.mark.anyio
    @_fan_out
    async def test_cursor_traverses_every_row(self, db_session, fan_out):
        await _posts_with_tags(db_session)
        seen: list[str] = []
        cursor = None

        for _ in range(_POST_COUNT):
            result = await PostTagSearchCrud.cursor_paginate(
                db_session,
                cursor=cursor,
                items_per_page=5,
                search="shared",
                **fan_out,
                schema=_PostTitle,
            )
            seen += [p.title for p in result.data]
            cursor = result.pagination.next_cursor
            if cursor is None:
                break

        assert len(seen) == len(set(seen)) == _POST_COUNT

    @pytest.mark.anyio
    async def test_ordering_by_a_related_column_collapses_it(self, db_session):
        await _posts_with_tags(db_session)

        result = await PostTagSearchCrud.offset_paginate(
            db_session,
            items_per_page=5,
            search="shared",
            order_by=Tag.name.desc(),
            order_joins=[Post.tags],
            schema=_PostTitle,
        )

        assert [p.title for p in result.data] == [f"post0{i}" for i in (9, 8, 7, 6, 5)]

    @pytest.mark.anyio
    @_methods
    async def test_a_raw_to_many_join_returns_full_pages(self, db_session, method):
        await _posts_with_tags(db_session)
        joins: list[Any] = [(post_tags, post_tags.c.post_id == Post.id)]

        result = await _paginate(
            PostTagSearchCrud,
            method,
            db_session,
            joins=joins,
            items_per_page=5,
            schema=_PostTitle,
        )
        rows = await PostCrud.get_multi(
            db_session, joins=joins, outer_join=True, limit=5
        )

        assert len(result.data) == 5 and result.pagination.has_more
        if method == "offset":
            assert result.pagination.total_count == _POST_COUNT
        assert len({r.id for r in rows}) == 5

    @pytest.mark.anyio
    async def test_total_matches_the_page_for_match_all(self, db_session):
        await _posts_with_tags(db_session)

        result = await PostTagSearchCrud.offset_paginate(
            db_session,
            search=SearchConfig(query="1", match_mode="all"),
            search_fields=[Post.title, (Post.tags, Tag.name)],
            schema=_PostTitle,
        )

        assert [p.title for p in result.data] == ["post01"]
        assert result.pagination.total_count == 1

    @pytest.mark.anyio
    async def test_count_is_distinct_only_when_a_join_can_repeat_rows(
        self, engine, db_session
    ):
        await _posts_with_tags(db_session)

        with capture_sql(engine) as plain:
            searched = await PostTagSearchCrud.offset_paginate(
                db_session,
                items_per_page=5,
                search="shared",
                include_facets=False,
                schema=_PostTitle,
            )
        with capture_sql(engine) as raw:
            joined = await PostTagSearchCrud.offset_paginate(
                db_session,
                items_per_page=5,
                joins=[(post_tags, post_tags.c.post_id == Post.id)],
                include_facets=False,
                schema=_PostTitle,
            )
        with capture_sql(engine) as filtered:
            narrowed = await PostFacetCrud.offset_paginate(
                db_session,
                items_per_page=1,
                filter_by={"tags__name": ["shared-0-0", "shared-0-1"]},
                include_facets=False,
                schema=_PostTitle,
            )

        assert "count(*)" in next(sql for sql in plain if "count(" in sql)
        assert any("count(distinct(" in sql for sql in raw)
        assert any("count(distinct(" in sql for sql in filtered)
        assert searched.pagination.total_count == joined.pagination.total_count == 10
        assert narrowed.pagination.total_count == 1
        assert [p.title for p in narrowed.data] == ["post00"]

    @pytest.mark.anyio
    async def test_a_composite_key_is_counted_whole(self, engine, db_session):
        db_session.add_all(
            [
                Permission(subject="users", action="read"),
                Permission(subject="users", action="write"),
                Permission(subject="posts", action="read"),
                Role(name="viewer"),
                Role(name="editor"),
            ]
        )
        await db_session.flush()

        with capture_sql(engine) as statements:
            result = await PermissionFacetCrud.offset_paginate(
                db_session,
                items_per_page=2,
                joins=[(Role, Role.name.isnot(None))],
                schema=_PermissionRead,
            )

        assert result.pagination.total_count == 3 and len(result.data) == 2
        assert result.filter_attributes == {"action": ["read", "write"]}
        aggregate = _aggregates(statements)[0]
        assert "SELECT DISTINCT permissions.subject" in aggregate
        assert aggregate.count("FROM permissions") == 1

    def test_a_facet_with_a_custom_join_condition_keeps_the_join(self):
        sql = str(_facet_rows(_Shelf, [_Shelf.active_books], [], [], prefiltered=False))

        assert "JOIN books" in sql and "IN (SELECT" not in sql


class TestFacets:
    """Distinct values per facet field, with and without filters."""

    @pytest.mark.anyio
    @_methods
    async def test_facets_list_distinct_typed_values(self, db_session, method):
        await _orders(db_session)

        result = await _paginate(OrderFacetCrud, method, db_session, schema=OrderRead)
        overridden = await _paginate(
            OrderFacetCrud,
            method,
            db_session,
            facet_fields=[Order.name],
            schema=OrderRead,
        )
        off = await _paginate(
            OrderFacetCrud, method, db_session, include_facets=False, schema=OrderRead
        )
        none = await _paginate(
            CrudFactory(Order, cursor_column=Order.id),
            method,
            db_session,
            schema=OrderRead,
        )

        assert result.filter_attributes == {
            "status": ["PENDING", "SHIPPED", "CANCELLED"],
            "priority": [1, 3],
            "color": ["RED", "BLUE"],
        }
        assert overridden.filter_attributes == {
            "name": ["order-1", "order-2", "order-3"]
        }
        assert off.filter_attributes is None and none.filter_attributes is None

    @pytest.mark.anyio
    async def test_bool_facets_keep_python_bools(self, db_session):
        await _users(db_session, "alice")
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com", is_active=False)
        )

        result = await UserSearchCrud.offset_paginate(db_session, schema=UserRead)

        assert result.filter_attributes is not None
        assert result.filter_attributes["is_active"] == [False, True]

    @pytest.mark.anyio
    async def test_facets_follow_the_filters_and_the_search(self, db_session):
        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        editor = await RoleCrud.create(db_session, RoleCreate(name="editor"))
        await _users(db_session, "alice", role=admin)
        await _users(db_session, "bob", role=editor)
        await _users(db_session, "carol")
        await ArticleCrud.create(
            db_session, ArticleCreate(title="a", labels=["x", "y"])
        )
        await ArticleCrud.create(db_session, ArticleCreate(title="b", labels=["z"]))

        everyone = await UserSearchCrud.offset_paginate(db_session, schema=UserRead)
        filtered = await UserSearchCrud.offset_paginate(
            db_session, filters=[User.username == "bob"], schema=UserRead
        )
        searched = await UserSearchCrud.offset_paginate(
            db_session, search="admin", schema=UserRead
        )
        arrays = await ArticleFacetCrud.offset_paginate(
            db_session, filters=[Article.title == "a"], schema=ArticleRead
        )

        assert everyone.filter_attributes is not None
        assert everyone.filter_attributes["role__name"] == ["admin", "editor"]
        assert filtered.filter_attributes is not None
        assert filtered.filter_attributes["role__name"] == ["editor"]
        assert searched.filter_attributes is not None
        assert searched.filter_attributes["role__name"] == ["admin"]
        assert arrays.filter_attributes == {"labels": ["x", "y"]}

    @pytest.mark.anyio
    @_methods
    async def test_search_and_filter_by_share_one_relationship_join(
        self, db_session, method
    ):
        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        editor = await RoleCrud.create(db_session, RoleCreate(name="editor"))
        await _users(db_session, "alice", role=admin)
        await _users(db_session, "bob", role=editor)

        result = await _paginate(
            UserSearchCrud,
            method,
            db_session,
            search="admin",
            filter_by={"role__name": "admin", "role__id": str(admin.id)},
            schema=UserRead,
        )

        assert [u.username for u in result.data] == ["alice"]
        assert result.filter_attributes is not None
        assert result.filter_attributes["role__name"] == ["admin"]

    @pytest.mark.anyio
    async def test_to_many_facets_see_the_matching_rows_only(self, db_session):
        await _posts_with_tags(db_session)
        tag = (
            await db_session.execute(select(Tag).where(Tag.name == "shared-0-0"))
        ).scalar_one()

        one_post = await PostFacetCrud.offset_paginate(
            db_session, filters=[Post.title == "post03"], schema=_PostTitle
        )
        narrowed = await PostFacetCrud.offset_paginate(
            db_session, filter_by={"tags__id": tag.id}, schema=_PostTitle
        )
        shared = await PostFacetCrud.offset_paginate(
            db_session, search="post", filter_by={"tags__id": tag.id}, schema=_PostTitle
        )

        assert one_post.filter_attributes is not None
        assert one_post.filter_attributes["tags__name"] == [
            "shared-3-0",
            "shared-3-1",
            "shared-3-2",
        ]
        for result in (narrowed, shared):
            assert result.pagination.total_count == 1
            assert result.filter_attributes is not None
            assert result.filter_attributes["tags__name"] == ["shared-0-0"]
            assert result.filter_attributes["is_published"] == [False]

    @pytest.mark.anyio
    async def test_cursor_facets_describe_the_whole_result(self, db_session):
        await _posts_with_tags(db_session)

        first = await PostFacetCrud.cursor_paginate(
            db_session, items_per_page=5, search="post", schema=_PostTitle
        )
        second = await PostFacetCrud.cursor_paginate(
            db_session,
            cursor=first.pagination.next_cursor,
            items_per_page=5,
            search="post",
            schema=_PostTitle,
        )

        assert first.filter_attributes is not None
        assert len(first.filter_attributes["tags__name"]) == 3 * _POST_COUNT
        assert second.filter_attributes == first.filter_attributes

    @pytest.mark.anyio
    async def test_the_total_and_the_facets_share_one_filtered_scan(
        self, engine, db_session
    ):
        await _posts_with_tags(db_session)

        with capture_sql(engine) as statements:
            result = await PostFacetCrud.offset_paginate(
                db_session,
                items_per_page=5,
                search="post0",
                filter_by={"is_published": "false"},
                schema=_PostTitle,
            )
        with capture_sql(engine) as unfiltered:
            plain = await PostFacetCrud.offset_paginate(
                db_session, items_per_page=5, schema=_PostTitle
            )
        with capture_sql(engine) as cursor:
            await PostFacetCrud.cursor_paginate(
                db_session, search="post0", schema=_PostTitle
            )

        shared = _aggregates(statements)
        assert len(shared) == 1 and shared[0].startswith("WITH")
        assert "count(" in shared[0] and shared[0].count("ILIKE") == 2
        assert result.pagination.total_count == _POST_COUNT
        assert result.filter_attributes is not None
        assert result.filter_attributes["is_published"] == [False]
        assert len(result.filter_attributes["tags__name"]) == 3 * _POST_COUNT
        assert "WITH" not in _aggregates(unfiltered)[0]
        assert "count(" in _aggregates(unfiltered)[0]
        assert plain.pagination.total_count == _POST_COUNT
        assert _aggregates(cursor)[0].startswith("WITH")
        assert "count(" not in _aggregates(cursor)[0]


class TestFilterBy:
    """filter_by on direct, related, bool, array and enum columns."""

    @pytest.mark.anyio
    @_methods
    async def test_filter_by_narrows_the_page_but_not_its_own_facet(
        self, db_session, method
    ):
        await _users(db_session, "alice", "bob")
        crud = CrudFactory(User, facet_fields=[User.username], cursor_column=User.id)

        scalar = await _paginate(
            crud, method, db_session, filter_by={"username": "alice"}, schema=UserRead
        )
        listed = await _paginate(
            crud,
            method,
            db_session,
            filter_by={"username": ["alice", "bob"]},
            schema=UserRead,
        )
        model = await _paginate(
            crud,
            method,
            db_session,
            filter_by=_UserFilter(username="bob"),
            schema=UserRead,
        )
        combined = await _paginate(
            crud,
            method,
            db_session,
            filters=[User.username != "alice"],
            filter_by={"username": ["alice", "bob"]},
            schema=UserRead,
        )

        assert [u.username for u in scalar.data] == ["alice"]
        assert scalar.filter_attributes == {"username": ["alice", "bob"]}
        assert len(listed.data) == 2
        assert [u.username for u in model.data] == ["bob"]
        assert [u.username for u in combined.data] == ["bob"]
        with pytest.raises(InvalidFacetFilterError) as exc:
            await _paginate(
                crud, method, db_session, filter_by={"nope": "x"}, schema=UserRead
            )
        assert exc.value.key == "nope" and exc.value.valid_keys == {"username"}

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("false", {"bob"}),
            ("true", {"alice"}),
            (True, {"alice"}),
            (["true", "false"], {"alice", "bob"}),
        ],
    )
    async def test_bool_filters_coerce_strings(self, db_session, value, expected):
        await _users(db_session, "alice")
        await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com", is_active=False)
        )

        result = await UserSearchCrud.offset_paginate(
            db_session, filter_by={"is_active": value}, schema=UserRead
        )

        assert {u.username for u in result.data} == expected

    @pytest.mark.parametrize("value", [42, "maybe"])
    def test_bool_coercion_rejects_other_values(self, value):
        with pytest.raises(ValueError, match="Cannot coerce"):
            _coerce_bool(value)

    @pytest.mark.anyio
    async def test_array_filters_check_containment_or_overlap(self, db_session):
        for title, labels in (
            ("Post 1", ["python", "fastapi"]),
            ("Post 2", ["rust", "axum"]),
            ("Post 3", ["python", "django"]),
        ):
            await ArticleCrud.create(
                db_session, ArticleCreate(title=title, labels=labels)
            )

        single = await ArticleFacetCrud.offset_paginate(
            db_session, filter_by={"labels": "python"}, schema=ArticleRead
        )
        overlap = await ArticleFacetCrud.offset_paginate(
            db_session, filter_by={"labels": ["rust", "django"]}, schema=ArticleRead
        )

        assert {a.title for a in single.data} == {"Post 1", "Post 3"}
        assert single.filter_attributes == {
            "labels": ["axum", "django", "fastapi", "python", "rust"]
        }
        assert {a.title for a in overlap.data} == {"Post 2", "Post 3"}
        with pytest.raises(UnsupportedFacetTypeError) as exc:
            await ArticleJsonCrud.offset_paginate(
                db_session, filter_by={"metadata_": {"k": "v"}}, schema=ArticleRead
            )
        assert exc.value.key == "metadata_" and "JSON" in exc.value.col_type

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("filter_by", "expected"),
        [
            ({"status": OrderStatus.PENDING}, {"order-1"}),
            (
                {"status": [OrderStatus.PENDING, OrderStatus.SHIPPED]},
                {"order-1", "order-2"},
            ),
            ({"status": "PENDING"}, {"order-1"}),
            ({"status": ["PENDING", "SHIPPED"]}, {"order-1", "order-2"}),
            ({"color": Color.BLUE}, {"order-2"}),
            ({"color": ["RED"]}, {"order-1", "order-3"}),
            ({"priority": OrderStatus.PENDING}, {"order-1", "order-3"}),
            ({"priority": 3}, {"order-2"}),
            ({"status": 1}, KeyError),
            ({"status": [1, 3]}, KeyError),
        ],
        ids=[
            "int-enum-member",
            "int-enum-list",
            "int-enum-name",
            "int-enum-names",
            "str-enum-member",
            "str-enum-names",
            "integer-with-member",
            "integer-with-int",
            "int-enum-plain-int",
            "int-enum-plain-list",
        ],
    )
    async def test_enum_and_integer_filters(self, db_session, filter_by, expected):
        await _orders(db_session)

        with raises_if(KeyError, expected is KeyError):
            result = await OrderFacetCrud.offset_paginate(
                db_session, filter_by=filter_by, schema=OrderRead
            )
            assert {o.name for o in result.data} == expected


class TestParams:
    """The FastAPI dependencies built by *_paginate_params()."""

    @pytest.mark.parametrize(
        ("factory", "fixed", "name"),
        [
            (
                "offset_paginate_params",
                {"page", "items_per_page"},
                "RoleOffsetPaginateParams",
            ),
            (
                "cursor_paginate_params",
                {"cursor", "items_per_page"},
                "RoleCursorPaginateParams",
            ),
            (
                "paginate_params",
                {"pagination_type", "page", "cursor", "items_per_page"},
                "RolePaginateParams",
            ),
        ],
    )
    def test_each_dependency_exposes_its_pagination_params(self, factory, fixed, name):
        dep = getattr(RoleCursorCrud, factory)(
            default_page_size=42,
            max_page_size=50,
            search=False,
            filter=False,
            order=False,
        )
        sig = inspect.signature(dep)
        size = sig.parameters["items_per_page"].default

        assert set(sig.parameters) == fixed
        assert dep.__name__ == name
        assert size.default == 42
        assert next(m.le for m in size.metadata if hasattr(m, "le")) == 50

    @pytest.mark.anyio
    async def test_fixed_options_are_forwarded_not_exposed(self):
        offset = RoleCrud.offset_paginate_params(
            include_total=False, search=False, filter=False, order=False
        )
        unified = RoleCursorCrud.paginate_params(
            default_pagination_type=PaginationType.CURSOR,
            include_facets=False,
            search=False,
            filter=False,
            order=False,
        )

        assert await offset(page=2, items_per_page=10) == {
            "page": 2,
            "items_per_page": 10,
            "include_total": False,
            "include_facets": True,
            "search_fields": [],
            "facet_fields": [],
            "order_fields": [],
        }
        params = await unified(
            pagination_type=PaginationType.OFFSET, page=1, cursor=None, items_per_page=5
        )
        assert params["include_total"] is True and params["include_facets"] is False
        assert (
            inspect.signature(unified).parameters["pagination_type"].default.default
            == PaginationType.CURSOR
        )
        assert "include_total" not in inspect.signature(unified).parameters

    @pytest.mark.anyio
    @_methods
    async def test_the_result_unpacks_into_the_paginator(self, db_session, method):
        await _users(db_session, "alice", "bob")
        dep = getattr(UserSearchCrud, f"{method}_paginate_params")()

        params = await dep(items_per_page=10, search="ali", **_paging(method))
        result = await _paginate(
            UserSearchCrud, method, db_session, **params, schema=UserRead
        )

        assert [u.username for u in result.data] == ["alice"]

    @pytest.mark.anyio
    async def test_the_unified_result_unpacks_into_paginate(self, db_session):
        dep = RoleCursorCrud.paginate_params()

        params = await dep(
            pagination_type=PaginationType.CURSOR,
            page=1,
            cursor=None,
            items_per_page=10,
        )
        result = await RoleCursorCrud.paginate(db_session, **params, schema=RoleRead)

        assert isinstance(result.pagination, CursorPagination)

    def test_search_filter_and_order_params_follow_the_fields(self):
        full = UserSearchCrud.offset_paginate_params()
        off = UserSearchCrud.offset_paginate_params(
            search=False, filter=False, order=False
        )
        overridden = UserSearchCrud.offset_paginate_params(
            search_fields=[User.email],
            facet_fields=[User.email],
            order_fields=[User.email],
        )
        bare = RoleCrud.offset_paginate_params()

        assert set(inspect.signature(full).parameters) == {
            "page",
            "items_per_page",
            "search",
            "search_column",
            "is_active",
            "role__name",
            "role__id",
            "order_by",
            "order",
        }
        assert set(inspect.signature(off).parameters) == {"page", "items_per_page"}
        params = inspect.signature(overridden).parameters
        assert set(params) == {
            "page",
            "items_per_page",
            "search",
            "search_column",
            "email",
            "order_by",
            "order",
        }
        assert params["search_column"].default.json_schema_extra["enum"] == ["email"]
        assert "email" in params["order_by"].default.description
        column = inspect.signature(full).parameters["search_column"]
        assert column.default.json_schema_extra["enum"] == [
            "id",
            "username",
            "role__name",
        ]
        assert set(inspect.signature(bare).parameters) == {
            "page",
            "items_per_page",
            "search",
            "search_column",
        }

    def test_no_fields_means_no_params(self):
        crud = CrudFactory(Role)
        crud.searchable_fields = None

        dep = crud.offset_paginate_params()

        assert set(inspect.signature(dep).parameters) == {"page", "items_per_page"}
        with pytest.raises(ValueError, match="conflicts with a reserved"):
            CrudFactory(
                _Reserved, facet_fields=[_Reserved.search]
            ).offset_paginate_params()

    @pytest.mark.anyio
    async def test_awaiting_the_dependency_collects_the_values(self):
        dep = UserSearchCrud.offset_paginate_params(default_order_field=User.username)
        plain = UserSearchCrud.offset_paginate_params(default_order="desc")

        given = await dep(
            page=1,
            items_per_page=20,
            search="ali",
            search_column="username",
            is_active=["true"],
            role__name=None,
            role__id=None,
            order_by="role__name",
            order="desc",
        )
        empty = await dep(page=1, items_per_page=20)
        none = await plain(page=1, items_per_page=20)

        assert given["search"] == "ali" and given["search_column"] == "username"
        assert given["filter_by"] == {"is_active": ["true"]}
        assert given["order_joins"] == [User.role]
        assert str(given["order_by"]).endswith("DESC")
        assert "search" not in empty and "search_column" not in empty
        assert empty["filter_by"] is None
        assert isinstance(empty["order_by"], UnaryExpression)
        assert str(empty["order_by"]).endswith("ASC")
        assert none["order_by"] is None
        assert inspect.signature(plain).parameters["order"].default.default == "desc"
        with pytest.raises(InvalidOrderFieldError) as exc:
            await dep(page=1, items_per_page=20, order_by="nope")
        assert exc.value.field == "nope"
        assert exc.value.valid_fields == ["role__name", "username"]

    @pytest.mark.anyio
    @_methods
    async def test_a_relation_order_field_joins_and_sorts(self, db_session, method):
        beta = await RoleCrud.create(db_session, RoleCreate(name="beta"))
        alpha = await RoleCrud.create(db_session, RoleCreate(name="alpha"))
        await _users(db_session, "u1", role=beta)
        await _users(db_session, "u2", role=alpha)
        await _users(db_session, "u3")
        dep = getattr(UserSearchCrud, f"{method}_paginate_params")()

        params = await dep(
            items_per_page=20, order_by="role__name", order="asc", **_paging(method)
        )
        result = await _paginate(
            UserSearchCrud, method, db_session, **params, schema=UserRead
        )

        usernames = [u.username for u in result.data]
        assert len(usernames) == 3
        if method == "offset":
            assert usernames.index("u2") < usernames.index("u1")

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"search": False, "filter": False, "order": False},
            {"search_fields": [], "facet_fields": [], "order_fields": []},
        ],
        ids=["flags", "empty-overrides"],
    )
    async def test_disabled_features_leave_the_response_metadata(
        self, db_session, kwargs
    ):
        await _users(db_session, "bob")

        off = await UserSearchCrud.offset_paginate(
            db_session,
            **(
                await UserSearchCrud.offset_paginate_params(**kwargs)(
                    page=1, items_per_page=10
                )
            ),
            schema=UserRead,
        )
        on = await UserSearchCrud.offset_paginate(
            db_session,
            **(
                await UserSearchCrud.offset_paginate_params()(page=1, items_per_page=10)
            ),
            schema=UserRead,
        )

        assert off.search_columns is None and off.order_columns is None
        assert off.filter_attributes is None
        assert on.search_columns == ["id", "username", "role__name"]
        assert on.order_columns == ["role__name", "username"]
        assert on.filter_attributes == {
            "is_active": [True],
            "role__name": [],
            "role__id": [],
        }

    def test_facet_keys_join_the_path(self):
        assert facet_keys([User.username, (User.role, Role.name)]) == [
            "username",
            "role__name",
        ]
        assert facet_keys([(User.role, Role.users, User.email)]) == [
            "role__users__email"
        ]
