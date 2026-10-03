"""Tests for ``fastapi_toolsets.models``: column mixins and ``EventSession`` events."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, patch

import pytest
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import ForeignKey, String, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    object_session,
    relationship,
    selectinload,
)

import fastapi_toolsets.models.watched as _watched_module
from fastapi_toolsets.crud import CrudFactory
from fastapi_toolsets.db import Database, lock_tables, transaction
from fastapi_toolsets.models import (
    EventSession,
    ModelEvent,
    TimestampMixin,
    UpdatedAtMixin,
    UUIDMixin,
    UUIDv7Mixin,
    listens_for,
)
from fastapi_toolsets.models.watched import (
    _EVENT_HANDLERS,
    _RELOAD_TRANSACTION_ERROR_MSG,
    _SESSION_CREATES,
    _SESSION_DELETES,
    _SESSION_PRELOADED,
    _SESSION_UPDATES,
    _after_rollback,
    _collect,
    _get_watched_fields,
    _invalidate_caches,
    _upsert_changes,
)
from fastapi_toolsets.pytest import create_async_client, create_db_session

from .conftest import DATABASE_URL, capture_sql, created_tables, following, selects

_INFO_KEYS = (_SESSION_CREATES, _SESSION_DELETES, _SESSION_UPDATES, _SESSION_PRELOADED)
_CLOSED_TRANSACTION = "Can't operate on closed transaction"


class MixinBase(DeclarativeBase):
    """Declarative base of the models in this module."""


class UUIDModel(MixinBase, UUIDMixin):
    __tablename__ = "mixin_uuid_models"

    name: Mapped[str] = mapped_column(String(50))


class UUIDv7Model(MixinBase, UUIDv7Mixin):
    __tablename__ = "mixin_uuidv7_models"

    name: Mapped[str] = mapped_column(String(50))


class TimestampModel(MixinBase, TimestampMixin):
    __tablename__ = "mixin_timestamp_models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50))


class StampedModel(MixinBase, UUIDMixin, UpdatedAtMixin):
    """A UUID key and ``updated_at`` together, written through the CRUD."""

    __tablename__ = "mixin_stamped_models"

    name: Mapped[str] = mapped_column(String(50))


_events: list[dict[str, Any]] = []
_calls: list[str] = []


def _record(obj: Any, event_type: ModelEvent, changes: Any) -> None:
    """The handler every static watched model shares: it logs the event."""
    _events.append(
        {"event": event_type.value, "obj": obj, "obj_id": obj.id, "changes": changes}
    )


def _kinds() -> list[str]:
    return [e["event"] for e in _events]


def _of(kind: str) -> list[dict[str, Any]]:
    return [e for e in _events if e["event"] == kind]


class WatchedModel(MixinBase, UUIDMixin):
    """Watches ``status`` only."""

    __tablename__ = "mixin_watched_models"
    __watched_fields__ = ("status",)

    status: Mapped[str] = mapped_column(String(50))
    other: Mapped[str] = mapped_column(String(50))


listens_for(WatchedModel)(_record)


class WatchedStampedModel(MixinBase, UUIDMixin, UpdatedAtMixin):
    """A watched model whose UPDATE leaves an ``onupdate`` column expired."""

    __tablename__ = "mixin_watched_stamped_models"

    status: Mapped[str] = mapped_column(String(50))


listens_for(WatchedStampedModel, [ModelEvent.CREATE, ModelEvent.UPDATE])(_record)


class PlainModel(MixinBase, UUIDMixin):
    """No ``__watched_fields__``: every column is watched; ``nickname`` is nullable."""

    __tablename__ = "mixin_plain_models"

    name: Mapped[str] = mapped_column(String(50))
    nickname: Mapped[str | None] = mapped_column(String(50), nullable=True)


@listens_for(PlainModel)
async def _plain_handler(obj: Any, event_type: ModelEvent, changes: Any) -> None:
    """Read the columns inside the callback, where an expired object would fail."""
    _record(obj, event_type, changes)
    _events[-1]["fields"] = {"name": obj.name, "nickname": obj.nickname}


class NonWatchedModel(MixinBase):
    """No handler is registered for this model."""

    __tablename__ = "mixin_non_watched_models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    value: Mapped[str] = mapped_column(String(50))


class FlakyModel(MixinBase, UUIDMixin):
    """A raising handler sits between two healthy ones, for every event."""

    __tablename__ = "mixin_flaky_models"

    name: Mapped[str] = mapped_column(String(50))


@listens_for(FlakyModel)
async def _flaky_first(obj: Any, event_type: ModelEvent, changes: Any) -> None:
    _calls.append(f"first:{event_type.value}")


@listens_for(FlakyModel)
async def _flaky_raises(obj: Any, event_type: ModelEvent, changes: Any) -> None:
    _calls.append(f"raises:{event_type.value}")
    raise RuntimeError("middle handler intentionally failed")


@listens_for(FlakyModel)
async def _flaky_last(obj: Any, event_type: ModelEvent, changes: Any) -> None:
    _calls.append(f"last:{event_type.value}")


class DeferredFieldModel(MixinBase, UUIDMixin):
    """A deferred column, whose previous value is never loaded."""

    __tablename__ = "mixin_deferred_field_models"

    name: Mapped[str] = mapped_column(String(50))
    payload: Mapped[str] = mapped_column(String(200), deferred=True)
    nickname: Mapped[str | None] = mapped_column(String(50), nullable=True)


listens_for(DeferredFieldModel, [ModelEvent.UPDATE])(_record)


class RelTarget(MixinBase, UUIDMixin):
    __tablename__ = "mixin_rel_targets"

    name: Mapped[str] = mapped_column(String(50))


class RelOwner(MixinBase, UUIDMixin):
    """A watched model with a relationship, to check eager loads survive the commit."""

    __tablename__ = "mixin_rel_owners"

    title: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mixin_rel_targets.id"))
    target: Mapped[RelTarget] = relationship()


listens_for(RelOwner, [ModelEvent.CREATE, ModelEvent.UPDATE])(_record)


class Animal(MixinBase, UUIDMixin):
    """STI root watching ``status``; subclasses inherit its handlers and filter."""

    __tablename__ = "mixin_animals"
    __watched_fields__ = ("status",)
    __mapper_args__ = {"polymorphic_on": "kind", "polymorphic_identity": "animal"}

    kind: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50))
    other: Mapped[str] = mapped_column(String(50))


listens_for(Animal)(_record)


class Dog(Animal):
    """Inherits ``__watched_fields__`` from ``Animal``."""

    __mapper_args__ = {"polymorphic_identity": "dog"}


class Cat(Animal):
    """Overrides ``__watched_fields__``."""

    __watched_fields__ = ("other",)
    __mapper_args__ = {"polymorphic_identity": "cat"}


class ListenerModel(MixinBase, UUIDMixin):
    """Its handlers are registered by each test and removed afterwards."""

    __tablename__ = "mixin_listener_models"
    __watched_fields__ = ("status",)

    status: Mapped[str] = mapped_column(String(50))
    other: Mapped[str] = mapped_column(String(50))


@pytest.fixture(autouse=True)
def _clear_events():
    _events.clear()
    _calls.clear()


@pytest.fixture
def listener_cleanup():
    """Drop the handlers a test registered on ``ListenerModel``."""
    yield
    for key in list(_EVENT_HANDLERS):
        if key[0] is ListenerModel:
            del _EVENT_HANDLERS[key]
    _invalidate_caches()


@pytest.fixture
async def session():
    """An ``EventSession`` with ``expire_on_commit=False`` over fresh tables."""
    async with create_db_session(DATABASE_URL, MixinBase) as session:
        yield session


@pytest.fixture(params=[False, True], ids=["keep", "expire"])
async def session_any(request):
    """An ``EventSession`` under both ``expire_on_commit`` settings."""
    async with create_db_session(
        DATABASE_URL, MixinBase, expire_on_commit=request.param
    ) as session:
        yield session


@pytest.fixture
async def event_maker(engine):
    """An ``EventSession`` factory with the tables created around the test."""
    async with created_tables(engine, MixinBase.metadata):
        yield async_sessionmaker(engine, expire_on_commit=False, class_=EventSession)


async def _committed(session: AsyncSession, obj: Any) -> Any:
    """Insert *obj*, commit, and forget the CREATE event it fired."""
    session.add(obj)
    await session.commit()
    _events.clear()
    return obj


async def _seed(maker: async_sessionmaker[EventSession]) -> uuid.UUID:
    """Commit a ``WatchedModel`` in a session of its own and return its id."""
    async with maker() as session:
        obj = await _committed(session, WatchedModel(status="initial", other="x"))
        return obj.id


async def _committed_owner(session: AsyncSession) -> uuid.UUID:
    """Commit an owner with its target, then drop both from the session."""
    owner = RelOwner(title="o", target=RelTarget(name="t"))
    await _committed(session, owner)
    session.expunge_all()
    return owner.id


async def _load_with_target(session: AsyncSession, owner_id: uuid.UUID) -> RelOwner:
    """Select the owner with its target eagerly loaded."""
    query = (
        select(RelOwner)
        .where(RelOwner.id == owner_id)
        .options(selectinload(RelOwner.target))
    )
    owner = (await session.execute(query)).scalar_one()
    assert "target" not in sa_inspect(owner).unloaded
    return owner


@asynccontextmanager
async def _block(maker: Any, enter: str) -> AsyncIterator[EventSession]:
    """A session inside a top-level transaction block, entered *enter*'s way."""
    if enter == "sessionmaker.begin":
        async with maker.begin() as session:
            yield session
        return
    async with maker() as session:
        ctx = session.begin() if enter == "session.begin" else transaction(session)
        async with ctx:
            yield session


