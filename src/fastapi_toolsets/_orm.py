"""SQLAlchemy instance state helpers."""

from typing import Any

from sqlalchemy import inspect as sa_inspect


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
