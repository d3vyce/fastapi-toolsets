# FastAPI Toolsets

A modular collection of production-ready utilities for FastAPI. Install only what you need — from async CRUD and database helpers to CLI tooling, Prometheus metrics, and pytest fixtures. Each module is independently installable via optional extras, keeping your dependency footprint minimal.

[![CI](https://github.com/d3vyce/fastapi-toolsets/actions/workflows/ci.yml/badge.svg)](https://github.com/d3vyce/fastapi-toolsets/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/d3vyce/fastapi-toolsets/graph/badge.svg)](https://codecov.io/gh/d3vyce/fastapi-toolsets)
[![ty](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json)](https://github.com/astral-sh/ty)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

**Documentation**: [https://fastapi-toolsets.d3vyce.fr](https://fastapi-toolsets.d3vyce.fr)

**Source Code**: [https://github.com/d3vyce/fastapi-toolsets](https://github.com/d3vyce/fastapi-toolsets)

---

## Installation

The base package includes the core modules (CRUD, database, schemas, exceptions, fixtures, dependencies, logging):

```bash
uv add fastapi-toolsets
```

Install only the extras you need:

```bash
uv add "fastapi-toolsets[cli]"      # CLI (typer)
uv add "fastapi-toolsets[metrics]"  # Prometheus metrics (prometheus_client)
uv add "fastapi-toolsets[pytest]"   # Pytest helpers (httpx, pytest-xdist)
```

Or install everything:

```bash
uv add "fastapi-toolsets[all]"
```

## Features

### Core

- **CRUD**: Generic async CRUD operations with `CrudFactory`, built-in full-text/faceted search and offset/cursor pagination.
- **Database**: Session management, transaction helpers, table locking, and polling-based row change detection
- **Dependencies**: FastAPI dependency factories (`PathDependency`, `BodyDependency`) for automatic DB lookups from path or body parameters
- **Fixtures**: Fixture system with dependency management, context support, and pytest integration
- **Standardized API Responses**: Consistent response format with `Response`, `PaginatedResponse`, and `PydanticBase`
- **Exception Handling**: Structured error responses with automatic OpenAPI documentation
- **Logging**: Logging configuration with uvicorn integration via `configure_logging` and `get_logger`

### Optional

- **CLI**: Django-like command-line interface with fixture management and custom commands support
- **Metrics**: Prometheus metrics endpoint with provider/collector registry
- **Pytest Helpers**: Async test client, database session management, `pytest-xdist` support, and table cleanup utilities

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.
