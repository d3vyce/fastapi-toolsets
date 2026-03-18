"""Tests for fastapi_toolsets.models mixins."""

import asyncio
import uuid
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import patch

import fastapi_toolsets.models as _models_module
import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fastapi_toolsets.models import (
    CreatedAtMixin,
    TimestampMixin,
    UUIDMixin,
    UUIDv7Mixin,
    UpdatedAtMixin,
    WatchedFieldsMixin,
    _SESSION_FIELD_CHANGES,
    _SESSION_PENDING_NEW,
    _after_commit,
    _after_flush,
    _after_flush_postexec,
    _after_rollback,
    _task_error_handler,
    _upsert_changes,
    watch_fields,
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


# --- WatchedFieldsMixin test models ---

_test_calls: list[dict] = []


@watch_fields("status")
class WatchedModel(MixinBase, UUIDMixin, WatchedFieldsMixin):
    __tablename__ = "mixin_watched_models"

    status: Mapped[str] = mapped_column(String(50))
    other: Mapped[str] = mapped_column(String(50))

    async def on_field_changes(self, changes: dict) -> None:
        _test_calls.append({"obj_id": self.id, "changes": changes})


class NonWatchedModel(MixinBase):
    __tablename__ = "mixin_non_watched_models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    value: Mapped[str] = mapped_column(String(50))


@pytest.fixture
def empty_watched_fields():
    """Temporarily clear _WATCHED_FIELDS to exercise early-exit guards."""
    original = dict(_models_module._WATCHED_FIELDS)
    _models_module._WATCHED_FIELDS.clear()
    yield
    _models_module._WATCHED_FIELDS.clear()
    _models_module._WATCHED_FIELDS.update(original)


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
        assert obj.updated_at.tzinfo is not None  # timezone-aware

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
        assert obj.created_at.tzinfo is not None  # timezone-aware

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


class TestWatchFieldsDecorator:
    def test_registers_fields(self):
        """watch_fields stores the field list in _WATCHED_FIELDS keyed by the class."""
        assert _models_module._WATCHED_FIELDS.get(WatchedModel) == ["status"]

    def test_preserves_class_identity(self):
        """watch_fields returns the same class unchanged."""

        class _Dummy(WatchedFieldsMixin):
            pass

        result = watch_fields("x")(_Dummy)
        assert result is _Dummy
        del _models_module._WATCHED_FIELDS[_Dummy]


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


class TestEarlyExitGuards:
    def test_after_flush_returns_early_when_no_watched_fields(
        self, empty_watched_fields
    ):
        session = SimpleNamespace(new=[], dirty=[], info={})
        _after_flush(session, None)
        assert _SESSION_PENDING_NEW not in session.info
        assert _SESSION_FIELD_CHANGES not in session.info

    def test_after_flush_postexec_returns_early_when_no_watched_fields(
        self, empty_watched_fields
    ):
        session = SimpleNamespace(info={})
        _after_flush_postexec(session, None)
        assert _SESSION_FIELD_CHANGES not in session.info

    def test_after_commit_returns_early_when_no_watched_fields(
        self, empty_watched_fields
    ):
        session = SimpleNamespace(info={})
        _after_commit(session)  # should not raise


class TestAfterRollback:
    def test_clears_both_session_info_keys(self):
        """_after_rollback removes both pending-new and field-changes from session.info."""
        session = SimpleNamespace(
            info={
                _SESSION_FIELD_CHANGES: {1: ("obj", {"f": {"old": "a", "new": "b"}})},
                _SESSION_PENDING_NEW: [("obj", ["f"])],
            }
        )
        _after_rollback(session)
        assert _SESSION_FIELD_CHANGES not in session.info
        assert _SESSION_PENDING_NEW not in session.info

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

        with patch.object(_models_module._logger, "error") as mock_error:
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

        with patch.object(_models_module._logger, "error") as mock_error:
            _task_error_handler(task)
            mock_error.assert_not_called()


class TestAfterCommitNoLoop:
    def test_no_task_scheduled_when_no_running_loop(self):
        """_after_commit silently returns when called outside an async context."""
        called = []
        obj = SimpleNamespace(on_field_changes=lambda c: called.append(c))
        session = SimpleNamespace(
            info={
                _SESSION_FIELD_CHANGES: {1: (obj, {"status": {"old": "a", "new": "b"}})}
            }
        )
        _after_commit(session)
        assert called == []

    def test_returns_early_when_pending_empty(self):
        """_after_commit does nothing when there are no pending changes."""
        session = SimpleNamespace(info={})
        _after_commit(session)  # should not raise


class TestWatchedFieldsMixin:
    @pytest.fixture(autouse=True)
    def clear_calls(self):
        _test_calls.clear()
        yield
        _test_calls.clear()

    @pytest.mark.anyio
    async def test_creation_fires_callback_with_old_none(self, mixin_session):
        """on_field_changes is called on INSERT with old=None."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert len(_test_calls) == 1
        assert _test_calls[0]["changes"]["status"] == {"old": None, "new": "active"}

    @pytest.mark.anyio
    async def test_server_defaults_available_in_callback(self, mixin_session):
        """id (server default via RETURNING) is populated before on_field_changes fires."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert _test_calls[0]["obj_id"] is not None
        assert isinstance(_test_calls[0]["obj_id"], uuid.UUID)

    @pytest.mark.anyio
    async def test_update_fires_callback_with_old_and_new(self, mixin_session):
        """on_field_changes reports the correct before/after values on UPDATE."""
        obj = WatchedModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)  # flush creation task before clearing
        _test_calls.clear()

        obj.status = "updated"
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert len(_test_calls) == 1
        assert _test_calls[0]["changes"]["status"] == {
            "old": "initial",
            "new": "updated",
        }

    @pytest.mark.anyio
    async def test_unwatched_field_update_no_callback(self, mixin_session):
        """Changing a field not listed in watch_fields does not trigger a callback."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)  # flush creation task before clearing
        _test_calls.clear()

        obj.other = "changed"
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert _test_calls == []

    @pytest.mark.anyio
    async def test_multiple_flushes_merge_earliest_old_latest_new(self, mixin_session):
        """Two flushes in one transaction produce a single callback with earliest old / latest new."""
        obj = WatchedModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)  # flush creation task before clearing
        _test_calls.clear()

        obj.status = "intermediate"
        await mixin_session.flush()

        obj.status = "final"
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert len(_test_calls) == 1
        assert _test_calls[0]["changes"]["status"] == {"old": "initial", "new": "final"}

    @pytest.mark.anyio
    async def test_rollback_suppresses_callback(self, mixin_session):
        """on_field_changes is NOT called when the transaction is rolled back."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        await asyncio.sleep(0)  # flush creation task before clearing
        _test_calls.clear()

        obj.status = "changed"
        await mixin_session.flush()
        await mixin_session.rollback()
        await asyncio.sleep(0)

        assert _test_calls == []

    @pytest.mark.anyio
    async def test_non_watched_model_dirty_no_callback(self, mixin_session):
        """Dirty objects whose type is not registered in watch_fields are skipped."""
        nw = NonWatchedModel(value="x")
        mixin_session.add(nw)
        await mixin_session.flush()
        nw.value = "y"
        await mixin_session.commit()
        await asyncio.sleep(0)

        assert _test_calls == []
