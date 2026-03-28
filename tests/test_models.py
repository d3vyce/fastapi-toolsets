"""Tests for fastapi_toolsets.models mixins."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import fastapi_toolsets.models.watched as _watched_module
from fastapi_toolsets.models import (
    CreatedAtMixin,
    ModelEvent,
    TimestampMixin,
    UpdatedAtMixin,
    UUIDMixin,
    UUIDv7Mixin,
    listens_for,
)
from fastapi_toolsets.models.watched import (
    _EVENT_HANDLERS,
    _SESSION_CREATES,
    _SESSION_DELETES,
    _SESSION_UPDATES,
    _WATCHED_MODELS,
    _after_flush,
    _after_rollback,
    _get_watched_fields,
    _invalidate_caches,
    _is_watched,
    _snapshot_column_attrs,
    _upsert_changes,
)
from fastapi_toolsets.pytest import create_db_session

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


class WatchedModel(MixinBase, UUIDMixin):
    __tablename__ = "mixin_watched_models"
    __watched_fields__ = ("status",)

    status: Mapped[str] = mapped_column(String(50))
    other: Mapped[str] = mapped_column(String(50))


@listens_for(WatchedModel, ModelEvent.CREATE)
async def _watched_on_create(obj):
    _test_events.append({"event": "create", "obj_id": obj.id})


@listens_for(WatchedModel, ModelEvent.DELETE)
async def _watched_on_delete(obj):
    _test_events.append({"event": "delete", "obj_id": obj.id})


@listens_for(WatchedModel, ModelEvent.UPDATE)
async def _watched_on_update(obj, changes):
    _test_events.append({"event": "update", "obj_id": obj.id, "changes": changes})


class WatchAllModel(MixinBase, UUIDMixin):
    """Model without __watched_fields__ — watches all mapped fields by default."""

    __tablename__ = "mixin_watch_all_models"

    status: Mapped[str] = mapped_column(String(50))
    other: Mapped[str] = mapped_column(String(50))


@listens_for(WatchAllModel, ModelEvent.UPDATE)
async def _watch_all_on_update(obj, changes):
    _test_events.append({"event": "update", "obj_id": obj.id, "changes": changes})


class FailingCallbackModel(MixinBase, UUIDMixin):
    """Model whose CREATE handler always raises to test exception logging."""

    __tablename__ = "mixin_failing_callback_models"

    name: Mapped[str] = mapped_column(String(50))


@listens_for(FailingCallbackModel, ModelEvent.CREATE)
async def _failing_on_create(obj):
    raise RuntimeError("callback intentionally failed")


@listens_for(FailingCallbackModel, ModelEvent.DELETE)
async def _failing_on_delete(obj):
    raise RuntimeError("delete callback intentionally failed")


@listens_for(FailingCallbackModel, ModelEvent.UPDATE)
async def _failing_on_update(obj, changes):
    raise RuntimeError("update callback intentionally failed")


class NonWatchedModel(MixinBase):
    __tablename__ = "mixin_non_watched_models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    value: Mapped[str] = mapped_column(String(50))


_poly_events: list[dict] = []


class PolyAnimal(MixinBase, UUIDMixin):
    """Base class for STI polymorphism tests."""

    __tablename__ = "mixin_poly_animals"
    __mapper_args__ = {"polymorphic_on": "kind", "polymorphic_identity": "animal"}

    kind: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(50))


@listens_for(PolyAnimal, ModelEvent.CREATE)
async def _poly_on_create(obj):
    _poly_events.append(
        {"event": "create", "type": type(obj).__name__, "obj_id": obj.id}
    )


@listens_for(PolyAnimal, ModelEvent.DELETE)
async def _poly_on_delete(obj):
    _poly_events.append(
        {"event": "delete", "type": type(obj).__name__, "obj_id": obj.id}
    )


class PolyDog(PolyAnimal):
    """STI subclass — shares the same table as PolyAnimal."""

    __mapper_args__ = {"polymorphic_identity": "dog"}


_watch_inherit_events: list[dict] = []


class WatchParent(MixinBase, UUIDMixin):
    """Base class with __watched_fields__ = ("status",) — subclasses inherit."""

    __tablename__ = "mixin_watch_parent"
    __watched_fields__ = ("status",)
    __mapper_args__ = {"polymorphic_on": "kind", "polymorphic_identity": "parent"}

    kind: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50))
    other: Mapped[str] = mapped_column(String(50))


@listens_for(WatchParent, ModelEvent.UPDATE)
async def _watch_parent_on_update(obj, changes):
    _watch_inherit_events.append({"type": type(obj).__name__, "changes": changes})


class WatchChild(WatchParent):
    """STI subclass that does NOT redeclare __watched_fields__ — inherits parent's filter."""

    __mapper_args__ = {"polymorphic_identity": "child"}


class WatchOverride(WatchParent):
    """STI subclass that overrides __watched_fields__ with a different field."""

    __watched_fields__ = ("other",)

    __mapper_args__ = {"polymorphic_identity": "override"}


_attr_access_events: list[dict] = []


