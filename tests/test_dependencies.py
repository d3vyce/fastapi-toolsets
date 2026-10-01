"""Tests for ``PathDependency`` and ``BodyDependency``: signature and fetching."""

import inspect
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Annotated, Any, cast

import pytest
from fastapi.params import Depends
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi_toolsets.crud import CrudFactory
from fastapi_toolsets.dependencies import (
    BodyDependency,
    PathDependency,
    _unwrap_session_dep,
)

from .conftest import Role, RoleCreate, RoleCrud, User, create_role, create_user


async def mock_get_db() -> AsyncGenerator[AsyncSession, None]:
    """Session dependency stand-in; the tests pass the session explicitly."""
    yield None  # type: ignore[misc]  # ty:ignore[invalid-yield]


MockSessionDep = Annotated[AsyncSession, Depends(mock_get_db)]
_AnnotatedNoDepends = Annotated[AsyncSession, "not_a_depends"]


def _path(**kwargs: Any) -> Any:
    """``PathDependency`` on ``User.id`` with the default session dep."""
    return PathDependency(User, User.id, session_dep=mock_get_db, **kwargs)


def _body(**kwargs: Any) -> Any:
    """``BodyDependency`` on ``User.id`` bound to the ``user_id`` body field."""
    return BodyDependency(
        User, User.id, session_dep=mock_get_db, body_field="user_id", **kwargs
    )


def _signature(dep: Any) -> inspect.Signature:
    return inspect.signature(dep.dependency)


async def _user_with_role(session: AsyncSession) -> User:
    role = await create_role(session, "load_opts_role")
    user = await create_user(session, "load_opts", role_id=role.id)
    session.expunge_all()
    return user


@pytest.mark.parametrize(
    ("session_dep", "expected"),
    [
        (mock_get_db, mock_get_db),
        (MockSessionDep, mock_get_db),
        (_AnnotatedNoDepends, _AnnotatedNoDepends),
    ],
    ids=["plain_callable", "annotated_depends", "annotated_without_depends"],
)
def test_unwrap_session_dep(session_dep: Any, expected: Any):
    """Only ``Annotated[..., Depends(fn)]`` is unwrapped; anything else passes through."""
    assert _unwrap_session_dep(session_dep) is expected


class TestSignature:
    """Both factories expose a lookup parameter and a ``session`` parameter."""

    @pytest.mark.parametrize(
        ("dep", "param", "annotation"),
        [
            (
                PathDependency(Role, Role.id, session_dep=mock_get_db),
                "role_id",
                uuid.UUID,
            ),
            (
                PathDependency(
                    Role, Role.id, session_dep=mock_get_db, param_name="role_uuid"
                ),
                "role_uuid",
                uuid.UUID,
            ),
            (
                PathDependency(User, User.username, session_dep=mock_get_db),
                "user_username",
                str,
            ),
            (
                BodyDependency(
                    Role, Role.id, session_dep=mock_get_db, body_field="role_id"
                ),
                "role_id",
                uuid.UUID,
            ),
            (
                BodyDependency(
                    User, User.id, session_dep=mock_get_db, body_field="user_uuid"
                ),
                "user_uuid",
                uuid.UUID,
            ),
        ],
        ids=[
            "path/default_name",
            "path/custom_name",
            "path/string_field",
            "body/role_id",
            "body/custom_field",
        ],
    )
    def test_lookup_param_and_session(self, dep: Any, param: str, annotation: type):
        """The param is named after the model field (or the override) and typed by it."""
        assert isinstance(dep, Depends)
        sig = _signature(dep)

        assert set(sig.parameters) == {param, "session"}
        assert sig.parameters[param].annotation is annotation
        assert sig.parameters["session"].annotation is AsyncSession
        assert isinstance(sig.parameters["session"].default, Depends)
        assert sig.parameters["session"].default.dependency is mock_get_db

    @pytest.mark.parametrize(
        "dep",
        [
            PathDependency(Role, Role.id, session_dep=MockSessionDep),
            BodyDependency(
                Role, Role.id, session_dep=MockSessionDep, body_field="role_id"
            ),
        ],
        ids=["path", "body"],
    )
    def test_annotated_session_dep_is_unwrapped(self, dep: Any):
        """``Annotated[AsyncSession, Depends(fn)]`` injects ``fn``, not the alias."""
        assert isinstance(dep, Depends)
        session = _signature(dep).parameters["session"]

        assert isinstance(session.default, Depends)
        assert session.default.dependency is mock_get_db


class TestFetch:
    """The generated dependency fetches one row by the lookup parameter."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "make_dep",
        [
            lambda dep: PathDependency(Role, Role.id, session_dep=dep),
            lambda dep: BodyDependency(
                Role, Role.id, session_dep=dep, body_field="role_id"
            ),
        ],
        ids=["path", "body"],
    )
    @pytest.mark.parametrize(
        "session_dep", [mock_get_db, MockSessionDep], ids=["plain", "annotated"]
    )
    async def test_fetches_row_by_field(
        self, db_session: AsyncSession, make_dep: Callable[..., Any], session_dep: Any
    ):
        role = await RoleCrud.create(db_session, RoleCreate(name="test_role"))
        dep = make_dep(session_dep)

        result = await dep.dependency(session=db_session, role_id=role.id)

        assert result.id == role.id
        assert result.name == "test_role"

    @pytest.mark.anyio
    async def test_bare_crud_leaves_relation_unloaded(self, db_session: AsyncSession):
        """Baseline for the load-option tests: without options nothing is eager-loaded."""
        user = await _user_with_role(db_session)

        result = await _path().dependency(session=db_session, user_id=user.id)

        assert "role" in sa_inspect(result).unloaded

    @pytest.mark.anyio
    @pytest.mark.parametrize("factory", [_path, _body], ids=["path", "body"])
    @pytest.mark.parametrize("option", ["load_options", "crud"])
    async def test_load_options_and_crud_eager_load(
        self, db_session: AsyncSession, factory: Callable[..., Any], option: str
    ):
        """Both ``load_options=`` and a configured ``crud=`` eager-load the relation."""
        user = await _user_with_role(db_session)
        eager = [selectinload(User.role)]
        value: Any = eager
        if option == "crud":
            value = CrudFactory(User, default_load_options=eager)
        dep = factory(**{option: value})

        result = await dep.dependency(session=db_session, user_id=user.id)

        assert "role" not in sa_inspect(result).unloaded
        assert result.role.name == "load_opts_role"

    def test_crud_bound_to_another_model_is_rejected(self):
        """A crud= for a different model would silently query the wrong table.

        ``ty`` rejects this statically; the runtime guard covers untyped callers.
        """
        with pytest.raises(ValueError, match="bound to Role, not User"):
            _path(crud=cast(Any, RoleCrud))
