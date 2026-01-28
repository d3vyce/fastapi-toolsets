from enum import Enum


class LoadStrategy(str, Enum):
    """Strategy for loading fixtures into the database."""

    INSERT = "insert"
    """Insert new records. Fails if record already exists."""

    MERGE = "merge"
    """Insert or update based on primary key (SQLAlchemy merge)."""

    SKIP_EXISTING = "skip_existing"
    """Insert only if record doesn't exist (based on primary key)."""


class Context(str, Enum):
    """Predefined fixture contexts."""

    BASE = "base"
    """Base fixtures loaded in all environments."""

    PRODUCTION = "production"
    """Production-only fixtures."""

    DEVELOPMENT = "development"
    """Development fixtures."""

    TESTING = "testing"
    """Test fixtures."""