class TestColumnMixins:
    """The id and timestamp mixins are filled in by the database."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("model", "default", "version"),
        [(UUIDModel, "gen_random_uuid", 4), (UUIDv7Model, "uuidv7", 7)],
        ids=["uuid", "uuidv7"],
    )
    async def test_uuid_key_is_generated_by_the_database(
        self, session, model, default, version
    ):
        column = model.__table__.c["id"]
        a, b = model(name="a"), model(name="b")
        session.add_all([a, b])
        await session.flush()

        assert [c.name for c in model.__table__.primary_key] == ["id"]
        assert column.server_default is not None
        assert default in str(column.server_default.arg)
        assert isinstance(a.id, uuid.UUID) and a.id != b.id
        assert a.id.version == version

    @pytest.mark.anyio
    async def test_uuidv7_rows_are_inserted_in_one_statement(self, session):
        objs = [UUIDv7Model(name=f"n{i}") for i in range(10)]
        with capture_sql(session.bind) as statements:
            session.add_all(objs)
            await session.flush()

        rows = dict(
            (await session.execute(select(UUIDv7Model.id, UUIDv7Model.name))).all()
        )
        assert len([sql for sql in statements if sql.startswith("INSERT")]) == 1
        assert {o.id: o.name for o in objs} == rows

    @pytest.mark.anyio
    async def test_timestamps_are_set_on_insert_and_only_updated_at_moves(
        self, session
    ):
        created = TimestampModel.__table__.c["created_at"]
        updated = TimestampModel.__table__.c["updated_at"]
        obj = TimestampModel(name="new")
        session.add(obj)
        await session.flush()
        await session.refresh(obj)
        first_created, first_updated = obj.created_at, obj.updated_at

        obj.name = "modified"
        await session.flush()
        await session.refresh(obj)

        assert not created.nullable and not updated.nullable
        assert created.server_default is not None and updated.server_default is not None
        assert created.onupdate is None and updated.onupdate is not None
        assert first_created.tzinfo is not None and first_updated.tzinfo is not None
        assert obj.created_at == first_created
        assert obj.updated_at >= first_updated


class _StampedCreate(BaseModel):
    name: str


class _StampedUpdate(BaseModel):
    name: str | None = None


StampedCrud = CrudFactory(StampedModel)


class TestCrudWritesWithMixins:
    """Server-generated mixin columns and the post-write refresh."""

    @pytest.mark.anyio
    async def test_create_returns_server_values_without_a_refresh(self, event_maker):
        """The INSERT returns the id and the timestamp, so nothing is re-read."""
        async with event_maker() as session:
            with capture_sql(event_maker.kw["bind"]) as statements:
                obj = await StampedCrud.create(session, _StampedCreate(name="a"))

        assert selects(statements) == []
        assert obj.id is not None
        assert obj.updated_at is not None

    @pytest.mark.anyio
    async def test_update_re_reads_an_onupdate_column(self, event_maker):
        """An UPDATE expires the ``onupdate`` column, which one refresh re-reads."""
        async with event_maker() as session:
            obj = await StampedCrud.create(session, _StampedCreate(name="a"))
            first = obj.updated_at
            with capture_sql(event_maker.kw["bind"]) as statements:
                obj = await StampedCrud.update(
                    session, _StampedUpdate(name="b"), [StampedModel.id == obj.id]
                )

        assert len(selects(following(statements, "UPDATE"))) == 1
        assert obj.name == "b"
        assert obj.updated_at > first


class TestWatchedFields:
    """``__watched_fields__`` is read from the class, inherited and validated."""

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            (WatchedModel, ("status",)),
            (PlainModel, None),
            (Dog, ("status",)),
            (Cat, ("other",)),
        ],
        ids=["declared", "absent", "inherited", "overridden"],
    )
    def test_the_watch_list_is_read_from_the_class(self, model, expected):
        assert _get_watched_fields(model) == expected

    def test_a_watch_list_that_is_not_a_tuple_of_strings_raises(self):
        class Bad:
            __watched_fields__ = ["status"]

        with pytest.raises(TypeError, match="Bad.__watched_fields__ must be a tuple"):
            _get_watched_fields(Bad)


class TestCollectAndRollback:
    """The flush and rollback listeners' bookkeeping, driven without a database."""

    def test_upsert_changes_keeps_the_earliest_old_and_the_latest_new(self):
        pending: dict[int, Any] = {}
        obj = object()

        _upsert_changes(pending, obj, {"status": {"old": "a", "new": "b"}})
        _upsert_changes(
            pending,
            obj,
            {"status": {"old": "b", "new": "c"}, "role": {"old": "u", "new": "admin"}},
        )

        assert pending == {
            id(obj): (
                obj,
                {
                    "status": {"old": "a", "new": "c"},
                    "role": {"old": "u", "new": "admin"},
                },
            )
        }

    @pytest.mark.parametrize("watched", [False, True], ids=["unwatched", "watched"])
    def test_collect_records_new_and_deleted_objects_that_have_handlers(self, watched):
        new, gone = object(), object()
        session = SimpleNamespace(new=[new], deleted=[gone], dirty=[], info={})
        handlers = [lambda *a: None] if watched else []

        with (
            patch.object(_watched_module, "_get_handlers", return_value=handlers),
            patch.object(
                _watched_module, "_snapshot_column_attrs", return_value={"id": 1}
            ),
        ):
            _collect(session)

        if watched:
            assert session.info[_SESSION_CREATES] == [new]
            assert session.info[_SESSION_DELETES] == [(gone, {"id": 1})]
            assert session.info[_SESSION_PRELOADED] == {id(new): set()}
        else:
            assert session.info == {}

    @pytest.mark.parametrize("savepoint", [False, True], ids=["outer", "savepoint"])
    def test_after_rollback_clears_pending_state_unless_a_transaction_remains(
        self, savepoint
    ):
        info = {key: {} for key in _INFO_KEYS}
        session = SimpleNamespace(info=dict(info), in_transaction=lambda: savepoint)
        empty = SimpleNamespace(info={}, in_transaction=lambda: savepoint)

        _after_rollback(session)
        _after_rollback(empty)

        assert session.info == (info if savepoint else {})
        assert empty.info == {}


