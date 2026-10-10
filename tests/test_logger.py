"""Tests for ``fastapi_toolsets.logger``: config, formatters, context, OTel, middleware."""

import asyncio
import json
import logging
import multiprocessing
import re
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import format_span_id, format_trace_id
from starlette.types import Message, Receive, Scope, Send

from fastapi_toolsets.logger import (
    ACCESS_FIELDS,
    ACCESS_LOGGER,
    DEFAULT_PROPAGATE_LOGGERS,
    ConsoleFormatter,
    JsonFormatter,
    LoggingConfig,
    RequestLoggingMiddleware,
    SharedRotatingFileHandler,
    bind_log_context,
    configure_logging,
    current_log_context,
    get_logger,
    init_logging,
    log_context,
)
from fastapi_toolsets.logger import context as context_module
from fastapi_toolsets.logger.config import DEFAULT_QUIET_LOGGERS, owned_handlers
from fastapi_toolsets.logger.context import request_log_context

_TOUCHED_LOGGERS = (
    ACCESS_LOGGER,
    *DEFAULT_PROPAGATE_LOGGERS,
    *DEFAULT_QUIET_LOGGERS,
    "sqlalchemy.engine",
    "myapp",
    "pgqueuer",
)


@pytest.fixture(autouse=True)
def _reset_logging():
    """Undo configure_logging and the bound context after each test."""
    token = context_module._log_context.set(None)
    request_token = context_module._request_context.set(None)
    yield
    context_module._request_context.reset(request_token)
    context_module._log_context.reset(token)
    root = logging.getLogger()
    for handler in owned_handlers():
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.WARNING)
    for name in _TOUCHED_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)


# JSON records to stdout, where ``capsys`` reads them, with no OpenTelemetry.
_JSON = LoggingConfig(otel=False, stream="stdout", style="json")


def _json_lines(text: str) -> list[dict]:
    """One parsed JSON record per non-empty line of *text*."""
    return [json.loads(line) for line in text.splitlines() if line]


def _record(msg: str = "hello", **extra) -> logging.LogRecord:
    """An INFO record from the ``app`` logger, with *extra* set as attributes."""
    record = logging.LogRecord("app", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _log_from_worker(
    file: Path, worker: int, lines: int, *, configure: bool = True
) -> None:
    """Log *lines* records, after configuring logging as a uvicorn worker would."""
    if configure:
        configure_logging(
            otel=False, style="json", file=file, file_max_bytes=400, file_backups=100
        )
    for n in range(lines):
        logging.getLogger("worker").warning("record", extra={"worker": worker, "n": n})
    logging.shutdown()


def _file_records(file: Path) -> list[dict]:
    """Every JSON record across *file* and its rotated copies."""
    files = [file, *sorted(file.parent.glob(f"{file.name}.*"))]
    return [
        record
        for path in files
        if path.suffix != ".lock"
        for record in _json_lines(path.read_text())
    ]


def _app(config: LoggingConfig | None = None, **kwargs) -> FastAPI:
    """App with logging, binding, failing and streaming routes, under init_logging."""
    app = FastAPI()
    logger = get_logger("myapp")

    def current_user() -> str:
        bind_log_context(user_id="u1")
        return "u1"

    @app.get("/items/{item_id}")
    def read_item(item_id: int, user: str = Depends(current_user)):
        logger.warning("reading")
        return {"item_id": item_id}

    @app.get("/export")
    def export():
        with log_context(job="export"):
            logger.warning("exporting")
        return {}

    @app.get("/boom")
    def boom():
        raise RuntimeError("boom")

    @app.get("/unavailable")
    def unavailable():
        raise HTTPException(status_code=503)

    @app.get("/stream")
    def stream():
        return StreamingResponse(iter([b"a", b"b"]))

    @app.get("/health")
    def health():
        return {}

    config = config or _JSON
    init_logging(app, config, **kwargs)
    return app


async def _get(app: FastAPI, path: str, *, raise_app_exceptions=False, **kwargs):
    """GET *path* from *app*, answering 500 instead of raising on app errors."""
    transport = ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, **kwargs)


def _access_lines(text: str, logger: str = ACCESS_LOGGER) -> list[dict]:
    """The records of *logger*, the access log by default, among the JSON lines of *text*."""
    return [line for line in _json_lines(text) if line["logger"] == logger]


