# Logger

Console or JSON logging with per-request context, an access log, and trace correlation for FastAPI's built-in OpenTelemetry support.

## Overview

The `logger` module configures the standard `logging` package once for your whole process:

- one handler writing readable lines on a terminal and JSON lines elsewhere,
- an optional rotating log file shared by every process of the application,
- fields bound to the current request (request id, client IP, your own) added to every record,
- `trace_id` and `span_id` added whenever an OpenTelemetry span is active,
- an optional bridge sending the same records to OpenTelemetry logs.

It builds on `logging` only: libraries and your own code keep calling `logger.info(...)`.

## Setup

Call [`init_logging`](../reference/logger.md#fastapi_toolsets.logger.init_logging) once, after adding your other middleware:

```python
from fastapi import FastAPI
from fastapi_toolsets.logger import LoggingConfig, init_logging

app = FastAPI()
init_logging(app, LoggingConfig.from_env())
```

It configures logging and adds [`RequestLoggingMiddleware`](../reference/logger.md#fastapi_toolsets.logger.RequestLoggingMiddleware), which replaces uvicorn's access log with one line per request:

```json
{"request_id": "5f0c...", "client_ip": "10.0.0.7", "method": "GET", "path": "/items/3", "route": "/items/{item_id}", "status": 200, "duration_ms": 4.2, "timestamp": "2026-09-30T21:14:18.583Z", "level": "INFO", "logger": "fastapi_toolsets.access", "message": "GET /items/3 200 4.2ms"}
```

The line is logged at `ERROR` for a 5xx status, and at `INFO` otherwise. When a route raises, it carries the traceback, so the error is tied to its request; uvicorn's own traceback of that error is then dropped. The 500 response itself has no `X-Request-ID` header, because Starlette sends it outside the middleware.

Outside an app (a CLI, a worker), call [`configure_logging`](../reference/logger.md#fastapi_toolsets.logger.configure_logging) instead:

```python
from fastapi_toolsets.logger import configure_logging

configure_logging(level="DEBUG", style="console")
```

Calling it again replaces the handlers it installed.

If you add [`RequestLoggingMiddleware`](../reference/logger.md#fastapi_toolsets.logger.RequestLoggingMiddleware) yourself, for instance because one `configure_logging` call is shared by the API, a worker and migrations, pass `access_log=True` so uvicorn's access log is capped at `WARNING` as `init_logging` does:

```python
configure_logging(LoggingConfig.from_env(), access_log=True)
app.add_middleware(RequestLoggingMiddleware)
```

## Configuration

[`LoggingConfig`](../reference/logger.md#fastapi_toolsets.logger.LoggingConfig) holds every setting. [`LoggingConfig.from_env`](../reference/logger.md#fastapi_toolsets.logger.LoggingConfig.from_env) reads them from environment variables:

| Variable | Field | Default |
| --- | --- | --- |
| `LOG_LEVEL` | `level` | `INFO` |
| `LOG_STYLE` | `style`: `console`, `json` or `auto` | `auto` |
| `LOG_STREAM` | `stream`: `stderr` or `stdout` | `stderr` |
| `LOG_COLORS` | `colors` | on a terminal |
| `LOG_SERVICE` | `service`, the `service` key of JSON records | `OTEL_SERVICE_NAME` |
| `LOG_FILE` | `file`, a log file written as well | unset |
| `LOG_FILE_MAX_BYTES` | `file_max_bytes`, size after which the file is rotated | `50000000` |
| `LOG_FILE_BACKUPS` | `file_backups`, rotated files kept | `5` |
| `LOG_SQL_ECHO` | `sql_echo`, SQLAlchemy statements at `INFO` | `false` |
| `LOG_OTEL` | `otel`: `true`, `false` or `auto` | `auto` |

With `style="auto"`, output is readable on a terminal and JSON otherwise, so the same code suits local development and containers.

Use `prefix` to read other names, and keyword arguments for your own defaults:

```python
config = LoggingConfig.from_env(prefix="MYAPP_LOG_", style="json")
```

`levels` sets per-logger levels and `quiet` lists loggers capped at `WARNING`:

```python
configure_logging(levels={"myapp.payments": "DEBUG"}, quiet=("httpx", "botocore"))
```

### Third-party loggers

Some libraries install their own handlers, so their records would be written twice or in another format. `propagate` lists the loggers whose handlers are removed so their records reach the root handlers like every other. It covers uvicorn's loggers by default; extend it for others:

```python
from fastapi_toolsets.logger import DEFAULT_PROPAGATE_LOGGERS

configure_logging(propagate=(*DEFAULT_PROPAGATE_LOGGERS, "pgqueuer"))
```

### With pydantic-settings

`from_env` reads plain environment variables and needs no extra package. If your application already uses `pydantic-settings`, declare the fields you expose on a `BaseSettings` model with the same names and build the config from it instead:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from fastapi_toolsets.logger import LoggingConfig, LogLevel, LogStyle


class LogSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOG_")

    level: LogLevel = "INFO"
    style: LogStyle = "auto"
    file: str | None = None
    sql_echo: bool = False


config = LoggingConfig(**LogSettings().model_dump())
```

You then get validation and `.env` files from pydantic, and the settings can live on your existing model.

## Console output

On a terminal, a request logs short lines:

```text
18:00:59.934 INFO  myapp.auth  Flag submitted challenge_id=42 correct=False [2fd09820]
18:00:59.934 INFO  access  GET /api/challenges/42 200 0.5ms [2fd09820] user=3f2a9c1e ip=127.0.0.1
```

- Each line shows fields passed through `extra=`, then the first characters of the request id in brackets.
- The access line shows the fields bound for the whole request once, shortened: `*_id` fields lose their suffix and are cut to the request id length, and `client_ip` becomes `ip`. `trace_id` and `span_id` are treated the same way.
- The access line leaves out `method`, `path`, `route`, `status` and `duration_ms`, which its message already shows.

Fields bound with `log_context` inside a handler end before the access line, so they are shown, shortened the same way, on the lines logged within the block:

```text
18:00:59.934 INFO  myapp.export  Export started [2fd09820] job=nightly-export
```

Outside a request, bound fields are shown on every line. JSON and OpenTelemetry output always keep every field and the full ids.

| Field | Purpose | Default |
| --- | --- | --- |
| `datefmt` | `strftime` format of the timestamp, followed by milliseconds | `"%H:%M:%S"` |
| `request_id_length` | Characters of the request id and other ids shown | `8` |

```python
configure_logging(style="console", datefmt="%Y-%m-%d %H:%M:%S")
```

To change which fields are hidden or how loggers are named, build a [`ConsoleFormatter`](../reference/logger.md#fastapi_toolsets.logger.ConsoleFormatter) with `hidden_fields` and `logger_names` and set it on your own handler.

!!! note
    A field is recognized as bound by comparing it with the context when the line is written, so records formatted in another thread, as with a `QueueHandler`, show it as a plain field.

## File output

Set `file` to write every record to a log file as well, rotated by size:

```python
configure_logging(file="logs/api.log", file_max_bytes=50_000_000, file_backups=5)
```

The file follows `style`, with `auto` meaning JSON. Console lines in it carry the date and no colors. Missing parent directories are created.

The file is a [`SharedRotatingFileHandler`](../reference/logger.md#fastapi_toolsets.logger.SharedRotatingFileHandler): several processes, such as uvicorn workers, write to the same file and one of them rotates it. Each record is written under a lock held on a `.lock` file next to the log, and a process whose file was renamed reopens it before writing. Run every process with the same `file`; there is nothing to prune afterwards.

If `logrotate` or another tool already rotates your files, set `file_max_bytes=0`. The handler then never rotates the file itself and reopens it after each external rotation, so configure the tool to move the file rather than copy and truncate it.

!!! note
    Processes coordinate through `fcntl`, which Windows lacks. There, each process must write its own file.

## Request context

Every record logged while a request is handled carries `request_id` and `client_ip`. Add your own fields with [`bind_log_context`](../reference/logger.md#fastapi_toolsets.logger.bind_log_context), for instance from an authentication dependency:

```python
from fastapi_toolsets.logger import bind_log_context


async def current_user(...) -> User:
    user = ...
    bind_log_context(user_id=str(user.id))
    return user
```

The field stays bound until the request ends, including on its access line. It works from sync dependencies too.

To bind fields for a block only, use [`log_context`](../reference/logger.md#fastapi_toolsets.logger.log_context):

```python
from fastapi_toolsets.logger import get_logger, log_context

logger = get_logger()

with log_context(job="nightly-export"):
    logger.info("Export started")
```

Fields passed through `extra=` take precedence over bound ones.

Tasks started during a request share its fields, so concurrent tasks binding the same key overwrite each other. Run each task in `log_context` to keep its fields to itself:

```python
import asyncio

from fastapi_toolsets.logger import bind_log_context, log_context


async def sync_item(item_id: int) -> None:
    with log_context(item_id=item_id):
        bind_log_context(attempt=1)  # stays in this task
        ...


async def sync_items(item_ids: list[int]) -> None:
    await asyncio.gather(*(sync_item(item_id) for item_id in item_ids))
```

### Middleware options

| Argument | Purpose | Default |
| --- | --- | --- |
| `access_log` | Log one line per request. `init_logging` falls back to `LoggingConfig.access_log` | `True` |
| `request_id_header` | Response header carrying the request id, `None` to omit it | `"X-Request-ID"` |
| `trust_incoming_id` | Reuse a valid id sent in that header, such as one set by your proxy | `False` |
| `client_ip` | Function returning the client IP from the ASGI scope | socket peer |
| `exclude` | Function returning `True` for requests to leave untouched | `None` |

`exclude` takes the same function as FastAPI's `telemetry["exclude"]`, so one filter can skip health checks everywhere:

```python
def is_health_check(scope) -> bool:
    return scope["path"] == "/health"


app = FastAPI(telemetry={"exclude": is_health_check})
init_logging(app, exclude=is_health_check)
```

Behind a reverse proxy, run uvicorn with `--proxy-headers` and `--forwarded-allow-ips` so the socket peer is the real client. Pass a `client_ip` function only when you need other logic.

## OpenTelemetry

FastAPI 0.142 traces requests with OpenTelemetry and exports unhandled errors as OpenTelemetry logs. Your own log calls are not part of that; this module connects them.

### Trace correlation

When a span is active, every record gets `trace_id` and `span_id`, so a log line in your log store leads to the matching trace. No extra install is needed.

### Sending logs to OpenTelemetry

Install the `otel` extra, which provides the handler:

=== "uv"
    ``` bash
    uv add "fastapi-toolsets[otel]"
    ```

=== "pip"
    ``` bash
    pip install "fastapi-toolsets[otel]"
    ```

With `otel="auto"` (the default), records are then also sent to the global OpenTelemetry logger provider, with bound fields as attributes. With `fastapi[standard]` installed, FastAPI can install that provider at startup and export to your collector. Since FastAPI 0.143 this is opt-in:

```bash
export FASTAPI_OTEL_AUTO_CONFIGURE=true
export OTEL_SERVICE_NAME=my-api
export OTEL_EXPORTER_OTLP_ENDPOINT=https://collector.example.com
```

`FastAPI(telemetry={"auto_configure": True})` opts in from code instead. Without either, and without a provider of your own, the global provider is a no-op and nothing is exported.

If you pass a provider to FastAPI directly rather than setting the global one, pass it here too:

```python
app = FastAPI(telemetry={"logger_provider": logger_provider})
init_logging(app, LoggingConfig(otel_logger_provider=logger_provider))
```

Set `otel=False` to keep logs out of OpenTelemetry, or `otel=True` to fail when the extra is missing.

!!! note
    FastAPI also exports unhandled exceptions, so with the bridge enabled they reach OpenTelemetry twice: once from FastAPI, once on the access line.

## Getting a logger

```python
from fastapi_toolsets.logger import get_logger

logger = get_logger(name=__name__)
logger.info("User created")
```

When called without arguments, [`get_logger`](../reference/logger.md#fastapi_toolsets.logger.get_logger) auto-detects the caller's module name via frame inspection:

```python
# Equivalent to get_logger(name=__name__)
logger = get_logger()
```

---

[:material-api: API Reference](../reference/logger.md)
