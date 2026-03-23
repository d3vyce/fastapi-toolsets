"""Tests for fastapi_toolsets.models mixins."""

import asyncio
import uuid
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import fastapi_toolsets.models.watched as _watched_module
from fastapi_toolsets.models import (
    CreatedAtMixin,
    ModelEvent,
    TimestampMixin,
    UpdatedAtMixin,
    UUIDMixin,
    UUIDv7Mixin,
    WatchedFieldsMixin,
    watch,
)
from fastapi_toolsets.models.watched import (
    _SESSION_CREATES,
    _SESSION_DELETES,
    _SESSION_PENDING_NEW,
    _SESSION_UPDATES,
    _after_commit,
    _after_flush,
    _after_flush_postexec,
    _after_rollback,
    _task_error_handler,
    _upsert_changes,
)

from .conftest import DATABASE_URL


class MixinBase(DeclarativeBase):
    pass


class UUIDModel(MixinBase, UUIDMixin):
    __tablename__ = "mixin_uuid_models"

    name: Mapped[str] = mapped_column(String(50))


class UpdatedAtModel(MixinBase, UpdatedAtMixin):
    __tablename__ = "mixin_updated_at_models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50))


class CreatedAtModel(MixinBase, CreatedAtMixin):
    __tablename__ = "mixin_created_at_models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50))


class TimestampModel(MixinBase, TimestampMixin):
    __tablename__ = "mixin_timestamp_models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50))


class UUIDv7Model(MixinBase, UUIDv7Mixin):
    __tablename__ = "mixin_uuidv7_models"

    name: Mapped[str] = mapped_column(String(50))


class FullMixinModel(MixinBase, UUIDMixin, UpdatedAtMixin):
    __tablename__ = "mixin_full_models"

    name: Mapped[str] = mapped_column(String(50))


_test_events: list[dict] = []


@watch("status")
class WatchedModel(MixinBase, UUIDMixin, WatchedFieldsMixin):
    __tablename__ = "mixin_watched_models"

    status: Mapped[str] = mapped_column(String(50))
    other: Mapped[str] = mapped_column(String(50))

    async def on_create(self) -> None:
        _test_events.append({"event": "create", "obj_id": self.id})

    async def on_delete(self) -> None:
        _test_events.append({"event": "delete", "obj_id": self.id})

    async def on_update(self, changes: dict) -> None:
        _test_events.append({"event": "update", "obj_id": self.id, "changes": changes})


@watch("value")
class OnEventModel(MixinBase, UUIDMixin, WatchedFieldsMixin):
    """Model that only overrides on_event to test the catch-all path."""

    __tablename__ = "mixin_on_event_models"

    value: Mapped[str] = mapped_column(String(50))

    async def on_event(self, event: ModelEvent, changes: dict | None = None) -> None:
        _test_events.append({"event": event, "obj_id": self.id, "changes": changes})


class WatchAllModel(MixinBase, UUIDMixin, WatchedFieldsMixin):
    """Model without @watch — watches all mapped fields by default."""

    __tablename__ = "mixin_watch_all_models"

    status: Mapped[str] = mapped_column(String(50))
    other: Mapped[str] = mapped_column(String(50))

    async def on_update(self, changes: dict) -> None:
        _test_events.append({"event": "update", "obj_id": self.id, "changes": changes})


class FailingCallbackModel(MixinBase, UUIDMixin, WatchedFieldsMixin):
    """Model whose on_create always raises to test exception logging."""

    __tablename__ = "mixin_failing_callback_models"

    name: Mapped[str] = mapped_column(String(50))

    async def on_create(self) -> None:
        raise RuntimeError("callback intentionally failed")


class NonWatchedModel(MixinBase):
    __tablename__ = "mixin_non_watched_models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    value: Mapped[str] = mapped_column(String(50))


_poly_events: list[dict] = []


class PolyAnimal(MixinBase, UUIDMixin, WatchedFieldsMixin):
    """Base class for STI polymorphism tests."""

    __tablename__ = "mixin_poly_animals"
    __mapper_args__ = {"polymorphic_on": "kind", "polymorphic_identity": "animal"}

    kind: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(50))

    async def on_create(self) -> None:
        _poly_events.append(
            {"event": "create", "type": type(self).__name__, "obj_id": self.id}
        )

    async def on_delete(self) -> None:
        _poly_events.append(
            {"event": "delete", "type": type(self).__name__, "obj_id": self.id}
        )