class TestConfigureLogging:
    """Handlers, styles and levels installed on the root and third-party loggers."""

    @pytest.mark.parametrize(
        ("config", "kwargs", "level"),
        [
            (None, {}, logging.INFO),
            (None, {"level": "DEBUG"}, logging.DEBUG),
            (None, {"level": logging.ERROR}, logging.ERROR),
            (LoggingConfig(level="ERROR"), {"level": "DEBUG"}, logging.DEBUG),
        ],
        ids=["info-by-default", "level-name", "level-int", "override-beats-config"],
    )
    def test_sets_the_level_of_the_root_logger(self, config, kwargs, level):
        root = configure_logging(config, otel=False, **kwargs)

        assert root is logging.getLogger()
        assert root.level == level

    @pytest.mark.parametrize(
        ("kwargs", "stream"),
        [({}, "stderr"), ({"stream": "stdout"}, "stdout")],
        ids=["stderr-by-default", "stdout"],
    )
    def test_writes_to_the_chosen_stream(self, kwargs, stream):
        configure_logging(otel=False, **kwargs)

        (handler,) = owned_handlers()
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is getattr(sys, stream)

    def test_reconfiguring_replaces_its_handlers_and_keeps_foreign_ones(self):
        foreign = logging.NullHandler()
        root = logging.getLogger()
        root.addHandler(foreign)
        try:
            for _ in range(3):
                configure_logging(otel=False)

            assert len(owned_handlers()) == 1
            assert foreign in root.handlers
        finally:
            root.removeHandler(foreign)

    @pytest.mark.parametrize(
        ("kwargs", "env", "pattern"),
        [
            ({}, {}, r'\{.*"message": "hello".*\}'),
            (
                {"style": "console"},
                {},
                r"\d\d:\d\d:\d\d\.\d{3} WARNING myapp  hello \[abcdef\]",
            ),
            (
                {
                    "style": "console",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                    "request_id_length": 4,
                },
                {},
                r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} WARNING myapp  hello \[abcd\]",
            ),
            (
                {"style": "json"},
                {"OTEL_SERVICE_NAME": "my-api"},
                r'\{.*"service": "my-api".*\}',
            ),
        ],
        ids=[
            "auto-is-json-off-a-terminal",
            "console-without-colors",
            "console-options",
            "json-service-from-otel-env",
        ],
    )
    def test_formats_records_in_the_chosen_style(
        self, capsys, monkeypatch, kwargs, env, pattern
    ):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        configure_logging(otel=False, stream="stdout", **kwargs)

        with log_context(request_id="abcdef"):
            get_logger("myapp").warning("hello")

        (line,) = capsys.readouterr().out.splitlines()
        assert re.fullmatch(pattern, line), line

    def test_an_unknown_style_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown log style"):
            configure_logging(otel=False, style="xml")

    @pytest.mark.parametrize(
        ("kwargs", "taken_over"),
        [
            ({}, DEFAULT_PROPAGATE_LOGGERS),
            (
                {"propagate": (*DEFAULT_PROPAGATE_LOGGERS, "pgqueuer")},
                (*DEFAULT_PROPAGATE_LOGGERS, "pgqueuer"),
            ),
            ({"propagate": ()}, ()),
        ],
        ids=["uvicorn-by-default", "extended", "none"],
    )
    def test_propagate_routes_the_listed_loggers_through_the_root(
        self, kwargs, taken_over
    ):
        third_party = (*DEFAULT_PROPAGATE_LOGGERS, "pgqueuer")
        for name in third_party:
            logging.getLogger(name).addHandler(logging.NullHandler())
            logging.getLogger(name).propagate = False
            logging.getLogger(name).setLevel(logging.ERROR)

        configure_logging(otel=False, **kwargs)

        for name in taken_over:
            logger = logging.getLogger(name)
            assert logger.handlers == [] and logger.propagate is True
            assert logger.level == logging.NOTSET
        for name in set(third_party) - set(taken_over):
            logger = logging.getLogger(name)
            assert len(logger.handlers) == 1 and logger.propagate is False

    @pytest.mark.parametrize(
        ("kwargs", "levels"),
        [
            ({}, {"uvicorn.access": logging.NOTSET, "httpx": logging.WARNING}),
            ({"access_log": True}, {"uvicorn.access": logging.WARNING}),
            ({"quiet": ("pgqueuer",)}, {"pgqueuer": logging.WARNING}),
            ({"sql_echo": True}, {"sqlalchemy.engine": logging.INFO}),
            ({"levels": {"myapp": "DEBUG"}}, {"myapp": logging.DEBUG}),
        ],
        ids=["defaults", "access-log-caps-uvicorn", "quiet", "sql-echo", "levels"],
    )
    def test_level_options_tune_the_named_loggers(self, kwargs, levels):
        configure_logging(otel=False, **kwargs)

        assert {name: logging.getLogger(name).level for name in levels} == levels


