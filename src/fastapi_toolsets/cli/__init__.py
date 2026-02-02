"""CLI for FastAPI projects."""

from .app import cli
from .utils import async_command

__all__ = ["async_command", "cli"]
