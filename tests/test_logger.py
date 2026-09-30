import json
import logging
import re
import sys
from decimal import Decimal

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.trace import TracerProvider
from starlette.types import Message, Receive, Scope, Send

from fastapi_toolsets.logger import (
    ACCESS_FIELDS,
    ACCESS_LOGGER,
    ConsoleFormatter,
    JsonFormatter,
    LoggingConfig,
    RequestLoggingMiddleware,
    bind_log_context,
    configure_logging,
    current_log_context,
    get_logger,
    init_logging,
    log_context,
)
from fastapi_toolsets.logger import context as context_module
from fastapi_toolsets.logger.config import (
    _UVICORN_LOGGERS,
    DEFAULT_QUIET_LOGGERS,
    owned_handlers,
)
from fastapi_toolsets.logger.context import request_log_context

TOUCHED_LOGGERS = (
    *_UVICORN_LOGGERS,
    *DEFAULT_QUIET_LOGGERS,
    "sqlalchemy.engine",
    "myapp",
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
    root.setLevel(logging.WARNING)
    for name in TOUCHED_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)


def json_lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line]


def make_record(msg: str = "hello", **extra) -> logging.LogRecord:
    record = logging.LogRecord("app", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestConfigureLogging:
    def test_returns_root_logger(self):
        assert configure_logging(otel=False) is logging.getLogger()

    def test_default_level_is_info(self):
        assert configure_logging(otel=False).level == logging.INFO

    @pytest.mark.parametrize(
        ("level", "expected"),
        [("DEBUG", logging.DEBUG), (logging.ERROR, logging.ERROR)],
    )
    def test_custom_level(self, level, expected):
        assert configure_logging(otel=False, level=level).level == expected

    @pytest.mark.parametrize(
        ("kwargs", "stream"),
        [({}, "stderr"), ({"stream": "stdout"}, "stdout")],
    )
    def test_stream(self, kwargs, stream):
        configure_logging(otel=False, **kwargs)

        (handler,) = owned_handlers()
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is getattr(sys, stream)

    def test_config_and_overrides(self):
        root = configure_logging(
            LoggingConfig(level="ERROR", otel=False), level="DEBUG"
        )

        assert root.level == logging.DEBUG

    def test_idempotent_no_duplicate_handlers(self):
        configure_logging(otel=False)
        configure_logging(otel=False)
        configure_logging(otel=False)

        assert len(owned_handlers()) == 1

    def test_keeps_foreign_handlers(self):
        foreign = logging.NullHandler()
        root = logging.getLogger()
        root.addHandler(foreign)
        try:
            configure_logging(otel=False)
            configure_logging(otel=False)

            assert foreign in root.handlers
        finally:
            root.removeHandler(foreign)

    def test_auto_style_is_json_off_a_terminal(self, capsys):
        configure_logging(otel=False, stream="stdout")
        get_logger("myapp").warning("hello")

        (line,) = json_lines(capsys.readouterr().out)
        assert line["message"] == "hello"

    def test_console_style(self, capsys):
        configure_logging(otel=False, stream="stdout", style="console")
        get_logger("myapp").warning("hello")

        out = capsys.readouterr().out
        assert "WARNING myapp  hello" in out
        assert "\033[" not in out

    def test_console_options(self, capsys):
        configure_logging(
            otel=False,
            stream="stdout",
            style="console",
            datefmt="%Y-%m-%d %H:%M:%S",
            request_id_length=4,
        )
        with log_context(request_id="abcdef"):
            get_logger("myapp").warning("hello")

        out = capsys.readouterr().out
        assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} ", out)
        assert out.rstrip().endswith("hello [abcd]")

    def test_service_from_otel_env(self, capsys, monkeypatch):
        monkeypatch.setenv("OTEL_SERVICE_NAME", "my-api")
        configure_logging(otel=False, stream="stdout", style="json")
        get_logger("myapp").warning("hello")

        (line,) = json_lines(capsys.readouterr().out)
        assert line["service"] == "my-api"

    def test_unknown_style_raises(self):
        with pytest.raises(ValueError, match="Unknown log style"):
            configure_logging(otel=False, style="xml")  # type: ignore[arg-type]

    def test_routes_uvicorn_through_root(self):
        for name in _UVICORN_LOGGERS:
            logging.getLogger(name).addHandler(logging.NullHandler())
            logging.getLogger(name).propagate = False

        configure_logging(otel=False)

        for name in _UVICORN_LOGGERS:
            uvicorn_logger = logging.getLogger(name)
            assert uvicorn_logger.handlers == []
            assert uvicorn_logger.propagate is True
        assert logging.getLogger("uvicorn.access").level == logging.NOTSET

    def test_quiet_levels_and_sql_echo(self):
        configure_logging(
            otel=False,
            quiet=("httpx",),
            sql_echo=True,
            levels={"myapp": "DEBUG"},
        )

        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("sqlalchemy.engine").level == logging.INFO
        assert logging.getLogger("myapp").level == logging.DEBUG


