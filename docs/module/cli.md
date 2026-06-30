# CLI

Typer-based command-line interface for managing your FastAPI application, with built-in fixture commands integration.

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

The `cli` module provides a `manager` entry point built with [Typer](https://typer.tiangolo.com/). It allow custom commands to be added in addition of the fixture commands when a [`FixtureRegistry`](../reference/fixtures.md#fastapi_toolsets.fixtures.registry.FixtureRegistry) and a database context are configured.

## Configuration

Configure the CLI in your `pyproject.toml`:

```toml
[tool.fastapi-toolsets]
custom_cli = "myapp.cli:cli"                   # Custom Typer app
fixtures = "myapp.fixtures:registry"    # FixtureRegistry instance
db_context = "myapp.db:db_context"      # Async context manager for sessions
```

All fields are optional. Without configuration, the `manager` command still works but no command are available.

## Usage

```bash
# Manager commands
manager --help

 Usage: manager [OPTIONS] COMMAND [ARGS]...

 FastAPI utilities CLI.

╭─ Options ────────────────────────────────────────────────────────────────────────╮
│ --install-completion          Install completion for the current shell.          │
│ --show-completion             Show completion for the current shell, to copy it  │
│                               or customize the installation.                     │
│ --help                        Show this message and exit.                        │
╰──────────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────────╮
│ check-db                                                                         │
│ fixtures  Manage database fixtures.                                              │
╰──────────────────────────────────────────────────────────────────────────────────╯

# Fixtures commands
manager fixtures --help

 Usage: manager fixtures [OPTIONS] COMMAND [ARGS]...

 Manage database fixtures.

╭─ Options ────────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                      │
╰──────────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────────╮
│ list  List all registered fixtures.                                              │
│ load  Load fixtures into the database.                                           │
╰──────────────────────────────────────────────────────────────────────────────────╯
```

### `fixtures load`

```bash
manager fixtures load [CONTEXTS]... [--strategy merge|insert|skip_existing] [--dry-run]
```

`CONTEXTS` defaults to `Context.BASE` when omitted, and can also be set via the `FIXTURES_CONTEXT` environment variable, handy for CI/deploy scripts that shouldn't need an explicit argument per environment:

```bash
FIXTURES_CONTEXT=testing manager fixtures load
```

An explicit CLI argument always takes precedence over the environment variable.

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
custom_cli = "myapp.cli:cli"
```

---

[:material-api: API Reference](../reference/cli.md)