class PolyDog(PolyAnimal):
    """STI subclass — shares the same table as PolyAnimal."""

    __mapper_args__ = {"polymorphic_identity": "dog"}


_attr_access_events: list[dict] = []


class AttrAccessModel(MixinBase, UUIDMixin, WatchedFieldsMixin):
    """Model used to verify that self attributes are accessible in every callback."""

    __tablename__ = "mixin_attr_access_models"

    name: Mapped[str] = mapped_column(String(50))

    async def on_create(self) -> None:
        _attr_access_events.append(
            {"event": "create", "id": self.id, "name": self.name}
        )

    async def on_delete(self) -> None:
        _attr_access_events.append(
            {"event": "delete", "id": self.id, "name": self.name}
        )

    async def on_update(self, changes: dict) -> None:
        _attr_access_events.append(
            {"event": "update", "id": self.id, "name": self.name}
        )


_sync_events: list[dict] = []
_future_events: list[str] = []


@watch("status")
class SyncCallbackModel(MixinBase, UUIDMixin, WatchedFieldsMixin):
    """Model with plain (sync) on_* callbacks."""

    __tablename__ = "mixin_sync_callback_models"

    status: Mapped[str] = mapped_column(String(50))

    def on_create(self) -> None:
        _sync_events.append({"event": "create", "obj_id": self.id})

    def on_delete(self) -> None:
        _sync_events.append({"event": "delete", "obj_id": self.id})

    def on_update(self, changes: dict) -> None:
        _sync_events.append({"event": "update", "changes": changes})


class FutureCallbackModel(MixinBase, UUIDMixin, WatchedFieldsMixin):
    """Model whose on_create returns an asyncio.Task (awaitable, not a coroutine)."""

    __tablename__ = "mixin_future_callback_models"

    name: Mapped[str] = mapped_column(String(50))

    def on_create(self) -> "asyncio.Task[None]":
        async def _work() -> None:
            _future_events.append("created")

        return asyncio.ensure_future(_work())


