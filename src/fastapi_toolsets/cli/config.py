"""CLI configuration and dynamic imports."""

from __future__ import annotations

import importlib
import sys
from typing import TYPE_CHECKING, Any, Literal, TypeVar, overload

import typer

from .pyproject import find_pyproject, load_pyproject

if TYPE_CHECKING:
    from ..fixtures import FixtureRegistry

T = TypeVar("T")


def _ensure_project_in_path():
    """Add project root to sys.path if not installed in editable mode."""
    pyproject = find_pyproject()
    if pyproject:
        project_root = str(pyproject.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)


def import_from_string(import_path: str) -> Any:
    """Import an object from a dotted string path.

    Args:
        import_path: Import path in ``"module.submodule:attribute"`` format

    Returns:
        The imported attribute

    Raises:
        typer.BadParameter: If the import path is invalid or import fails
    """
    if ":" not in import_path:
        raise typer.BadParameter(
            f"Invalid import path '{import_path}'. Expected format: 'module:attribute'"
        )

    module_path, attr_name = import_path.rsplit(":", 1)

    _ensure_project_in_path()

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise typer.BadParameter(f"Cannot import module '{module_path}': {e}")

    if not hasattr(module, attr_name):
        raise typer.BadParameter(
            f"Module '{module_path}' has no attribute '{attr_name}'"
        )

    return getattr(module, attr_name)


@overload
def get_config_value(key: str, required: Literal[True]) -> Any: ...  # pragma: no cover
@overload
def get_config_value(
    key: str, required: bool = False
) -> Any | None: ...  # pragma: no cover
def get_config_value(key: str, required: bool = False) -> Any | None:
    """Get a configuration value from pyproject.toml.

    Args:
        key: The configuration key in [tool.fastapi-toolsets].
        required: If True, raises an error when the key is missing.

    Returns:
        The configuration value, or None if not found and not required.

    Raises:
        typer.BadParameter: If required=True and the key is missing.
    """
    config = load_pyproject()
    value = config.get(key)

    if required and value is None:
        raise typer.BadParameter(
            f"No '{key}' configured. "
            f"Add '{key}' to [tool.fastapi-toolsets] in pyproject.toml."
        )

    return value


@overload
def _import_typed(
    key: str, expected_type: type[T], *, required: Literal[True]
) -> T: ...  # pragma: no cover
@overload
def _import_typed(
    key: str, expected_type: type[T], *, required: bool
) -> T | None: ...  # pragma: no cover
def _import_typed(key: str, expected_type: type[T], *, required: bool) -> T | None:
    """Import a config value by key and validate its type.

    Raises:
        typer.BadParameter: If required and missing, or if the imported
            value isn't an instance of *expected_type*.
    """
    import_path = get_config_value(key, required=required)
    if not import_path:
        return None

    obj = import_from_string(import_path)
    if not isinstance(obj, expected_type):
        raise typer.BadParameter(
            f"'{key}' must be a {expected_type.__name__} instance, got {type(obj).__name__}"
        )

    return obj


def get_fixtures_registry() -> FixtureRegistry:
    """Import and return the fixtures registry from config."""
    from ..fixtures import FixtureRegistry

    return _import_typed("fixtures", FixtureRegistry, required=True)


def get_db_context() -> Any:
    """Import and return the db_context function from config."""
    import_path = get_config_value("db_context", required=True)
    return import_from_string(import_path)


def get_custom_cli() -> typer.Typer | None:
    """Import and return the custom CLI Typer instance from config."""
    return _import_typed("custom_cli", typer.Typer, required=False)
