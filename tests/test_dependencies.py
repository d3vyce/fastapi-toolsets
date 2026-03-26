"""Tests for fastapi_toolsets.dependencies module."""

import inspect
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated, Any, cast

import pytest
from fastapi.params import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_toolsets.dependencies import (
    BodyDependency,
    PathDependency,
    _unwrap_session_dep,
)

from .conftest import Role, RoleCreate, RoleCrud, User


async def mock_get_db() -> AsyncGenerator[AsyncSession, None]:
    """Mock session dependency for testing."""
    yield None  # type: ignore[misc]  # ty:ignore[invalid-yield]


MockSessionDep = Annotated[AsyncSession, Depends(mock_get_db)]


class TestUnwrapSessionDep:
    def test_plain_callable_returned_as_is(self):
        """Plain callable is returned unchanged."""
        assert _unwrap_session_dep(mock_get_db) is mock_get_db

    def test_annotated_with_depends_unwrapped(self):
        """Annotated form with Depends is unwrapped to the plain callable."""
        assert _unwrap_session_dep(MockSessionDep) is mock_get_db

    def test_annotated_without_depends_returned_as_is(self):
        """Annotated form with no Depends falls back to returning session_dep as-is."""
        annotated_no_dep = Annotated[AsyncSession, "not_a_depends"]
        assert _unwrap_session_dep(annotated_no_dep) is annotated_no_dep


class TestPathDependency:
    """Tests for PathDependency factory."""

    def test_returns_depends_instance(self):
        """PathDependency returns a Depends instance."""
        dep = PathDependency(Role, Role.id, session_dep=mock_get_db)
        assert isinstance(dep, Depends)

    def test_signature_has_default_param_name(self):
        """PathDependency uses model_field as default param name."""
        dep = cast(Any, PathDependency(Role, Role.id, session_dep=mock_get_db))
        func = dep.dependency

        sig = inspect.signature(func)
        params = list(sig.parameters.keys())

        assert "role_id" in params
        assert "session" in params

    def test_signature_has_correct_type_annotation(self):
        """PathDependency uses field's python type for annotation."""
        dep = cast(Any, PathDependency(Role, Role.id, session_dep=mock_get_db))
        func = dep.dependency

        sig = inspect.signature(func)

        assert sig.parameters["role_id"].annotation == uuid.UUID
        assert sig.parameters["session"].annotation == AsyncSession

    def test_signature_session_has_depends_default(self):
        """PathDependency session param has Depends as default."""
        dep = cast(Any, PathDependency(Role, Role.id, session_dep=mock_get_db))
        func = dep.dependency

        sig = inspect.signature(func)

        assert isinstance(sig.parameters["session"].default, Depends)

    def test_custom_param_name_in_signature(self):
        """PathDependency uses custom param_name in signature."""
        dep = cast(
            Any,
            PathDependency(
                Role, Role.id, session_dep=mock_get_db, param_name="role_uuid"
            ),
        )
        func = dep.dependency

        sig = inspect.signature(func)
        params = list(sig.parameters.keys())

        assert "role_uuid" in params
        assert "id" not in params

    def test_string_field_type(self):
        """PathDependency handles string field types."""
        dep = cast(Any, PathDependency(User, User.username, session_dep=mock_get_db))
        func = dep.dependency

        sig = inspect.signature(func)

        assert sig.parameters["user_username"].annotation is str

    @pytest.mark.anyio
    async def test_dependency_fetches_object(self, db_session):
        """PathDependency inner function fetches object from database."""
        role = await RoleCrud.create(db_session, RoleCreate(name="test_role"))

        dep = cast(Any, PathDependency(Role, Role.id, session_dep=mock_get_db))
        func = dep.dependency

        result = await func(session=db_session, role_id=role.id)

        assert result.id == role.id
        assert result.name == "test_role"

    def test_annotated_session_dep_returns_depends_instance(self):
        """PathDependency accepts Annotated[AsyncSession, Depends(...)] form."""
        dep = PathDependency(Role, Role.id, session_dep=MockSessionDep)
        assert isinstance(dep, Depends)

    def test_annotated_session_dep_signature(self):
        """PathDependency with Annotated session_dep produces a valid signature."""
        dep = cast(Any, PathDependency(Role, Role.id, session_dep=MockSessionDep))
        sig = inspect.signature(dep.dependency)

        assert "role_id" in sig.parameters
        assert "session" in sig.parameters
        assert isinstance(sig.parameters["session"].default, Depends)

    def test_annotated_session_dep_unwraps_callable(self):
        """PathDependency with Annotated form uses the underlying callable, not the Annotated type."""
        dep = cast(Any, PathDependency(Role, Role.id, session_dep=MockSessionDep))
        sig = inspect.signature(dep.dependency)

        inner_dep = sig.parameters["session"].default
        assert inner_dep.dependency is mock_get_db

    @pytest.mark.anyio
    async def test_annotated_session_dep_fetches_object(self, db_session):
        """PathDependency with Annotated session_dep correctly fetches object from database."""
        role = await RoleCrud.create(db_session, RoleCreate(name="annotated_role"))

        dep = cast(Any, PathDependency(Role, Role.id, session_dep=MockSessionDep))
        result = await dep.dependency(session=db_session, role_id=role.id)

        assert result.id == role.id
        assert result.name == "annotated_role"