class TestLoggingConfigFromEnv:
    def test_defaults(self):
        assert LoggingConfig.from_env(environ={}) == LoggingConfig()

    def test_reads_prefixed_variables(self):
        config = LoggingConfig.from_env(
            prefix="APP_LOG_",
            environ={
                "APP_LOG_LEVEL": "debug",
                "APP_LOG_STYLE": "JSON",
                "APP_LOG_STREAM": "stdout",
                "APP_LOG_COLORS": "no",
                "APP_LOG_SERVICE": "My-API",
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
            sql_echo=True,
            otel=False,
        )

    def test_otel_auto(self):
        assert LoggingConfig.from_env(environ={"LOG_OTEL": "auto"}).otel == "auto"

    def test_defaults_apply_when_unset(self):
        config = LoggingConfig.from_env(
            environ={"LOG_LEVEL": "ERROR"}, style="json", level="DEBUG"
        )

        assert config.style == "json"
        assert config.level == "ERROR"

    def test_invalid_bool_raises(self):
        with pytest.raises(ValueError, match="LOG_SQL_ECHO"):
            LoggingConfig.from_env(environ={"LOG_SQL_ECHO": "maybe"})


class TestFormatters:
    def test_json_base_keys_win_over_extra(self):
        record = make_record(level="fake", logger="fake", user_id="u1")

        payload = json.loads(JsonFormatter().format(record))

        assert payload["level"] == "INFO"
        assert payload["logger"] == "app"
        assert payload["user_id"] == "u1"
        assert payload["timestamp"].endswith("Z")
        assert "service" not in payload

    def test_json_serializes_unknown_types(self):
        payload = json.loads(JsonFormatter().format(make_record(amount=Decimal("1.5"))))

        assert payload["amount"] == "1.5"

    def test_json_exception(self):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            record = logging.LogRecord(
                "app", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
            )

        payload = json.loads(JsonFormatter().format(record))

        assert "RuntimeError: boom" in payload["exception"]
        assert "RuntimeError: boom" in ConsoleFormatter().format(record)

    def test_console_fields(self):
        line = ConsoleFormatter().format(make_record(user_id="u1", status=200))

        assert re.fullmatch(
            r"\d\d:\d\d:\d\d\.\d{3} INFO  app  hello user_id=u1 status=200", line
        )

    def test_console_short_request_id_after_extras(self):
        context = {"request_id": "2fd09820bc444164b617ed8d49196874", "client_ip": "ip"}

        with request_log_context(**context):
            line = ConsoleFormatter().format(make_record(challenge_id=42, **context))

        assert line.endswith(" app  hello challenge_id=42 [2fd09820]")

    def test_console_access_line(self):
        context = {
            "request_id": "2fd09820bc444164b617ed8d49196874",
            "client_ip": "127.0.0.1",
            "user_id": "3f2a9c1e-8b7d-4e21-9a0f-1c2d3e4f5a6b",
        }
        record = make_record(
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

    def test_console_extra_overriding_context_stays_visible(self):
        with log_context(request_id="r1", user_id="bound"):
            line = ConsoleFormatter().format(
                make_record(request_id="r1", user_id="explicit")
            )

        assert line.endswith("hello user_id=explicit [r1]")

    def test_console_nested_context_within_request(self):
        formatter = ConsoleFormatter()

        with request_log_context(request_id="r1", user_id="u1"):
            with log_context(job="export", user_id="u2"):
                bind_log_context(step="2")
                line = formatter.format(make_record(**current_log_context()))

        assert line.endswith("hello [r1] user=u2 job=export step=2")

    def test_console_request_bind_deferred_to_access_line(self):
        with request_log_context(request_id="r1"):
            bind_log_context(user_id="u1")
            line = ConsoleFormatter().format(make_record(**current_log_context()))

        assert line.endswith("hello [r1]")

    def test_console_context_without_request_id(self):
        with log_context(job="nightly", run_id="0123456789"):
            line = ConsoleFormatter().format(
                make_record(n=1, job="nightly", run_id="0123456789")
            )

        assert line.endswith("hello n=1 job=nightly run=01234567")

    def test_console_trace_ids_on_access_line_only(self):
        tracer = TracerProvider().get_tracer("test")
        formatter = ConsoleFormatter()
        access = make_record(request_id="r1")
        access.name = ACCESS_LOGGER

        with (
            request_log_context(request_id="r1"),
            tracer.start_as_current_span("work") as span,
        ):
            ordinary = formatter.format(make_record(request_id="r1"))
            access_line = formatter.format(access)

        trace_id = format(span.get_span_context().trace_id, "032x")
        assert ordinary.endswith("hello [r1]")
        assert f"[r1] trace={trace_id[:8]} span=" in access_line

    def test_console_custom_hidden_fields_and_names(self):
        formatter = ConsoleFormatter(
            hidden_fields={"app": {"secret"}}, logger_names={"app": "a"}
        )

        line = formatter.format(make_record(secret="x", shown="y"))

        assert line.endswith(" a  hello shown=y")

    def test_console_access_fields_kept_without_defaults(self):
        record = make_record(method="GET")
        record.name = ACCESS_LOGGER

        line = ConsoleFormatter(hidden_fields={}, logger_names={}).format(record)

        assert line.endswith(f" {ACCESS_LOGGER}  hello method=GET")

    def test_console_colors(self):
        line = ConsoleFormatter(colors=True).format(make_record(user_id="u1"))

        assert "\033[32mINFO" in line
        assert "\033[2muser_id=u1" in line
        assert "\033[2mapp\033[0m" in line

    def test_stack_info(self):
        record = make_record()
        record.stack_info = "Stack (most recent call last):\n  here"

        assert "here" in ConsoleFormatter().format(record)
        assert "here" in json.loads(JsonFormatter().format(record))["stack"]


class TestLogContext:
    def test_log_context_restores_previous_values(self):
        with log_context(request_id="r1"):
            with log_context(user_id="u1"):
                assert current_log_context() == {"request_id": "r1", "user_id": "u1"}
            assert current_log_context() == {"request_id": "r1"}
        assert current_log_context() == {}

    def test_bind_outside_a_context(self):
        bind_log_context(user_id="u1")

        assert current_log_context() == {"user_id": "u1"}

    def test_bind_inside_a_context_ends_with_it(self):
        with log_context(request_id="r1"):
            bind_log_context(user_id="u1")
            assert current_log_context() == {"request_id": "r1", "user_id": "u1"}
        assert current_log_context() == {}

    def test_filter_adds_fields_but_extra_wins(self, capsys):
        configure_logging(otel=False, stream="stdout", style="json")
        with log_context(user_id="u1", tenant="t1", skipped=None):
            get_logger("myapp").warning("hello", extra={"tenant": "explicit"})

        (line,) = json_lines(capsys.readouterr().out)
        assert line["user_id"] == "u1"
        assert line["tenant"] == "explicit"
        assert "skipped" not in line

    def test_trace_ids_from_active_span(self, capsys):
        configure_logging(otel=False, stream="stdout", style="json")
        tracer = TracerProvider().get_tracer("test")

        with tracer.start_as_current_span("work") as span:
            get_logger("myapp").warning("inside")
        get_logger("myapp").warning("outside")

        inside, outside = json_lines(capsys.readouterr().out)
        span_context = span.get_span_context()
        assert inside["trace_id"] == format(span_context.trace_id, "032x")
        assert inside["span_id"] == format(span_context.span_id, "016x")
        assert "trace_id" not in outside


class TestOtelBridge:
    @pytest.fixture
    def exporter(self):
        exporter = InMemoryLogRecordExporter()
        provider = LoggerProvider()
        provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
        configure_logging(stream="stdout", otel=True, otel_logger_provider=provider)
        return exporter

    def test_forwards_records_with_context(self, exporter):
        with log_context(user_id="u1"):
            get_logger("myapp").warning("hello", extra={"order": 7})

        (exported,) = exporter.get_finished_logs()
        record = exported.log_record
        assert record.body == "hello"
        assert record.attributes["user_id"] == "u1"
        assert record.attributes["order"] == 7

    def test_links_span_without_duplicate_attributes(self, exporter):
        tracer = TracerProvider().get_tracer("test")

        with tracer.start_as_current_span("work") as span:
            get_logger("myapp").warning("inside")

        record = exporter.get_finished_logs()[0].log_record
        assert record.trace_id == span.get_span_context().trace_id
        assert "trace_id" not in record.attributes
        assert "span_id" not in record.attributes

    def test_skips_opentelemetry_records(self, exporter):
        get_logger("opentelemetry.sdk._logs").warning("export failed")

        assert exporter.get_finished_logs() == ()

    def test_auto_attaches_when_installed(self):
        configure_logging()

        assert len(owned_handlers()) == 2

    def test_disabled(self):
        configure_logging(otel=False)

        assert len(owned_handlers()) == 1

    def test_missing_extra(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "fastapi_toolsets.logger.otel", None)

        configure_logging(otel="auto")
        assert len(owned_handlers()) == 1

        with pytest.raises(ImportError, match=r"fastapi-toolsets\[otel\]"):
            configure_logging(otel=True)


def build_app(config: LoggingConfig | None = None, **kwargs) -> FastAPI:
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

    @app.get("/stream")
    def stream():
        return StreamingResponse(iter([b"a", b"b"]))

    @app.get("/health")
    def health():
        return {}

    config = config or LoggingConfig(otel=False, stream="stdout", style="json")
    init_logging(app, config, **kwargs)
    return app


async def call(app: FastAPI, path: str, **kwargs):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, **kwargs)


def access_lines(text: str) -> list[dict]:
    return [line for line in json_lines(text) if line["logger"] == ACCESS_LOGGER]


class TestRequestLoggingMiddleware:
    @pytest.mark.anyio
    async def test_access_line_and_request_id(self, capsys):
        response = await call(build_app(), "/items/3")

        app_line, access = json_lines(capsys.readouterr().out)
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
    async def test_console_request(self, capsys):
        config = LoggingConfig(otel=False, stream="stdout", style="console")
        response = await call(build_app(config), "/items/3")

        app_line, access = capsys.readouterr().out.splitlines()
        short_id = response.headers["X-Request-ID"][:8]
        assert app_line.endswith(f" myapp  reading [{short_id}]")
        assert re.search(
            rf" access  GET /items/3 200 [\d.]+ms \[{short_id}\] user=u1 ip=127\.0\.0\.1$",
            access,
        )

    @pytest.mark.anyio
    async def test_console_nested_context(self, capsys):
        config = LoggingConfig(otel=False, stream="stdout", style="console")
        response = await call(build_app(config), "/export")

        app_line, access = capsys.readouterr().out.splitlines()
        short_id = response.headers["X-Request-ID"][:8]
        assert app_line.endswith(f" myapp  exporting [{short_id}] job=export")
        assert access.endswith(f"[{short_id}] ip=127.0.0.1")

    @pytest.mark.anyio
    async def test_bind_from_sync_dependency_reaches_request(self, capsys):
        await call(build_app(), "/items/3")

        app_line, access = json_lines(capsys.readouterr().out)
        assert app_line["user_id"] == "u1"
        assert access["user_id"] == "u1"

    @pytest.mark.anyio
    async def test_requests_do_not_share_context(self, capsys):
        app = build_app()
        await call(app, "/items/3")
        await call(app, "/health")

        *_, health = access_lines(capsys.readouterr().out)
        assert "user_id" not in health
        assert current_log_context() == {}

    @pytest.mark.anyio
    async def test_unhandled_error_is_logged_as_500(self, capsys):
        response = await call(build_app(), "/boom")

        assert response.status_code == 500
        (access,) = access_lines(capsys.readouterr().out)
        assert access["status"] == 500

    @pytest.mark.anyio
    async def test_streaming_response(self, capsys):
        response = await call(build_app(), "/stream")

        assert response.content == b"ab"
        (access,) = access_lines(capsys.readouterr().out)
        assert access["status"] == 200

    @pytest.mark.anyio
    async def test_exclude(self, capsys):
        app = build_app(exclude=lambda scope: scope["path"] == "/health")
        response = await call(app, "/health")

        assert "X-Request-ID" not in response.headers
        assert access_lines(capsys.readouterr().out) == []

    @pytest.mark.anyio
    async def test_incoming_id_ignored_by_default(self):
        response = await call(build_app(), "/health", headers={"X-Request-ID": "abc"})

        assert response.headers["X-Request-ID"] != "abc"

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("incoming", "kept"),
        [("proxy-id.1:2", True), ("bad id\nforged=1", False), ("x" * 129, False)],
    )
    async def test_trusted_incoming_id(self, incoming, kept):
        app = build_app(trust_incoming_id=True)
        response = await call(app, "/health", headers={"X-Request-ID": incoming})

        assert (response.headers["X-Request-ID"] == incoming) is kept

    @pytest.mark.anyio
    async def test_custom_header_and_client_ip(self, capsys):
        app = build_app(request_id_header="X-Trace", client_ip=lambda scope: "10.0.0.1")
        response = await call(app, "/health")

        assert "X-Trace" in response.headers
        (access,) = access_lines(capsys.readouterr().out)
        assert access["client_ip"] == "10.0.0.1"

    @pytest.mark.anyio
    async def test_no_header(self):
        response = await call(build_app(request_id_header=None), "/health")

        assert "X-Request-ID" not in response.headers

    @pytest.mark.anyio
    async def test_access_log_disabled(self, capsys):
        app = build_app(access_log=False)
        await call(app, "/health")

        assert access_lines(capsys.readouterr().out) == []
        assert logging.getLogger("uvicorn.access").level == logging.NOTSET

    def test_init_logging_silences_uvicorn_access(self):
        build_app()

        assert logging.getLogger("uvicorn.access").level == logging.WARNING

    def test_explicit_uvicorn_access_level_wins(self):
        config = LoggingConfig(otel=False, levels={"uvicorn.access": "INFO"})
        init_logging(FastAPI(), config)

        assert logging.getLogger("uvicorn.access").level == logging.INFO

    @pytest.mark.anyio
    async def test_non_http_scope_passes_through(self):
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
    def test_returns_named_logger(self):
        logger = get_logger("myapp.services")

        assert isinstance(logger, logging.Logger)
        assert logger.name == "myapp.services"

    def test_returns_root_logger_when_none(self):
        assert get_logger(None) is logging.getLogger()

    def test_defaults_to_caller_module_name(self):
        assert get_logger().name == __name__

    def test_same_name_returns_same_logger(self):
        assert get_logger("myapp") is get_logger("myapp")
