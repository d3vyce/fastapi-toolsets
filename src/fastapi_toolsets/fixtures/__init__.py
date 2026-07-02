"""Fixture system for seeding databases with dependency resolution."""

from .enum import Context, LoadStrategy

__all__ = [
    "Context",
    "FixtureRegistry",
    "LoadStrategy",
    "load_fixtures",
    "load_fixtures_by_context",
]

_LAZY = {
    "FixtureRegistry": ".registry",
    "load_fixtures": ".utils",
    "load_fixtures_by_context": ".utils",
}


def __getattr__(name: str):
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib

    module = importlib.import_module(module_name, __name__)
    return getattr(module, name)
