"""Tests for the CRUD operations of ``AsyncCrud``: reads, writes and loading."""

import uuid
from typing import Any, Generic, TypeVar

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, aliased, joinedload, selectinload

from fastapi_toolsets.crud import CrudFactory
from fastapi_toolsets.crud.factory import AsyncCrud
from fastapi_toolsets.exceptions import NotFoundError
from fastapi_toolsets.schemas import Response

from .conftest import (
    Post,
    PostCreate,
    PostCrud,
    PostDeferredCrud,
    PostM2MCreate,
    PostM2MCrud,
    PostM2MUpdate,
    PostTagsLoadCrud,
    PostWithTagsRead,
    Role,
    RoleCreate,
    RoleCrud,
    RoleUpdate,
    Tag,
    TagCreate,
    TagCrud,
    Transfer,
    TransferCreate,
    TransferCrud,
    User,
    UserCreate,
    UserCrud,
    UserCursorCrud,
    UserRead,
    UserRoleLoadCrud,
    UserUpdate,
    UserWithRoleRead,
    capture_sql,
    create_role,
    create_user,
    following,
    selects,
)

T = TypeVar("T", bound=DeclarativeBase)


async def _tags(session: AsyncSession, *names: str) -> list[Tag]:
    return [await TagCrud.create(session, TagCreate(name=name)) for name in names]


async def _post(session: AsyncSession, author: User, tags: list[Tag]) -> Post:
    return await PostM2MCrud.create(
        session,
        PostM2MCreate(title="Hello", author_id=author.id, tag_ids=[t.id for t in tags]),
    )


class TestFactory:
    """CrudFactory and direct subclassing configure the class variables."""

    def test_factory_builds_a_bound_class_per_call(self):
        options = [selectinload(User.role)]
        crud = CrudFactory(User, default_load_options=options)
        plain = CrudFactory(User)

        assert issubclass(crud, AsyncCrud)
        assert crud.model is User and "User" in crud.__name__
        assert crud is not plain
        assert crud.default_load_options == options
        assert plain.default_load_options is None
        assert plain.facet_fields is None and plain.cursor_column is None

    def test_base_class_is_inherited_and_the_key_still_injected(self):
        class CustomBase(AsyncCrud[T], Generic[T]):
            @classmethod
            def custom(cls) -> str:
                return cls.model.__name__

        crud = CrudFactory(User, base_class=CustomBase)

        assert issubclass(crud, CustomBase)
        assert crud.custom() == "User"
        assert crud.searchable_fields == [User.id]

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            (None, [User.id]),
            ([User.username], [User.id, User.username]),
            ([User.id, User.username], [User.id, User.username]),
        ],
        ids=["none", "prepended", "already-first"],
    )
    def test_subclass_prepends_the_key_to_searchable_fields(self, declared, expected):
        namespace: dict[str, Any] = {"model": User}
        if declared is not None:
            namespace["searchable_fields"] = declared
        crud = type("UserCrudDirect", (AsyncCrud,), namespace)

        assert crud.searchable_fields == expected

    def test_abstract_subclass_without_model_is_left_alone(self):
        class AbstractCrud(AsyncCrud[User]):
            pass

        assert AbstractCrud.searchable_fields is None

    def test_resolve_load_options(self):
        default = [selectinload(User.role)]
        given = [joinedload(User.role)]
        crud = CrudFactory(User, default_load_options=default)

        assert crud._resolve_load_options(None) is default
        assert crud._resolve_load_options([]) == []
        assert crud._resolve_load_options(given) is given
        assert CrudFactory(User)._resolve_load_options(None) is None

    def test_resolve_search_and_order_columns(self):
        crud = CrudFactory(
            User,
            searchable_fields=[User.username, (User.role, Role.name)],
            order_fields=[User.username, User.email, (User.role, Role.name)],
        )

        assert crud._resolve_search_columns(None) == ["id", "username", "role__name"]
        assert crud._resolve_search_columns([User.email]) == ["email"]
        assert crud._resolve_search_columns([]) is None
        assert crud._resolve_order_columns(None) == ["email", "role__name", "username"]
        assert crud._resolve_order_columns([User.email]) == ["email"]
        assert crud._resolve_order_columns([]) is None
        assert CrudFactory(Role)._resolve_order_columns(None) is None


