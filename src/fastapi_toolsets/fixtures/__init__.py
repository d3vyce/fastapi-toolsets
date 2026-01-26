from .fixtures import (
    Context,
    FixtureRegistry,
    LoadStrategy,
    load_fixtures,
    load_fixtures_by_context,
)
from .utils import get_obj_by_attr

__all__ = [
    "Context",
    "FixtureRegistry",
    "LoadStrategy",
    "get_obj_by_attr",
    "load_fixtures",
    "load_fixtures_by_context",
    "register_fixtures",
]


# We lazy-load register_fixtures to avoid needing pytest when using fixtures CLI
def __getattr__(name: str):
    if name == "register_fixtures":
        from .pytest_plugin import register_fixtures

        return register_fixtures
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