class TestDispatch:
    """Which events a commit dispatches, and with which changes."""

    @pytest.mark.anyio
    async def test_each_write_fires_its_own_event_once(self, session):
        obj = WatchedModel(status="initial", other="x")
        session.add(obj)
        await session.commit()
        obj.status = "updated"
        await session.commit()
        obj_id = obj.id
        await session.delete(obj)
        await session.commit()

        assert _kinds() == ["create", "update", "delete"]
        assert isinstance(_events[0]["obj_id"], uuid.UUID)
        assert _events[1]["changes"] == {"status": {"old": "initial", "new": "updated"}}
        assert _events[2]["obj_id"] == obj_id

    @pytest.mark.anyio
    async def test_changes_outside_the_watch_list_fire_nothing(self, session):
        """An unwatched field, and a model without handlers, collect nothing."""
        watched = await _committed(session, WatchedModel(status="a", other="x"))
        plain = NonWatchedModel(value="x")
        session.add(plain)
        await session.flush()

        watched.other = "changed"
        plain.value = "y"
        await session.commit()
        await session.delete(plain)
        await session.commit()

        assert _events == []
        assert not any(key in session.info for key in _INFO_KEYS)

    @pytest.mark.anyio
    async def test_a_model_without_a_watch_list_reports_every_changed_field(
        self, session
    ):
        obj = await _committed(session, PlainModel(name="n", nickname="a"))

        obj.name = "m"
        obj.nickname = "b"
        await session.commit()

        assert _kinds() == ["update"]
        assert _events[0]["changes"] == {
            "name": {"old": "n", "new": "m"},
            "nickname": {"old": "a", "new": "b"},
        }

    @pytest.mark.anyio
    async def test_flushes_within_one_transaction_merge_into_one_update(self, session):
        obj = await _committed(session, WatchedModel(status="initial", other="x"))

        obj.status = "intermediate"
        await session.flush()
        obj.status = "final"
        await session.commit()

        assert _kinds() == ["update"]
        assert _events[0]["changes"]["status"] == {"old": "initial", "new": "final"}

    @pytest.mark.anyio
    async def test_an_update_before_the_first_commit_is_part_of_the_create(
        self, session
    ):
        obj = WatchedModel(status="initial", other="x")
        session.add(obj)
        await session.flush()
        obj.status = "updated-before-commit"
        await session.commit()

        assert _kinds() == ["create"]

    @pytest.mark.anyio
    async def test_a_rollback_discards_the_pending_events(self, session):
        obj = await _committed(session, WatchedModel(status="a", other="x"))

        obj.status = "changed"
        await session.flush()
        await session.rollback()
        await session.commit()

        assert _events == []

    @pytest.mark.anyio
    async def test_an_object_created_and_deleted_in_one_transaction_fires_nothing(
        self, session
    ):
        """CREATE still fires for the object that survives the transaction."""
        survivor = WatchedModel(status="kept", other="x")
        transient = WatchedModel(status="gone", other="y")
        session.add_all([survivor, transient])
        await session.flush()
        await session.delete(transient)
        await session.commit()

        assert [(e["event"], e["obj_id"]) for e in _events] == [("create", survivor.id)]

    @pytest.mark.anyio
    async def test_an_object_updated_then_deleted_fires_only_delete(self, session):
        """A new object committed alongside still fires CREATE."""
        existing = await _committed(session, WatchedModel(status="old", other="x"))

        existing.status = "changed"
        await session.flush()
        await session.delete(existing)
        session.add(WatchedModel(status="new", other="y"))
        await session.commit()

        assert _kinds() == ["create", "delete"]


