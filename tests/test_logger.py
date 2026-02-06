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
    def test_sets_up_handler_and_format(self):
        logger = configure_logging()

        assert len(logger.handlers) == 1
        handler = logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stdout
        assert handler.formatter is not None
        assert handler.formatter._fmt == DEFAULT_FORMAT

    def test_default_level_is_info(self):
        logger = configure_logging()

        assert logger.level == logging.INFO

    def test_custom_level_string(self):
        logger = configure_logging(level="DEBUG")

        assert logger.level == logging.DEBUG

    def test_custom_level_int(self):
        logger = configure_logging(level=logging.WARNING)

        assert logger.level == logging.WARNING

    def test_custom_format(self):
        custom_fmt = "%(levelname)s: %(message)s"
        logger = configure_logging(fmt=custom_fmt)

        handler = logger.handlers[0]
        assert handler.formatter is not None
        assert handler.formatter._fmt == custom_fmt

    def test_named_logger(self):
        logger = configure_logging(logger_name="myapp")

        assert logger.name == "myapp"
        assert len(logger.handlers) == 1

    def test_default_configures_root_logger(self):
        logger = configure_logging()

        assert logger is logging.getLogger()

    def test_idempotent_no_duplicate_handlers(self):
        configure_logging()
        configure_logging()
        logger = configure_logging()

        assert len(logger.handlers) == 1

    def test_configures_uvicorn_loggers(self):
        configure_logging(level="DEBUG")

        for name in UVICORN_LOGGERS:
            uv_logger = logging.getLogger(name)
            assert len(uv_logger.handlers) == 1
            assert uv_logger.level == logging.DEBUG
            handler = uv_logger.handlers[0]
            assert handler.formatter is not None
            assert handler.formatter._fmt == DEFAULT_FORMAT

    def test_returns_configured_logger(self):
        logger = configure_logging(logger_name="test.return")

        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.return"


class TestGetLogger:
    def test_returns_named_logger(self):
        logger = get_logger("myapp.services")

        assert isinstance(logger, logging.Logger)
        assert logger.name == "myapp.services"

    def test_returns_root_logger_when_none(self):
        logger = get_logger(None)

        assert logger is logging.getLogger()

    def test_defaults_to_caller_module_name(self):
        logger = get_logger()

        assert logger.name == __name__

    def test_same_name_returns_same_logger(self):
        a = get_logger("myapp")
        b = get_logger("myapp")

        assert a is b