class TestCreate:
    """create() inserts, resolves M2M ids and returns the model or a Response."""

    @pytest.mark.anyio
    async def test_create_returns_the_instance_or_a_response(self, db_session):
        role = await create_role(db_session, "admin")
        user = await create_user(db_session, "alice", role_id=role.id)
        wrapped = await UserCrud.create(
            db_session, UserCreate(username="bob", email="b@test.com"), schema=UserRead
        )

        assert user.id is not None and user.role_id == role.id and user.is_active
        assert isinstance(wrapped, Response) and wrapped.data is not None
        assert wrapped.data.username == "bob"
        assert not hasattr(wrapped.data, "email")

    @pytest.mark.anyio
    @pytest.mark.parametrize("count", [0, 1, 2])
    async def test_create_with_m2m_ids_links_the_rows(self, db_session, count):
        author = await create_user(db_session, "author")
        tags = await _tags(db_session, *[f"t{i}" for i in range(count)])

        post = await _post(db_session, author, tags)

        loaded = await PostCrud.get(
            db_session, [Post.id == post.id], load_options=[selectinload(Post.tags)]
        )
        assert {t.name for t in loaded.tags} == {t.name for t in tags}

    @pytest.mark.anyio
    async def test_create_without_m2m_ids_uses_the_default(self, db_session):
        author = await create_user(db_session, "author")

        post = await PostM2MCrud.create(
            db_session, PostM2MCreate(title="Hello", author_id=author.id)
        )

        loaded = await PostTagsLoadCrud.get(db_session, [Post.id == post.id])
        assert loaded.tags == []

    @pytest.mark.anyio
    async def test_create_with_an_unknown_m2m_id_raises(self, db_session):
        author = await create_user(db_session, "author")

        with pytest.raises(NotFoundError, match="Related Tag not found"):
            await PostM2MCrud.create(
                db_session,
                PostM2MCreate(title="x", author_id=author.id, tag_ids=[uuid.uuid4()]),
            )

    @pytest.mark.anyio
    async def test_crud_without_m2m_fields_passes_the_schema_through(self, db_session):
        author = await create_user(db_session, "author")

        post = await PostCrud.create(
            db_session, PostCreate(title="plain", author_id=author.id)
        )
        updated = await PostCrud.update(
            db_session, PostM2MUpdate(title="renamed"), [Post.id == post.id]
        )

        assert updated.title == "renamed"
        assert await PostCrud._resolve_m2m(db_session, PostM2MUpdate()) == {}
        assert await PostM2MCrud._resolve_m2m(db_session, PostM2MUpdate()) == {
            "tags": []
        }


