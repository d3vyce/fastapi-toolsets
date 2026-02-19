# Fixtures

Dependency-aware database seeding with context-based loading strategies.

## Overview

The `fixtures` module lets you define named fixtures with dependencies between them, then load them into the database in the correct order. Fixtures can be scoped to contexts (e.g. base data, testing data) so that only the relevant ones are loaded for each environment.

## Defining fixtures

```python
from fastapi_toolsets.fixtures import FixtureRegistry, Context

fixtures = FixtureRegistry()

@fixtures.register
def roles():
    return [
        Role(id=1, name="admin"),
        Role(id=2, name="user"),
    ]

@fixtures.register(depends_on=["roles"], contexts=[Context.TESTING])
def test_users():
    return [
        User(id=1, username="alice", role_id=1),
        User(id=2, username="bob", role_id=2),
    ]
```

Dependencies declared via `depends_on` are resolved topologically — `roles` will always be loaded before `test_users`.

## Loading fixtures

### By context

```python
from fastapi_toolsets.fixtures import load_fixtures_by_context

async with db_context() as session:
    await load_fixtures_by_context(session, registry=fixtures, context=Context.TESTING)
```

### Directly

```python
from fastapi_toolsets.fixtures import load_fixtures

async with db_context() as session:
    await load_fixtures(session, registry=fixtures)
```

## Contexts

[`Context`](../reference/fixtures.md#fastapi_toolsets.fixtures.enum.Context) is an enum with predefined values:

| Context | Description |
|---------|-------------|
| `Context.BASE` | Core data required in all environments |
| `Context.TESTING` | Data only loaded during tests |
| `Context.PRODUCTION` | Data only loaded in production |

A fixture with no `contexts` argument is loaded in all contexts.

## Load strategies

[`LoadStrategy`](../reference/fixtures.md#fastapi_toolsets.fixtures.enum.LoadStrategy) controls how the fixture loader handles rows that already exist:

| Strategy | Description |
|----------|-------------|
| `LoadStrategy.INSERT` | Insert only, fail on duplicates |
| `LoadStrategy.UPSERT` | Insert or update on conflict |
| `LoadStrategy.SKIP` | Skip rows that already exist |

## Pytest integration

Use [`register_fixtures`](../reference/pytest.md#fastapi_toolsets.pytest.plugin.register_fixtures) to expose each fixture in your registry as an injectable pytest fixture named `fixture_{name}`:

```python
# conftest.py
import pytest
from fastapi_toolsets.pytest import create_db_session, register_fixtures
from app.fixtures import registry
from app.models import Base

DATABASE_URL = "postgresql+asyncpg://user:pass@localhost/test_db"

@pytest.fixture
async def db_session():
    async with create_db_session(database_url=DATABASE_URL, base=Base, cleanup=True) as session:
        yield session

register_fixtures(registry=registry, namespace=globals())
```

```python
# test_users.py
async def test_user_can_login(fixture_users, fixture_roles, client):
    # fixture_roles is loaded first (dependency), then fixture_users
    response = await client.post("/auth/login", json={"username": "alice"})
    assert response.status_code == 200
```


The load order is resolved automatically from the `depends_on` declarations in your registry. Each generated fixture receives `db_session` as a dependency and returns the list of loaded model instances.

## CLI integration

Fixtures can be triggered from the CLI. See the [CLI module](cli.md) for setup instructions.

---

[:material-api: API Reference](../reference/fixtures.md)