@pytest.fixture(scope="function")
async def mixin_session():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(MixinBase.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()

    try:
        yield session
    finally:
        await session.close()
        async with engine.begin() as conn:
            await conn.run_sync(MixinBase.metadata.drop_all)
        await engine.dispose()


@pytest.fixture(scope="function")
async def mixin_session_expire():
    """Session with expire_on_commit=True (the default) to exercise attribute access after commit."""
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(MixinBase.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=True)
    session = session_factory()

    try:
        yield session
    finally:
        await session.close()
        async with engine.begin() as conn:
            await conn.run_sync(MixinBase.metadata.drop_all)
        await engine.dispose()


class TestUUIDMixin:
    @pytest.mark.anyio
    async def test_uuid_generated_by_db(self, mixin_session):
        """UUID is generated server-side and populated after flush."""
        obj = UUIDModel(name="test")
        mixin_session.add(obj)
        await mixin_session.flush()

        assert obj.id is not None
        assert isinstance(obj.id, uuid.UUID)

    @pytest.mark.anyio
    async def test_uuid_is_primary_key(self):
        """UUIDMixin adds id as primary key column."""
        pk_cols = [c.name for c in UUIDModel.__table__.primary_key]
        assert pk_cols == ["id"]

    @pytest.mark.anyio
    async def test_each_row_gets_unique_uuid(self, mixin_session):
        """Each inserted row gets a distinct UUID."""
        a = UUIDModel(name="a")
        b = UUIDModel(name="b")
        mixin_session.add_all([a, b])
        await mixin_session.flush()

        assert a.id != b.id

    @pytest.mark.anyio
    async def test_uuid_server_default_set(self):
        """Column has gen_random_uuid() as server default."""
        col = UUIDModel.__table__.c["id"]
        assert col.server_default is not None
        assert "gen_random_uuid" in str(col.server_default.arg)


class TestUpdatedAtMixin:
    @pytest.mark.anyio
    async def test_updated_at_set_on_insert(self, mixin_session):
        """updated_at is populated after insert."""
        obj = UpdatedAtModel(name="initial")
        mixin_session.add(obj)
        await mixin_session.flush()
        await mixin_session.refresh(obj)

        assert obj.updated_at is not None
        assert obj.updated_at.tzinfo is not None

    @pytest.mark.anyio
    async def test_updated_at_changes_on_update(self, mixin_session):
        """updated_at is updated when the row is modified."""
        obj = UpdatedAtModel(name="initial")
        mixin_session.add(obj)
        await mixin_session.flush()
        await mixin_session.refresh(obj)

        original_ts = obj.updated_at

        obj.name = "modified"
        await mixin_session.flush()
        await mixin_session.refresh(obj)

        assert obj.updated_at >= original_ts

    @pytest.mark.anyio
    async def test_updated_at_column_is_not_nullable(self):
        """updated_at column is non-nullable."""
        col = UpdatedAtModel.__table__.c["updated_at"]
        assert not col.nullable

    @pytest.mark.anyio
    async def test_updated_at_has_server_default(self):
        """updated_at column has a server-side default."""
        col = UpdatedAtModel.__table__.c["updated_at"]
        assert col.server_default is not None

    @pytest.mark.anyio
    async def test_updated_at_has_onupdate(self):
        """updated_at column has an onupdate clause."""
        col = UpdatedAtModel.__table__.c["updated_at"]
        assert col.onupdate is not None


class TestCreatedAtMixin:
    @pytest.mark.anyio
    async def test_created_at_set_on_insert(self, mixin_session):
        """created_at is populated after insert."""
        obj = CreatedAtModel(name="new")
        mixin_session.add(obj)
        await mixin_session.flush()
        await mixin_session.refresh(obj)

        assert obj.created_at is not None
        assert obj.created_at.tzinfo is not None

    @pytest.mark.anyio
    async def test_created_at_not_changed_on_update(self, mixin_session):
        """created_at is not modified when the row is updated."""
        obj = CreatedAtModel(name="original")
        mixin_session.add(obj)
        await mixin_session.flush()
        await mixin_session.refresh(obj)

        original_ts = obj.created_at

        obj.name = "updated"
        await mixin_session.flush()
        await mixin_session.refresh(obj)

        assert obj.created_at == original_ts

    @pytest.mark.anyio
    async def test_created_at_column_is_not_nullable(self):
        """created_at column is non-nullable."""
        col = CreatedAtModel.__table__.c["created_at"]
        assert not col.nullable

    @pytest.mark.anyio
    async def test_created_at_has_no_onupdate(self):
        """created_at column has no onupdate clause."""
        col = CreatedAtModel.__table__.c["created_at"]
        assert col.onupdate is None


class TestTimestampMixin:
    @pytest.mark.anyio
    async def test_both_columns_set_on_insert(self, mixin_session):
        """created_at and updated_at are both populated after insert."""
        obj = TimestampModel(name="new")
        mixin_session.add(obj)
        await mixin_session.flush()
        await mixin_session.refresh(obj)

        assert obj.created_at is not None
        assert obj.updated_at is not None

    @pytest.mark.anyio
    async def test_created_at_stable_updated_at_changes_on_update(self, mixin_session):
        """On update: created_at stays the same, updated_at advances."""
        obj = TimestampModel(name="original")
        mixin_session.add(obj)
        await mixin_session.flush()
        await mixin_session.refresh(obj)

        original_created = obj.created_at
        original_updated = obj.updated_at

        obj.name = "modified"
        await mixin_session.flush()
        await mixin_session.refresh(obj)

        assert obj.created_at == original_created
        assert obj.updated_at >= original_updated

    @pytest.mark.anyio
    async def test_timestamp_mixin_has_both_columns(self):
        """TimestampModel exposes both created_at and updated_at columns."""
        col_names = {c.name for c in TimestampModel.__table__.columns}
        assert "created_at" in col_names
        assert "updated_at" in col_names


class TestUUIDv7Mixin:
    @pytest.mark.anyio
    async def test_uuid7_generated_by_db(self, mixin_session):
        """UUIDv7 is generated server-side and populated after flush."""
        obj = UUIDv7Model(name="test")
        mixin_session.add(obj)
        await mixin_session.flush()

        assert obj.id is not None
        assert isinstance(obj.id, uuid.UUID)

    @pytest.mark.anyio
    async def test_uuid7_is_primary_key(self):
        """UUIDv7Mixin adds id as primary key column."""
        pk_cols = [c.name for c in UUIDv7Model.__table__.primary_key]
        assert pk_cols == ["id"]

    @pytest.mark.anyio
    async def test_each_row_gets_unique_uuid7(self, mixin_session):
        """Each inserted row gets a distinct UUIDv7."""
        a = UUIDv7Model(name="a")
        b = UUIDv7Model(name="b")
        mixin_session.add_all([a, b])
        await mixin_session.flush()

        assert a.id != b.id

    @pytest.mark.anyio
    async def test_uuid7_version(self, mixin_session):
        """Generated UUIDs have version 7."""
        obj = UUIDv7Model(name="test")
        mixin_session.add(obj)
        await mixin_session.flush()

        assert obj.id.version == 7

    @pytest.mark.anyio
    async def test_uuid7_server_default_set(self):
        """Column has uuidv7() as server default."""
        col = UUIDv7Model.__table__.c["id"]
        assert col.server_default is not None
        assert "uuidv7" in str(col.server_default.arg)


class TestFullMixinModel:
    @pytest.mark.anyio
    async def test_combined_mixins_work_together(self, mixin_session):
        """UUIDMixin and UpdatedAtMixin can be combined on the same model."""
        obj = FullMixinModel(name="combined")
        mixin_session.add(obj)
        await mixin_session.flush()
        await mixin_session.refresh(obj)

        assert isinstance(obj.id, uuid.UUID)
        assert obj.updated_at is not None
        assert obj.updated_at.tzinfo is not None


class TestWatchDecorator:
    def test_registers_specific_fields(self):
        """@watch("field") stores the field list in _WATCHED_FIELDS."""
        assert _watched_module._WATCHED_FIELDS.get(WatchedModel) == ["status"]

    def test_no_decorator_not_in_watched_fields(self):
        """A model without @watch has no entry in _WATCHED_FIELDS (watch all)."""
        assert WatchAllModel not in _watched_module._WATCHED_FIELDS

    def test_preserves_class_identity(self):
        """watch returns the same class unchanged."""

        class _Dummy(WatchedFieldsMixin):
            pass

        result = watch("x")(_Dummy)
        assert result is _Dummy
        del _watched_module._WATCHED_FIELDS[_Dummy]

    def test_raises_when_no_fields_given(self):
        """@watch() with no field names raises ValueError."""
        with pytest.raises(ValueError, match="@watch requires at least one field name"):
            watch()


class TestUpsertChanges:
    def test_inserts_new_entry(self):
        """New key is inserted with the full changes dict."""
        pending: dict = {}
        obj = object()
        changes = {"status": {"old": None, "new": "active"}}
        _upsert_changes(pending, obj, changes)
        assert pending[id(obj)] == (obj, changes)

    def test_merges_existing_field_keeps_old_updates_new(self):
        """When the field already exists, old is preserved and new is overwritten."""
        obj = object()
        pending = {
            id(obj): (obj, {"status": {"old": "initial", "new": "intermediate"}})
        }
        _upsert_changes(
            pending, obj, {"status": {"old": "intermediate", "new": "final"}}
        )
        assert pending[id(obj)][1]["status"] == {"old": "initial", "new": "final"}

    def test_adds_new_field_to_existing_entry(self):
        """A previously unseen field is added alongside existing ones."""
        obj = object()
        pending = {id(obj): (obj, {"status": {"old": "a", "new": "b"}})}
        _upsert_changes(pending, obj, {"role": {"old": "user", "new": "admin"}})
        fields = pending[id(obj)][1]
        assert fields["status"] == {"old": "a", "new": "b"}
        assert fields["role"] == {"old": "user", "new": "admin"}


class TestAfterFlush:
    def test_does_nothing_with_empty_session(self):
        """_after_flush writes nothing to session.info when all collections are empty."""
        session = SimpleNamespace(new=[], deleted=[], dirty=[], info={})
        _after_flush(session, None)
        assert session.info == {}

    def test_captures_new_watched_mixin_objects(self):
        """New WatchedFieldsMixin instances are added to _SESSION_PENDING_NEW."""
        obj = WatchedFieldsMixin()
        session = SimpleNamespace(new=[obj], deleted=[], dirty=[], info={})
        _after_flush(session, None)
        assert session.info[_SESSION_PENDING_NEW] == [obj]

    def test_ignores_new_non_mixin_objects(self):
        """New objects that are not WatchedFieldsMixin are not captured."""
        obj = object()
        session = SimpleNamespace(new=[obj], deleted=[], dirty=[], info={})
        _after_flush(session, None)
        assert _SESSION_PENDING_NEW not in session.info

    def test_captures_deleted_watched_mixin_objects(self):
        """Deleted WatchedFieldsMixin instances are added to _SESSION_DELETES."""
        obj = WatchedFieldsMixin()
        session = SimpleNamespace(new=[], deleted=[obj], dirty=[], info={})
        _after_flush(session, None)
        assert session.info[_SESSION_DELETES] == [obj]

    def test_ignores_deleted_non_mixin_objects(self):
        """Deleted objects that are not WatchedFieldsMixin are not captured."""
        obj = object()
        session = SimpleNamespace(new=[], deleted=[obj], dirty=[], info={})
        _after_flush(session, None)
        assert _SESSION_DELETES not in session.info


class TestAfterFlushPostexec:
    def test_does_nothing_when_no_pending_new(self):
        """_after_flush_postexec does nothing when _SESSION_PENDING_NEW is absent."""
        session = SimpleNamespace(info={})
        _after_flush_postexec(session, None)
        assert _SESSION_CREATES not in session.info

    def test_moves_pending_new_to_creates(self):
        """Objects from _SESSION_PENDING_NEW are moved to _SESSION_CREATES."""
        obj = object()
        session = SimpleNamespace(info={_SESSION_PENDING_NEW: [obj]})
        _after_flush_postexec(session, None)
        assert _SESSION_PENDING_NEW not in session.info
        assert session.info[_SESSION_CREATES] == [obj]

    def test_extends_existing_creates(self):
        """Multiple flushes accumulate in _SESSION_CREATES."""
        a, b = object(), object()
        session = SimpleNamespace(
            info={_SESSION_PENDING_NEW: [b], _SESSION_CREATES: [a]}
        )
        _after_flush_postexec(session, None)
        assert session.info[_SESSION_CREATES] == [a, b]


class TestAfterRollback:
    def test_clears_all_session_info_keys(self):
        """_after_rollback removes all four tracking keys from session.info."""
        session = SimpleNamespace(
            info={
                _SESSION_PENDING_NEW: [object()],
                _SESSION_CREATES: [object()],
                _SESSION_DELETES: [object()],
                _SESSION_UPDATES: {1: ("obj", {"f": {"old": "a", "new": "b"}})},
            }
        )
        _after_rollback(session)
        assert _SESSION_PENDING_NEW not in session.info
        assert _SESSION_CREATES not in session.info
        assert _SESSION_DELETES not in session.info
        assert _SESSION_UPDATES not in session.info

    def test_tolerates_missing_keys(self):
        """_after_rollback does not raise when session.info has no pending data."""
        session = SimpleNamespace(info={})
        _after_rollback(session)  # must not raise


class TestTaskErrorHandler:
    @pytest.mark.anyio
    async def test_logs_exception_from_failed_task(self):
        """_task_error_handler calls _logger.error when the task raised."""

        async def failing() -> None:
            raise ValueError("boom")

        task = asyncio.create_task(failing())
        await asyncio.sleep(0)

        with patch.object(_watched_module._logger, "error") as mock_error:
            _task_error_handler(task)
            mock_error.assert_called_once()

    @pytest.mark.anyio
    async def test_ignores_cancelled_task(self):
        """_task_error_handler does not log when the task was cancelled."""

        async def slow() -> None:
            await asyncio.sleep(100)

        task = asyncio.create_task(slow())
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        with patch.object(_watched_module._logger, "error") as mock_error:
            _task_error_handler(task)
            mock_error.assert_not_called()


class TestAfterCommitNoLoop:
    def test_no_task_scheduled_when_no_running_loop(self):
        """_after_commit silently returns when called outside an async context."""
        called = []
        obj = SimpleNamespace(on_create=lambda: called.append("create"))
        session = SimpleNamespace(info={_SESSION_CREATES: [obj]})
        _after_commit(session)
        assert called == []

    def test_returns_early_when_all_pending_empty(self):
        """_after_commit does nothing when all pending lists are empty."""
        session = SimpleNamespace(info={})
        _after_commit(session)  # should not raise


class TestWatchedFieldsMixin:
    @pytest.fixture(autouse=True)
    def clear_events(self):
        _test_events.clear()
        yield
        _test_events.clear()

    # --- on_create ---

    @pytest.mark.anyio
    async def test_on_create_fires_after_insert(self, mixin_session):
        """on_create is called after INSERT commit."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)

        creates = [e for e in _test_events if e["event"] == "create"]
        assert len(creates) == 1

    @pytest.mark.anyio
    async def test_on_create_server_defaults_populated(self, mixin_session):
        """id (server default via RETURNING) is available inside on_create."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)

        creates = [e for e in _test_events if e["event"] == "create"]
        assert creates[0]["obj_id"] is not None
        assert isinstance(creates[0]["obj_id"], uuid.UUID)

    @pytest.mark.anyio
    async def test_on_create_not_fired_on_update(self, mixin_session):
        """on_create is NOT called when an existing row is updated."""
        obj = WatchedModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)
        _test_events.clear()

        obj.status = "updated"
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert not any(e["event"] == "create" for e in _test_events)

    # --- on_delete ---

    @pytest.mark.anyio
    async def test_on_delete_fires_after_delete(self, mixin_session):
        """on_delete is called after DELETE commit."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)
        saved_id = obj.id
        _test_events.clear()

        await mixin_session.delete(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)

        deletes = [e for e in _test_events if e["event"] == "delete"]
        assert len(deletes) == 1
        assert deletes[0]["obj_id"] == saved_id

    @pytest.mark.anyio
    async def test_on_delete_not_fired_on_insert(self, mixin_session):
        """on_delete is NOT called when a new row is inserted."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert not any(e["event"] == "delete" for e in _test_events)

    # --- on_update ---

    @pytest.mark.anyio
    async def test_on_update_fires_on_update(self, mixin_session):
        """on_update reports the correct before/after values on UPDATE."""
        obj = WatchedModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)
        _test_events.clear()

        obj.status = "updated"
        await mixin_session.commit()
        await asyncio.sleep(0)

        changes_events = [e for e in _test_events if e["event"] == "update"]
        assert len(changes_events) == 1
        assert changes_events[0]["changes"]["status"] == {
            "old": "initial",
            "new": "updated",
        }

    @pytest.mark.anyio
    async def test_on_update_not_fired_on_insert(self, mixin_session):
        """on_update is NOT called on INSERT (on_create handles that)."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert not any(e["event"] == "update" for e in _test_events)

    @pytest.mark.anyio
    async def test_unwatched_field_update_no_callback(self, mixin_session):
        """Changing a field not listed in @update does not fire on_update."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)
        _test_events.clear()

        obj.other = "changed"
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert _test_events == []

    @pytest.mark.anyio
    async def test_multiple_flushes_merge_earliest_old_latest_new(self, mixin_session):
        """Two flushes in one transaction produce a single callback with earliest old / latest new."""
        obj = WatchedModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)
        _test_events.clear()

        obj.status = "intermediate"
        await mixin_session.flush()

        obj.status = "final"
        await mixin_session.commit()
        await asyncio.sleep(0)

        changes_events = [e for e in _test_events if e["event"] == "update"]
        assert len(changes_events) == 1
        assert changes_events[0]["changes"]["status"] == {
            "old": "initial",
            "new": "final",
        }

    @pytest.mark.anyio
    async def test_rollback_suppresses_all_callbacks(self, mixin_session):
        """No callbacks are fired when the transaction is rolled back."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)
        _test_events.clear()

        obj.status = "changed"
        await mixin_session.flush()
        await mixin_session.rollback()
        await asyncio.sleep(0)

        assert _test_events == []

    @pytest.mark.anyio
    async def test_callback_exception_is_logged(self, mixin_session):
        """Exceptions raised inside on_create are logged, not propagated."""
        obj = FailingCallbackModel(name="boom")
        mixin_session.add(obj)
        with patch.object(_watched_module._logger, "error") as mock_error:
            await mixin_session.commit()
            await asyncio.sleep(0)
            mock_error.assert_called_once()

    @pytest.mark.anyio
    async def test_non_watched_model_no_callback(self, mixin_session):
        """Dirty objects whose type is not a WatchedFieldsMixin are skipped."""
        nw = NonWatchedModel(value="x")
        mixin_session.add(nw)
        await mixin_session.flush()
        nw.value = "y"
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert _test_events == []

    # --- on_event (catch-all) ---

    @pytest.mark.anyio
    async def test_on_event_receives_create(self, mixin_session):
        """on_event is called with ModelEvent.CREATE on INSERT when only on_event is overridden."""
        obj = OnEventModel(value="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)

        creates = [e for e in _test_events if e["event"] == ModelEvent.CREATE]
        assert len(creates) == 1
        assert creates[0]["changes"] is None

    @pytest.mark.anyio
    async def test_on_event_receives_delete(self, mixin_session):
        """on_event is called with ModelEvent.DELETE on DELETE when only on_event is overridden."""
        obj = OnEventModel(value="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)
        _test_events.clear()

        await mixin_session.delete(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)

        deletes = [e for e in _test_events if e["event"] == ModelEvent.DELETE]
        assert len(deletes) == 1
        assert deletes[0]["changes"] is None

    @pytest.mark.anyio
    async def test_on_event_receives_field_change(self, mixin_session):
        """on_event is called with ModelEvent.UPDATE on UPDATE when only on_event is overridden."""
        obj = OnEventModel(value="initial")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)
        _test_events.clear()

        obj.value = "updated"
        await mixin_session.commit()
        await asyncio.sleep(0)

        changes_events = [e for e in _test_events if e["event"] == ModelEvent.UPDATE]
        assert len(changes_events) == 1
        assert changes_events[0]["changes"]["value"] == {
            "old": "initial",
            "new": "updated",
        }


class TestTransientObject:
    """Create + delete within the same transaction should fire no events."""

    @pytest.fixture(autouse=True)
    def clear_events(self):
        _test_events.clear()
        yield
        _test_events.clear()

    @pytest.mark.anyio
    async def test_no_events_when_created_and_deleted_in_same_transaction(
        self, mixin_session
    ):
        """Neither on_create nor on_delete fires when the object never survives a commit."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.flush()
        await mixin_session.delete(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert _test_events == []

    @pytest.mark.anyio
    async def test_other_objects_unaffected(self, mixin_session):
        """on_create still fires for objects that are not deleted in the same transaction."""
        survivor = WatchedModel(status="active", other="x")
        transient = WatchedModel(status="gone", other="y")
        mixin_session.add(survivor)
        mixin_session.add(transient)
        await mixin_session.flush()
        await mixin_session.delete(transient)
        await mixin_session.commit()
        await asyncio.sleep(0)

        creates = [e for e in _test_events if e["event"] == "create"]
        deletes = [e for e in _test_events if e["event"] == "delete"]
        assert len(creates) == 1
        assert creates[0]["obj_id"] == survivor.id
        assert deletes == []

    @pytest.mark.anyio
    async def test_distinct_create_and_delete_both_fire(self, mixin_session):
        """on_create and on_delete both fire when different objects are created and deleted."""
        existing = WatchedModel(status="old", other="x")
        mixin_session.add(existing)
        await mixin_session.commit()
        await asyncio.sleep(0)
        _test_events.clear()

        new_obj = WatchedModel(status="new", other="y")
        mixin_session.add(new_obj)
        await mixin_session.delete(existing)
        await mixin_session.commit()
        await asyncio.sleep(0)

        creates = [e for e in _test_events if e["event"] == "create"]
        deletes = [e for e in _test_events if e["event"] == "delete"]
        assert len(creates) == 1
        assert len(deletes) == 1


class TestPolymorphism:
    """WatchedFieldsMixin with STI (Single Table Inheritance)."""

    @pytest.fixture(autouse=True)
    def clear_events(self):
        _poly_events.clear()
        yield
        _poly_events.clear()

    @pytest.mark.anyio
    async def test_on_create_fires_once_for_subclass(self, mixin_session):
        """on_create fires exactly once for a STI subclass instance."""
        dog = PolyDog(name="Rex")
        mixin_session.add(dog)
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert len(_poly_events) == 1
        assert _poly_events[0]["event"] == "create"
        assert _poly_events[0]["type"] == "PolyDog"

    @pytest.mark.anyio
    async def test_on_delete_fires_for_subclass(self, mixin_session):
        """on_delete fires for a STI subclass instance."""
        dog = PolyDog(name="Rex")
        mixin_session.add(dog)
        await mixin_session.commit()
        await asyncio.sleep(0)
        _poly_events.clear()

        await mixin_session.delete(dog)
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert len(_poly_events) == 1
        assert _poly_events[0]["event"] == "delete"
        assert _poly_events[0]["type"] == "PolyDog"

    @pytest.mark.anyio
    async def test_transient_subclass_fires_no_events(self, mixin_session):
        """Create + delete of a STI subclass in one transaction fires no events."""
        dog = PolyDog(name="Rex")
        mixin_session.add(dog)
        await mixin_session.flush()
        await mixin_session.delete(dog)
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert _poly_events == []


class TestWatchAll:
    @pytest.fixture(autouse=True)
    def clear_events(self):
        _test_events.clear()
        yield
        _test_events.clear()

    @pytest.mark.anyio
    async def test_watch_all_fires_for_any_field(self, mixin_session):
        """Model without @watch fires on_update for any changed field."""
        obj = WatchAllModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)
        _test_events.clear()

        obj.other = "changed"
        await mixin_session.commit()
        await asyncio.sleep(0)

        changes_events = [e for e in _test_events if e["event"] == "update"]
        assert len(changes_events) == 1
        assert "other" in changes_events[0]["changes"]

    @pytest.mark.anyio
    async def test_watch_all_captures_multiple_fields(self, mixin_session):
        """Model without @watch captures all fields changed in a single commit."""
        obj = WatchAllModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)
        _test_events.clear()

        obj.status = "updated"
        obj.other = "changed"
        await mixin_session.commit()
        await asyncio.sleep(0)

        changes_events = [e for e in _test_events if e["event"] == "update"]
        assert len(changes_events) == 1
        assert "status" in changes_events[0]["changes"]
        assert "other" in changes_events[0]["changes"]