class TestRead:
    """get(), get_or_none(), first() and get_multi()."""

    @pytest.mark.anyio
    async def test_get_returns_one_row_or_raises(self, db_session):
        user = await create_user(db_session, "alice")
        await create_user(db_session, "bob")

        found = await UserCrud.get(
            db_session, [User.username == "alice", User.is_active.is_(True)]
        )
        wrapped = await UserCrud.get(db_session, [User.id == user.id], schema=UserRead)

        assert found.id == user.id
        assert isinstance(wrapped, Response) and wrapped.data is not None
        assert wrapped.data.id == user.id
        with pytest.raises(NotFoundError):
            await UserCrud.get(db_session, [User.username == "nobody"])

    @pytest.mark.anyio
    async def test_get_or_none_returns_none_when_missing(self, db_session):
        user = await create_user(db_session, "alice")

        wrapped = await UserCrud.get_or_none(
            db_session, [User.id == user.id], schema=UserRead
        )

        assert (await UserCrud.get_or_none(db_session, [User.id == user.id])) is user
        assert wrapped is not None and wrapped.data is not None
        assert wrapped.data.id == user.id
        assert await UserCrud.get_or_none(db_session, [User.username == "x"]) is None
        assert (
            await UserCrud.get_or_none(
                db_session, [User.username == "x"], schema=UserRead
            )
            is None
        )

    @pytest.mark.anyio
    async def test_first_returns_one_entity_with_a_limit(self, engine, db_session):
        author = await create_user(db_session, "author")
        tags = await _tags(db_session, "python", "fastapi", "sqlalchemy")
        posts = [
            await _post(db_session, author, tags),
            await _post(db_session, author, tags[:1]),
        ]
        tag_counts = {posts[0].id: 3, posts[1].id: 1}
        lowest, highest = sorted(tag_counts)

        with capture_sql(engine) as statements:
            post = await PostCrud.first(
                db_session,
                [Post.title == "Hello"],
                load_options=[joinedload(Post.tags)],
                schema=PostWithTagsRead,
            )

        assert isinstance(post, Response) and post.data is not None
        assert len(post.data.tags) in (1, 3)
        assert len(statements) == 1 and "LIMIT" in statements[0]
        assert "ORDER BY" not in statements[0]
        for order_by, expected in ((Post.id, lowest), (Post.id.desc(), highest)):
            with capture_sql(engine) as statements:
                found = await PostCrud.first(
                    db_session,
                    [Post.title == "Hello"],
                    load_options=[joinedload(Post.tags)],
                    order_by=order_by,
                )
            assert found is not None and found.id == expected
            assert len(found.tags) == tag_counts[expected]
            assert "ORDER BY" in statements[0]
        assert await PostCrud.first(db_session, [Post.title == "none"]) is None
        assert (
            await PostCrud.first(
                db_session, [Post.title == "none"], schema=PostWithTagsRead
            )
            is None
        )
        assert await PostCrud.first(db_session) is not None

    @pytest.mark.anyio
    async def test_get_multi_filters_orders_and_slices(self, db_session):
        for name in ("charlie", "alice", "bob"):
            await create_user(db_session, name)

        everyone = await UserCrud.get_multi(db_session, order_by=User.username)
        page = await UserCrud.get_multi(
            db_session, order_by=User.username, limit=1, offset=1
        )
        active = await UserCrud.get_multi(
            db_session, filters=[User.username.like("%li%")]
        )
        rest = await UserCrud.get_multi(db_session, order_by=User.username, offset=1)

        assert [u.username for u in everyone] == ["alice", "bob", "charlie"]
        assert [u.username for u in page] == ["bob"]
        assert [u.username for u in rest] == ["bob", "charlie"]
        assert {u.username for u in active} == {"alice", "charlie"}

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "read",
        ["get_multi_limit", "get_multi_offset", "offset", "offset_joined"],
    )
    async def test_pages_break_ties_on_the_primary_key(self, engine, db_session, read):
        users = [await create_user(db_session, f"u{i}") for i in range(4)]
        by_key = sorted(u.id for u in users)
        joins: list[Any] = [(Role, User.role_id == Role.id)]

        async def page(n: int) -> list[Any]:
            if read == "get_multi_limit":
                rows = await UserCrud.get_multi(
                    db_session, order_by=User.is_active, limit=1, offset=n
                )
            elif read == "get_multi_offset":
                rows = (
                    await UserCrud.get_multi(
                        db_session, order_by=User.is_active, offset=n
                    )
                )[:1]
            else:
                result = await UserCrud.offset_paginate(
                    db_session,
                    order_by=User.is_active,
                    joins=joins if read == "offset_joined" else None,
                    outer_join=True,
                    page=n + 1,
                    items_per_page=1,
                    include_total=False,
                    include_facets=False,
                    schema=UserRead,
                )
                rows = result.data
            return [u.id for u in rows]

        with capture_sql(engine) as statements:
            walked = [uid for n in range(len(users)) for uid in await page(n)]

        assert walked == by_key
        paging = [s for s in statements if "LIMIT" in s or "OFFSET" in s]
        assert len(paging) == len(users)
        assert all("users.id" in s.split("ORDER BY")[1] for s in paging)

    @pytest.mark.anyio
    @pytest.mark.parametrize("outer", [False, True], ids=["inner", "outer"])
    async def test_raw_joins_filter_rows_on_every_read(self, db_session, outer):
        role = await create_role(db_session, "member")
        for i in range(3):
            await create_user(db_session, f"u{i}", role_id=role.id)
        await create_user(db_session, "norole")
        joins: list[Any] = [(Role, User.role_id == Role.id)]
        expected = 4 if outer else 3

        rows = await UserCrud.get_multi(db_session, joins=joins, outer_join=outer)
        total = await UserCrud.count(db_session, joins=joins, outer_join=outer)
        offset = await UserCrud.offset_paginate(
            db_session, joins=joins, outer_join=outer, schema=UserRead
        )
        cursor = await UserCursorCrud.cursor_paginate(
            db_session, joins=joins, outer_join=outer, schema=UserRead
        )
        first = await UserCrud.first(
            db_session, [User.username == "norole"], joins=joins, outer_join=outer
        )
        found = await UserCrud.get_or_none(
            db_session, [User.username == "norole"], joins=joins, outer_join=outer
        )

        assert len(rows) == total == offset.pagination.total_count == expected
        assert len(offset.data) == len(cursor.data) == expected
        assert (first is not None) is outer and (found is not None) is outer
        assert (
            await UserCrud.exists(
                db_session, [User.username == "norole"], joins=joins, outer_join=outer
            )
            is outer
        )

    @pytest.mark.anyio
    async def test_aliased_joins_can_join_one_table_twice(self, db_session):
        alice = await create_user(db_session, "alice")
        bob = await create_user(db_session, "bob")
        for amount, sender, receiver in (("50", alice, bob), ("75", bob, alice)):
            await TransferCrud.create(
                db_session,
                TransferCreate(
                    amount=amount, sender_id=sender.id, receiver_id=receiver.id
                ),
            )
        sender, receiver = aliased(User), aliased(User)
        joins: list[Any] = [
            (sender, Transfer.sender_id == sender.id),
            (receiver, Transfer.receiver_id == receiver.id),
        ]

        rows = await TransferCrud.get_multi(
            db_session,
            joins=joins,
            filters=[sender.username == "alice", receiver.username == "bob"],
        )
        none = await TransferCrud.get_multi(
            db_session, joins=joins, filters=[sender.username == "nobody"]
        )
        total = await TransferCrud.count(
            db_session, joins=joins[:1], filters=[sender.username == "bob"]
        )

        assert [t.amount for t in rows] == ["50"] and none == []
        assert total == 1

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("mode", "clause"),
        [
            (True, "FOR UPDATE"),
            ("nowait", "FOR UPDATE NOWAIT"),
            ("skip_locked", "SKIP LOCKED"),
        ],
    )
    @pytest.mark.parametrize(
        "method", ["get", "get_or_none", "first", "get_multi", "update"]
    )
    async def test_with_for_update_locks_the_rows(
        self, engine, db_session, method, mode, clause
    ):
        role = await create_role(db_session, "admin")

        with capture_sql(engine) as statements:
            if method == "get_multi":
                result = await RoleCrud.get_multi(
                    db_session, filters=[Role.id == role.id], with_for_update=mode
                )
            elif method == "update":
                result = await RoleCrud.update(
                    db_session,
                    RoleUpdate(name="owner"),
                    [Role.id == role.id],
                    with_for_update=mode,
                )
            else:
                result = await getattr(RoleCrud, method)(
                    db_session, [Role.id == role.id], with_for_update=mode
                )

        assert result is not None
        assert clause in selects(statements)[0]


