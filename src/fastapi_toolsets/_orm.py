"""SQLAlchemy state and expression helpers."""

from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import tuple_


def key_expr(columns: Sequence[Any]) -> Any:
    """*columns* as one comparable expression: the column, or a tuple of them."""
    return columns[0] if len(columns) == 1 else tuple_(*columns)


def is_to_many(rels: Iterable[Any]) -> bool:
    """True if the relationship path *rels* crosses a collection."""
    return any(rel.property.uselist for rel in rels)


def is_expired(obj: Any) -> bool:
    """True when *obj* or any of its attributes must be re-read from the database."""
    state = sa_inspect(obj)
    return bool(state.expired or state.expired_attributes)


def loaded_relationships(obj: Any) -> set[str]:
    """Relationship keys currently loaded on *obj*."""
    state = sa_inspect(obj, raiseerr=False)
    if state is None:
        return set()
    unloaded = state.unloaded
    return {
        rel.key
        for rel in state.mapper.relationships
        if rel.key not in unloaded and rel.lazy not in ("dynamic", "write_only")
    }
