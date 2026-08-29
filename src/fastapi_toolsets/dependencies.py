"""Dependency factories for FastAPI routes."""

import inspect
import typing
from collections.abc import Callable, Sequence
from typing import Any, cast

from fastapi import Depends
from fastapi.params import Depends as DependsClass
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.base import ExecutableOption

from .crud import AsyncCrud, CrudFactory
from .types import ModelType, SessionDependency

__all__ = ["BodyDependency", "PathDependency"]


def _unwrap_session_dep(session_dep: SessionDependency) -> Callable[..., Any]:
    """Extract the plain callable from ``Annotated[AsyncSession, Depends(fn)]`` if needed."""
    if typing.get_origin(session_dep) is typing.Annotated:
        for arg in typing.get_args(session_dep)[1:]:
            if isinstance(arg, DependsClass):
                return arg.dependency
    return session_dep


def _fetch_dependency(
    model: type[ModelType],
    field: Any,
    *,
    session_dep: SessionDependency,
    param_name: str,
    crud: type[AsyncCrud[ModelType]] | None,
    load_options: Sequence[ExecutableOption] | None,
) -> ModelType:
    """Build a Depends() that fetches one row by ``field == <param_name>``."""
    session_callable = _unwrap_session_dep(session_dep)
    if crud is not None and crud.model is not model:
        raise ValueError(
            f"crud is bound to {crud.model.__name__}, not {model.__name__}"
        )
    crud = crud or CrudFactory(model)

    # `session` has no default here: the __signature__ override below is what
    # FastAPI reads, and it always passes `session` explicitly.
    async def dependency(session: AsyncSession, **kwargs: Any) -> ModelType:
        return await crud.get(
            session,
            filters=[field == kwargs[param_name]],
            load_options=load_options,
        )

    dependency.__signature__ = inspect.Signature(  # ty:ignore[unresolved-attribute]
        parameters=[
            inspect.Parameter(
                param_name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=field.type.python_type,
            ),
            inspect.Parameter(
                "session",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=AsyncSession,
                default=Depends(session_callable),
            ),
        ]
    )

    return cast(ModelType, Depends(cast(Callable[..., ModelType], dependency)))


def PathDependency(
    model: type[ModelType],
    field: Any,
    *,
    session_dep: SessionDependency,
    param_name: str | None = None,
    crud: type[AsyncCrud[ModelType]] | None = None,
    load_options: Sequence[ExecutableOption] | None = None,
) -> ModelType:
    """Create a dependency that fetches a DB object from a path parameter.

    Args:
        model: SQLAlchemy model class
        field: Model field to filter by (e.g., User.id)
        session_dep: Session dependency function (e.g., get_db)
        param_name: Path parameter name (defaults to model_field, e.g., user_id)
        crud: Existing CRUD class to fetch with, so its ``default_load_options``
            apply. Defaults to a bare ``CrudFactory(model)``.
        load_options: SQLAlchemy loader options for the fetch. Overrides the CRUD's
            ``default_load_options`` entirely rather than merging with them.

    Returns:
        A Depends() instance that resolves to the model instance

    Raises:
        NotFoundError: If no matching record is found

    Example:
        ```python
        UserDep = PathDependency(User, User.id, session_dep=get_db)

        @router.get("/user/{id}")
        async def get(
            user: User = UserDep,
        ): ...
        ```
    """
    return _fetch_dependency(
        model,
        field,
        session_dep=session_dep,
        param_name=param_name or f"{model.__name__.lower()}_{field.key}",
        crud=crud,
        load_options=load_options,
    )


def BodyDependency(
    model: type[ModelType],
    field: Any,
    *,
    session_dep: SessionDependency,
    body_field: str,
    crud: type[AsyncCrud[ModelType]] | None = None,
    load_options: Sequence[ExecutableOption] | None = None,
) -> ModelType:
    """Create a dependency that fetches a DB object from a body field.

    Args:
        model: SQLAlchemy model class
        field: Model field to filter by (e.g., User.id)
        session_dep: Session dependency function (e.g., get_db)
        body_field: Name of the field in the request body
        crud: Existing CRUD class to fetch with, so its ``default_load_options``
            apply. Defaults to a bare ``CrudFactory(model)``.
        load_options: SQLAlchemy loader options for the fetch. Overrides the CRUD's
            ``default_load_options`` entirely rather than merging with them.

    Returns:
        A Depends() instance that resolves to the model instance

    Raises:
        NotFoundError: If no matching record is found

    Example:
        ```python
        UserDep = BodyDependency(
            User, User.ctfd_id, session_dep=get_db, body_field="user_id"
        )

        @router.post("/assign")
        async def assign(
            user: User = UserDep,
        ): ...
        ```
    """
    return _fetch_dependency(
        model,
        field,
        session_dep=session_dep,
        param_name=body_field,
        crud=crud,
        load_options=load_options,
    )