class TestHandlerFailures:
    """A failure in one handler, in the reload or in a snapshot is logged and isolated."""

    @pytest.mark.anyio
    @pytest.mark.parametrize("event", ["create", "update", "delete"])
    async def test_a_raising_handler_is_logged_and_the_others_still_run(
        self, session, event
    ):
        obj = FlakyModel(name="x")
        session.add(obj)
        with patch.object(_watched_module._logger, "error") as mock_error:
            if event != "create":
                await session.commit()
                mock_error.reset_mock()
                _calls.clear()
            if event == "update":
                obj.name = "changed"
            elif event == "delete":
                await session.delete(obj)
            await session.commit()

        assert _calls == [f"first:{event}", f"raises:{event}", f"last:{event}"]
        mock_error.assert_called_once()

    @pytest.mark.anyio
    async def test_a_failed_snapshot_restore_skips_only_that_object(self, session):
        doomed = WatchedModel(status="a", other="x")
        healthy = WatchedModel(status="b", other="y")
        session.add_all([doomed, healthy])
        await session.commit()
        _events.clear()
        await session.delete(doomed)
        await session.delete(healthy)
        real_set = _watched_module._sa_set_committed_value

        def fail_for_doomed(obj: Any, key: str, value: Any) -> None:
            if obj is doomed:
                raise RuntimeError("snapshot restore intentionally failed")
            real_set(obj, key, value)

        with (
            patch.object(_watched_module, "_sa_set_committed_value", fail_for_doomed),
            patch.object(_watched_module._logger, "error") as mock_error,
        ):
            await session.commit()

        assert [(e["event"], e["obj_id"]) for e in _events] == [("delete", healthy.id)]
        mock_error.assert_called_once()

    @pytest.mark.anyio
    async def test_a_failed_reload_is_logged_and_create_still_fires(self, session):
        obj = WatchedModel(status="active", other="x")
        session.add(obj)
        await session.flush()
        session.sync_session.expire(obj, ["other"])  # leave something to re-read

        async def failing_batch_reload(*args: Any) -> None:
            raise RuntimeError("reload failed")

        with (
            patch.object(_watched_module, "_batch_reload", failing_batch_reload),
            patch.object(_watched_module._logger, "error") as mock_error,
        ):
            await session.commit()

        mock_error.assert_called_once()
        assert _kinds() == ["create"]

    @pytest.mark.anyio
    async def test_a_row_deleted_before_the_reload_still_fires_create(self, session):
        """Another transaction removes the row right after the commit (#341)."""
        keep = WatchedModel(status="a", other="x")
        doomed = WatchedModel(status="b", other="y")
        session.add_all([keep, doomed])
        await session.flush()
        doomed_id = doomed.id
        session.sync_session.expire(doomed, ["other"])  # leave something to re-read
        real_batch_reload = _watched_module._batch_reload

        async def racing_batch_reload(*args: Any) -> None:
            async with async_sessionmaker(session.bind)() as other:
                await other.delete(await other.get_one(WatchedModel, doomed_id))
                await other.commit()
            await real_batch_reload(*args)

        with (
            patch.object(_watched_module, "_batch_reload", racing_batch_reload),
            patch.object(_watched_module._logger, "error") as mock_error,
        ):
            await session.commit()

        mock_error.assert_not_called()
        assert {e["obj_id"] for e in _of("create")} == {keep.id, doomed_id}


