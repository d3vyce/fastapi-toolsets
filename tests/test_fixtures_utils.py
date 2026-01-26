"""Tests for fastapi_toolsets.fixtures.utils."""

import pytest

from fastapi_toolsets.fixtures import FixtureRegistry
from fastapi_toolsets.fixtures.utils import get_obj_by_attr

from .conftest import Role, User

registry = FixtureRegistry()


@registry.register
def roles() -> list[Role]:
    return [
        Role(id=1, name="admin"),
        Role(id=2, name="user"),
        Role(id=3, name="moderator"),
    ]


@registry.register(depends_on=["roles"])
def users() -> list[User]:
    return [
        User(id=1, username="alice", email="alice@example.com", role_id=1),
        User(id=2, username="bob", email="bob@example.com", role_id=1),
    ]


class TestGetObjByAttr:
    """Tests for get_obj_by_attr."""

    def test_get_by_id(self):
        """Get an object by its id attribute."""
        role = get_obj_by_attr(roles, "id", 1)
        assert role.name == "admin"

    def test_get_user_by_username(self):
        """Get a user by username."""
        user = get_obj_by_attr(users, "username", "bob")
        assert user.id == 2
        assert user.email == "bob@example.com"

    def test_returns_first_match(self):
        """Returns the first matching object when multiple could match."""
        user = get_obj_by_attr(users, "role_id", 1)
        assert user.username == "alice"

    def test_no_match_raises_stop_iteration(self):
        """Raises StopIteration when no object matches."""
        with pytest.raises(StopIteration):
            get_obj_by_attr(roles, "name", "nonexistent")

    def test_no_match_on_wrong_value_type(self):
        """Raises StopIteration when value type doesn't match."""
        with pytest.raises(StopIteration):
            get_obj_by_attr(roles, "id", "1")
