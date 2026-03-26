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

By context with [`load_fixtures_by_context`](../reference/fixtures.md#fastapi_toolsets.fixtures.utils.load_fixtures_by_context):

```python
from fastapi_toolsets.fixtures import load_fixtures_by_context

async with db_context() as session:
    await load_fixtures_by_context(session, fixtures, Context.TESTING)
```

Directly by name with [`load_fixtures`](../reference/fixtures.md#fastapi_toolsets.fixtures.utils.load_fixtures):

```python
from fastapi_toolsets.fixtures import load_fixtures

async with db_context() as session:
    await load_fixtures(session, fixtures, "roles", "test_users")
```

Both functions return a `dict[str, list[...]]` mapping each fixture name to the list of loaded instances.

## Contexts

[`Context`](../reference/fixtures.md#fastapi_toolsets.fixtures.enum.Context) is an enum with predefined values:

| Context | Description |
|---------|-------------|
| `Context.BASE` | Core data required in all environments |
| `Context.TESTING` | Data only loaded during tests |
| `Context.DEVELOPMENT` | Data only loaded in development |
| `Context.PRODUCTION` | Data only loaded in production |

A fixture with no `contexts` defined takes `Context.BASE` by default.

### Custom contexts

Plain strings and any `Enum` subclass are accepted wherever a `Context` enum is expected.

```python
from enum import Enum

class AppContext(str, Enum):
    STAGING = "staging"
    DEMO = "demo"

@fixtures.register(contexts=[AppContext.STAGING])
def staging_data():
    return [Config(key="feature_x", enabled=True)]

await load_fixtures_by_context(session, fixtures, AppContext.STAGING)
```

### Default context for a registry

Pass `contexts` to `FixtureRegistry` to set a default for all fixtures registered in it:

```python
testing_registry = FixtureRegistry(contexts=[Context.TESTING])

@testing_registry.register          # implicitly contexts=[Context.TESTING]
def test_orders():
    return [Order(id=1, total=99)]
```

### Same fixture name, multiple context variants

The same fixture name may be registered under different (non-overlapping) context sets. When multiple contexts are loaded together, all matching variants are merged:

```python
@fixtures.register(contexts=[Context.BASE])
def users():
    return [User(id=1, username="admin")]

@fixtures.register(contexts=[Context.TESTING])
def users():
    return [User(id=2, username="tester")]

# loads both admin and tester
await load_fixtures_by_context(session, fixtures, Context.BASE, Context.TESTING)
```

Registering two variants with overlapping context sets raises `ValueError`.

## Load strategies

[`LoadStrategy`](../reference/fixtures.md#fastapi_toolsets.fixtures.enum.LoadStrategy) controls how the fixture loader handles rows that already exist:

| Strategy | Description |
|----------|-------------|
| `LoadStrategy.INSERT` | Insert only, fail on duplicates |
| `LoadStrategy.MERGE` | Insert or update on conflict (default) |
| `LoadStrategy.SKIP_EXISTING` | Skip rows that already exist |

```python
await load_fixtures_by_context(
    session, fixtures, Context.BASE, strategy=LoadStrategy.SKIP_EXISTING
)
```

## Merging registries

Split fixture definitions across modules and merge them:

```python
from myapp.fixtures.dev import dev_fixtures
from myapp.fixtures.prod import prod_fixtures

fixtures = FixtureRegistry()
fixtures.include_registry(registry=dev_fixtures)
fixtures.include_registry(registry=prod_fixtures)
```

Fixtures with the same name are allowed as long as their context sets do not overlap. Conflicting contexts raise `ValueError`.

## Looking up fixture instances

[`get_obj_by_attr`](../reference/fixtures.md#fastapi_toolsets.fixtures.utils.get_obj_by_attr) retrieves a specific instance from a fixture function by attribute value — useful when building cross-fixture `depends_on` relationships:

```python
from fastapi_toolsets.fixtures import get_obj_by_attr

@fixtures.register(depends_on=["roles"])
def users():
    admin_role = get_obj_by_attr(roles, "name", "admin")
    return [User(id=1, username="alice", role_id=admin_role.id)]
```

Raises `StopIteration` if no matching instance is found.

## Pytest integration

Use [`register_fixtures`](../reference/pytest.md#fastapi_toolsets.pytest.plugin.register_fixtures) to expose each fixture in your registry as an injectable pytest fixture named `fixture_{name}` by default:

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
async def test_user_can_login(fixture_users: list[User], fixture_roles: list[Role]):
    ...
```

The load order is resolved automatically from the `depends_on` declarations in your registry. Each generated fixture receives `db_session` as a dependency and returns the list of loaded model instances.

## CLI integration

Fixtures can be triggered from the CLI. See the [CLI module](cli.md) for setup instructions.

---

[:material-api: API Reference](../reference/fixtures.md)