class TestInheritance:
    """Handlers and ``__watched_fields__`` of an STI root apply to its subclasses."""

    @pytest.mark.anyio
    async def test_a_subclass_dispatches_the_parent_handlers_once(self, session):
        dog = Dog(status="s", other="o")
        session.add(dog)
        await session.commit()
        await session.delete(dog)
        await session.commit()
        transient = Dog(status="s", other="o")
        session.add(transient)
        await session.flush()
        await session.delete(transient)
        await session.commit()

        assert [(e["event"], type(e["obj"]).__name__) for e in _events] == [
            ("create", "Dog"),
            ("delete", "Dog"),
        ]

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("model", "field", "fires"),
        [
            (Dog, "status", True),
            (Dog, "other", False),
            (Cat, "other", True),
            (Cat, "status", False),
        ],
        ids=[
            "inherited-watched",
            "inherited-ignored",
            "overridden-watched",
            "overridden-ignored",
        ],
    )
    async def test_the_watch_list_is_inherited_unless_overridden(
        self, session, model, field, fires
    ):
        obj = await _committed(session, model(status="s", other="o"))

        setattr(obj, field, "changed")
        await session.commit()

        assert [list(e["changes"]) for e in _events] == ([[field]] if fires else [])


class TestAttributeAccess:
    """Columns are readable inside every callback, even when the commit expired them."""

    @pytest.mark.anyio
    @pytest.mark.parametrize("nickname", [None, "nick"], ids=["null", "set"])
    async def test_fields_are_readable_in_every_callback(self, session_any, nickname):
        flipped = "nick" if nickname is None else None
        obj = PlainModel(name="hello", nickname=nickname)
        session_any.add(obj)
        await session_any.commit()
        obj.nickname = flipped
        await session_any.commit()
        await session_any.delete(obj)
        await session_any.commit()

        assert [(e["event"], e["fields"]) for e in _events] == [
            ("create", {"name": "hello", "nickname": nickname}),
            ("update", {"name": "hello", "nickname": flipped}),
            ("delete", {"name": "hello", "nickname": flipped}),
        ]
        assert all(isinstance(e["obj_id"], uuid.UUID) for e in _events)

    @pytest.mark.anyio
    async def test_fields_are_readable_after_a_commit_inside_a_begin_block(
        self, session_any
    ):
        async with session_any.begin():
            session_any.add(PlainModel(name="test", nickname=None))
            await session_any.commit()

        assert [(e["event"], e["fields"]) for e in _events] == [
            ("create", {"name": "test", "nickname": None})
        ]


