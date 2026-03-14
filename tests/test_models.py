"""Tests for fastapi_toolsets.models mixins."""

import uuid

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
