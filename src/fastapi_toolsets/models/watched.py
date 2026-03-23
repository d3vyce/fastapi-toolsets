"""Field-change monitoring via SQLAlchemy session events."""

import asyncio
import weakref
from collections.abc import Awaitable
from enum import Enum
from typing import Any, TypeVar

from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value as _sa_set_committed_value

from ..logger import get_logger

__all__ = ["ModelEvent", "WatchedFieldsMixin", "watch"]

_logger = get_logger()
_T = TypeVar("_T")
_CALLBACK_ERROR_MSG = "WatchedFieldsMixin callback raised an unhandled exception"
_WATCHED_FIELDS: weakref.WeakKeyDictionary[type, list[str]] = (
    weakref.WeakKeyDictionary()
)
_SESSION_PENDING_NEW = "_ft_pending_new"
_SESSION_CREATES = "_ft_creates"
_SESSION_DELETES = "_ft_deletes"
_SESSION_UPDATES = "_ft_updates"


class ModelEvent(str, Enum):
    """Event types emitted by :class:`WatchedFieldsMixin`."""

    CREATE = "create"
    DELETE = "delete"
    UPDATE = "update"


def watch(*fields: str) -> Any:
    """Class decorator to filter which fields trigger ``on_update``.

    Args:
        *fields: One or more field names to watch. At least one name is required.

    Raises:
        ValueError: If called with no field names.
    """
    if not fields:
        raise ValueError("@watch requires at least one field name.")

    def decorator(cls: type[_T]) -> type[_T]:
        _WATCHED_FIELDS[cls] = list(fields)
        return cls

    return decorator


def _snapshot_column_attrs(obj: Any) -> dict[str, Any]:
    """Read currently-loaded column values into a plain dict."""
    state = sa_inspect(obj)  # InstanceState
    state_dict = state.dict
    return {
        prop.key: state_dict[prop.key]
        for prop in state.mapper.column_attrs
        if prop.key in state_dict
    }


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
    # New objects: capture references while session.new is still populated.
    # Values are read in _after_flush_postexec once RETURNING has been processed.
    for obj in session.new:
        if isinstance(obj, WatchedFieldsMixin):
            session.info.setdefault(_SESSION_PENDING_NEW, []).append(obj)

    # Deleted objects: capture before they leave the identity map.
    for obj in session.deleted:
        if isinstance(obj, WatchedFieldsMixin):
            session.info.setdefault(_SESSION_DELETES, []).append(obj)

    # Dirty objects: read old/new from SQLAlchemy attribute history.
    for obj in session.dirty:
        if not isinstance(obj, WatchedFieldsMixin):
            continue

        # None = not in dict = watch all fields; list = specific fields only
        watched = _WATCHED_FIELDS.get(type(obj))
        changes: dict[str, dict[str, Any]] = {}

        attrs = (
            # Specific fields
            ((field, sa_inspect(obj).attrs[field]) for field in watched)
            if watched is not None
            # All mapped fields
            else ((s.key, s) for s in sa_inspect(obj).attrs)
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


@event.listens_for(AsyncSession.sync_session_class, "after_flush_postexec")
def _after_flush_postexec(session: Any, flush_context: Any) -> None:
    # New objects are now persistent and RETURNING values have been applied,
    # so server defaults (id, created_at, …) are available via getattr.
    pending_new: list[Any] = session.info.pop(_SESSION_PENDING_NEW, [])
    if not pending_new:
        return
    session.info.setdefault(_SESSION_CREATES, []).extend(pending_new)


@event.listens_for(AsyncSession.sync_session_class, "after_rollback")
def _after_rollback(session: Any) -> None:
    get_nested = getattr(session, "get_nested_transaction", None)
    if get_nested is not None and get_nested() is not None:
        return

    session.info.pop(_SESSION_PENDING_NEW, None)
    session.info.pop(_SESSION_CREATES, None)
    session.info.pop(_SESSION_DELETES, None)
    session.info.pop(_SESSION_UPDATES, None)


def _task_error_handler(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and (exc := task.exception()):
        _logger.error(_CALLBACK_ERROR_MSG, exc_info=exc)


def _schedule_with_snapshot(
    loop: asyncio.AbstractEventLoop, obj: Any, fn: Any, *args: Any
) -> None:
    """Snapshot *obj*'s column attrs now (before expire_on_commit wipes them),
    then schedule a coroutine that restores the snapshot and calls *fn*.
    """
    snapshot = _snapshot_column_attrs(obj)

    async def _run(
        obj: Any = obj,
        fn: Any = fn,
        snapshot: dict[str, Any] = snapshot,
        args: tuple = args,
    ) -> None:
        for key, value in snapshot.items():
            _sa_set_committed_value(obj, key, value)
        try:
            result = fn(*args)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            _logger.error(_CALLBACK_ERROR_MSG, exc_info=exc)

    task = loop.create_task(_run())
    task.add_done_callback(_task_error_handler)


@event.listens_for(AsyncSession.sync_session_class, "after_commit")
def _after_commit(session: Any) -> None:
    get_nested = getattr(session, "get_nested_transaction", None)
    if get_nested is not None and get_nested() is not None:
        return

    creates: list[Any] = session.info.pop(_SESSION_CREATES, [])
    deletes: list[Any] = session.info.pop(_SESSION_DELETES, [])
    field_changes: dict[int, tuple[Any, dict[str, dict[str, Any]]]] = session.info.pop(
        _SESSION_UPDATES, {}
    )

    if not creates and not deletes and not field_changes:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    for obj in creates:
        _schedule_with_snapshot(loop, obj, obj.on_create)

    for obj in deletes:
        _schedule_with_snapshot(loop, obj, obj.on_delete)

    for obj, changes in field_changes.values():
        _schedule_with_snapshot(loop, obj, obj.on_update, changes)


class WatchedFieldsMixin:
    """Mixin that enables lifecycle callbacks for SQLAlchemy models."""

    def on_event(
        self, event: ModelEvent, changes: dict[str, dict[str, Any]] | None = None
    ) -> Awaitable[None] | None:
        """Catch-all callback fired for every lifecycle event.

        Args:
            event: The event type (:attr:`ModelEvent.CREATE`, :attr:`ModelEvent.DELETE`,
                or :attr:`ModelEvent.UPDATE`).
            changes: Field changes for :attr:`ModelEvent.UPDATE`, ``None`` otherwise.
        """

    def on_create(self) -> Awaitable[None] | None:
        """Called after INSERT commit."""
        return self.on_event(ModelEvent.CREATE)

    def on_delete(self) -> Awaitable[None] | None:
        """Called after DELETE commit."""
        return self.on_event(ModelEvent.DELETE)

    def on_update(self, changes: dict[str, dict[str, Any]]) -> Awaitable[None] | None:
        """Called after UPDATE commit when watched fields change."""
        return self.on_event(ModelEvent.UPDATE, changes=changes)
