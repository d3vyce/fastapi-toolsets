"""Mixins for the declarative base."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime


class TimezoneAwareMixin:
    """Mixin for the declarative base that maps ``Mapped[datetime]`` to ``TIMESTAMPTZ``."""

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: DateTime(timezone=True),
    }
