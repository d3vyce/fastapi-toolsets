"""Tests for fastapi_toolsets.db module."""

import asyncio
import uuid

import pytest
from sqlalchemy import (
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Table,
    Uuid,
    select,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    selectinload,
)

from fastapi_toolsets.db import (
    LockMode,
    cleanup_tables,
    create_database,
    create_db_context,
    create_db_dependency,
    get_transaction,
    lock_tables,
    m2m_add,
    m2m_remove,
    m2m_set,
    wait_for_row_change,
)
from fastapi_toolsets.exceptions import NotFoundError
from fastapi_toolsets.pytest import create_db_session

from .conftest import DATABASE_URL, Base, Post, Role, RoleCrud, Tag, User, UserCrud


class TestCreateDbDependency:
    """Tests for create_db_dependency."""

    @pytest.mark.anyio
    async def test_yields_session(self):
        """Dependency yields a valid session."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db = create_db_dependency(session_factory)

        async for session in get_db():
            assert isinstance(session, AsyncSession)
            break

        await engine.dispose()

    @pytest.mark.anyio
    async def test_auto_commits_transaction(self):
        """Dependency auto-commits if transaction is active."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        try:
            get_db = create_db_dependency(session_factory)

            async for session in get_db():
                role = Role(name="test_role_dep")
                session.add(role)
                await session.flush()

            async with session_factory() as verify_session:
                result = await RoleCrud.first(
                    verify_session, [Role.name == "test_role_dep"]
                )
                assert result is not None
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

    @pytest.mark.anyio
    async def test_in_transaction_on_yield(self):
        """Session is already in a transaction when the endpoint body starts."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db = create_db_dependency(session_factory)

        async for session in get_db():
            assert session.in_transaction()
            break

        await engine.dispose()

    @pytest.mark.anyio
    async def test_no_commit_when_not_in_transaction(self):
        """Dependency skips commit if the session is no longer in a transaction on exit."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db = create_db_dependency(session_factory)

        async for session in get_db():
            # Manually commit — session exits the transaction
            await session.commit()
            assert not session.in_transaction()
            # The dependency's post-yield path must not call commit again (no error)

        await engine.dispose()

    @pytest.mark.anyio
    async def test_data_inside_lock_is_committed(self):
        """Changes made inside lock_tables are committed when the context exits."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        try:
            async with lock_tables(session_factory, [Role]) as session:
                role = Role(name="lock_committed")
                session.add(role)

            async with session_factory() as verify:
                result = await RoleCrud.first(verify, [Role.name == "lock_committed"])
                assert result is not None
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()


class TestCreateDbContext:
    """Tests for create_db_context."""

    @pytest.mark.anyio
    async def test_context_manager_yields_session(self):
        """Context manager yields a valid session."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        get_db_context = create_db_context(session_factory)

        async with get_db_context() as session:
            assert isinstance(session, AsyncSession)

        await engine.dispose()

    @pytest.mark.anyio
    async def test_context_manager_commits(self):
        """Context manager commits on exit."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        try:
            get_db_context = create_db_context(session_factory)

            async with get_db_context() as session:
                role = Role(name="context_role")
                session.add(role)
                await session.flush()

            async with session_factory() as verify_session:
                result = await RoleCrud.first(
                    verify_session, [Role.name == "context_role"]
                )
                assert result is not None
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()


class TestGetTransaction:
    """Tests for get_transaction context manager."""

    @pytest.mark.anyio
    async def test_starts_transaction(self, db_session: AsyncSession):
        """get_transaction starts a new transaction."""
        async with get_transaction(db_session):
            role = Role(name="tx_role")
            db_session.add(role)

        result = await RoleCrud.first(db_session, [Role.name == "tx_role"])
        assert result is not None

    @pytest.mark.anyio
    async def test_nested_transaction_uses_savepoint(self, db_session: AsyncSession):
        """Nested transactions use savepoints."""
        async with get_transaction(db_session):
            role1 = Role(name="outer_role")
            db_session.add(role1)
            await db_session.flush()

            async with get_transaction(db_session):
                role2 = Role(name="inner_role")
                db_session.add(role2)

        results = await RoleCrud.get_multi(db_session)
        names = {r.name for r in results}
        assert "outer_role" in names
        assert "inner_role" in names

    @pytest.mark.anyio
    async def test_rollback_on_exception(self, db_session: AsyncSession):
        """Transaction rolls back on exception."""
        try:
            async with get_transaction(db_session):
                role = Role(name="rollback_role")
                db_session.add(role)
                await db_session.flush()
                raise ValueError("Simulated error")
        except ValueError:
            pass

        result = await RoleCrud.first(db_session, [Role.name == "rollback_role"])
        assert result is None

    @pytest.mark.anyio
    async def test_nested_rollback_preserves_outer(self, db_session: AsyncSession):
        """Nested rollback preserves outer transaction."""
        async with get_transaction(db_session):
            role1 = Role(name="preserved_role")
            db_session.add(role1)
            await db_session.flush()

            try:
                async with get_transaction(db_session):
                    role2 = Role(name="rolled_back_role")
                    db_session.add(role2)
                    await db_session.flush()
                    raise ValueError("Inner error")
            except ValueError:
                pass

        outer = await RoleCrud.first(db_session, [Role.name == "preserved_role"])
        inner = await RoleCrud.first(db_session, [Role.name == "rolled_back_role"])
        assert outer is not None
        assert inner is None


class TestLockMode:
    """Tests for LockMode enum."""

    def test_lock_modes_exist(self):
        """All expected lock modes are defined."""
        assert LockMode.ACCESS_SHARE == "ACCESS SHARE"
        assert LockMode.ROW_SHARE == "ROW SHARE"
        assert LockMode.ROW_EXCLUSIVE == "ROW EXCLUSIVE"
        assert LockMode.SHARE_UPDATE_EXCLUSIVE == "SHARE UPDATE EXCLUSIVE"
        assert LockMode.SHARE == "SHARE"
        assert LockMode.SHARE_ROW_EXCLUSIVE == "SHARE ROW EXCLUSIVE"
        assert LockMode.EXCLUSIVE == "EXCLUSIVE"
        assert LockMode.ACCESS_EXCLUSIVE == "ACCESS EXCLUSIVE"

    def test_lock_mode_is_string(self):
        """Lock modes are string enums."""
        assert isinstance(LockMode.EXCLUSIVE, str)
        assert LockMode.EXCLUSIVE.value == "EXCLUSIVE"


class TestLockTables:
    """Tests for lock_tables context manager (PostgreSQL-specific)."""

    @pytest.mark.anyio
    async def test_lock_single_table(self, session_maker):
        """Lock a single table; changes inside are committed on context exit."""
        async with lock_tables(session_maker, [Role]) as session:
            role = Role(name="locked_role")
            session.add(role)

        async with session_maker() as verify:
            result = await RoleCrud.first(verify, [Role.name == "locked_role"])
            assert result is not None

    @pytest.mark.anyio
    async def test_lock_multiple_tables(self, session_maker):
        """Lock multiple tables."""
        async with lock_tables(session_maker, [Role, User]) as session:
            role = Role(name="multi_lock_role")
            session.add(role)

        async with session_maker() as verify:
            result = await RoleCrud.first(verify, [Role.name == "multi_lock_role"])
            assert result is not None

    @pytest.mark.anyio
    async def test_lock_with_custom_mode(self, session_maker):
        """Lock with custom lock mode."""
        async with lock_tables(
            session_maker, [Role], mode=LockMode.EXCLUSIVE
        ) as session:
            role = Role(name="exclusive_lock_role")
            session.add(role)

        async with session_maker() as verify:
            result = await RoleCrud.first(verify, [Role.name == "exclusive_lock_role"])
            assert result is not None

    @pytest.mark.anyio
    async def test_lock_rollback_on_exception(self, session_maker):
        """Lock context rolls back on exception."""
        try:
            async with lock_tables(session_maker, [Role]) as session:
                role = Role(name="lock_rollback_role")
                session.add(role)
                await session.flush()
                raise ValueError("Simulated error")
        except ValueError:
            pass

        async with session_maker() as verify:
            result = await RoleCrud.first(verify, [Role.name == "lock_rollback_role"])
            assert result is None


class TestWaitForRowChange:
    """Tests for wait_for_row_change polling function."""

    @pytest.mark.anyio
    async def test_detects_update(self, db_session: AsyncSession, engine):
        """Returns updated instance when a column value changes."""
        role = Role(name="watch_role")
        db_session.add(role)
        await db_session.commit()

        async def update_later():
            await asyncio.sleep(0.15)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as other:
                r = await other.get(Role, role.id)
                assert r is not None
                r.name = "updated_role"
                await other.commit()

        update_task = asyncio.create_task(update_later())
        result = await wait_for_row_change(db_session, Role, role.id, interval=0.05)
        await update_task

        assert result.name == "updated_role"

    @pytest.mark.anyio
    async def test_watches_specific_columns(self, db_session: AsyncSession, engine):
        """Only triggers on changes to specified columns."""
        user = User(username="testuser", email="test@example.com")
        db_session.add(user)
        await db_session.commit()

        async def update_later():
            factory = async_sessionmaker(engine, expire_on_commit=False)
            # First: change email (not watched) — should not trigger
            await asyncio.sleep(0.15)
            async with factory() as other:
                u = await other.get(User, user.id)
                assert u is not None
                u.email = "new@example.com"
                await other.commit()
            # Second: change username (watched) — should trigger
            await asyncio.sleep(0.15)
            async with factory() as other:
                u = await other.get(User, user.id)
                assert u is not None
                u.username = "newuser"
                await other.commit()

        update_task = asyncio.create_task(update_later())
        result = await wait_for_row_change(
            db_session, User, user.id, columns=["username"], interval=0.05
        )
        await update_task

        assert result.username == "newuser"
        assert result.email == "new@example.com"

    @pytest.mark.anyio
    async def test_nonexistent_row_raises(self, db_session: AsyncSession):
        """Raises NotFoundError when the row does not exist."""
        fake_id = uuid.uuid4()
        with pytest.raises(NotFoundError, match="not found"):
            await wait_for_row_change(db_session, Role, fake_id, interval=0.05)

    @pytest.mark.anyio
    async def test_timeout_raises(self, db_session: AsyncSession):
        """Raises TimeoutError when no change is detected within timeout."""
        role = Role(name="timeout_role")
        db_session.add(role)
        await db_session.commit()

        with pytest.raises(TimeoutError):
            await wait_for_row_change(
                db_session, Role, role.id, interval=0.05, timeout=0.2
            )

    @pytest.mark.anyio
    async def test_deleted_row_raises(self, db_session: AsyncSession, engine):
        """Raises NotFoundError when the row is deleted during polling."""
        role = Role(name="delete_role")
        db_session.add(role)
        await db_session.commit()

        async def delete_later():
            await asyncio.sleep(0.15)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as other:
                r = await other.get(Role, role.id)
                await other.delete(r)
                await other.commit()

        delete_task = asyncio.create_task(delete_later())
        with pytest.raises(NotFoundError):
            await wait_for_row_change(db_session, Role, role.id, interval=0.05)
        await delete_task


class TestCreateDatabase:
    """Tests for create_database."""

    @pytest.mark.anyio
    async def test_creates_database(self):
        """Database is created by create_database."""
        target_url = (
            make_url(DATABASE_URL)
            .set(database="test_create_db_general")
            .render_as_string(hide_password=False)
        )
        expected_db = make_url(target_url).database
        assert expected_db is not None

        engine = create_async_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f"DROP DATABASE IF EXISTS {expected_db}"))

            await create_database(db_name=expected_db, server_url=DATABASE_URL)

            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": expected_db},
                )
                assert result.scalar() == 1

            # Cleanup
            async with engine.connect() as conn:
                await conn.execute(text(f"DROP DATABASE IF EXISTS {expected_db}"))
        finally:
            await engine.dispose()


class TestCleanupTables:
    """Tests for cleanup_tables helper."""

    @pytest.mark.anyio
    async def test_truncates_all_tables(self):
        """All table rows are removed after cleanup_tables."""
        async with create_db_session(DATABASE_URL, Base, drop_tables=True) as session:
            role = Role(id=uuid.uuid4(), name="cleanup_role")
            session.add(role)
            await session.flush()

            user = User(
                id=uuid.uuid4(),
                username="cleanup_user",
                email="cleanup@test.com",
                role_id=role.id,
            )
            session.add(user)
            await session.commit()

            # Verify rows exist
            roles_count = await RoleCrud.count(session)
            users_count = await UserCrud.count(session)
            assert roles_count == 1
            assert users_count == 1

            await cleanup_tables(session, Base)

            # Verify tables are empty
            roles_count = await RoleCrud.count(session)
            users_count = await UserCrud.count(session)
            assert roles_count == 0
            assert users_count == 0

    @pytest.mark.anyio
    async def test_noop_for_empty_metadata(self):
        """cleanup_tables does not raise when metadata has no tables."""

        class EmptyBase(DeclarativeBase):
            pass

        async with create_db_session(DATABASE_URL, Base, drop_tables=True) as session:
            # Should not raise
            await cleanup_tables(session, EmptyBase)


class TestM2MAdd:
    """Tests for m2m_add helper."""

    @pytest.mark.anyio
    async def test_adds_single_related(self, db_session: AsyncSession):
        """Associates one related instance via the secondary table."""
        user = User(username="m2m_author", email="m2m@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post A", author_id=user.id)
        tag = Tag(name="python")
        db_session.add_all([post, tag])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag)

        result = await db_session.execute(
            select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
        )
        loaded = result.scalar_one()
        assert len(loaded.tags) == 1
        assert loaded.tags[0].id == tag.id

    @pytest.mark.anyio
    async def test_adds_multiple_related(self, db_session: AsyncSession):
        """Associates multiple related instances in a single call."""
        user = User(username="m2m_author2", email="m2m2@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post B", author_id=user.id)
        tag1 = Tag(name="web")
        tag2 = Tag(name="api")
        tag3 = Tag(name="async")
        db_session.add_all([post, tag1, tag2, tag3])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag1, tag2, tag3)

        result = await db_session.execute(
            select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
        )
        loaded = result.scalar_one()
        assert {t.id for t in loaded.tags} == {tag1.id, tag2.id, tag3.id}

    @pytest.mark.anyio
    async def test_noop_for_empty_related(self, db_session: AsyncSession):
        """Calling with no related instances is a no-op."""
        user = User(username="m2m_author3", email="m2m3@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post C", author_id=user.id)
        db_session.add(post)
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags)  # no related instances

        result = await db_session.execute(
            select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
        )
        loaded = result.scalar_one()
        assert loaded.tags == []

    @pytest.mark.anyio
    async def test_ignore_conflicts_true(self, db_session: AsyncSession):
        """Duplicate inserts are silently skipped when ignore_conflicts=True."""
        user = User(username="m2m_author4", email="m2m4@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post D", author_id=user.id)
        tag = Tag(name="duplicate_tag")
        db_session.add_all([post, tag])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag)

        # Second call with ignore_conflicts=True must not raise
        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag, ignore_conflicts=True)

        result = await db_session.execute(
            select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
        )
        loaded = result.scalar_one()
        assert len(loaded.tags) == 1

    @pytest.mark.anyio
    async def test_ignore_conflicts_false_raises(self, db_session: AsyncSession):
        """Duplicate inserts raise IntegrityError when ignore_conflicts=False (default)."""
        user = User(username="m2m_author5", email="m2m5@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post E", author_id=user.id)
        tag = Tag(name="conflict_tag")
        db_session.add_all([post, tag])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag)

        with pytest.raises(IntegrityError):
            async with get_transaction(db_session):
                await m2m_add(db_session, post, Post.tags, tag)

    @pytest.mark.anyio
    async def test_non_m2m_raises_type_error(self, db_session: AsyncSession):
        """Passing a non-M2M relationship attribute raises TypeError."""
        user = User(username="m2m_author6", email="m2m6@test.com")
        db_session.add(user)
        await db_session.flush()

        role = Role(name="type_err_role")
        db_session.add(role)
        await db_session.flush()

        with pytest.raises(TypeError, match="Many-to-Many"):
            await m2m_add(db_session, user, User.role, role)

    @pytest.mark.anyio
    async def test_works_inside_lock_tables(self, session_maker):
        """m2m_add works correctly inside a lock_tables context."""
        async with lock_tables(session_maker, [Tag]) as session:
            user = User(username="m2m_lock_author", email="m2m_lock@test.com")
            session.add(user)
            await session.flush()

            tag = Tag(name="locked_tag")
            session.add(tag)
            await session.flush()

            post = Post(title="Post Lock", author_id=user.id)
            session.add(post)
            await session.flush()

            await m2m_add(session, post, Post.tags, tag)

        async with session_maker() as verify:
            result = await verify.execute(
                select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
            )
            loaded = result.scalar_one()
            assert len(loaded.tags) == 1
            assert loaded.tags[0].name == "locked_tag"


class _LocalBase(DeclarativeBase):
    pass


_comp_assoc = Table(
    "_comp_assoc",
    _LocalBase.metadata,
    Column("owner_id", Uuid, ForeignKey("_comp_owners.id"), primary_key=True),
    Column("item_group", String(50), primary_key=True),
    Column("item_code", String(50), primary_key=True),
    ForeignKeyConstraint(
        ["item_group", "item_code"],
        ["_comp_items.group_id", "_comp_items.item_code"],
    ),
)


class _CompOwner(_LocalBase):
    __tablename__ = "_comp_owners"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    items: Mapped[list["_CompItem"]] = relationship(secondary=_comp_assoc)


class _CompItem(_LocalBase):
    __tablename__ = "_comp_items"
    group_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    item_code: Mapped[str] = mapped_column(String(50), primary_key=True)


class TestM2MRemove:
    """Tests for m2m_remove helper."""

    async def _setup(
        self, session: AsyncSession, username: str, email: str, *tag_names: str
    ):
        """Create a user, post, and tags; associate all tags with the post."""
        user = User(username=username, email=email)
        session.add(user)
        await session.flush()

        post = Post(title=f"Post {username}", author_id=user.id)
        tags = [Tag(name=n) for n in tag_names]
        session.add(post)
        session.add_all(tags)
        await session.flush()

        async with get_transaction(session):
            await m2m_add(session, post, Post.tags, *tags)

        return post, tags

    async def _load_tags(self, session: AsyncSession, post: Post) -> list[Tag]:
        result = await session.execute(
            select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
        )
        return result.scalar_one().tags

    @pytest.mark.anyio
    async def test_removes_single(self, db_session: AsyncSession):
        """Removes one association, leaving others intact."""
        post, (tag1, tag2) = await self._setup(
            db_session, "rm_author1", "rm1@test.com", "tag_rm_a", "tag_rm_b"
        )

        async with get_transaction(db_session):
            await m2m_remove(db_session, post, Post.tags, tag1)

        remaining = await self._load_tags(db_session, post)
        assert len(remaining) == 1
        assert remaining[0].id == tag2.id

    @pytest.mark.anyio
    async def test_removes_multiple(self, db_session: AsyncSession):
        """Removes multiple associations in one call."""
        post, (tag1, tag2, tag3) = await self._setup(
            db_session, "rm_author2", "rm2@test.com", "tag_rm_c", "tag_rm_d", "tag_rm_e"
        )

        async with get_transaction(db_session):
            await m2m_remove(db_session, post, Post.tags, tag1, tag3)

        remaining = await self._load_tags(db_session, post)
        assert len(remaining) == 1
        assert remaining[0].id == tag2.id

    @pytest.mark.anyio
    async def test_noop_for_empty_related(self, db_session: AsyncSession):
        """Calling with no related instances is a no-op."""
        post, (tag,) = await self._setup(
            db_session, "rm_author3", "rm3@test.com", "tag_rm_f"
        )

        async with get_transaction(db_session):
            await m2m_remove(db_session, post, Post.tags)

        remaining = await self._load_tags(db_session, post)
        assert len(remaining) == 1

    @pytest.mark.anyio
    async def test_idempotent_for_missing_association(self, db_session: AsyncSession):
        """Removing a non-existent association does not raise."""
        post, (tag1,) = await self._setup(
            db_session, "rm_author4", "rm4@test.com", "tag_rm_g"
        )
        tag2 = Tag(name="tag_rm_h")
        db_session.add(tag2)
        await db_session.flush()

        # tag2 was never associated — should not raise
        async with get_transaction(db_session):
            await m2m_remove(db_session, post, Post.tags, tag2)

        remaining = await self._load_tags(db_session, post)
        assert len(remaining) == 1

    @pytest.mark.anyio
    async def test_non_m2m_raises_type_error(self, db_session: AsyncSession):
        """Passing a non-M2M relationship attribute raises TypeError."""
        user = User(username="rm_author5", email="rm5@test.com")
        db_session.add(user)
        await db_session.flush()

        role = Role(name="rm_type_err_role")
        db_session.add(role)
        await db_session.flush()

        with pytest.raises(TypeError, match="Many-to-Many"):
            await m2m_remove(db_session, user, User.role, role)

    @pytest.mark.anyio
    async def test_removes_composite_pk_related(self):
        """Composite-PK branch: DELETE uses tuple IN when related side has multi-col PK."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(_LocalBase.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                owner = _CompOwner()
                item1 = _CompItem(group_id="g1", item_code="c1")
                item2 = _CompItem(group_id="g1", item_code="c2")
                session.add_all([owner, item1, item2])
                await session.flush()

                async with get_transaction(session):
                    await m2m_add(session, owner, _CompOwner.items, item1, item2)

                async with get_transaction(session):
                    await m2m_remove(session, owner, _CompOwner.items, item1)

                await session.commit()

            async with session_factory() as verify:
                from sqlalchemy import select
                from sqlalchemy.orm import selectinload

                result = await verify.execute(
                    select(_CompOwner)
                    .where(_CompOwner.id == owner.id)
                    .options(selectinload(_CompOwner.items))
                )
                loaded = result.scalar_one()
                assert len(loaded.items) == 1
                assert (loaded.items[0].group_id, loaded.items[0].item_code) == (
                    "g1",
                    "c2",
                )
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(_LocalBase.metadata.drop_all)
            await engine.dispose()


