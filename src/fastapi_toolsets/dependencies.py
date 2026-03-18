"""Dependency factories for FastAPI routes."""

import inspect
import typing
from collections.abc import Callable
from typing import Any, cast

from fastapi import Depends
from fastapi.params import Depends as DependsClass
from sqlalchemy.ext.asyncio import AsyncSession

from .crud import CrudFactory
from .types import ModelType, SessionDependency

__all__ = ["BodyDependency", "PathDependency"]


def _unwrap_session_dep(session_dep: SessionDependency) -> Callable[..., Any]:
    """Extract the plain callable from ``Annotated[AsyncSession, Depends(fn)]`` if needed."""
    if typing.get_origin(session_dep) is typing.Annotated:
        for arg in typing.get_args(session_dep)[1:]:
            if isinstance(arg, DependsClass):
                return arg.dependency
    return session_dep


def PathDependency(
    model: type[ModelType],
    field: Any,
    *,
    session_dep: SessionDependency,
    param_name: str | None = None,
) -> ModelType:
    """Create a dependency that fetches a DB object from a path parameter.

    Args:
        model: SQLAlchemy model class
        field: Model field to filter by (e.g., User.id)
        session_dep: Session dependency function (e.g., get_db)
        param_name: Path parameter name (defaults to model_field, e.g., user_id)

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
    session_callable = _unwrap_session_dep(session_dep)
    crud = CrudFactory(model)
    name = (
        param_name
        if param_name is not None
        else "{}_{}".format(model.__name__.lower(), field.key)
    )
    python_type = field.type.python_type

    async def dependency(
        session: AsyncSession = Depends(session_callable), **kwargs: Any
    ) -> ModelType:
        value = kwargs[name]
        return await crud.get(session, filters=[field == value])

    setattr(
        dependency,
        "__signature__",
        inspect.Signature(
            parameters=[
                inspect.Parameter(
                    name, inspect.Parameter.KEYWORD_ONLY, annotation=python_type
                ),
                inspect.Parameter(
                    "session",
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=AsyncSession,
                    default=Depends(session_callable),
                ),
            ]
        ),
    )

    return cast(ModelType, Depends(cast(Callable[..., ModelType], dependency)))


def BodyDependency(
    model: type[ModelType],
    field: Any,
    *,
    session_dep: SessionDependency,
    body_field: str,
) -> ModelType:
    """Create a dependency that fetches a DB object from a body field.

    Args:
        model: SQLAlchemy model class
        field: Model field to filter by (e.g., User.id)
        session_dep: Session dependency function (e.g., get_db)
        body_field: Name of the field in the request body

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
    session_callable = _unwrap_session_dep(session_dep)
    crud = CrudFactory(model)
    python_type = field.type.python_type

    async def dependency(
        session: AsyncSession = Depends(session_callable), **kwargs: Any
    ) -> ModelType:
        value = kwargs[body_field]
        return await crud.get(session, filters=[field == value])

    setattr(
        dependency,
        "__signature__",
        inspect.Signature(
            parameters=[
                inspect.Parameter(
                    body_field, inspect.Parameter.KEYWORD_ONLY, annotation=python_type
                ),
                inspect.Parameter(
                    "session",
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=AsyncSession,
                    default=Depends(session_callable),
                ),
            ]
        ),
    )

    return cast(ModelType, Depends(cast(Callable[..., ModelType], dependency)))