class TestLoggingConfigFromEnv:
    """``from_env`` reads ``LOG_*`` variables over the defaults it is given."""

    @pytest.mark.parametrize(
        ("environ", "defaults", "expected"),
        [
            ({}, {}, LoggingConfig()),
            ({"LOG_OTEL": "auto"}, {"otel": False}, LoggingConfig(otel="auto")),
            (
                {"LOG_LEVEL": "ERROR"},
                {"style": "json", "level": "DEBUG"},
                LoggingConfig(style="json", level="ERROR"),
            ),
        ],
        ids=["no-variables", "otel-auto", "variable-beats-default"],
    )
    def test_variables_win_over_the_given_defaults(self, environ, defaults, expected):
        assert LoggingConfig.from_env(environ=environ, **defaults) == expected

    def test_reads_every_variable_under_a_custom_prefix(self):
        config = LoggingConfig.from_env(
            prefix="APP_LOG_",
            environ={
                "APP_LOG_LEVEL": "debug",
                "APP_LOG_STYLE": "JSON",
                "APP_LOG_STREAM": "stdout",
                "APP_LOG_COLORS": "no",
                "APP_LOG_SERVICE": "My-API",
                "APP_LOG_FILE": "logs/api.log",
                "APP_LOG_FILE_MAX_BYTES": "1000",
                "APP_LOG_FILE_BACKUPS": "3",
                "APP_LOG_SQL_ECHO": "1",
                "APP_LOG_OTEL": "false",
            },
        )

        assert config == LoggingConfig(
            level="DEBUG",
            style="json",
            stream="stdout",
            colors=False,
            service="My-API",
            file="logs/api.log",
            file_max_bytes=1000,
            file_backups=3,
            sql_echo=True,
            otel=False,
        )

    @pytest.mark.parametrize(
        "environ",
        [{"LOG_SQL_ECHO": "maybe"}, {"LOG_FILE_BACKUPS": "many"}],
        ids=["bool", "int"],
    )
    def test_an_unparsable_value_names_the_variable(self, environ):
        (name,) = environ

        with pytest.raises(ValueError, match=name):
            LoggingConfig.from_env(environ=environ)