class TestBodyDependency:
    """Tests for BodyDependency factory."""

    def test_returns_depends_instance(self):
        """BodyDependency returns a Depends instance."""
        dep = BodyDependency(
            Role, Role.id, session_dep=mock_get_db, body_field="role_id"
        )
        assert isinstance(dep, Depends)

    def test_signature_has_body_field_as_param(self):
        """BodyDependency uses body_field as param name."""
        dep = cast(
            Any,
            BodyDependency(
                Role, Role.id, session_dep=mock_get_db, body_field="role_id"
            ),
        )
        func = dep.dependency

        sig = inspect.signature(func)
        params = list(sig.parameters.keys())

        assert "role_id" in params
        assert "session" in params

    def test_signature_has_correct_type_annotation(self):
        """BodyDependency uses field's python type for annotation."""
        dep = cast(
            Any,
            BodyDependency(
                Role, Role.id, session_dep=mock_get_db, body_field="role_id"
            ),
        )
        func = dep.dependency

        sig = inspect.signature(func)

        assert sig.parameters["role_id"].annotation == uuid.UUID
        assert sig.parameters["session"].annotation == AsyncSession

    def test_signature_session_has_depends_default(self):
        """BodyDependency session param has Depends as default."""
        dep = cast(
            Any,
            BodyDependency(
                Role, Role.id, session_dep=mock_get_db, body_field="role_id"
            ),
        )
        func = dep.dependency

        sig = inspect.signature(func)

        assert isinstance(sig.parameters["session"].default, Depends)

    def test_different_body_field_name(self):
        """BodyDependency can use any body_field name."""
        dep = cast(
            Any,
            BodyDependency(
                User, User.id, session_dep=mock_get_db, body_field="user_uuid"
            ),
        )
        func = dep.dependency

        sig = inspect.signature(func)
        params = list(sig.parameters.keys())

        assert "user_uuid" in params
        assert "id" not in params

    @pytest.mark.anyio
    async def test_dependency_fetches_object(self, db_session):
        """BodyDependency inner function fetches object from database."""
        role = await RoleCrud.create(db_session, RoleCreate(name="body_test_role"))

        dep = cast(
            Any,
            BodyDependency(
                Role, Role.id, session_dep=mock_get_db, body_field="role_id"
            ),
        )
        func = dep.dependency

        result = await func(session=db_session, role_id=role.id)

        assert result.id == role.id
        assert result.name == "body_test_role"

    def test_annotated_session_dep_returns_depends_instance(self):
        """BodyDependency accepts Annotated[AsyncSession, Depends(...)] form."""
        dep = BodyDependency(
            Role, Role.id, session_dep=MockSessionDep, body_field="role_id"
        )
        assert isinstance(dep, Depends)

    def test_annotated_session_dep_unwraps_callable(self):
        """BodyDependency with Annotated form uses the underlying callable, not the Annotated type."""
        dep = cast(
            Any,
            BodyDependency(
                Role, Role.id, session_dep=MockSessionDep, body_field="role_id"
            ),
        )
        sig = inspect.signature(dep.dependency)

        inner_dep = sig.parameters["session"].default
        assert inner_dep.dependency is mock_get_db

    @pytest.mark.anyio
    async def test_annotated_session_dep_fetches_object(self, db_session):
        """BodyDependency with Annotated session_dep correctly fetches object from database."""
        role = await RoleCrud.create(db_session, RoleCreate(name="body_annotated_role"))

        dep = cast(
            Any,
            BodyDependency(
                Role, Role.id, session_dep=MockSessionDep, body_field="role_id"
            ),
        )
        result = await dep.dependency(session=db_session, role_id=role.id)

        assert result.id == role.id
        assert result.name == "body_annotated_role"
