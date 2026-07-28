"""The ``Database`` facade: session lifecycle, dependency, middleware, transactions."""

from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from pydantic import PostgresDsn
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..exceptions import PoolExhaustedError
from .locks import LockMode, lock_tables


@asynccontextmanager
async def transaction(
    session: AsyncSession,
) -> AsyncGenerator[AsyncSession, None]:
    """Run a block inside a savepoint-aware transaction.

    If *session* is already in a transaction, a nested transaction (savepoint)
    is opened so the block can roll back independently. Otherwise a top-level
    transaction is started. Commits on clean exit, rolls back on exception.

    Args:
        session: AsyncSession instance.

    Yields:
        The session within the transaction context.

    Example:
        ```python
        from fastapi_toolsets.db import transaction

        async with transaction(session):
            session.add(model)
        ```
    """
    if session.in_transaction():
        async with session.begin_nested():
            yield session
    else:
        async with session.begin():
            yield session


class _CommitOnResponseMiddleware:
    """Commit the request's DB session before the response is sent."""

    def __init__(self, app: ASGIApp, *, state_attr: str) -> None:
        self.app = app
        self.state_attr = state_attr

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                # ``scope["state"]`` is the same dict ``request.state`` writes
                # to, so this is the session stashed by the dependency.
                state = scope.get("state")
                session = state.get(self.state_attr) if state else None
                if session is not None and session.in_transaction():
                    await session.commit()
            await send(message)

        await self.app(scope, receive, send_wrapper)


