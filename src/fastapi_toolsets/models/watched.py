"""Field-change monitoring via SQLAlchemy session events."""

import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import Enum
from typing import Any

from sqlalchemy import event, select, tuple_
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction
from sqlalchemy.ext.asyncio import async_session as _async_session
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value as _sa_set_committed_value

from ..logger import get_logger

_logger = get_logger()


class ModelEvent(str, Enum):
    """Event types dispatched by :class:`EventSession`."""

    CREATE = "create"
    DELETE = "delete"
    UPDATE = "update"


_CALLBACK_ERROR_MSG = "Event callback raised an unhandled exception"
_RELOAD_TRANSACTION_ERROR_MSG = "Closing the post-commit reload transaction failed"
_SESSION_CREATES = "_ft_creates"
_SESSION_DELETES = "_ft_deletes"
_SESSION_UPDATES = "_ft_updates"
_SESSION_PRELOADED = "_ft_preloaded"
_DEFERRED_STRATEGY_KEY = (("deferred", True), ("instrument", True))
_EVENT_HANDLERS: dict[tuple[type, ModelEvent], list[Callable[..., Any]]] = {}
_HANDLER_CACHE: dict[tuple[type, ModelEvent], list[Callable[..., Any]]] = {}


def _invalidate_caches() -> None:
    """Clear lookup caches after handler registration."""
    _HANDLER_CACHE.clear()


