"""Field-change monitoring via SQLAlchemy session events."""

import inspect
from collections.abc import Callable
from enum import Enum
from typing import Any

from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value as _sa_set_committed_value

from ..logger import get_logger

_logger = get_logger()


class ModelEvent(str, Enum):
    """Event types dispatched by :class:`EventSession`."""

    CREATE = "create"
    DELETE = "delete"
    UPDATE = "update"


_CALLBACK_ERROR_MSG = "Event callback raised an unhandled exception"
_SESSION_CREATES = "_ft_creates"
_SESSION_DELETES = "_ft_deletes"
_SESSION_UPDATES = "_ft_updates"
_DEFERRED_STRATEGY_KEY = (("deferred", True), ("instrument", True))
_EVENT_HANDLERS: dict[tuple[type, ModelEvent], list[Callable[..., Any]]] = {}
_WATCHED_MODELS: set[type] = set()
_WATCHED_CACHE: dict[type, bool] = {}
_HANDLER_CACHE: dict[tuple[type, ModelEvent], list[Callable[..., Any]]] = {}


def _invalidate_caches() -> None:
    """Clear lookup caches after handler registration."""
    _WATCHED_CACHE.clear()
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
        _WATCHED_MODELS.add(model_class)
        _invalidate_caches()
        return fn

    return decorator


def _is_watched(obj: Any) -> bool:
    """Return True if *obj*'s type (or any ancestor) has registered handlers."""
    cls = type(obj)
    try:
        return _WATCHED_CACHE[cls]
    except KeyError:
        result = any(klass in _WATCHED_MODELS for klass in cls.__mro__)
        _WATCHED_CACHE[cls] = result
        return result


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


@event.listens_for(AsyncSession.sync_session_class, "after_flush")
def _after_flush(session: Any, flush_context: Any) -> None:
    # New objects: capture reference. Attributes will be refreshed after commit.
    for obj in session.new:
        if _is_watched(obj):
            session.info.setdefault(_SESSION_CREATES, []).append(obj)

    # Deleted objects: snapshot now while attributes are still loaded.
    for obj in session.deleted:
        if _is_watched(obj):
            snapshot = _snapshot_column_attrs(obj)
            session.info.setdefault(_SESSION_DELETES, []).append((obj, snapshot))

    # Dirty objects: read old/new from SQLAlchemy attribute history.
    for obj in session.dirty:
        if not _is_watched(obj):
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
            if history.has_changes() and history.deleted:
                changes[field] = {
                    "old": history.deleted[0],
                    "new": history.added[0] if history.added else None,
                }

        if changes:
            _upsert_changes(
                session.info.setdefault(_SESSION_UPDATES, {}),
                obj,
                changes,
            )


@event.listens_for(AsyncSession.sync_session_class, "after_rollback")
def _after_rollback(session: Any) -> None:
    if session.in_transaction():
        return
    session.info.pop(_SESSION_CREATES, None)
    session.info.pop(_SESSION_DELETES, None)
    session.info.pop(_SESSION_UPDATES, None)


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


async def _reload_if_present(session: AsyncSession, obj: Any, state: Any) -> None:
    """Re-populate *obj* from the DB if its row still exists."""
    await session.get(type(obj), state.key[1], populate_existing=True)


class EventSession(AsyncSession):
    """AsyncSession subclass that dispatches lifecycle callbacks after commit."""

    async def commit(self) -> None:  # noqa: C901
        await super().commit()

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

        # Dispatch CREATE callbacks.
        for obj in creates:
            try:
                state = sa_inspect(obj, raiseerr=False)
                if (
                    state is None or state.detached or state.transient
                ):  # pragma: no cover
                    continue
                await _reload_if_present(self, obj, state)
                for handler in _get_handlers(type(obj), ModelEvent.CREATE):
                    await _invoke_callback(handler, obj, ModelEvent.CREATE, None)
            except Exception as exc:
                _logger.error(_CALLBACK_ERROR_MSG, exc_info=exc)

        # Dispatch DELETE callbacks (restore snapshot; row is gone).
        for obj, snapshot in deletes:
            try:
                for key, value in snapshot.items():
                    _sa_set_committed_value(obj, key, value)
                for handler in _get_handlers(type(obj), ModelEvent.DELETE):
                    await _invoke_callback(handler, obj, ModelEvent.DELETE, None)
            except Exception as exc:
                _logger.error(_CALLBACK_ERROR_MSG, exc_info=exc)

        # Dispatch UPDATE callbacks.
        for obj, changes in field_changes.values():
            try:
                state = sa_inspect(obj, raiseerr=False)
                if (
                    state is None or state.detached or state.transient
                ):  # pragma: no cover
                    continue
                await _reload_if_present(self, obj, state)
                for handler in _get_handlers(type(obj), ModelEvent.UPDATE):
                    await _invoke_callback(handler, obj, ModelEvent.UPDATE, changes)
            except Exception as exc:
                _logger.error(_CALLBACK_ERROR_MSG, exc_info=exc)

    async def rollback(self) -> None:
        await super().rollback()
        self.info.pop(_SESSION_CREATES, None)
        self.info.pop(_SESSION_DELETES, None)
        self.info.pop(_SESSION_UPDATES, None)