class TestSyncCallbacks:
    @pytest.fixture(autouse=True)
    def clear_events(self):
        _sync_events.clear()
        yield
        _sync_events.clear()

    @pytest.mark.anyio
    async def test_sync_on_create_fires(self, mixin_session):
        """Sync on_create is called after INSERT commit."""
        obj = SyncCallbackModel(status="active")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)

        creates = [e for e in _sync_events if e["event"] == "create"]
        assert len(creates) == 1
        assert isinstance(creates[0]["obj_id"], uuid.UUID)

    @pytest.mark.anyio
    async def test_sync_on_delete_fires(self, mixin_session):
        """Sync on_delete is called after DELETE commit."""
        obj = SyncCallbackModel(status="active")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)
        _sync_events.clear()

        await mixin_session.delete(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)

        deletes = [e for e in _sync_events if e["event"] == "delete"]
        assert len(deletes) == 1

    @pytest.mark.anyio
    async def test_sync_on_update_fires(self, mixin_session):
        """Sync on_update is called after UPDATE commit with correct changes."""
        obj = SyncCallbackModel(status="initial")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)
        _sync_events.clear()

        obj.status = "updated"
        await mixin_session.commit()
        await asyncio.sleep(0)

        updates = [e for e in _sync_events if e["event"] == "update"]
        assert len(updates) == 1
        assert updates[0]["changes"]["status"] == {"old": "initial", "new": "updated"}