class AttrAccessModel(MixinBase, UUIDMixin):
    """Model used to verify that attributes are accessible in every callback."""

    __tablename__ = "mixin_attr_access_models"

    name: Mapped[str] = mapped_column(String(50))
    callback_url: Mapped[str | None] = mapped_column(String(200), nullable=True)


@listens_for(AttrAccessModel, ModelEvent.CREATE)
async def _attr_on_create(obj):
    _attr_access_events.append(
        {
            "event": "create",
            "id": obj.id,
            "name": obj.name,
            "callback_url": obj.callback_url,
        }
    )


@listens_for(AttrAccessModel, ModelEvent.DELETE)
async def _attr_on_delete(obj):
    _attr_access_events.append(
        {
            "event": "delete",
            "id": obj.id,
            "name": obj.name,
            "callback_url": obj.callback_url,
        }
    )


@listens_for(AttrAccessModel, ModelEvent.UPDATE)
async def _attr_on_update(obj, changes):
    _attr_access_events.append(
        {
            "event": "update",
            "id": obj.id,
            "name": obj.name,
            "callback_url": obj.callback_url,
        }
    )


_sync_events: list[dict] = []
_future_events: list[str] = []


class SyncCallbackModel(MixinBase, UUIDMixin):
    """Model with plain (sync) callbacks."""

    __tablename__ = "mixin_sync_callback_models"
    __watched_fields__ = ("status",)

    status: Mapped[str] = mapped_column(String(50))


@listens_for(SyncCallbackModel, ModelEvent.CREATE)
def _sync_on_create(obj):
    _sync_events.append({"event": "create", "obj_id": obj.id})


@listens_for(SyncCallbackModel, ModelEvent.DELETE)
def _sync_on_delete(obj):
    _sync_events.append({"event": "delete", "obj_id": obj.id})


@listens_for(SyncCallbackModel, ModelEvent.UPDATE)
def _sync_on_update(obj, changes):
    _sync_events.append({"event": "update", "changes": changes})


class FutureCallbackModel(MixinBase, UUIDMixin):
    """Model whose CREATE handler returns an asyncio.Task (awaitable, not a coroutine)."""

    __tablename__ = "mixin_future_callback_models"

    name: Mapped[str] = mapped_column(String(50))


@listens_for(FutureCallbackModel, ModelEvent.CREATE)
def _future_on_create(obj):
    async def _work():
        _future_events.append("created")

    return asyncio.ensure_future(_work())


class ListenerModel(MixinBase, UUIDMixin):
    """Model for testing the listens_for decorator with dynamic registration."""

    __tablename__ = "mixin_listener_models"
    __watched_fields__ = ("status",)

    status: Mapped[str] = mapped_column(String(50))
    other: Mapped[str] = mapped_column(String(50))


_listener_events: list[dict] = []


@pytest.fixture(scope="function")
async def mixin_session():
    async with create_db_session(DATABASE_URL, MixinBase) as session:
        yield session


@pytest.fixture(scope="function")
async def mixin_session_expire():
    """Session with expire_on_commit=True (the default) to exercise attribute access after commit."""
    async with create_db_session(
        DATABASE_URL, MixinBase, expire_on_commit=True
    ) as session:
        yield session


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


class TestWatchedFields:
    def test_specific_fields_set(self):
        """__watched_fields__ stores the watched field tuple."""
        assert WatchedModel.__watched_fields__ == ("status",)

    def test_no_watched_fields_means_all(self):
        """A model without __watched_fields__ watches all fields."""
        assert _get_watched_fields(WatchAllModel) is None

    def test_inherits_from_parent(self):
        """Subclass without __watched_fields__ inherits parent's value."""
        assert WatchChild.__watched_fields__ == ("status",)

    def test_override_takes_precedence(self):
        """Subclass __watched_fields__ overrides parent's value."""
        assert WatchOverride.__watched_fields__ == ("other",)


