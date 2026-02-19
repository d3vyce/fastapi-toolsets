"""Pytest helpers for FastAPI testing: sessions, clients, and fixtures."""

try:
    from .plugin import register_fixtures
except ImportError:
    from .._imports import require_extra

    require_extra(package="pytest", extra="pytest")

try:
    from .utils import (
        cleanup_tables,
        create_async_client,
        create_db_session,
        create_worker_database,
        worker_database_url,
    )
except ImportError:
    from .._imports import require_extra

    require_extra(package="httpx", extra="pytest")

__all__ = [
    "cleanup_tables",
    "create_async_client",
    "create_db_session",
    "create_worker_database",
    "register_fixtures",
    "worker_database_url",
]