class TestFutureCallbacks:
    """Callbacks returning a non-coroutine awaitable (asyncio.Task / Future)."""

    @pytest.fixture(autouse=True)
    def clear_events(self):
        _future_events.clear()
        yield
        _future_events.clear()

    @pytest.mark.anyio
    async def test_task_callback_is_awaited(self, mixin_session):
        """on_create returning an asyncio.Task is awaited and its work completes."""
        obj = FutureCallbackModel(name="test")
        mixin_session.add(obj)
        await mixin_session.commit()
        # Two turns: one for _run() to execute, one for the inner _work() task.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert _future_events == ["created"]


class TestAttributeAccessInCallbacks:
    """Verify that self attributes are accessible inside every callback type.

    Uses expire_on_commit=True (the SQLAlchemy default) so the tests would fail
    without the snapshot-restore logic in _schedule_with_snapshot.
    """

    @pytest.fixture(autouse=True)
    def clear_events(self):
        _attr_access_events.clear()
        yield
        _attr_access_events.clear()

    @pytest.mark.anyio
    async def test_on_create_pk_and_field_accessible(self, mixin_session_expire):
        """id (server default) and regular fields are readable inside on_create."""
        obj = AttrAccessModel(name="hello")
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()
        await asyncio.sleep(0)

        events = [e for e in _attr_access_events if e["event"] == "create"]
        assert len(events) == 1
        assert isinstance(events[0]["id"], uuid.UUID)
        assert events[0]["name"] == "hello"

    @pytest.mark.anyio
    async def test_on_delete_pk_and_field_accessible(self, mixin_session_expire):
        """id and regular fields are readable inside on_delete."""
        obj = AttrAccessModel(name="to-delete")
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()
        await asyncio.sleep(0)
        _attr_access_events.clear()

        await mixin_session_expire.delete(obj)
        await mixin_session_expire.commit()
        await asyncio.sleep(0)

        events = [e for e in _attr_access_events if e["event"] == "delete"]
        assert len(events) == 1
        assert isinstance(events[0]["id"], uuid.UUID)
        assert events[0]["name"] == "to-delete"

    @pytest.mark.anyio
    async def test_on_update_pk_and_updated_field_accessible(
        self, mixin_session_expire
    ):
        """id and the new field value are readable inside on_update."""
        obj = AttrAccessModel(name="original")
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()
        await asyncio.sleep(0)
        _attr_access_events.clear()

        obj.name = "updated"
        await mixin_session_expire.commit()
        await asyncio.sleep(0)

        events = [e for e in _attr_access_events if e["event"] == "update"]
        assert len(events) == 1
        assert isinstance(events[0]["id"], uuid.UUID)
        assert events[0]["name"] == "updated"