class Database:
    """One object that owns the engine, sessions, dependency, and middleware.

    Provide exactly one of *url* (the facade builds and disposes the engine) or
    *engine* (an engine you own, e.g. for Alembic or ``event.listen``, left
    untouched).

    Args:
        url: Database connection URL. Accepts a plain string or a Pydantic
            :class:`~pydantic.PostgresDsn`.
        engine: An existing :class:`AsyncEngine` to reuse instead of *url*.
        session_class: Session class for the sessionmaker (e.g. ``EventSession``).
        expire_on_commit: Expire attributes after commit. Defaults to ``False``.
        autoflush: Autoflush the session before queries. Defaults to ``True``.
        connect_args: DBAPI-level connection arguments forwarded to
            :func:`create_async_engine` (URL mode only).
        **engine_options: Extra keyword arguments forwarded to
            :func:`create_async_engine` (URL mode only).

    Raises:
        TypeError: If neither or both of *url* and *engine* are given, or if
            *connect_args*/*engine_options* are passed together with *engine*.

    Example:
        ```python
        from fastapi import Depends, FastAPI
        from fastapi_toolsets.db import Database

        db = Database("postgresql+asyncpg://postgres:postgres@localhost/app")

        app = FastAPI()
        db.install(app)

        @app.get("/users/{user_id}")
        async def get_user(user_id: int, session=Depends(db)):
            return await UserCrud.get(session, [User.id == user_id])
        ```
    """

    def __init__(
        self,
        url: str | PostgresDsn | None = None,
        *,
        engine: AsyncEngine | None = None,
        session_class: type[AsyncSession] = AsyncSession,
        expire_on_commit: bool = False,
        autoflush: bool = True,
        connect_args: dict[str, Any] | None = None,
        **engine_options: Any,
    ) -> None:
        if (url is None) == (engine is None):
            raise TypeError(
                "Database requires exactly one of 'url' or 'engine' "
                "(got both or neither)."
            )
        if engine is not None and (engine_options or connect_args is not None):
            raise TypeError(
                "connect_args/engine_options are only valid in URL mode; "
                "configure the engine you pass via 'engine=' yourself."
            )

        if engine is not None:
            self._owns_engine = False
            self.engine: AsyncEngine = engine
        else:
            assert url is not None  # guaranteed by the XOR check above
            self._owns_engine = True
            if connect_args is not None:
                engine_options["connect_args"] = connect_args
            # ``PostgresDsn`` (and other URL objects) are not str subclasses, so
            # coerce to the string form SQLAlchemy expects.
            self.engine = create_async_engine(str(url), **engine_options)
        self._sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine,
            class_=session_class,
            expire_on_commit=expire_on_commit,
            autoflush=autoflush,
        )
        # Private, per-instance state attribute; cannot collide with another
        # Database or be mismatched against the middleware.
        self._state_attr = f"_ft_db_session_{id(self):x}"
        self._middleware_installed = False
        self._disposed = False

    async def _dispose(self) -> None:
        """Dispose the engine once, only if we own it (idempotent)."""
        if self._owns_engine and not self._disposed:
            self._disposed = True
            await self.engine.dispose()

    @asynccontextmanager
    async def lifespan(self, app: Any) -> AsyncGenerator[None, None]:
        """Dispose the engine on shutdown; use as ``FastAPI(lifespan=db.lifespan)``.

        Args:
            app: The ASGI application (unused; required by the lifespan protocol).

        Yields:
            Control to the application for its lifetime.

        Example:
            ```python
            app = FastAPI(lifespan=db.lifespan)
            ```
        """
        try:
            yield
        finally:
            await self._dispose()

    def install(self, app: Any) -> None:
        """Wire the commit middleware and engine disposal onto *app*.

        Args:
            app: The FastAPI/Starlette application to wire.

        Example:
            ```python
            @asynccontextmanager
            async def lifespan(app):
                ...  # your startup
                yield
                ...  # your shutdown

            app = FastAPI(lifespan=lifespan)
            db.install(app)
            ```
        """
        app.add_middleware(_CommitOnResponseMiddleware, state_attr=self._state_attr)
        self._middleware_installed = True

        inner_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def _composed(app_: Any) -> AsyncGenerator[None, None]:
            async with self.lifespan(app_), inner_lifespan(app_):
                yield

        app.router.lifespan_context = _composed

    @asynccontextmanager
    async def _open(self) -> AsyncGenerator[AsyncSession, None]:
        """Open a session and eagerly acquire a connection (fail-fast on pool)."""
        async with self._sessionmaker() as session:
            try:
                await session.connection()
            except sa_exc.TimeoutError as e:
                raise PoolExhaustedError() from e
            yield session

    async def __call__(self, request: Request) -> AsyncGenerator[AsyncSession, None]:
        """FastAPI dependency: yield a session and commit once at the right time.

        Args:
            request: The incoming request (injected by FastAPI).

        Yields:
            An AsyncSession for the duration of the request.

        Example:
            ```python
            @app.get("/users/{user_id}")
            async def get_user(user_id: int, session=Depends(db)):
                return await UserCrud.get(session, [User.id == user_id])
            ```
        """
        async with self._open() as session:
            setattr(request.state, self._state_attr, session)
            yield session
            if not self._middleware_installed and session.in_transaction():
                await session.commit()

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Open a session outside request handlers (background tasks, CLI, tests).

        Commits on clean exit, rolls back on exception.

        Yields:
            An AsyncSession ready for database operations.

        Example:
            ```python
            async with db.session() as session:
                user = await UserCrud.get(session, [User.id == 1])
            ```
        """
        async with self._open() as session:
            yield session
            if session.in_transaction():
                await session.commit()

    @asynccontextmanager
    async def begin(self) -> AsyncGenerator[AsyncSession, None]:
        """Open a session already inside a transaction (sugar for the common case).

        Equivalent to ``session()`` + :func:`transaction`. Commits on clean exit,
        rolls back on exception.

        Yields:
            An AsyncSession open within a transaction.

        Example:
            ```python
            async with db.begin() as session:
                session.add(User(name="ada"))
            ```
        """
        async with self.session() as session, transaction(session):
            yield session

    def lock_tables(
        self,
        tables: list[type[DeclarativeBase]],
        *,
        mode: LockMode = LockMode.SHARE_UPDATE_EXCLUSIVE,
        timeout: str = "5s",
    ) -> AbstractAsyncContextManager[AsyncSession]:
        """Lock PostgreSQL tables for the duration of a dedicated transaction.

        Opens its own session from the facade's sessionmaker, changes are
        committed when the context exits.

        Args:
            tables: List of SQLAlchemy model classes to lock.
            mode: Lock mode (default: ``SHARE UPDATE EXCLUSIVE``).
            timeout: Lock timeout (default: ``"5s"``).

        Yields:
            The dedicated session, open within the locked transaction.

        Raises:
            LockTimeoutError: If the lock cannot be acquired within *timeout*.
            PoolExhaustedError: If the connection pool is exhausted.

        Example:
            ```python
            async with db.lock_tables([User, Account]) as session:
                user = await UserCrud.get(session, [User.id == 1])
                user.balance += 100
            ```
        """
        return lock_tables(self._sessionmaker, tables, mode=mode, timeout=timeout)
