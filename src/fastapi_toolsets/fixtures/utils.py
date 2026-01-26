from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from sqlalchemy.orm import DeclarativeBase

T = TypeVar("T", bound=DeclarativeBase)


def get_obj_by_attr(
    fixtures: Callable[[], Sequence[T]], attr_name: str, value: Any
) -> T:
    """Get a SQLAlchemy model instance by matching an attribute value.

    Args:
        fixtures: A fixture function registered via ``@registry.register``
            that returns a sequence of SQLAlchemy model instances.
        attr_name: Name of the attribute to match against.
        value: Value to match.

    Returns:
        The first model instance where the attribute matches the given value.

    Raises:
        StopIteration: If no matching object is found.
    """
    return next(obj for obj in fixtures() if getattr(obj, attr_name) == value)
