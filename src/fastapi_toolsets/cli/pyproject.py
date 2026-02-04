"""Pyproject.toml discovery and loading."""

import tomllib
from pathlib import Path

TOOL_NAME = "fastapi-toolsets"


def find_pyproject(start_path: Path | None = None) -> Path | None:
    """Find pyproject.toml by walking up the directory tree.

    Similar to how pytest, black, and ruff discover their config files.
    """
    path = (start_path or Path.cwd()).resolve()

    for directory in [path, *path.parents]:
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            return pyproject

    return None


def load_pyproject(path: Path | None = None) -> dict:
    """Load tool configuration from pyproject.toml.

    Args:
        path: Explicit path to pyproject.toml. If None, searches up from cwd.

    Returns:
        The [tool.fastapi-toolsets] section as a dict, or empty dict if not found.
    """
    pyproject_path = path or find_pyproject()

    if not pyproject_path:
        return {}

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("tool", {}).get(TOOL_NAME, {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}