class TestFileOutput:
    """``file`` adds a rotating file shared by every process of the application."""

    @pytest.mark.parametrize(
        ("kwargs", "pattern"),
        [
            ({}, r'^\{"request_id": "r1", .*"message": "hello"'),
            (
                {"style": "console", "colors": True},
                r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} INFO  myapp  hello \[r1\]$",
            ),
        ],
        ids=["auto-is-json", "console-dated-without-colors"],
    )
    def test_writes_the_configured_style_with_the_bound_context(
        self, tmp_path, kwargs, pattern
    ):
        file = tmp_path / "logs" / "app.log"
        configure_logging(otel=False, file=file, **kwargs)

        with log_context(request_id="r1"):
            logging.getLogger("myapp").info("hello")

        (line,) = file.read_text().splitlines()
        assert re.match(pattern, line), line

    def test_reconfiguring_replaces_the_file_handler_and_releases_its_files(
        self, tmp_path
    ):
        configure_logging(otel=False, file=tmp_path / "app.log")
        first = next(
            h for h in owned_handlers() if isinstance(h, SharedRotatingFileHandler)
        )
        logging.getLogger("myapp").warning("one")

        configure_logging(otel=False, file=tmp_path / "app.log")
        logging.getLogger("myapp").warning("two")

        file_handlers = [
            h for h in owned_handlers() if isinstance(h, SharedRotatingFileHandler)
        ]
        assert len(file_handlers) == 1 and file_handlers[0] is not first
        assert first.stream is None and first._lock_fd is None
        assert [r["message"] for r in _file_records(tmp_path / "app.log")] == [
            "one",
            "two",
        ]

    def test_follows_a_file_moved_by_an_external_rotation(self, tmp_path):
        file = tmp_path / "app.log"
        configure_logging(otel=False, style="json", file=file, file_max_bytes=0)
        logging.getLogger("myapp").warning("before")

        file.rename(tmp_path / "app.log.1")
        logging.getLogger("myapp").warning("after")

        assert [r["message"] for r in _file_records(file)] == ["after", "before"]

    def test_delay_closed_before_any_record_leaves_no_file(self, tmp_path):
        SharedRotatingFileHandler(tmp_path / "app.log", delay=True).close()

        assert list(tmp_path.iterdir()) == []

    def test_delay_opens_the_file_on_the_first_record_and_still_rotates(self, tmp_path):
        file = tmp_path / "app.log"
        handler = SharedRotatingFileHandler(
            file, maxBytes=200, backupCount=10, delay=True
        )
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("myapp")
        logger.addHandler(handler)
        try:
            for n in range(6):
                logger.warning("record %d", n)
        finally:
            logger.removeHandler(handler)
            handler.close()

        assert sorted(r["message"] for r in _file_records(file)) == [
            f"record {n}" for n in range(6)
        ]
        assert len(list(tmp_path.glob("app.log.[0-9]*"))) == 5

    def test_a_lock_that_cannot_be_opened_is_reported_and_the_next_record_lands(
        self, tmp_path, capsys
    ):
        file = tmp_path / "app.log"
        configure_logging(otel=False, style="json", stream="stdout", file=file)
        lock = file.with_name("app.log.lock")
        lock.mkdir()

        logging.getLogger("myapp").warning("lost")
        lock.rmdir()
        logging.getLogger("myapp").warning("after")

        assert "--- Logging error ---" in capsys.readouterr().err
        assert [r["message"] for r in _file_records(file)] == ["after"]

    def test_a_file_that_cannot_be_reopened_is_reported_and_the_next_record_lands(
        self, tmp_path, capsys
    ):
        logs = tmp_path / "logs"
        file = logs / "app.log"
        configure_logging(otel=False, style="json", stream="stdout", file=file)
        logging.getLogger("myapp").warning("deleted")
        for path in logs.iterdir():
            path.unlink()
        logs.rmdir()

        logging.getLogger("myapp").warning("lost")
        logs.mkdir()
        logging.getLogger("myapp").warning("after")

        assert "--- Logging error ---" in capsys.readouterr().err
        assert [r["message"] for r in _file_records(file)] == ["after"]

    @pytest.mark.skipif(sys.platform == "win32", reason="fork is not available")
    @pytest.mark.parametrize(
        "configured_before_fork", [False, True], ids=["per-worker", "preloaded"]
    )
    def test_several_processes_share_one_rotated_file_without_losing_records(
        self, tmp_path, configured_before_fork
    ):
        file = tmp_path / "app.log"
        if configured_before_fork:
            # As under gunicorn --preload: the children inherit the open handler.
            configure_logging(
                otel=False,
                style="json",
                file=file,
                file_max_bytes=400,
                file_backups=100,
            )
            logging.getLogger("worker").warning("record", extra={"worker": -1, "n": 0})
        context = multiprocessing.get_context("fork")
        workers = [
            context.Process(
                target=_log_from_worker,
                args=(file, worker, 25),
                kwargs={"configure": not configured_before_fork},
            )
            for worker in range(4)
        ]

        for process in workers:
            process.start()
        for process in workers:
            process.join(timeout=30)

        assert [p.exitcode for p in workers] == [0, 0, 0, 0]
        records = _file_records(file)
        assert {(r["worker"], r["n"]) for r in records} >= {
            (worker, n) for worker in range(4) for n in range(25)
        }
        assert len(records) == 100 + configured_before_fork
        assert len(list(tmp_path.glob("app.log.*"))) > 2


