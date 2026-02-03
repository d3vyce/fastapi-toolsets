"""Dependency factories for FastAPI routes."""

import inspect
from collections.abc import AsyncGenerator, Callable
from typing import Any, TypeVar, cast

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from ..crud import CrudFactory

ModelType = TypeVar("ModelType", bound=DeclarativeBase)
SessionDependency = Callable[[], AsyncGenerator[AsyncSession, None]]


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
        UserDep = PathDependency(User, User.id, session_dep=get_db)

        @router.get("/user/{id}")
        async def get(
            user: User = UserDep,
        ): ...
    """
    crud = CrudFactory(model)
    name = (
        param_name
        if param_name is not None
        else "{}_{}".format(model.__name__.lower(), field.key)
    )
    python_type = field.type.python_type

    async def dependency(
        session: AsyncSession = Depends(session_dep), **kwargs: Any
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
                    default=Depends(session_dep),
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
        UserDep = BodyDependency(
            User, User.ctfd_id, session_dep=get_db, body_field="user_id"
        )

        @router.post("/assign")
        async def assign(
            user: User = UserDep,
        ): ...
    """
    crud = CrudFactory(model)
    python_type = field.type.python_type

    async def dependency(
        session: AsyncSession = Depends(session_dep), **kwargs: Any
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
                    default=Depends(session_dep),
                ),
            ]
        ),
    )

    return cast(ModelType, Depends(cast(Callable[..., ModelType], dependency)))
