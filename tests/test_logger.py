"""Tests for ``configure_logging`` and ``get_logger``."""

import logging
import sys

import pytest

from fastapi_toolsets.logger import (
    DEFAULT_FORMAT,
    UVICORN_LOGGERS,
    configure_logging,
    get_logger,
)


@pytest.fixture(autouse=True)
def _reset_loggers():
    """Reset the root and uvicorn loggers after each test."""
    yield
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    for name in UVICORN_LOGGERS:
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.setLevel(logging.NOTSET)


class TestConfigureLogging:
    """One stdout handler, shared with the uvicorn loggers."""

    @pytest.mark.parametrize(
        ("kwargs", "level", "fmt", "name"),
        [
            ({}, logging.INFO, DEFAULT_FORMAT, None),
            ({"level": "DEBUG"}, logging.DEBUG, DEFAULT_FORMAT, None),
            ({"level": logging.WARNING}, logging.WARNING, DEFAULT_FORMAT, None),
            (
                {"fmt": "%(levelname)s: %(message)s"},
                logging.INFO,
                "%(levelname)s: %(message)s",
                None,
            ),
            ({"logger_name": "myapp"}, logging.INFO, DEFAULT_FORMAT, "myapp"),
        ],
        ids=["defaults", "level-name", "level-int", "custom-format", "named-logger"],
    )
    def test_applies_level_format_and_target_to_app_and_uvicorn_loggers(
        self, kwargs, level, fmt, name
    ):
        logger = configure_logging(**kwargs)

        assert logger is logging.getLogger(name)
        assert logger.level == level
        (handler,) = logger.handlers
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stdout
        assert handler.formatter is not None and handler.formatter._fmt == fmt
        for uvicorn_name in UVICORN_LOGGERS:
            uvicorn_logger = logging.getLogger(uvicorn_name)
            assert uvicorn_logger.handlers == [handler]
            assert uvicorn_logger.level == level

    def test_reconfiguring_replaces_the_handler_instead_of_stacking(self):
        first = configure_logging().handlers[0]
        configure_logging()

        logger = configure_logging()

        assert len(logger.handlers) == 1 and logger.handlers[0] is not first
        assert all(len(logging.getLogger(n).handlers) == 1 for n in UVICORN_LOGGERS)


class TestGetLogger:
    """``get_logger`` resolves the name from its argument or the caller."""

    @pytest.mark.parametrize(
        ("args", "expected"),
        [((), __name__), ((None,), "root"), (("myapp.services",), "myapp.services")],
        ids=["caller-module", "root", "explicit-name"],
    )
    def test_resolves_the_logger_name(self, args, expected):
        logger = get_logger(*args)

        assert isinstance(logger, logging.Logger) and logger.name == expected
        assert get_logger(*args) is logger
