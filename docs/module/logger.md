# Logger

Lightweight logging utilities with consistent formatting and uvicorn integration.

## Overview

The `logger` module provides two helpers: one to configure the root logger (and uvicorn loggers) at startup, and one to retrieve a named logger anywhere in your codebase.

## Setup

Call [`configure_logging`](../reference/logger.md#fastapi_toolsets.logger.configure_logging) once at application startup:

```python
from fastapi_toolsets.logger import configure_logging

configure_logging(level="INFO")
```

This sets up a stdout handler with a consistent format and also configures uvicorn's access and error loggers so all log output shares the same style.

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

[:material-api: API Reference](../reference/logger.md)
