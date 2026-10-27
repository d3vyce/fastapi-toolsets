"""Tests for the bulk operations of ``AsyncCrud``."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fastapi_toolsets.crud import CrudFactory
from fastapi_toolsets.crud import factory as crud_factory
from fastapi_toolsets.exceptions import (
    BulkLimitExceededError,
    ForbiddenError,
    NotFoundError,
)
from fastapi_toolsets.models import EventSession, ModelEvent, listens_for
from fastapi_toolsets.models.watched import _EVENT_HANDLERS, _invalidate_caches
from fastapi_toolsets.schemas import (
    BulkItemError,
    BulkRequest,
    BulkResult,
    BulkUpdateRequest,
    Response,
)

from .conftest import (
    Permission,
    Post,
    PostCreate,
    PostCrud,
    PostM2MUpdate,
    PostTagsLoadCrud,
    Role,
    RoleCrud,
    RoleUpdate,
    Tag,
    TagCreate,
    TagCrud,
    User,
    UserCrud,
    UserUpdate,
    capture_sql,
    create_role,
    create_user,
    created_tables,
)

PermissionCrud = CrudFactory(Permission)


async def _names(session: AsyncSession) -> list[str]:
    return list(await session.scalars(select(Role.name).order_by(Role.name)))


async def _forbid_admin(session: AsyncSession, role: Role) -> None:
    if role.name == "admin":
        raise ForbiddenError()
    role.name = f"{role.name}-done"


class TestSchemas:
    """Request and result models."""

    def test_requests_parse_ids_and_patch(self):
        role_id = uuid.uuid4()

        body = BulkUpdateRequest[uuid.UUID, RoleUpdate].model_validate(
            {"ids": [str(role_id)], "data": {"name": "x"}}
        )

        assert body.ids == [role_id] and body.data.name == "x"
        with pytest.raises(ValueError):
            BulkRequest[uuid.UUID](ids=[])

    def test_result_defaults_to_empty_lists(self):
        assert BulkResult[int]().model_dump() == {"succeeded": [], "failed": []}


class TestBulkApply:
    """bulk_apply(): selection, modes, limits and locking."""

    @pytest.mark.anyio
    async def test_atomic_applies_the_action_once_per_distinct_id(self, db_session):
        a, b = await create_role(db_session, "a"), await create_role(db_session, "b")
        seen: list[uuid.UUID] = []

        async def action(session: AsyncSession, role: Role) -> None:
            seen.append(role.id)
            role.name = role.name.upper()

        result = await RoleCrud.bulk_apply(db_session, [b.id, a.id, b.id], action)

        assert result == BulkResult(succeeded=[b.id, a.id])
        assert seen == [b.id, a.id]
        assert await _names(db_session) == ["A", "B"]

    @pytest.mark.anyio
    async def test_atomic_rolls_back_every_row_on_a_missing_id(self, db_session):
        role = await create_role(db_session, "a")
        missing = uuid.uuid4()

        with pytest.raises(NotFoundError, match=f"Role not found for IDs: {missing}"):
            await RoleCrud.bulk_apply(db_session, [role.id, missing], _forbid_admin)

        db_session.expire_all()
        assert await _names(db_session) == ["a"]

    @pytest.mark.anyio
    async def test_atomic_rolls_back_every_row_on_an_error(self, db_session):
        user = await create_role(db_session, "user")
        admin = await create_role(db_session, "admin")

        with pytest.raises(ForbiddenError):
            await RoleCrud.bulk_apply(db_session, [user.id, admin.id], _forbid_admin)

        db_session.expire_all()
        assert await _names(db_session) == ["admin", "user"]

    @pytest.mark.anyio
    async def test_partial_reports_failures_and_keeps_the_rest(self, db_session):
        user = await create_role(db_session, "user")
        admin = await create_role(db_session, "admin")
        missing = uuid.uuid4()

        response = await RoleCrud.bulk_apply(
            db_session,
            [admin.id, missing, user.id],
            _forbid_admin,
            mode="partial",
            return_response=True,
        )

        assert isinstance(response, Response)
        assert response.data == BulkResult(
            succeeded=[user.id],
            failed=[
                BulkItemError(
                    id=admin.id,
                    error_code="AUTH-403",
                    message="Forbidden",
                    description=ForbiddenError.api_error.desc,
                ),
                BulkItemError(
                    id=missing,
                    error_code="RES-404",
                    message="Not Found",
                    description=NotFoundError.api_error.desc,
                ),
            ],
        )
        db_session.expire_all()
        assert await _names(db_session) == ["admin", "user-done"]

    @pytest.mark.anyio
    async def test_the_limit_counts_distinct_ids(self, db_session):
        a, b = await create_role(db_session, "a"), await create_role(db_session, "b")

        with pytest.raises(BulkLimitExceededError, match="Bulk Limit Exceeded"):
            await RoleCrud.bulk_apply(
                db_session, [a.id, b.id], _forbid_admin, max_items=1
            )
        same = await RoleCrud.bulk_apply(
            db_session, [a.id, a.id], _forbid_admin, max_items=1
        )
        unlimited = await RoleCrud.bulk_apply(
            db_session, [a.id, b.id], _forbid_admin, max_items=None
        )

        assert same.succeeded == [a.id] and unlimited.succeeded == [a.id, b.id]

    @pytest.mark.anyio
    async def test_no_ids_sends_no_query(self, engine, db_session):
        with capture_sql(engine) as statements:
            result = await RoleCrud.bulk_apply(db_session, [], _forbid_admin)

        assert result == BulkResult() and statements == []

    @pytest.mark.anyio
    async def test_rows_load_in_one_query_in_key_order_and_can_be_locked(
        self, engine, db_session
    ):
        a, b = await create_role(db_session, "a"), await create_role(db_session, "b")

        with capture_sql(engine) as statements:
            await RoleCrud.bulk_apply(
                db_session, [a.id, b.id], _forbid_admin, with_for_update="nowait"
            )

        [load] = [sql for sql in statements if sql.startswith("SELECT")]
        assert "ORDER BY roles.id" in load and load.endswith("FOR UPDATE NOWAIT")

    @pytest.mark.anyio
    async def test_inside_an_open_transaction_only_the_batch_rolls_back(
        self, db_session
    ):
        admin = await create_role(db_session, "admin")

        async with db_session.begin():
            await create_role(db_session, "kept")
            with pytest.raises(ForbiddenError):
                await RoleCrud.bulk_apply(db_session, [admin.id], _forbid_admin)

        assert await _names(db_session) == ["admin", "kept"]


class TestBulkDelete:
    """bulk_delete()."""

    @pytest.mark.anyio
    async def test_deletes_the_rows(self, db_session):
        a, b = await create_role(db_session, "a"), await create_role(db_session, "b")
        await create_role(db_session, "c")

        result = await RoleCrud.bulk_delete(db_session, [a.id, b.id])

        assert result.succeeded == [a.id, b.id]
        assert await _names(db_session) == ["c"]

    @pytest.mark.anyio
    async def test_partial_reports_constraint_violations(self, db_session):
        author = await create_user(db_session, "author")
        idle = await create_user(db_session, "idle")
        await PostCrud.create(db_session, PostCreate(title="t", author_id=author.id))

        response = await UserCrud.bulk_delete(
            db_session, [author.id, idle.id], mode="partial", return_response=True
        )

        assert isinstance(response, Response) and response.data is not None
        assert response.data.succeeded == [idle.id]
        assert [(f.id, f.error_code) for f in response.data.failed] == [
            (author.id, "DB-409-FK")
        ]
        assert [u.id for u in await UserCrud.get_multi(db_session)] == [author.id]

    @pytest.mark.anyio
    async def test_partial_raises_an_unknown_constraint_violation(
        self, db_session, monkeypatch
    ):
        author = await create_user(db_session, "author")
        await PostCrud.create(db_session, PostCreate(title="t", author_id=author.id))
        monkeypatch.setattr(crud_factory, "from_integrity_error", lambda exc: None)

        with pytest.raises(Exception, match="ForeignKeyViolationError"):
            await UserCrud.bulk_delete(db_session, [author.id], mode="partial")

    @pytest.mark.anyio
    async def test_composite_keys_are_given_as_tuples(self, db_session):
        db_session.add_all(
            [Permission(subject="post", action=a) for a in ("read", "write")]
        )
        await db_session.commit()

        result = await PermissionCrud.bulk_delete(
            db_session, [("post", "write"), ("post", "nope")], mode="partial"
        )

        assert result.succeeded == [("post", "write")]
        assert [f.id for f in result.failed] == [("post", "nope")]
        assert [p.action for p in await PermissionCrud.get_multi(db_session)] == [
            "read"
        ]


class TestBulkUpdate:
    """bulk_update()."""

    @pytest.mark.anyio
    async def test_applies_the_set_fields_to_every_row(self, db_session):
        alice = await create_user(db_session, "alice")
        bob = await create_user(db_session, "bob")

        response = await UserCrud.bulk_update(
            db_session,
            [alice.id, bob.id],
            UserUpdate(is_active=False),
            return_response=True,
        )

        assert isinstance(response, Response) and response.data is not None
        assert response.data.succeeded == [alice.id, bob.id]
        db_session.expire_all()
        users = await UserCrud.get_multi(db_session, order_by=User.username)
        assert [(u.username, u.is_active) for u in users] == [
            ("alice", False),
            ("bob", False),
        ]

    @pytest.mark.anyio
    async def test_partial_reports_constraint_violations(self, db_session):
        # A rolled-back savepoint expires the rows it changed: keep plain ids.
        alice = (await create_user(db_session, "alice")).id
        bob = (await create_user(db_session, "bob")).id

        result = await UserCrud.bulk_update(
            db_session, [alice, bob], UserUpdate(email="same"), mode="partial"
        )

        assert result.succeeded == [alice]
        assert [(f.id, f.error_code) for f in result.failed] == [(bob, "DB-409-UNIQUE")]

    @pytest.mark.anyio
    async def test_m2m_ids_are_resolved_once_for_every_row(self, engine, db_session):
        author = await create_user(db_session, "author")
        posts = [
            await PostCrud.create(db_session, PostCreate(title=t, author_id=author.id))
            for t in ("a", "b")
        ]
        tag = await TagCrud.create(db_session, TagCreate(name="news"))

        with capture_sql(engine) as statements:
            await PostTagsLoadCrud.bulk_update(
                db_session, [p.id for p in posts], PostM2MUpdate(tag_ids=[tag.id])
            )

        assert sum("WHERE tags.id IN" in sql for sql in statements) == 1
        rows = await PostTagsLoadCrud.get_multi(db_session, order_by=Post.title)
        assert [[t.id for t in p.tags] for p in rows] == [[tag.id], [tag.id]]

    @pytest.mark.anyio
    async def test_the_limit_is_checked_before_resolving_m2m_ids(
        self, engine, db_session
    ):
        with capture_sql(engine) as statements:
            with pytest.raises(BulkLimitExceededError):
                await PostTagsLoadCrud.bulk_update(
                    db_session,
                    [uuid.uuid4(), uuid.uuid4()],
                    PostM2MUpdate(tag_ids=[uuid.uuid4()]),
                    max_items=1,
                )

        assert statements == []

    @pytest.mark.anyio
    async def test_an_unknown_m2m_id_rolls_back_in_any_mode(self, db_session):
        author = await create_user(db_session, "author")
        post = await PostCrud.create(
            db_session, PostCreate(title="a", author_id=author.id)
        )

        with pytest.raises(NotFoundError, match="Related Tag not found"):
            await PostTagsLoadCrud.bulk_update(
                db_session,
                [post.id],
                PostM2MUpdate(title="x", tag_ids=[uuid.uuid4()]),
                mode="partial",
            )

        assert await db_session.scalar(select(Post.title)) == "a"
        assert await db_session.scalar(select(Tag.id)) is None


class TestEvents:
    """Lifecycle events under an ``EventSession``."""

    @pytest.fixture
    async def event_session(self, engine):
        async with created_tables(engine):
            maker = async_sessionmaker(
                engine, expire_on_commit=False, class_=EventSession
            )
            async with maker() as session:
                yield session
        for key in list(_EVENT_HANDLERS):
            if key[0] is Role:
                del _EVENT_HANDLERS[key]
        _invalidate_caches()

    @pytest.mark.anyio
    async def test_only_the_applied_rows_dispatch(self, event_session):
        user = await create_role(event_session, "user")
        admin = await create_role(event_session, "admin")
        seen: list[tuple[str, Any]] = []

        @listens_for(Role, [ModelEvent.UPDATE])
        async def _on_update(obj: Role, event_type: ModelEvent, changes: Any) -> None:
            seen.append((obj.name, changes["name"]["old"]))

        await RoleCrud.bulk_apply(
            event_session, [admin.id, user.id], _forbid_admin, mode="partial"
        )

        assert seen == [("user-done", "user")]