@pytest.mark.usefixtures("listener_cleanup")
class TestListensFor:
    """Registration through the decorator: event lists, defaults, handler results."""

    @pytest.mark.anyio
    async def test_handlers_run_for_the_listed_events_or_all_by_default(self, session):
        seen: list[str] = []

        @listens_for(ListenerModel)
        async def _on_any(obj: Any, event_type: ModelEvent, changes: Any) -> None:
            seen.append(f"any:{event_type.value}")

        @listens_for(ListenerModel, [ModelEvent.CREATE, ModelEvent.UPDATE])
        async def _on_write(obj: Any, event_type: ModelEvent, changes: Any) -> None:
            seen.append(f"write:{event_type.value}")

        @listens_for(ListenerModel, [ModelEvent.UPDATE])
        async def _on_update(obj: Any, event_type: ModelEvent, changes: Any) -> None:
            seen.append(f"update:{changes['status']['new']}")

        obj = ListenerModel(status="initial", other="x")
        session.add(obj)
        await session.commit()
        obj.status = "updated"
        await session.commit()
        await session.delete(obj)
        await session.commit()

        assert seen == [
            "any:create",
            "write:create",
            "any:update",
            "write:update",
            "update:updated",
            "any:delete",
        ]

    @pytest.mark.anyio
    @pytest.mark.parametrize("kind", ["sync", "coroutine", "task"])
    async def test_a_handler_may_return_nothing_or_any_awaitable(self, session, kind):
        seen: list[str] = []

        def _handler(obj: Any, event_type: ModelEvent, changes: Any) -> Any:
            async def _work() -> None:
                seen.append(event_type.value)

            if kind == "sync":
                seen.append(event_type.value)
                return None
            return _work() if kind == "coroutine" else asyncio.ensure_future(_work())

        listens_for(ListenerModel, [ModelEvent.CREATE])(_handler)
        session.add(ListenerModel(status="a", other="x"))
        await session.commit()

        assert seen == ["create"]

    @pytest.mark.anyio
    async def test_a_callback_write_is_left_for_the_caller_to_commit(self, session):
        """A callback's ``session.add`` stays pending until the caller commits."""

        @listens_for(ListenerModel, [ModelEvent.CREATE])
        async def _on_create(obj: Any, event_type: ModelEvent, changes: Any) -> None:
            if obj.status == "seed":
                owner = object_session(obj)
                assert owner is not None
                owner.add(ListenerModel(status="from-callback", other="y"))

        session.add(ListenerModel(status="seed", other="x"))
        await session.commit()
        pending = len(session.new)
        await session.commit()

        query = select(ListenerModel).where(ListenerModel.status == "from-callback")
        assert pending == 1
        assert len((await session.execute(query)).scalars().all()) == 1