def listens_for(
    model_class: type,
    event_types: list[ModelEvent] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a callback for one or more model lifecycle events.

    Args:
        model_class: The SQLAlchemy model class to listen on.
        event_types: List of :class:`ModelEvent` values to listen for.
            Defaults to all event types.
    """
    evs = event_types if event_types is not None else list(ModelEvent)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        for ev in evs:
            _EVENT_HANDLERS.setdefault((model_class, ev), []).append(fn)
        _invalidate_caches()
        return fn

    return decorator


def _get_handlers(cls: type, ev: ModelEvent) -> list[Callable[..., Any]]:
    """Return registered handlers for *cls* and *ev*, walking the MRO."""
    key = (cls, ev)
    try:
        return _HANDLER_CACHE[key]
    except KeyError:
        handlers: list[Callable[..., Any]] = []
        for klass in cls.__mro__:
            handlers.extend(_EVENT_HANDLERS.get((klass, ev), []))
        _HANDLER_CACHE[key] = handlers
        return handlers


def _snapshot_column_attrs(obj: Any) -> dict[str, Any]:
    """Read currently-loaded column values into a plain dict."""
    state = sa_inspect(obj)  # InstanceState
    state_dict = state.dict
    snapshot: dict[str, Any] = {}
    for prop in state.mapper.column_attrs:
        if prop.key in state_dict:
            snapshot[prop.key] = state_dict[prop.key]
        elif (  # pragma: no cover
            not state.expired
            and prop.strategy_key != _DEFERRED_STRATEGY_KEY
            and all(
                col.nullable
                and col.server_default is None
                and col.server_onupdate is None
                for col in prop.columns
            )
        ):
            snapshot[prop.key] = None
    return snapshot


def _get_watched_fields(cls: type) -> tuple[str, ...] | None:
    """Return the watched fields for *cls*."""
    fields = getattr(cls, "__watched_fields__", None)
    if fields is not None and (
        not isinstance(fields, tuple) or not all(isinstance(f, str) for f in fields)
    ):
        raise TypeError(
            f"{cls.__name__}.__watched_fields__ must be a tuple[str, ...], "
            f"got {type(fields).__name__}"
        )
    return fields


def _upsert_changes(
    pending: dict[int, tuple[Any, dict[str, dict[str, Any]]]],
    obj: Any,
    changes: dict[str, dict[str, Any]],
) -> None:
    """Insert or merge *changes* into *pending* for *obj*."""
    key = id(obj)
    if key in pending:
        existing = pending[key][1]
        for field, change in changes.items():
            if field in existing:
                existing[field]["new"] = change["new"]
            else:
                existing[field] = change
    else:
        pending[key] = (obj, changes)


def _dispatches(session: Any) -> bool:
    """True when *session* is driven by an :class:`EventSession`."""
    return isinstance(_async_session(session), EventSession)


@event.listens_for(AsyncSession.sync_session_class, "after_flush")
def _after_flush(session: Any, flush_context: Any) -> None:
    if _dispatches(session):
        _collect(session)


def _collect(session: Any) -> None:
    """Record the flushed changes that the next commit will dispatch."""
    # New objects: capture reference. Attributes will be refreshed after commit.
    for obj in session.new:
        if _get_handlers(type(obj), ModelEvent.CREATE):
            session.info.setdefault(_SESSION_CREATES, []).append(obj)
            _record_loaded_relationships(session, obj)

    # Deleted objects: snapshot now while attributes are still loaded.
    for obj in session.deleted:
        if _get_handlers(type(obj), ModelEvent.DELETE):
            snapshot = _snapshot_column_attrs(obj)
            session.info.setdefault(_SESSION_DELETES, []).append((obj, snapshot))

    # Dirty objects: read old/new from SQLAlchemy attribute history.
    for obj in session.dirty:
        if not _get_handlers(type(obj), ModelEvent.UPDATE):
            continue

        watched = _get_watched_fields(type(obj))
        changes: dict[str, dict[str, Any]] = {}

        inst_attrs = sa_inspect(obj).attrs
        attrs = (
            ((field, inst_attrs[field]) for field in watched)
            if watched is not None
            else ((s.key, s) for s in inst_attrs)
        )
        for field, attr_state in attrs:
            history = attr_state.history
            if not history.has_changes():
                continue
            change: dict[str, Any] = {
                "new": history.added[0] if history.added else None
            }
            if history.deleted:
                change["old"] = history.deleted[0]
            changes[field] = change

        if changes:
            _upsert_changes(
                session.info.setdefault(_SESSION_UPDATES, {}),
                obj,
                changes,
            )
            _record_loaded_relationships(session, obj)


@event.listens_for(AsyncSession.sync_session_class, "after_rollback")
def _after_rollback(session: Any) -> None:
    if session.in_transaction():
        return
    session.info.pop(_SESSION_CREATES, None)
    session.info.pop(_SESSION_DELETES, None)
    session.info.pop(_SESSION_UPDATES, None)
    session.info.pop(_SESSION_PRELOADED, None)


async def _invoke_callback(
    fn: Callable[..., Any],
    obj: Any,
    event_type: ModelEvent,
    changes: dict[str, dict[str, Any]] | None,
) -> None:
    """Call *fn* and await the result if it is awaitable."""
    result = fn(obj, event_type, changes)
    if inspect.isawaitable(result):
        await result


async def _dispatch(
    obj: Any,
    event_type: ModelEvent,
    changes: dict[str, dict[str, Any]] | None,
) -> None:
    """Run every handler for *obj*, isolating a failure to the handler that raised."""
    for handler in _get_handlers(type(obj), event_type):
        try:
            await _invoke_callback(handler, obj, event_type, changes)
        except Exception as exc:
            _logger.error(_CALLBACK_ERROR_MSG, exc_info=exc)


def _loaded_relationships(obj: Any) -> set[str]:
    """Relationship keys currently loaded on *obj*."""
    state = sa_inspect(obj, raiseerr=False)
    if state is None:
        return set()
    unloaded = state.unloaded
    return {
        rel.key
        for rel in state.mapper.relationships
        if rel.key not in unloaded and rel.lazy not in ("dynamic", "write_only")
    }


def _record_loaded_relationships(session: Any, obj: Any) -> None:
    """Merge the relationships loaded on *obj* into the session's record."""
    store: dict[int, set[str]] = session.info.setdefault(_SESSION_PRELOADED, {})
    store.setdefault(id(obj), set()).update(_loaded_relationships(obj))


def _snapshot_loaded_relationships(session: Any) -> dict[int, set[str]]:
    """Loaded relationships for the tracked objects, keyed by ``id``."""
    snapshot = {
        key: set(value)
        for key, value in session.info.get(_SESSION_PRELOADED, {}).items()
    }
    objs = list(session.info.get(_SESSION_CREATES, []))
    objs += [obj for obj, _ in session.info.get(_SESSION_UPDATES, {}).values()]
    for obj in objs:
        snapshot.setdefault(id(obj), set()).update(_loaded_relationships(obj))
    return snapshot


@contextmanager
def _suspended_trans_ctx(session: AsyncSession) -> Iterator[None]:
    """Allow post-commit SQL while an outer ``session.begin()`` block is open."""
    sync_session = session.sync_session
    ctx = getattr(sync_session, "_trans_context_manager", None)
    if ctx is None:
        yield
        return
    sync_session._trans_context_manager = None
    try:
        yield
    finally:
        sync_session._trans_context_manager = ctx


async def _batch_reload(
    session: AsyncSession,
    model: type,
    objs: list[Any],
    preloaded: dict[int, set[str]],
) -> None:
    """Re-populate all rows of *model* in one round trip."""
    pk_cols = sa_inspect(model, raiseerr=True).primary_key
    pk_tuples = [sa_inspect(obj).key[1] for obj in objs]
    where = (
        pk_cols[0].in_([pk[0] for pk in pk_tuples])
        if len(pk_cols) == 1
        else tuple_(*pk_cols).in_(pk_tuples)
    )
    q = select(model).where(where).execution_options(populate_existing=True)
    loaded: set[str] = set()
    for obj in objs:
        loaded |= preloaded.get(id(obj), set())
    if loaded:
        q = q.options(*(selectinload(getattr(model, key)) for key in loaded))
    await session.execute(q)


class _EventSessionTransaction(AsyncSessionTransaction):
    """Transaction context manager that dispatches on a real commit."""

    __slots__ = ()

    async def __aexit__(self, type_: object, value: object, traceback: object) -> None:
        session = self.session
        commits = (
            type_ is None
            and not self.nested
            and isinstance(session, EventSession)
            and self.is_active
        )
        preloaded = _snapshot_loaded_relationships(session) if commits else {}
        await super().__aexit__(type_, value, traceback)
        if commits:
            await session._dispatch_pending(preloaded)


class EventSession(AsyncSession):
    """AsyncSession subclass that dispatches lifecycle callbacks after commit."""

    def begin(self) -> AsyncSessionTransaction:
        """Return a transaction context manager that dispatches on commit."""
        return _EventSessionTransaction(self)

    def begin_nested(self) -> AsyncSessionTransaction:
        """Return a savepoint context manager; events wait for the real commit."""
        return _EventSessionTransaction(self, nested=True)

    async def commit(self) -> None:
        preloaded = _snapshot_loaded_relationships(self)
        await super().commit()
        await self._dispatch_pending(preloaded)

    async def _dispatch_pending(self, preloaded: dict[int, set[str]]) -> None:
        """Run the callbacks collected for the transaction that just committed."""
        # The commit itself flushes, so objects first collected there are only
        # recorded now; merge them into the pre-commit snapshot.
        for key, value in self.info.pop(_SESSION_PRELOADED, {}).items():
            preloaded.setdefault(key, set()).update(value)
        creates: list[Any] = self.info.pop(_SESSION_CREATES, [])
        deletes: list[tuple[Any, dict[str, Any]]] = self.info.pop(_SESSION_DELETES, [])
        field_changes: dict[int, tuple[Any, dict[str, dict[str, Any]]]] = self.info.pop(
            _SESSION_UPDATES, {}
        )

        if not creates and not deletes and not field_changes:
            return

        # Suppress transient objects (created + deleted in same transaction).
        if creates and deletes:
            created_ids = {id(o) for o in creates}
            deleted_ids = {id(o) for o, _ in deletes}
            transient_ids = created_ids & deleted_ids
            if transient_ids:
                creates = [o for o in creates if id(o) not in transient_ids]
                deletes = [(o, s) for o, s in deletes if id(o) not in transient_ids]
                field_changes = {
                    k: v for k, v in field_changes.items() if k not in transient_ids
                }

        # Suppress updates for deleted objects (row is gone, refresh would fail).
        if deletes and field_changes:
            deleted_ids = {id(o) for o, _ in deletes}
            field_changes = {
                k: v for k, v in field_changes.items() if k not in deleted_ids
            }

        # Suppress updates for newly created objects (CREATE-only semantics).
        if creates and field_changes:
            create_ids = {id(o) for o in creates}
            field_changes = {
                k: v for k, v in field_changes.items() if k not in create_ids
            }

        # Resolve reloadable state up front and group PKs by model type so
        # the post-commit reload is one query per type instead of one
        # session.get() per object.
        create_items: list[Any] = []
        update_items: list[tuple[Any, dict[str, dict[str, Any]]]] = []
        objs_by_type: dict[type, list[Any]] = {}

        for obj in creates:
            state = sa_inspect(obj, raiseerr=False)
            if state is None or state.detached or state.transient:  # pragma: no cover
                continue
            create_items.append(obj)
            objs_by_type.setdefault(type(obj), []).append(obj)

        for obj, changes in field_changes.values():
            state = sa_inspect(obj, raiseerr=False)
            if state is None or state.detached or state.transient:  # pragma: no cover
                continue
            update_items.append((obj, changes))
            objs_by_type.setdefault(type(obj), []).append(obj)

        with _suspended_trans_ctx(self):
            had_transaction = self.in_transaction()
            for model, objs in objs_by_type.items():
                try:
                    await _batch_reload(self, model, objs, preloaded)
                except Exception as exc:
                    _logger.error(_CALLBACK_ERROR_MSG, exc_info=exc)
            if not had_transaction and self.in_transaction():
                await self._end_reload_transaction()

            # Dispatch CREATE callbacks.
            for obj in create_items:
                await _dispatch(obj, ModelEvent.CREATE, None)

            # Dispatch DELETE callbacks (restore snapshot; row is gone).
            for obj, snapshot in deletes:
                try:
                    for key, value in snapshot.items():
                        _sa_set_committed_value(obj, key, value)
                except Exception as exc:
                    _logger.error(_CALLBACK_ERROR_MSG, exc_info=exc)
                    continue
                await _dispatch(obj, ModelEvent.DELETE, None)

            # Dispatch UPDATE callbacks.
            for obj, changes in update_items:
                await _dispatch(obj, ModelEvent.UPDATE, changes)

    async def _end_reload_transaction(self) -> None:
        """Commit the read-only transaction the reload opened, keeping state loaded."""
        sync_session = self.sync_session
        expire_on_commit = sync_session.expire_on_commit
        sync_session.expire_on_commit = False
        try:
            await super().commit()
        except Exception as exc:
            _logger.error(_RELOAD_TRANSACTION_ERROR_MSG, exc_info=exc)
        finally:
            sync_session.expire_on_commit = expire_on_commit

    async def rollback(self) -> None:
        await super().rollback()
        self.info.pop(_SESSION_CREATES, None)
        self.info.pop(_SESSION_DELETES, None)
        self.info.pop(_SESSION_UPDATES, None)
        self.info.pop(_SESSION_PRELOADED, None)
