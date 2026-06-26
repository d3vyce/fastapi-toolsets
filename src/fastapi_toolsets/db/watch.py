"""Row-watching helpers: poll a database row until it changes."""

import asyncio
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from ..exceptions import NotFoundError

_M = TypeVar("_M", bound=DeclarativeBase)


async def wait_for_row_change(
    session: AsyncSession,
    model: type[_M],
    pk_value: Any,
    *,
    columns: list[str] | None = None,
    interval: float = 0.5,
    timeout: float | None = None,
) -> _M:
    """Poll a database row until a change is detected.

    Queries the row every ``interval`` seconds and returns the model instance
    once a change is detected in any column (or only the specified ``columns``).

    Args:
        session: AsyncSession instance.
        model: SQLAlchemy model class.
        pk_value: Primary key value of the row to watch.
        columns: Optional list of column names to watch. If None, all columns
            are watched.
        interval: Polling interval in seconds (default: 0.5).
        timeout: Maximum time to wait in seconds. None means wait forever.

    Returns:
        The refreshed model instance with updated values.

    Raises:
        NotFoundError: If the row does not exist or is deleted during polling.
        TimeoutError: If timeout expires before a change is detected.

    Example:
        ```python
        from fastapi_toolsets.db import wait_for_row_change

        # Wait for any column to change
        updated = await wait_for_row_change(session, User, user_id)

        # Watch specific columns with a timeout
        updated = await wait_for_row_change(
            session, User, user_id,
            columns=["status", "email"],
            interval=1.0,
            timeout=30.0,
        )
        ```
    """

    async def _reload() -> _M | None:
        await session.rollback()
        return await session.get(model, pk_value, populate_existing=True)

    instance = await _reload()
    if instance is None:
        raise NotFoundError(f"{model.__name__} with pk={pk_value!r} not found")

    if columns is not None:
        watch_cols = columns
    else:
        watch_cols = [attr.key for attr in model.__mapper__.column_attrs]

    initial = {col: getattr(instance, col) for col in watch_cols}

    elapsed = 0.0
    while True:
        await asyncio.sleep(interval)
        elapsed += interval

        if timeout is not None and elapsed >= timeout:
            raise TimeoutError(
                f"No change detected on {model.__name__} "
                f"with pk={pk_value!r} within {timeout}s"
            )

        instance = await _reload()

        if instance is None:
            raise NotFoundError(f"{model.__name__} with pk={pk_value!r} was deleted")

        current = {col: getattr(instance, col) for col in watch_cols}
        if current != initial:
            return instance