class TestFormatters:
    """What the JSON and console formatters render from one record."""

    def test_json_base_keys_win_over_extra_and_unknown_types_become_strings(self):
        record = _record(
            level="fake", logger="fake", user_id="u1", amount=Decimal("1.5")
        )

        payload = json.loads(JsonFormatter().format(record))

        assert payload["level"] == "INFO" and payload["logger"] == "app"
        assert payload["user_id"] == "u1" and payload["amount"] == "1.5"
        assert payload["timestamp"].endswith("Z")
        assert "service" not in payload

    def test_exception_and_stack_are_rendered_by_both_formatters(self):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            record = _record(
                "failed",
                exc_info=sys.exc_info(),
                stack_info="Stack (most recent call last):\n  here",
            )

        payload = json.loads(JsonFormatter().format(record))
        line = ConsoleFormatter().format(record)

        assert "RuntimeError: boom" in payload["exception"]
        assert "here" in payload["stack"]
        assert "RuntimeError: boom" in line and "here" in line

    def test_console_shows_extras_as_key_value_pairs(self):
        line = ConsoleFormatter().format(_record(user_id="u1", status=200))

        assert re.fullmatch(
            r"\d\d:\d\d:\d\d\.\d{3} INFO  app  hello user_id=u1 status=200", line
        )

    def test_console_ends_with_the_short_request_id_and_hides_request_fields(self):
        context = {"request_id": "2fd09820bc444164b617ed8d49196874", "client_ip": "ip"}

        with request_log_context(**context):
            line = ConsoleFormatter().format(_record(challenge_id=42, **context))

        assert line.endswith(" app  hello challenge_id=42 [2fd09820]")

    def test_console_access_line_shows_user_and_ip_without_changing_the_record(self):
        context = {
            "request_id": "2fd09820bc444164b617ed8d49196874",
            "client_ip": "127.0.0.1",
            "user_id": "3f2a9c1e-8b7d-4e21-9a0f-1c2d3e4f5a6b",
        }
        record = _record(
            "GET /c/42 200 0.5ms",
            method="GET",
            path="/c/42",
            route="/c/{cid}",
            status=200,
            duration_ms=0.5,
            **context,
        )
        record.name = ACCESS_LOGGER
        before = dict(vars(record))

        with request_log_context(**context):
            line = ConsoleFormatter().format(record)

        assert line.endswith(
            " INFO  access  GET /c/42 200 0.5ms [2fd09820] user=3f2a9c1e ip=127.0.0.1"
        )
        # The record is shared with the JSON and OpenTelemetry handlers.
        assert vars(record) == before

    def test_console_keeps_an_extra_that_overrides_a_bound_field(self):
        with log_context(request_id="r1", user_id="bound"):
            line = ConsoleFormatter().format(
                _record(request_id="r1", user_id="explicit")
            )

        assert line.endswith("hello user_id=explicit [r1]")

    def test_console_shows_fields_bound_inside_a_request_after_its_id(self):
        formatter = ConsoleFormatter()

        with request_log_context(request_id="r1", user_id="u1"):
            with log_context(job="export", user_id="u2"):
                bind_log_context(step="2")
                line = formatter.format(_record(**current_log_context()))

        assert line.endswith("hello [r1] user=u2 job=export step=2")

    def test_console_defers_fields_bound_on_the_request_to_the_access_line(self):
        with request_log_context(request_id="r1"):
            bind_log_context(user_id="u1")
            line = ConsoleFormatter().format(_record(**current_log_context()))

        assert line.endswith("hello [r1]")

    def test_console_shows_bound_fields_on_every_line_outside_a_request(self):
        with log_context(job="nightly", run_id="0123456789"):
            line = ConsoleFormatter().format(
                _record(n=1, job="nightly", run_id="0123456789")
            )

        assert line.endswith("hello n=1 job=nightly run=01234567")

    def test_console_shows_trace_ids_on_the_access_line_only(self):
        tracer = TracerProvider().get_tracer("test")
        formatter = ConsoleFormatter()
        access = _record(request_id="r1")
        access.name = ACCESS_LOGGER

        with (
            request_log_context(request_id="r1"),
            tracer.start_as_current_span("work") as span,
        ):
            ordinary = formatter.format(_record(request_id="r1"))
            access_line = formatter.format(access)

        trace_id = format_trace_id(span.get_span_context().trace_id)
        assert ordinary.endswith("hello [r1]")
        assert f"[r1] trace={trace_id[:8]} span=" in access_line

    @pytest.mark.parametrize(
        ("kwargs", "name", "extra", "suffix"),
        [
            (
                {"hidden_fields": {"app": {"secret"}}, "logger_names": {"app": "a"}},
                "app",
                {"secret": "x", "shown": "y"},
                " a  hello shown=y",
            ),
            (
                {"hidden_fields": {}, "logger_names": {}},
                ACCESS_LOGGER,
                {"method": "GET"},
                f" {ACCESS_LOGGER}  hello method=GET",
            ),
        ],
        ids=["custom", "without-defaults"],
    )
    def test_console_hidden_fields_and_logger_names_are_configurable(
        self, kwargs, name, extra, suffix
    ):
        record = _record(**extra)
        record.name = name

        line = ConsoleFormatter(**kwargs).format(record)

        assert line.endswith(suffix)

    def test_console_colors_the_level_and_dims_the_rest(self):
        line = ConsoleFormatter(colors=True).format(_record(user_id="u1"))

        assert "\033[32mINFO" in line
        assert "\033[2muser_id=u1" in line
        assert "\033[2mapp\033[0m" in line

    def test_console_colors_add_nothing_after_a_line_without_fields(self):
        line = ConsoleFormatter(colors=True).format(_record())

        assert line.endswith("\033[2mapp\033[0m  hello")