class TestTransactions:
    """Savepoints, ``begin()`` blocks, ``lock_tables`` and the ``Database`` dependency."""

    @pytest.mark.anyio
    async def test_savepoints_dispatch_only_on_the_outer_commit(self, session):
        """Released savepoints wait for the commit; a rolled-back one is dropped."""
        existing = await _committed(session, WatchedModel(status="initial", other="x"))
        await session.connection()  # autobegin, so the blocks nest savepoints

        async with transaction(session):
            session.add(WatchedModel(status="first", other="x"))
        async with session.begin_nested():
            existing.status = "updated"
        with pytest.raises(ValueError, match="rollback"):
            async with transaction(session):
                session.add(WatchedModel(status="doomed", other="y"))
                await session.flush()
                raise ValueError("rollback this savepoint")
        assert _events == []

        await session.commit()

        assert _kinds() == ["create", "update"]
        assert _events[1]["changes"]["status"] == {"old": "initial", "new": "updated"}

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "enter", ["session.begin", "sessionmaker.begin", "transaction"]
    )
    async def test_a_top_level_block_dispatches_on_exit(self, event_maker, enter):
        async with _block(event_maker, enter) as session:
            session.add(WatchedModel(status="active", other="x"))

        assert _kinds() == ["create"]

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "explicit_commit", [False, True], ids=["on-exit", "explicit-commit"]
    )
    @pytest.mark.parametrize("event", ["create", "update", "delete"])
    async def test_a_begin_block_dispatches_each_write_once(
        self, event_maker, caplog, event, explicit_commit
    ):
        """An explicit commit inside the block neither raises nor dispatches twice."""
        obj_id = None if event == "create" else await _seed(event_maker)

        async with event_maker() as session:
            with caplog.at_level(logging.ERROR):
                async with session.begin():
                    if event == "create":
                        session.add(WatchedModel(status="active", other="x"))
                    elif event == "update":
                        obj = await session.get_one(WatchedModel, obj_id)
                        obj.status = "updated"
                    else:
                        await session.delete(
                            await session.get_one(WatchedModel, obj_id)
                        )
                    if explicit_commit:
                        await session.commit()
            # The session is still usable once the block exits.
            rows = (await session.execute(select(WatchedModel))).scalars().all()

        assert _CLOSED_TRANSACTION not in caplog.text
        assert len(rows) == (0 if event == "delete" else 1)
        assert _kinds() == [event]
        if event == "update":
            assert _events[0]["changes"] == {
                "status": {"old": "initial", "new": "updated"}
            }

    @pytest.mark.anyio
    async def test_a_commit_inside_a_savepoint_block(self, event_maker, caplog):
        async with event_maker() as session:
            await session.connection()  # autobegin, as Database._open() does
            with caplog.at_level(logging.ERROR):
                async with transaction(session):  # nested -> savepoint
                    session.add(WatchedModel(status="savepoint", other="x"))
                    await session.commit()

        assert _CLOSED_TRANSACTION not in caplog.text
        assert _kinds() == ["create"]

    @pytest.mark.anyio
    async def test_a_block_that_raises_dispatches_nothing(self, event_maker):
        async with event_maker() as session:
            with pytest.raises(RuntimeError, match="boom"):
                async with session.begin():
                    session.add(WatchedModel(status="active", other="x"))
                    raise RuntimeError("boom")

        assert _events == []

    @pytest.mark.anyio
    async def test_lock_tables_dispatches_on_exit(self, event_maker):
        async with lock_tables(event_maker, [WatchedModel]) as session:
            session.add(WatchedModel(status="locked", other="x"))

        assert _kinds() == ["create"]

    @pytest.mark.anyio
    @pytest.mark.parametrize("event", ["create", "update"])
    async def test_the_database_dependency_dispatches_after_the_request(
        self, event_maker, event
    ):
        db = Database(engine=event_maker.kw["bind"], session_class=EventSession)
        app = FastAPI()

        @app.post("/watched")
        async def create_watched(session: AsyncSession = Depends(db)) -> None:
            session.add(WatchedModel(status="from-api", other="x"))

        @app.put("/watched/{item_id}")
        async def update_watched(
            item_id: uuid.UUID, session: AsyncSession = Depends(db)
        ) -> None:
            obj = await session.get_one(WatchedModel, item_id)
            obj.status = "updated-via-api"

        async with db.session() as seed:  # commits the open transaction on exit
            obj = WatchedModel(status="initial", other="x")
            seed.add(obj)
            await seed.flush()
            obj_id = obj.id
        _events.clear()
        async with create_async_client(app) as client:
            if event == "create":
                response = await client.post("/watched")
            else:
                response = await client.put(f"/watched/{obj_id}")

        assert response.status_code == 200
        assert _kinds() == [event]


class TestEagerLoads:
    """The post-commit reload keeps what was loaded and leaves the session clean."""

    @pytest.mark.anyio
    async def test_a_relation_loaded_before_the_commit_survives_it(self, session_any):
        target = RelTarget(name="t")
        session_any.add(target)
        await session_any.flush()
        owner = RelOwner(title="o", target_id=target.id)
        session_any.add(owner)
        await session_any.flush()
        owner = await _load_with_target(session_any, owner.id)

        await session_any.commit()

        assert "target" not in sa_inspect(owner).unloaded
        assert owner.target.name == "t"
        assert _kinds() == ["create"]

    @pytest.mark.anyio
    @pytest.mark.parametrize("via", ["commit", "begin"])
    async def test_a_relation_survives_when_the_commit_itself_flushes(
        self, session, via
    ):
        """The dirty object is only collected by the commit's own flush."""
        owner_id = await _committed_owner(session)

        if via == "commit":
            owner = await _load_with_target(session, owner_id)
            owner.title = "changed"
            await session.commit()
        else:
            async with session.begin():
                owner = await _load_with_target(session, owner_id)
                owner.title = "changed"

        assert "target" not in sa_inspect(owner).unloaded
        assert _kinds() == ["update"]

    @pytest.mark.anyio
    async def test_only_what_was_loaded_is_restored(self, session_any):
        """A relation assigned on create stays loaded; one never loaded stays unloaded."""
        target = RelTarget(name="t")
        session_any.add(target)
        await session_any.flush()
        assigned = RelOwner(title="a", target=target)
        session_any.add(assigned)
        await session_any.commit()
        assigned_loaded = "target" not in sa_inspect(assigned).unloaded
        by_id = RelOwner(title="b", target_id=target.id)
        session_any.add(by_id)
        await session_any.commit()

        assert assigned_loaded
        assert "target" in sa_inspect(by_id).unloaded
        assert _kinds() == ["create", "create"]

    @pytest.mark.anyio
    async def test_the_commit_leaves_no_transaction_open(self, session_any):
        """The reload's transaction is closed without expiring what it loaded."""
        expire = session_any.sync_session.expire_on_commit
        owner = RelOwner(title="o", target=RelTarget(name="t"))
        session_any.add(owner)

        await session_any.commit()

        assert session_any.in_transaction() is False
        assert "target" not in sa_inspect(owner).unloaded
        assert session_any.sync_session.expire_on_commit is expire
        async with session_any.begin():  # right after a commit, must not raise
            owner.title = "changed"
        assert _kinds() == ["create", "update"]