class TestWatchInheritance:
    @pytest.fixture(autouse=True)
    def clear_events(self):
        _watch_inherit_events.clear()
        yield
        _watch_inherit_events.clear()

    @pytest.mark.anyio
    async def test_child_inherits_parent_watch_filter(self, mixin_session):
        """Subclass without __watched_fields__ inherits the parent's field filter."""
        obj = WatchChild(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        obj.other = "changed"  # not watched by parent's __watched_fields__
        await mixin_session.commit()

        assert _watch_inherit_events == []

    @pytest.mark.anyio
    async def test_child_triggers_on_watched_field(self, mixin_session):
        """Subclass without __watched_fields__ triggers handler for the parent's watched field."""
        obj = WatchChild(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        obj.status = "updated"
        await mixin_session.commit()

        assert len(_watch_inherit_events) == 1
        assert _watch_inherit_events[0]["type"] == "WatchChild"
        assert "status" in _watch_inherit_events[0]["changes"]

    @pytest.mark.anyio
    async def test_subclass_override_takes_precedence(self, mixin_session):
        """Subclass __watched_fields__ overrides the parent's field filter."""
        obj = WatchOverride(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        obj.status = "changed"  # overridden by child's __watched_fields__ = ("other",)
        await mixin_session.commit()

        assert _watch_inherit_events == []

        obj.other = "changed"
        await mixin_session.commit()

        assert len(_watch_inherit_events) == 1
        assert "other" in _watch_inherit_events[0]["changes"]


class TestIsWatched:
    def test_watched_model_is_watched(self):
        """_is_watched returns True for models with registered handlers."""
        obj = WatchedModel(status="x", other="y")
        assert _is_watched(obj) is True

    def test_non_watched_model_is_not_watched(self):
        """_is_watched returns False for models without registered handlers."""
        assert _is_watched(object()) is False

    def test_subclass_of_watched_model_is_watched(self):
        """_is_watched returns True for subclasses of watched models (via MRO)."""
        dog = PolyDog(name="Rex")
        assert _is_watched(dog) is True


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

    def test_captures_new_watched_objects(self):
        """New watched objects are added to _SESSION_CREATES."""
        obj = object()
        session = SimpleNamespace(new=[obj], deleted=[], dirty=[], info={})
        with patch("fastapi_toolsets.models.watched._is_watched", return_value=True):
            _after_flush(session, None)
        assert session.info[_SESSION_CREATES] == [obj]

    def test_ignores_new_non_watched_objects(self):
        """New objects that are not watched are not captured."""
        obj = object()
        session = SimpleNamespace(new=[obj], deleted=[], dirty=[], info={})
        _after_flush(session, None)
        assert _SESSION_CREATES not in session.info

    def test_captures_deleted_watched_objects(self):
        """Deleted watched objects are stored as (obj, snapshot) tuples."""
        obj = object()
        session = SimpleNamespace(new=[], deleted=[obj], dirty=[], info={})
        with (
            patch("fastapi_toolsets.models.watched._is_watched", return_value=True),
            patch(
                "fastapi_toolsets.models.watched._snapshot_column_attrs",
                return_value={"id": 1},
            ),
        ):
            _after_flush(session, None)
        assert len(session.info[_SESSION_DELETES]) == 1
        assert session.info[_SESSION_DELETES][0][0] is obj
        assert session.info[_SESSION_DELETES][0][1] == {"id": 1}

    def test_ignores_deleted_non_watched_objects(self):
        """Deleted objects that are not watched are not captured."""
        obj = object()
        session = SimpleNamespace(new=[], deleted=[obj], dirty=[], info={})
        _after_flush(session, None)
        assert _SESSION_DELETES not in session.info


class TestAfterRollback:
    def test_clears_all_session_info_keys(self):
        """_after_rollback removes all three tracking keys on full rollback."""
        session = SimpleNamespace(
            info={
                _SESSION_CREATES: [object()],
                _SESSION_DELETES: [object()],
                _SESSION_UPDATES: {1: ("obj", {"f": {"old": "a", "new": "b"}})},
            },
            in_transaction=lambda: False,
        )
        _after_rollback(session)
        assert _SESSION_CREATES not in session.info
        assert _SESSION_DELETES not in session.info
        assert _SESSION_UPDATES not in session.info

    def test_tolerates_missing_keys(self):
        """_after_rollback does not raise when session.info has no pending data."""
        session = SimpleNamespace(info={}, in_transaction=lambda: False)
        _after_rollback(session)  # must not raise

    def test_preserves_events_on_savepoint_rollback(self):
        """_after_rollback keeps events when still in a transaction (savepoint)."""
        creates = [object()]
        session = SimpleNamespace(
            info={
                _SESSION_CREATES: creates,
                _SESSION_DELETES: [],
                _SESSION_UPDATES: {},
            },
            in_transaction=lambda: True,
        )
        _after_rollback(session)
        assert session.info[_SESSION_CREATES] is creates


class TestEventCallbacks:
    @pytest.fixture(autouse=True)
    def clear_events(self):
        _test_events.clear()
        yield
        _test_events.clear()

    # --- CREATE ---

    @pytest.mark.anyio
    async def test_create_fires_after_insert(self, mixin_session):
        """CREATE handler is called after INSERT commit."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        creates = [e for e in _test_events if e["event"] == "create"]
        assert len(creates) == 1

    @pytest.mark.anyio
    async def test_create_server_defaults_populated(self, mixin_session):
        """id (server default via RETURNING) is available inside CREATE handler."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        creates = [e for e in _test_events if e["event"] == "create"]
        assert creates[0]["obj_id"] is not None
        assert isinstance(creates[0]["obj_id"], uuid.UUID)

    @pytest.mark.anyio
    async def test_create_not_fired_on_update(self, mixin_session):
        """CREATE handler is NOT called when an existing row is updated."""
        obj = WatchedModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        _test_events.clear()

        obj.status = "updated"
        await mixin_session.commit()

        assert not any(e["event"] == "create" for e in _test_events)

    # --- DELETE ---

    @pytest.mark.anyio
    async def test_delete_fires_after_delete(self, mixin_session):
        """DELETE handler is called after DELETE commit."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        saved_id = obj.id
        _test_events.clear()

        await mixin_session.delete(obj)
        await mixin_session.commit()

        deletes = [e for e in _test_events if e["event"] == "delete"]
        assert len(deletes) == 1
        assert deletes[0]["obj_id"] == saved_id

    @pytest.mark.anyio
    async def test_delete_not_fired_on_insert(self, mixin_session):
        """DELETE handler is NOT called when a new row is inserted."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        assert not any(e["event"] == "delete" for e in _test_events)

    # --- UPDATE ---

    @pytest.mark.anyio
    async def test_update_fires_on_update(self, mixin_session):
        """UPDATE handler reports the correct before/after values."""
        obj = WatchedModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        _test_events.clear()

        obj.status = "updated"
        await mixin_session.commit()

        changes_events = [e for e in _test_events if e["event"] == "update"]
        assert len(changes_events) == 1
        assert changes_events[0]["changes"]["status"] == {
            "old": "initial",
            "new": "updated",
        }

    @pytest.mark.anyio
    async def test_update_not_fired_on_insert(self, mixin_session):
        """UPDATE handler is NOT called on INSERT (CREATE handles that)."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        assert not any(e["event"] == "update" for e in _test_events)

    @pytest.mark.anyio
    async def test_create_and_update_in_same_tx_only_fires_create(self, mixin_session):
        """Modifying a watched field before commit only fires CREATE, not UPDATE."""
        obj = WatchedModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.flush()

        obj.status = "updated-before-commit"
        await mixin_session.commit()

        creates = [e for e in _test_events if e["event"] == "create"]
        updates = [e for e in _test_events if e["event"] == "update"]
        assert len(creates) == 1
        assert updates == []

    @pytest.mark.anyio
    async def test_unwatched_field_update_no_callback(self, mixin_session):
        """Changing a field not in __watched_fields__ does not fire UPDATE handler."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        _test_events.clear()

        obj.other = "changed"
        await mixin_session.commit()

        assert _test_events == []

    @pytest.mark.anyio
    async def test_multiple_flushes_merge_earliest_old_latest_new(self, mixin_session):
        """Two flushes in one transaction produce a single callback with earliest old / latest new."""
        obj = WatchedModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        _test_events.clear()

        obj.status = "intermediate"
        await mixin_session.flush()

        obj.status = "final"
        await mixin_session.commit()

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

        _test_events.clear()

        obj.status = "changed"
        await mixin_session.flush()
        await mixin_session.rollback()

        assert _test_events == []

    @pytest.mark.anyio
    async def test_create_callback_exception_is_logged(self, mixin_session):
        """Exceptions raised inside a CREATE handler are logged, not propagated."""
        obj = FailingCallbackModel(name="boom")
        mixin_session.add(obj)
        with patch.object(_watched_module._logger, "error") as mock_error:
            await mixin_session.commit()

            mock_error.assert_called_once()

    @pytest.mark.anyio
    async def test_delete_callback_exception_is_logged(self, mixin_session):
        """Exceptions raised inside a DELETE handler are logged, not propagated."""
        obj = FailingCallbackModel(name="boom")
        mixin_session.add(obj)
        await mixin_session.commit()  # CREATE handler fails (logged)

        await mixin_session.delete(obj)
        with patch.object(_watched_module._logger, "error") as mock_error:
            await mixin_session.commit()

            mock_error.assert_called_once()

    @pytest.mark.anyio
    async def test_update_callback_exception_is_logged(self, mixin_session):
        """Exceptions raised inside an UPDATE handler are logged, not propagated."""
        obj = FailingCallbackModel(name="boom")
        mixin_session.add(obj)
        await mixin_session.commit()  # CREATE handler fails (logged)

        obj.name = "changed"
        with patch.object(_watched_module._logger, "error") as mock_error:
            await mixin_session.commit()

            mock_error.assert_called_once()

    @pytest.mark.anyio
    async def test_non_watched_model_no_callback(self, mixin_session):
        """Dirty objects whose type has no registered handlers are skipped."""
        nw = NonWatchedModel(value="x")
        mixin_session.add(nw)
        await mixin_session.flush()
        nw.value = "y"
        await mixin_session.commit()

        assert _test_events == []


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
        """Neither CREATE nor DELETE fires when the object never survives a commit."""
        obj = WatchedModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.flush()
        await mixin_session.delete(obj)
        await mixin_session.commit()

        assert _test_events == []

    @pytest.mark.anyio
    async def test_other_objects_unaffected(self, mixin_session):
        """CREATE still fires for objects that are not deleted in the same transaction."""
        survivor = WatchedModel(status="active", other="x")
        transient = WatchedModel(status="gone", other="y")
        mixin_session.add(survivor)
        mixin_session.add(transient)
        await mixin_session.flush()
        await mixin_session.delete(transient)
        await mixin_session.commit()

        creates = [e for e in _test_events if e["event"] == "create"]
        deletes = [e for e in _test_events if e["event"] == "delete"]
        assert len(creates) == 1
        assert creates[0]["obj_id"] == survivor.id
        assert deletes == []

    @pytest.mark.anyio
    async def test_distinct_create_and_delete_both_fire(self, mixin_session):
        """CREATE and DELETE both fire when different objects are created and deleted."""
        existing = WatchedModel(status="old", other="x")
        mixin_session.add(existing)
        await mixin_session.commit()

        _test_events.clear()

        new_obj = WatchedModel(status="new", other="y")
        mixin_session.add(new_obj)
        await mixin_session.delete(existing)
        await mixin_session.commit()

        creates = [e for e in _test_events if e["event"] == "create"]
        deletes = [e for e in _test_events if e["event"] == "delete"]
        assert len(creates) == 1
        assert len(deletes) == 1


class TestPolymorphism:
    """Event dispatch with STI (Single Table Inheritance)."""

    @pytest.fixture(autouse=True)
    def clear_events(self):
        _poly_events.clear()
        yield
        _poly_events.clear()

    @pytest.mark.anyio
    async def test_create_fires_once_for_subclass(self, mixin_session):
        """CREATE fires exactly once for a STI subclass instance."""
        dog = PolyDog(name="Rex")
        mixin_session.add(dog)
        await mixin_session.commit()

        assert len(_poly_events) == 1
        assert _poly_events[0]["event"] == "create"
        assert _poly_events[0]["type"] == "PolyDog"

    @pytest.mark.anyio
    async def test_delete_fires_for_subclass(self, mixin_session):
        """DELETE fires for a STI subclass instance."""
        dog = PolyDog(name="Rex")
        mixin_session.add(dog)
        await mixin_session.commit()

        _poly_events.clear()

        await mixin_session.delete(dog)
        await mixin_session.commit()

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

        assert _poly_events == []


class TestWatchAll:
    @pytest.fixture(autouse=True)
    def clear_events(self):
        _test_events.clear()
        yield
        _test_events.clear()

    @pytest.mark.anyio
    async def test_watch_all_fires_for_any_field(self, mixin_session):
        """Model without __watched_fields__ fires UPDATE for any changed field."""
        obj = WatchAllModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        _test_events.clear()

        obj.other = "changed"
        await mixin_session.commit()

        changes_events = [e for e in _test_events if e["event"] == "update"]
        assert len(changes_events) == 1
        assert "other" in changes_events[0]["changes"]

    @pytest.mark.anyio
    async def test_watch_all_captures_multiple_fields(self, mixin_session):
        """Model without __watched_fields__ captures all fields changed in a single commit."""
        obj = WatchAllModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        _test_events.clear()

        obj.status = "updated"
        obj.other = "changed"
        await mixin_session.commit()

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
    async def test_sync_create_fires(self, mixin_session):
        """Sync CREATE handler is called after INSERT commit."""
        obj = SyncCallbackModel(status="active")
        mixin_session.add(obj)
        await mixin_session.commit()

        creates = [e for e in _sync_events if e["event"] == "create"]
        assert len(creates) == 1
        assert isinstance(creates[0]["obj_id"], uuid.UUID)

    @pytest.mark.anyio
    async def test_sync_delete_fires(self, mixin_session):
        """Sync DELETE handler is called after DELETE commit."""
        obj = SyncCallbackModel(status="active")
        mixin_session.add(obj)
        await mixin_session.commit()

        _sync_events.clear()

        await mixin_session.delete(obj)
        await mixin_session.commit()

        deletes = [e for e in _sync_events if e["event"] == "delete"]
        assert len(deletes) == 1

    @pytest.mark.anyio
    async def test_sync_update_fires(self, mixin_session):
        """Sync UPDATE handler is called after UPDATE commit with correct changes."""
        obj = SyncCallbackModel(status="initial")
        mixin_session.add(obj)
        await mixin_session.commit()

        _sync_events.clear()

        obj.status = "updated"
        await mixin_session.commit()

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
        """CREATE handler returning an asyncio.Task is awaited and its work completes."""
        obj = FutureCallbackModel(name="test")
        mixin_session.add(obj)
        await mixin_session.commit()

        assert _future_events == ["created"]


class TestAttributeAccessInCallbacks:
    """Verify that object attributes are accessible inside every callback type.

    Uses expire_on_commit=True (the SQLAlchemy default) so the tests would fail
    without the refresh/snapshot-restore logic in EventSession.commit().
    """

    @pytest.fixture(autouse=True)
    def clear_events(self):
        _attr_access_events.clear()
        yield
        _attr_access_events.clear()

    @pytest.mark.anyio
    async def test_create_pk_and_field_accessible(self, mixin_session_expire):
        """id (server default) and regular fields are readable inside CREATE handler."""
        obj = AttrAccessModel(name="hello")
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()

        events = [e for e in _attr_access_events if e["event"] == "create"]
        assert len(events) == 1
        assert isinstance(events[0]["id"], uuid.UUID)
        assert events[0]["name"] == "hello"

    @pytest.mark.anyio
    async def test_delete_pk_and_field_accessible(self, mixin_session_expire):
        """id and regular fields are readable inside DELETE handler."""
        obj = AttrAccessModel(name="to-delete")
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()

        _attr_access_events.clear()

        await mixin_session_expire.delete(obj)
        await mixin_session_expire.commit()

        events = [e for e in _attr_access_events if e["event"] == "delete"]
        assert len(events) == 1
        assert isinstance(events[0]["id"], uuid.UUID)
        assert events[0]["name"] == "to-delete"

    @pytest.mark.anyio
    async def test_update_pk_and_updated_field_accessible(self, mixin_session_expire):
        """id and the new field value are readable inside UPDATE handler."""
        obj = AttrAccessModel(name="original")
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()

        _attr_access_events.clear()

        obj.name = "updated"
        await mixin_session_expire.commit()

        events = [e for e in _attr_access_events if e["event"] == "update"]
        assert len(events) == 1
        assert isinstance(events[0]["id"], uuid.UUID)
        assert events[0]["name"] == "updated"

    @pytest.mark.anyio
    async def test_nullable_column_none_accessible_in_create(
        self, mixin_session_expire
    ):
        """Nullable column left as None is accessible in CREATE handler without greenlet error."""
        obj = AttrAccessModel(name="no-url")  # callback_url not set → None
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()

        events = [e for e in _attr_access_events if e["event"] == "create"]
        assert len(events) == 1
        assert events[0]["callback_url"] is None

    @pytest.mark.anyio
    async def test_nullable_column_with_value_accessible_in_create(
        self, mixin_session_expire
    ):
        """Nullable column set to a value is accessible in CREATE handler without greenlet error."""
        obj = AttrAccessModel(name="with-url", callback_url="https://example.com/hook")
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()

        events = [e for e in _attr_access_events if e["event"] == "create"]
        assert len(events) == 1
        assert events[0]["callback_url"] == "https://example.com/hook"

    @pytest.mark.anyio
    async def test_nullable_column_accessible_after_update_to_none(
        self, mixin_session_expire
    ):
        """Nullable column updated to None is accessible in UPDATE handler without greenlet error."""
        obj = AttrAccessModel(name="x", callback_url="https://example.com/hook")
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()

        _attr_access_events.clear()

        obj.callback_url = None
        await mixin_session_expire.commit()

        events = [e for e in _attr_access_events if e["event"] == "update"]
        assert len(events) == 1
        assert events[0]["callback_url"] is None

    @pytest.mark.anyio
    async def test_snapshot_on_loaded_object_captures_nullable_column(
        self, mixin_session_expire
    ):
        """_snapshot_column_attrs on a loaded (non-expired) object captures
        nullable columns correctly — used for delete snapshots at flush time."""
        obj = AttrAccessModel(name="original", callback_url="https://example.com/hook")
        mixin_session_expire.add(obj)
        await mixin_session_expire.flush()

        # Object is loaded (just flushed) — snapshot should capture everything.
        snapshot = _snapshot_column_attrs(obj)
        assert snapshot["callback_url"] == "https://example.com/hook"
        assert snapshot["name"] == "original"


class TestListensFor:
    """Test the listens_for decorator for external handler registration."""

    @pytest.fixture(autouse=True)
    def clear_events(self):
        _listener_events.clear()
        yield
        _listener_events.clear()
        # Clean up registered handlers for ListenerModel.
        for key in list(_EVENT_HANDLERS):
            if key[0] is ListenerModel:
                del _EVENT_HANDLERS[key]
        _WATCHED_MODELS.discard(ListenerModel)
        _invalidate_caches()

    @pytest.mark.anyio
    async def test_create_handler_fires(self, mixin_session):
        """Registered CREATE handler is called after INSERT commit."""

        @listens_for(ListenerModel, ModelEvent.CREATE)
        async def _on_create(obj):
            _listener_events.append({"event": "create", "id": obj.id})

        obj = ListenerModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        creates = [e for e in _listener_events if e["event"] == "create"]
        assert len(creates) == 1
        assert isinstance(creates[0]["id"], uuid.UUID)

    @pytest.mark.anyio
    async def test_delete_handler_fires(self, mixin_session):
        """Registered DELETE handler is called after DELETE commit."""

        @listens_for(ListenerModel, ModelEvent.DELETE)
        async def _on_delete(obj):
            _listener_events.append({"event": "delete", "id": obj.id})

        obj = ListenerModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()
        saved_id = obj.id

        await mixin_session.delete(obj)
        await mixin_session.commit()

        deletes = [e for e in _listener_events if e["event"] == "delete"]
        assert len(deletes) == 1
        assert deletes[0]["id"] == saved_id

    @pytest.mark.anyio
    async def test_update_handler_receives_changes(self, mixin_session):
        """Registered UPDATE handler receives the object and changes dict."""

        @listens_for(ListenerModel, ModelEvent.UPDATE)
        async def _on_update(obj, changes):
            _listener_events.append(
                {"event": "update", "id": obj.id, "changes": changes}
            )

        obj = ListenerModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        obj.status = "updated"
        await mixin_session.commit()

        updates = [e for e in _listener_events if e["event"] == "update"]
        assert len(updates) == 1
        assert updates[0]["changes"]["status"] == {
            "old": "initial",
            "new": "updated",
        }

    @pytest.mark.anyio
    async def test_string_event_type_works(self, mixin_session):
        """listens_for accepts string event type."""

        @listens_for(ListenerModel, "create")
        async def _on_create(obj):
            _listener_events.append({"event": "create"})

        obj = ListenerModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        assert len(_listener_events) == 1

    @pytest.mark.anyio
    async def test_multiple_handlers_all_fire(self, mixin_session):
        """Multiple handlers registered for the same event all fire."""

        @listens_for(ListenerModel, ModelEvent.CREATE)
        async def _handler_a(obj):
            _listener_events.append({"handler": "a"})

        @listens_for(ListenerModel, ModelEvent.CREATE)
        async def _handler_b(obj):
            _listener_events.append({"handler": "b"})

        obj = ListenerModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        handlers = [e["handler"] for e in _listener_events]
        assert "a" in handlers
        assert "b" in handlers

    @pytest.mark.anyio
    async def test_sync_handler_works(self, mixin_session):
        """Sync (non-async) registered handler is called."""

        @listens_for(ListenerModel, ModelEvent.CREATE)
        def _on_create(obj):
            _listener_events.append({"event": "create", "id": obj.id})

        obj = ListenerModel(status="active", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        assert len(_listener_events) == 1

    @pytest.mark.anyio
    async def test_multiple_event_types(self, mixin_session):
        """listens_for accepts multiple event types and registers for all of them."""

        @listens_for(ListenerModel, ModelEvent.CREATE, ModelEvent.UPDATE)
        async def _on_change(obj, *args):
            _listener_events.append({"event": "change", "id": obj.id})

        obj = ListenerModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        obj.status = "updated"
        await mixin_session.commit()

        assert len(_listener_events) == 2
        assert all(e["event"] == "change" for e in _listener_events)


class TestEventSessionWithGetTransaction:
    """Verify callbacks fire correctly when using get_transaction / lock_tables."""

    @pytest.fixture(autouse=True)
    def clear_events(self):
        _test_events.clear()
        yield
        _test_events.clear()

    @pytest.mark.anyio
    async def test_callbacks_fire_after_outer_commit_not_savepoint(self, mixin_session):
        """get_transaction creates a savepoint; callbacks fire only on outer commit."""
        from fastapi_toolsets.db import get_transaction

        async with get_transaction(mixin_session):
            obj = WatchedModel(status="active", other="x")
            mixin_session.add(obj)

        # Still inside the session's outer transaction — savepoint committed,
        # but EventSession.commit() hasn't been called yet.
        assert _test_events == []

        await mixin_session.commit()

        creates = [e for e in _test_events if e["event"] == "create"]
        assert len(creates) == 1

    @pytest.mark.anyio
    async def test_nested_transactions_accumulate_events(self, mixin_session):
        """Multiple get_transaction blocks accumulate events for a single commit."""
        from fastapi_toolsets.db import get_transaction

        async with get_transaction(mixin_session):
            obj1 = WatchedModel(status="first", other="x")
            mixin_session.add(obj1)

        async with get_transaction(mixin_session):
            obj2 = WatchedModel(status="second", other="y")
            mixin_session.add(obj2)

        assert _test_events == []

        await mixin_session.commit()

        creates = [e for e in _test_events if e["event"] == "create"]
        assert len(creates) == 2

    @pytest.mark.anyio
    async def test_savepoint_rollback_suppresses_events(self, mixin_session):
        """Objects from a rolled-back savepoint don't fire callbacks."""
        from fastapi_toolsets.db import get_transaction

        survivor = WatchedModel(status="kept", other="x")
        mixin_session.add(survivor)
        await mixin_session.flush()

        try:
            async with get_transaction(mixin_session):
                doomed = WatchedModel(status="doomed", other="y")
                mixin_session.add(doomed)
                await mixin_session.flush()
                raise ValueError("rollback this savepoint")
        except ValueError:
            pass

        await mixin_session.commit()

        creates = [e for e in _test_events if e["event"] == "create"]
        assert len(creates) == 1
        assert creates[0]["obj_id"] == survivor.id

    @pytest.mark.anyio
    async def test_lock_tables_with_events(self, mixin_session):
        """Events fire correctly after lock_tables context."""
        from fastapi_toolsets.db import lock_tables

        async with lock_tables(mixin_session, [WatchedModel]):
            obj = WatchedModel(status="locked", other="x")
            mixin_session.add(obj)

        await mixin_session.commit()

        creates = [e for e in _test_events if e["event"] == "create"]
        assert len(creates) == 1

    @pytest.mark.anyio
    async def test_update_inside_get_transaction(self, mixin_session):
        """UPDATE events fire with correct changes after get_transaction commit."""
        from fastapi_toolsets.db import get_transaction

        obj = WatchedModel(status="initial", other="x")
        mixin_session.add(obj)
        await mixin_session.commit()

        _test_events.clear()

        async with get_transaction(mixin_session):
            obj.status = "updated"

        await mixin_session.commit()

        updates = [e for e in _test_events if e["event"] == "update"]
        assert len(updates) == 1
        assert updates[0]["changes"]["status"] == {
            "old": "initial",
            "new": "updated",
        }


class TestEventSessionWithNullableFields:
    """Regression tests for nullable field access in callbacks (the original bug)."""

    @pytest.fixture(autouse=True)
    def clear_events(self):
        _attr_access_events.clear()
        yield
        _attr_access_events.clear()

    @pytest.mark.anyio
    async def test_nullable_field_none_in_create(self, mixin_session_expire):
        """Nullable field left as None is accessible in CREATE callback (expire_on_commit=True)."""
        obj = AttrAccessModel(name="test")
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()

        events = [e for e in _attr_access_events if e["event"] == "create"]
        assert len(events) == 1
        assert events[0]["callback_url"] is None
        assert events[0]["name"] == "test"

    @pytest.mark.anyio
    async def test_nullable_field_set_in_create(self, mixin_session_expire):
        """Nullable field with a value is accessible in CREATE callback (expire_on_commit=True)."""
        obj = AttrAccessModel(name="test", callback_url="https://hook.example.com")
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()

        events = [e for e in _attr_access_events if e["event"] == "create"]
        assert len(events) == 1
        assert events[0]["callback_url"] == "https://hook.example.com"

    @pytest.mark.anyio
    async def test_nullable_field_in_delete(self, mixin_session_expire):
        """Nullable field is accessible in DELETE callback via snapshot restore."""
        obj = AttrAccessModel(name="to-delete", callback_url="https://hook.example.com")
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()

        _attr_access_events.clear()

        await mixin_session_expire.delete(obj)
        await mixin_session_expire.commit()

        events = [e for e in _attr_access_events if e["event"] == "delete"]
        assert len(events) == 1
        assert events[0]["callback_url"] == "https://hook.example.com"
        assert events[0]["name"] == "to-delete"

    @pytest.mark.anyio
    async def test_nullable_field_updated_to_none(self, mixin_session_expire):
        """Nullable field changed to None is accessible in UPDATE callback."""
        obj = AttrAccessModel(name="x", callback_url="https://hook.example.com")
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()

        _attr_access_events.clear()

        obj.callback_url = None
        await mixin_session_expire.commit()

        events = [e for e in _attr_access_events if e["event"] == "update"]
        assert len(events) == 1
        assert events[0]["callback_url"] is None

    @pytest.mark.anyio
    async def test_nullable_field_updated_from_none(self, mixin_session_expire):
        """Nullable field changed from None to a value is accessible in UPDATE callback."""
        obj = AttrAccessModel(name="x")
        mixin_session_expire.add(obj)
        await mixin_session_expire.commit()

        _attr_access_events.clear()

        obj.callback_url = "https://new-hook.example.com"
        await mixin_session_expire.commit()

        events = [e for e in _attr_access_events if e["event"] == "update"]
        assert len(events) == 1
        assert events[0]["callback_url"] == "https://new-hook.example.com"


class TestEventSessionWithFastAPIDependency:
    """Verify EventSession works when session comes from create_db_dependency."""

    @pytest.fixture(autouse=True)
    def clear_events(self):
        _test_events.clear()
        yield
        _test_events.clear()

    @pytest.mark.anyio
    async def test_create_event_fires_via_dependency(self):
        """CREATE callback fires when session is provided by create_db_dependency."""
        from fastapi import Depends, FastAPI
        from httpx import ASGITransport, AsyncClient
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        from fastapi_toolsets.db import create_db_dependency
        from fastapi_toolsets.models import EventSession

        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=EventSession
        )

        async with engine.begin() as conn:
            await conn.run_sync(MixinBase.metadata.create_all)

        get_db = create_db_dependency(session_factory)
        app = FastAPI()

        @app.post("/watched")
        async def create_watched(session: AsyncSession = Depends(get_db)):
            obj = WatchedModel(status="from-api", other="x")
            session.add(obj)
            return {"id": str(obj.id)}

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.post("/watched")

            assert response.status_code == 200

            creates = [e for e in _test_events if e["event"] == "create"]
            assert len(creates) == 1
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(MixinBase.metadata.drop_all)
            await engine.dispose()

    @pytest.mark.anyio
    async def test_update_event_fires_via_dependency(self):
        """UPDATE callback fires when session is provided by create_db_dependency."""
        from fastapi import Depends, FastAPI
        from httpx import ASGITransport, AsyncClient
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        from fastapi_toolsets.db import create_db_dependency
        from fastapi_toolsets.models import EventSession

        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=EventSession
        )

        async with engine.begin() as conn:
            await conn.run_sync(MixinBase.metadata.create_all)

        get_db = create_db_dependency(session_factory)
        app = FastAPI()

        # Pre-seed an object.
        async with session_factory() as seed_session:
            obj = WatchedModel(status="initial", other="x")
            seed_session.add(obj)
            await seed_session.commit()
            obj_id = obj.id

        _test_events.clear()

        @app.put("/watched/{item_id}")
        async def update_watched(item_id: str, session: AsyncSession = Depends(get_db)):
            from sqlalchemy import select

            stmt = select(WatchedModel).where(WatchedModel.id == item_id)
            result = await session.execute(stmt)
            item = result.scalar_one()
            item.status = "updated-via-api"
            return {"ok": True}

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.put(f"/watched/{obj_id}")

            assert response.status_code == 200

            updates = [e for e in _test_events if e["event"] == "update"]
            assert len(updates) == 1
            assert updates[0]["changes"]["status"]["new"] == "updated-via-api"
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(MixinBase.metadata.drop_all)
            await engine.dispose()
