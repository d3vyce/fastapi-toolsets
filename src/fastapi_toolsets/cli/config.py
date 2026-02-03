"""CLI configuration and dynamic imports."""

import importlib
import sys

import typer

from .pyproject import find_pyproject, load_pyproject


def _ensure_project_in_path():
    """Add project root to sys.path if not installed in editable mode."""
    pyproject = find_pyproject()
    if pyproject:
        project_root = str(pyproject.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)


def import_from_string(import_path: str):
    """Import an object from a string path like 'module.submodule:attribute'.

    Raises:
        typer.BadParameter: If the import path is invalid or import fails.
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


def get_config_value(key: str, required: bool = False):
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


def get_fixtures_registry():
    """Import and return the fixtures registry from config."""
    from ..fixtures import FixtureRegistry

    import_path = get_config_value("fixtures", required=True)
    registry = import_from_string(import_path)

    if not isinstance(registry, FixtureRegistry):
        raise typer.BadParameter(
            f"'fixtures' must be a FixtureRegistry instance, got {type(registry).__name__}"
        )

    return registry


def get_db_context():
    """Import and return the db_context function from config."""
    import_path = get_config_value("db_context", required=True)
    return import_from_string(import_path)


def get_custom_cli() -> typer.Typer | None:
    """Import and return the custom CLI Typer instance from config."""
    import_path = get_config_value("custom_cli")
    if not import_path:
        return None

    custom = import_from_string(import_path)

    if not isinstance(custom, typer.Typer):
        raise typer.BadParameter(
            f"'custom_cli' must be a Typer instance, got {type(custom).__name__}"
        )

    return custom