class TestPostCommitReload:
    """The reload before dispatch only runs for objects the commit left stale."""

    @pytest.mark.anyio
    async def test_reload_only_when_the_commit_expired_the_object(self, session_any):
        """RETURNING keeps the object current; expiring it costs one SELECT."""
        expire = session_any.sync_session.expire_on_commit
        obj = WatchedModel(status="active", other="x")
        session_any.add(obj)

        with capture_sql(session_any.bind) as statements:
            await session_any.commit()

        assert len(selects(statements)) == (1 if expire else 0)
        assert session_any.in_transaction() is False
        assert [e["obj_id"] for e in _of("create")] == [obj.id]

    @pytest.mark.anyio
    async def test_reload_when_an_onupdate_column_is_expired(self, session):
        """An UPDATE leaves the onupdate column expired, so it is re-read."""
        obj = WatchedStampedModel(status="new")
        session.add(obj)
        await session.commit()
        obj.status = "done"

        with capture_sql(session.bind) as statements:
            await session.commit()

        assert len(selects(statements)) == 1
        assert obj.updated_at is not None
        assert _kinds() == ["create", "update"]

    @pytest.mark.anyio
    async def test_a_failure_closing_the_reload_transaction_is_logged(self, session):
        """The error is logged, ``expire_on_commit`` restored, and dispatch goes on."""
        session.sync_session.expire_on_commit = True
        real_commit = AsyncSession.commit
        commits: list[int] = []

        async def flaky_commit(self: Any) -> None:
            commits.append(1)
            if len(commits) == 2:  # the reload's own commit
                raise RuntimeError("close failed")
            await real_commit(self)

        session.add(WatchedModel(status="active", other="x"))
        with (
            patch.object(AsyncSession, "commit", flaky_commit),
            patch.object(_watched_module._logger, "error") as mock_error,
        ):
            await session.commit()

        mock_error.assert_called_once_with(_RELOAD_TRANSACTION_ERROR_MSG, exc_info=ANY)
        assert session.sync_session.expire_on_commit is True
        assert _kinds() == ["create"]


class TestNonDispatchingSessions:
    """The flush listener collects for an ``EventSession`` only."""

    @pytest.mark.anyio
    async def test_a_plain_session_collects_nothing(self, event_maker):
        maker = async_sessionmaker(event_maker.kw["bind"], class_=AsyncSession)
        async with maker() as session:
            obj = WatchedModel(status="initial", other="x")
            session.add(obj)
            await session.commit()
            obj.status = "updated"
            await session.commit()
            await session.delete(obj)
            await session.commit()

            assert not any(key in session.info for key in _INFO_KEYS)

        assert _events == []

    @pytest.mark.anyio
    async def test_an_event_session_collects_on_flush_and_drains_on_commit(
        self, session
    ):
        session.add(WatchedModel(status="active", other="x"))
        await session.flush()
        collected = len(session.info[_SESSION_CREATES])
        before_commit = list(_events)

        await session.commit()

        assert collected == 1 and before_commit == []
        assert _SESSION_CREATES not in session.info
        assert _kinds() == ["create"]


class TestDeferredFields:
    """A change whose previous value was never loaded still fires UPDATE."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("field", "load_first", "expected"),
        [
            ("payload", False, {"new": "after"}),
            ("payload", True, {"old": "before", "new": "after"}),
            ("nickname", False, {"old": None, "new": "after"}),
        ],
        ids=["deferred-unloaded", "deferred-loaded", "null-loaded"],
    )
    async def test_old_is_reported_only_when_it_was_loaded(
        self, session, field, load_first, expected
    ):
        obj = await _committed(
            session, DeferredFieldModel(name="n", payload="before", nickname=None)
        )
        session.expunge_all()  # the deferred column comes back unloaded
        obj = await session.get_one(DeferredFieldModel, obj.id)
        if load_first:
            await session.refresh(obj, [field])

        setattr(obj, field, "after")
        await session.commit()

        assert [e["changes"] for e in _events] == [{field: expected}]