class TestWrite:
    """update(), upsert(), delete(), count() and exists()."""

    @pytest.mark.anyio
    async def test_update_sets_only_the_given_fields(self, db_session):
        user = await create_user(db_session, "alice")

        updated = await UserCrud.update(
            db_session, UserUpdate(username="alicia"), [User.id == user.id]
        )
        wrapped = await UserCrud.update(
            db_session,
            UserUpdate(is_active=False, email=None),
            [User.id == user.id],
            exclude_none=True,
            schema=UserRead,
        )

        assert updated.username == "alicia" and updated.email == "alice@test.com"
        assert isinstance(wrapped, Response) and wrapped.data is not None
        assert wrapped.data.is_active is False
        assert user.email == "alice@test.com"
        with pytest.raises(NotFoundError):
            await UserCrud.update(
                db_session, UserUpdate(username="x"), [User.username == "nobody"]
            )

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("fields", "swap_tags", "expected"),
        [
            ({}, True, {"c"}),
            ({"tag_ids": []}, False, set()),
            ({"title": "renamed"}, False, {"a", "b"}),
            ({"title": "both"}, True, {"c"}),
        ],
        ids=["replace", "clear", "scalar-only", "scalar-and-m2m"],
    )
    async def test_update_replaces_m2m_links_only_when_given(
        self, db_session, fields, swap_tags, expected
    ):
        author = await create_user(db_session, "author")
        a, b, c = await _tags(db_session, "a", "b", "c")
        post = await _post(db_session, author, [a, b])
        update = PostM2MUpdate(**fields, **({"tag_ids": [c.id]} if swap_tags else {}))

        updated = await PostTagsLoadCrud.update(
            db_session, update, [Post.id == post.id]
        )

        assert {t.name for t in updated.tags} == expected
        assert updated.title == fields.get("title", "Hello")

    @pytest.mark.anyio
    async def test_update_with_an_unknown_m2m_id_raises(self, db_session):
        author = await create_user(db_session, "author")
        post = await _post(db_session, author, [])

        with pytest.raises(NotFoundError, match="Related Tag not found"):
            await PostM2MCrud.update(
                db_session, PostM2MUpdate(tag_ids=[uuid.uuid4()]), [Post.id == post.id]
            )

    @pytest.mark.anyio
    async def test_upsert_inserts_updates_or_does_nothing(self, db_session):
        created = await RoleCrud.upsert(db_session, RoleCreate(name="admin"), ["name"])
        assert created is not None
        updated = await RoleCrud.upsert(
            db_session,
            RoleCreate(id=created.id, name="admin"),
            ["id"],
            set_=RoleUpdate(name="owner"),
        )
        kept = await RoleCrud.upsert(db_session, RoleCreate(name="owner"), ["name"])
        unchanged = await RoleCrud.upsert(
            db_session,
            RoleCreate(id=created.id, name="owner"),
            ["id"],
            set_=RoleUpdate(name="x"),
            where=Role.name == "nobody",
        )

        assert updated is not None and updated.id == created.id
        assert kept is not None and kept.id == created.id
        assert unchanged is not None and unchanged.id == created.id
        assert await db_session.scalar(select(Role.name)) == "owner"
        assert await RoleCrud.count(db_session) == 1

    @pytest.mark.anyio
    async def test_delete_removes_rows_and_m2m_links_only(self, db_session):
        author = await create_user(db_session, "author")
        tag, *_ = await _tags(db_session, "shared")
        post = await _post(db_session, author, [tag])
        other = await _post(db_session, author, [tag])
        keep = await create_role(db_session, "keep")
        await create_role(db_session, "gone-1")
        await create_role(db_session, "gone-2")

        await PostM2MCrud.delete(db_session, [Post.id == post.id])
        response = await RoleCrud.delete(
            db_session, [Role.name.like("gone-%")], return_response=True
        )

        assert isinstance(response, Response) and response.data is None
        assert await PostCrud.get_or_none(db_session, [Post.id == post.id]) is None
        assert await TagCrud.exists(db_session, [Tag.id == tag.id])
        assert [r.id for r in await RoleCrud.get_multi(db_session)] == [keep.id]
        assert await PostCrud.count(db_session, [Post.id == other.id]) == 1
        links = await db_session.execute(
            select(Post.id).join(Post.tags).where(Tag.id == tag.id)
        )
        assert [row[0] for row in links] == [other.id]

    @pytest.mark.anyio
    async def test_count_and_exists_honour_filters(self, db_session):
        await create_user(db_session, "alice")
        await create_user(db_session, "bob")

        assert await UserCrud.count(db_session) == 2
        assert await UserCrud.count(db_session, [User.username == "alice"]) == 1
        assert await UserCrud.exists(db_session, [User.username == "alice"])
        assert not await UserCrud.exists(db_session, [User.username == "nobody"])