class TestLogContext:
    """``log_context`` and ``bind_log_context`` scope fields to the current task."""

    def test_nested_contexts_restore_the_previous_fields(self):
        with log_context(request_id="r1"):
            with log_context(user_id="u1"):
                assert current_log_context() == {"request_id": "r1", "user_id": "u1"}
            assert current_log_context() == {"request_id": "r1"}
        assert current_log_context() == {}

    def test_bind_outside_a_context_sets_the_fields(self):
        bind_log_context(user_id="u1")

        assert current_log_context() == {"user_id": "u1"}

    def test_bind_inside_a_context_ends_with_it(self):
        with log_context(request_id="r1"):
            bind_log_context(user_id="u1")
            assert current_log_context() == {"request_id": "r1", "user_id": "u1"}
        assert current_log_context() == {}

    def test_bound_fields_reach_records_unless_none_or_overridden_by_extra(
        self, capsys
    ):
        configure_logging(_JSON)

        with log_context(user_id="u1", tenant="t1", skipped=None):
            get_logger("myapp").warning("hello", extra={"tenant": "explicit"})

        (line,) = _json_lines(capsys.readouterr().out)
        assert line["user_id"] == "u1"
        assert line["tenant"] == "explicit"
        assert "skipped" not in line

    def test_records_inside_a_span_carry_its_trace_and_span_ids(self, capsys):
        configure_logging(_JSON)
        tracer = TracerProvider().get_tracer("test")

        with tracer.start_as_current_span("work") as span:
            get_logger("myapp").warning("inside")
        get_logger("myapp").warning("outside")

        inside, outside = _json_lines(capsys.readouterr().out)
        span_context = span.get_span_context()
        assert inside["trace_id"] == format_trace_id(span_context.trace_id)
        assert inside["span_id"] == format_span_id(span_context.span_id)
        assert "trace_id" not in outside


class TestOtelBridge:
    """``otel`` sends records to an OpenTelemetry logger provider."""

    @pytest.fixture
    def exporter(self):
        """In-memory exporter behind the provider the bridge sends to."""
        exporter = InMemoryLogRecordExporter()
        provider = LoggerProvider()
        provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
        configure_logging(stream="stdout", otel=True, otel_logger_provider=provider)
        return exporter

    def test_forwards_records_with_bound_fields_as_attributes(self, exporter):
        with log_context(user_id="u1"):
            get_logger("myapp").warning("hello", extra={"order": 7})

        (exported,) = exporter.get_finished_logs()
        record = exported.log_record
        assert record.body == "hello"
        assert record.attributes["user_id"] == "u1"
        assert record.attributes["order"] == 7

    def test_links_the_active_span_without_duplicating_its_ids(self, exporter):
        tracer = TracerProvider().get_tracer("test")

        with tracer.start_as_current_span("work") as span:
            get_logger("myapp").warning("inside")

        record = exporter.get_finished_logs()[0].log_record
        assert record.trace_id == span.get_span_context().trace_id
        assert "trace_id" not in record.attributes
        assert "span_id" not in record.attributes

    def test_does_not_forward_the_opentelemetry_sdk_own_records(self, exporter):
        get_logger("opentelemetry.sdk._logs").warning("export failed")

        assert exporter.get_finished_logs() == ()

    @pytest.mark.parametrize(
        ("otel", "extra_installed", "handlers"),
        [("auto", True, 2), (False, True, 1), ("auto", False, 1)],
        ids=["auto-with-extra", "disabled", "auto-without-extra"],
    )
    def test_attaches_the_bridge_when_enabled_and_installed(
        self, monkeypatch, otel, extra_installed, handlers
    ):
        if not extra_installed:
            # Re-import the bridge while the package of the ``otel`` extra is missing.
            monkeypatch.delitem(sys.modules, "fastapi_toolsets.logger.otel")
            monkeypatch.setitem(
                sys.modules, "opentelemetry.instrumentation.logging.handler", None
            )

        configure_logging(otel=otel)

        assert len(owned_handlers()) == handlers


