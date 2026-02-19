# CLI

Typer-based command-line interface for managing your FastAPI application, with built-in fixture loading.

## Installation

=== "uv"
    ``` bash
    uv add "fastapi-toolsets[cli]"
    ```

=== "pip"
    ``` bash
    pip install "fastapi-toolsets[cli]"
    ```

## Overview

The `cli` module provides a `manager` entry point built with [Typer](https://typer.tiangolo.com/). It auto-discovers fixture commands when a [`FixtureRegistry`](../reference/fixtures.md#fastapi_toolsets.fixtures.registry.FixtureRegistry) and a database context are configured.

## Configuration

Configure the CLI in your `pyproject.toml`:

```toml
[tool.fastapi-toolsets]
cli = "myapp.cli:cli"           # optional: your custom Typer app
fixtures = "myapp.fixtures:registry"  # FixtureRegistry instance
db_context = "myapp.db:db_context"    # async context manager for sessions
```

All fields are optional. Without configuration, the `manager` command still works but only includes the built-in commands.

## Usage

```bash
# List available commands
manager --help

# Load fixtures for a specific context
manager fixtures load --context testing

# Load all fixtures (no context filter)
manager fixtures load
```

## Custom CLI

You can extend the CLI by providing your own Typer app. The `manager` entry point will merge your app's commands with the built-in ones:

```python
# myapp/cli.py
import typer

cli = typer.Typer()

@cli.command()
def hello():
    print("Hello from my app!")
```

```toml
[tool.fastapi-toolsets]
cli = "myapp.cli:cli"
```

## Entry point

The `manager` script is registered automatically when the package is installed:

```bash
manager --help
```

---

[:material-api: API Reference](../reference/cli.md)