class TestLoadOptions:
    """default_load_options on reads and the reload after a write."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "method", ["get", "get_multi", "first", "offset_paginate", "cursor_paginate"]
    )
    async def test_default_load_options_apply_to_every_read(self, db_session, method):
        role = await create_role(db_session, "admin")
        user = await create_user(db_session, "alice", role_id=role.id)
        crud = CrudFactory(
            User, default_load_options=[selectinload(User.role)], cursor_column=User.id
        )

        if method == "get":
            found = await crud.get(db_session, [User.id == user.id])
        elif method == "first":
            found = await crud.first(db_session)
        elif method == "get_multi":
            found = (await crud.get_multi(db_session))[0]
        else:
            page = await getattr(crud, method)(db_session, schema=UserWithRoleRead)
            found = page.data[0]

        assert found is not None
        assert found.role is not None and found.role.name == "admin"

    @pytest.mark.anyio
    async def test_explicit_options_replace_the_defaults(self, engine, db_session):
        author = await create_user(db_session, "author")
        post = await _post(db_session, author, [])

        with capture_sql(engine) as statements:
            await PostTagsLoadCrud.get(
                db_session, [Post.id == post.id], load_options=[]
            )
        bare = statements
        with capture_sql(engine) as statements:
            await PostTagsLoadCrud.get(db_session, [Post.id == post.id])

        assert len(bare) == 1
        assert any("JOIN tags" in sql for sql in statements)

    @pytest.mark.anyio
    async def test_create_and_update_reload_the_graph_once(
        self, engine, db_session_any
    ):
        role = await create_role(db_session_any, "admin")

        with capture_sql(engine) as statements:
            user = await UserRoleLoadCrud.create(
                db_session_any,
                UserCreate(username="alice", email="a@test.com", role_id=role.id),
            )
        creates = selects(statements)
        with capture_sql(engine) as statements:
            updated = await UserRoleLoadCrud.update(
                db_session_any, UserUpdate(username="alicia"), [User.id == user.id]
            )
        before = statements[: statements.index(following(statements, "UPDATE")[0])]
        after = following(statements, "UPDATE")

        assert [sql for sql in creates if "FROM users" in sql] == [creates[0]]
        assert any("FROM roles" in sql for sql in creates)
        assert not any("FROM roles" in sql for sql in before)
        assert len([sql for sql in after if "FROM users" in sql]) == 1
        assert user.role is not None and user.role.name == "admin"
        assert updated is user and updated.username == "alicia"
        assert updated.role is not None and updated.role.name == "admin"

    @pytest.mark.anyio
    async def test_reload_keeps_relationships_the_options_leave_out(
        self, db_session_any
    ):
        author = await create_user(db_session_any, "author")
        tag, *_ = await _tags(db_session_any, "python")

        created = await PostDeferredCrud.create(
            db_session_any,
            PostM2MCreate(title="Hello", author_id=author.id, tag_ids=[tag.id]),
        )
        names = [t.name for t in created.tags]
        updated = await PostDeferredCrud.update(
            db_session_any, PostM2MUpdate(tag_ids=[]), [Post.id == created.id]
        )

        assert names == ["python"]
        assert updated.tags == []

    @pytest.mark.anyio
    async def test_reload_does_not_expire_other_loaded_objects(self, db_session):
        role = await create_role(db_session, "admin")
        role = await RoleCrud.get(
            db_session, [Role.id == role.id], load_options=[selectinload(Role.users)]
        )

        await UserRoleLoadCrud.create(
            db_session, UserCreate(username="a", email="a@test.com", role_id=role.id)
        )

        assert role.users == []

    @pytest.mark.anyio
    async def test_without_options_the_row_is_re_read_only_when_expired(
        self, engine, db_session_any
    ):
        expires = db_session_any.sync_session.expire_on_commit

        with capture_sql(engine) as statements:
            role = await RoleCrud.create(db_session_any, RoleCreate(name="admin"))
        creates = selects(statements)
        with capture_sql(engine) as statements:
            updated = await RoleCrud.update(
                db_session_any, RoleUpdate(name="owner"), [Role.id == role.id]
            )
        updates = selects(following(statements, "UPDATE"))

        assert len(creates) == len(updates) == int(expires)
        assert updated is role and updated.name == "owner"