class TestRequestLoggingMiddleware:
    """Request ids, request-scoped fields and one access line per request."""

    @pytest.mark.anyio
    async def test_each_request_gets_an_id_shared_by_its_records_and_access_line(
        self, capsys
    ):
        response = await _get(_app(), "/items/3")

        app_line, access = _json_lines(capsys.readouterr().out)
        request_id = response.headers["X-Request-ID"]
        assert len(request_id) == 32
        assert app_line["request_id"] == request_id
        assert app_line["client_ip"] == "127.0.0.1"
        assert access["message"].startswith("GET /items/3 200 ")
        assert access["method"] == "GET"
        assert access["path"] == "/items/3"
        assert access["route"] == "/items/{item_id}"
        assert access["status"] == 200
        assert access["duration_ms"] >= 0
        assert access["request_id"] == request_id
        assert set(ACCESS_FIELDS) <= access.keys()

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("path", "app_suffix", "access_pattern"),
        [
            (
                "/items/3",
                " myapp  reading [{id}]",
                r" access  GET /items/3 200 [\d.]+ms \[{id}\] user=u1 ip=127\.0\.0\.1$",
            ),
            (
                "/export",
                " myapp  exporting [{id}] job=export",
                r" access  GET /export 200 [\d.]+ms \[{id}\] ip=127\.0\.0\.1$",
            ),
        ],
        ids=["bound-user", "nested-context"],
    )
    async def test_console_lines_carry_the_short_request_id(
        self, capsys, path, app_suffix, access_pattern
    ):
        config = LoggingConfig(otel=False, stream="stdout", style="console")
        response = await _get(_app(config), path)

        app_line, access = capsys.readouterr().out.splitlines()
        short_id = response.headers["X-Request-ID"][:8]
        assert app_line.endswith(app_suffix.format(id=short_id))
        assert re.search(access_pattern.format(id=short_id), access), access

    @pytest.mark.anyio
    async def test_a_field_bound_in_a_sync_dependency_reaches_the_request_records(
        self, capsys
    ):
        await _get(_app(), "/items/3")

        app_line, access = _json_lines(capsys.readouterr().out)
        assert app_line["user_id"] == "u1"
        assert access["user_id"] == "u1"

    @pytest.mark.anyio
    async def test_requests_do_not_share_bound_fields(self, capsys):
        app = _app()

        await _get(app, "/items/3")
        await _get(app, "/health")

        *_, health = _access_lines(capsys.readouterr().out)
        assert "user_id" not in health
        assert current_log_context() == {}

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("path", "status", "body"),
        [("/boom", 500, b"Internal Server Error"), ("/stream", 200, b"ab")],
        ids=["unhandled-error", "streaming"],
    )
    async def test_access_line_records_the_status_sent(
        self, capsys, path, status, body
    ):
        response = await _get(_app(), path)

        assert (response.status_code, response.content) == (status, body)
        (access,) = _access_lines(capsys.readouterr().out)
        assert access["status"] == status

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("path", "level", "traceback"),
        [
            ("/health", "INFO", ""),
            ("/missing", "INFO", ""),
            ("/unavailable", "ERROR", ""),
            ("/boom", "ERROR", "RuntimeError: boom"),
        ],
        ids=["ok", "client-error", "server-error", "unhandled-error"],
    )
    async def test_access_line_level_follows_the_outcome(
        self, capsys, path, level, traceback
    ):
        await _get(_app(), path)

        (access,) = _access_lines(capsys.readouterr().out)
        last_traceback_line = access.get("exception", "").rsplit("\n", 1)[-1]
        assert (access["level"], last_traceback_line) == (level, traceback)

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("kwargs", "uvicorn_traceback_kept"),
        [
            ({}, False),
            ({"exclude": lambda scope: scope["path"] == "/boom"}, True),
            ({"access_log": False}, True),
            (
                {"config": replace(_JSON, levels={ACCESS_LOGGER: "CRITICAL"})},
                True,
            ),
        ],
        ids=[
            "on-the-access-line",
            "excluded-request",
            "access-log-off",
            "access-line-filtered-out",
        ],
    )
    async def test_uvicorn_traceback_is_dropped_only_when_the_access_line_has_it(
        self, capsys, kwargs, uvicorn_traceback_kept
    ):
        app = _app(**kwargs)
        with pytest.raises(RuntimeError) as raised:
            await _get(app, "/boom", raise_app_exceptions=True)

        # What uvicorn does once the exception leaves the app.
        logging.getLogger("uvicorn.error").error(
            "Exception in ASGI application", exc_info=raised.value
        )

        uvicorn_lines = _access_lines(capsys.readouterr().out, "uvicorn.error")
        assert len(uvicorn_lines) == uvicorn_traceback_kept

    @pytest.mark.anyio
    async def test_a_field_bound_behind_another_middleware_reaches_the_access_line(
        self, capsys
    ):
        app = FastAPI()

        # BaseHTTPMiddleware runs the rest of the app in its own task.
        @app.middleware("http")
        async def passthrough(request, call_next):
            return await call_next(request)

        @app.get("/me")
        async def me():
            bind_log_context(user_id="u1")
            return {}

        init_logging(app, _JSON)
        await _get(app, "/me")

        (access,) = _access_lines(capsys.readouterr().out)
        assert access["user_id"] == "u1"

    @pytest.mark.anyio
    async def test_concurrent_tasks_keep_their_fields_inside_log_context(self, capsys):
        app = FastAPI()
        logger = get_logger("myapp")

        async def work(item_id: int) -> None:
            with log_context(item_id=item_id):
                bind_log_context(attempt=item_id)
                # Reverse order, so each task logs after the others bound.
                await asyncio.sleep(0.01 * (3 - item_id))
                logger.warning("worked")

        @app.get("/fanout")
        async def fanout():
            await asyncio.gather(*(work(item_id) for item_id in range(3)))
            return {}

        init_logging(app, _JSON)
        await _get(app, "/fanout")

        *worked, access = _json_lines(capsys.readouterr().out)
        assert sorted((line["item_id"], line["attempt"]) for line in worked) == [
            (0, 0),
            (1, 1),
            (2, 2),
        ]
        assert "item_id" not in access and "attempt" not in access

    @pytest.mark.anyio
    async def test_excluded_requests_get_no_id_and_no_access_line(self, capsys):
        app = _app(exclude=lambda scope: scope["path"] == "/health")

        response = await _get(app, "/health")

        assert "X-Request-ID" not in response.headers
        assert _access_lines(capsys.readouterr().out) == []

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("trusted", "incoming", "kept"),
        [
            (False, "abc", False),
            (True, "proxy-id.1:2", True),
            (True, "bad id\nforged=1", False),
            (True, "x" * 129, False),
        ],
        ids=["untrusted", "trusted", "trusted-but-unsafe", "trusted-but-too-long"],
    )
    async def test_an_incoming_request_id_is_kept_only_when_trusted_and_safe(
        self, trusted, incoming, kept
    ):
        app = _app(trust_incoming_id=trusted)

        response = await _get(app, "/health", headers={"X-Request-ID": incoming})

        assert (response.headers["X-Request-ID"] == incoming) is kept

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("header", "sent"),
        [("X-Trace", {"x-trace"}), (None, set())],
        ids=["renamed", "turned-off"],
    )
    async def test_the_request_id_header_can_be_renamed_or_turned_off(
        self, header, sent
    ):
        response = await _get(_app(request_id_header=header), "/health")

        assert {"x-request-id", "x-trace"} & set(response.headers) == sent

    @pytest.mark.anyio
    async def test_a_client_ip_function_replaces_the_socket_peer(self, capsys):
        await _get(_app(client_ip=lambda scope: "10.0.0.1"), "/health")

        (access,) = _access_lines(capsys.readouterr().out)
        assert access["client_ip"] == "10.0.0.1"

    @pytest.mark.parametrize(
        ("config", "kwargs", "access_lines_logged", "uvicorn_access_level"),
        [
            (None, {}, 1, logging.WARNING),
            (None, {"access_log": False}, 0, logging.NOTSET),
            (
                replace(_JSON, access_log=False),
                {},
                0,
                logging.NOTSET,
            ),
            (
                replace(_JSON, access_log=False),
                {"access_log": True},
                1,
                logging.WARNING,
            ),
            (
                replace(_JSON, levels={"uvicorn.access": "INFO"}),
                {},
                1,
                logging.INFO,
            ),
        ],
        ids=[
            "default",
            "disabled",
            "disabled-in-config",
            "argument-overrides-config",
            "explicit-level-wins",
        ],
    )
    @pytest.mark.anyio
    async def test_access_log_replaces_uvicorns(
        self, capsys, config, kwargs, access_lines_logged, uvicorn_access_level
    ):
        app = _app(config, **kwargs)

        await _get(app, "/openapi.json")

        assert len(_access_lines(capsys.readouterr().out)) == access_lines_logged
        assert logging.getLogger("uvicorn.access").level == uvicorn_access_level

    @pytest.mark.anyio
    async def test_non_http_scopes_pass_through_untouched(self):
        seen = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            seen.append(scope["type"])

        async def receive() -> Message:
            return {"type": "lifespan.startup"}

        async def send(message: Message) -> None:
            pass

        await RequestLoggingMiddleware(app)({"type": "lifespan"}, receive, send)

        assert seen == ["lifespan"]


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