class TestM2MSet:
    """Tests for m2m_set helper."""

    async def _load_tags(self, session: AsyncSession, post: Post) -> list[Tag]:
        result = await session.execute(
            select(Post).where(Post.id == post.id).options(selectinload(Post.tags))
        )
        return result.scalar_one().tags

    @pytest.mark.anyio
    async def test_replaces_existing_set(self, db_session: AsyncSession):
        """Replaces the full association set atomically."""
        user = User(username="set_author1", email="set1@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post Set A", author_id=user.id)
        tag1 = Tag(name="tag_set_a")
        tag2 = Tag(name="tag_set_b")
        tag3 = Tag(name="tag_set_c")
        db_session.add_all([post, tag1, tag2, tag3])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag1, tag2)

        async with get_transaction(db_session):
            await m2m_set(db_session, post, Post.tags, tag3)

        remaining = await self._load_tags(db_session, post)
        assert len(remaining) == 1
        assert remaining[0].id == tag3.id

    @pytest.mark.anyio
    async def test_clears_all_when_no_related(self, db_session: AsyncSession):
        """Passing no related instances clears all associations."""
        user = User(username="set_author2", email="set2@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post Set B", author_id=user.id)
        tag = Tag(name="tag_set_d")
        db_session.add_all([post, tag])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_add(db_session, post, Post.tags, tag)

        async with get_transaction(db_session):
            await m2m_set(db_session, post, Post.tags)

        remaining = await self._load_tags(db_session, post)
        assert remaining == []

    @pytest.mark.anyio
    async def test_set_on_empty_then_populate(self, db_session: AsyncSession):
        """m2m_set works on a post with no existing associations."""
        user = User(username="set_author3", email="set3@test.com")
        db_session.add(user)
        await db_session.flush()

        post = Post(title="Post Set C", author_id=user.id)
        tag1 = Tag(name="tag_set_e")
        tag2 = Tag(name="tag_set_f")
        db_session.add_all([post, tag1, tag2])
        await db_session.flush()

        async with get_transaction(db_session):
            await m2m_set(db_session, post, Post.tags, tag1, tag2)

        remaining = await self._load_tags(db_session, post)
        assert {t.id for t in remaining} == {tag1.id, tag2.id}

    @pytest.mark.anyio
    async def test_non_m2m_raises_type_error(self, db_session: AsyncSession):
        """Passing a non-M2M relationship attribute raises TypeError."""
        user = User(username="set_author4", email="set4@test.com")
        db_session.add(user)
        await db_session.flush()

        role = Role(name="set_type_err_role")
        db_session.add(role)
        await db_session.flush()

        with pytest.raises(TypeError, match="Many-to-Many"):
            await m2m_set(db_session, user, User.role, role)
