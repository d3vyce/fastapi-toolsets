"""Tests for the fixture system: registry, dependency resolution and loading."""

import uuid
from collections.abc import Sequence
from enum import Enum
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

import fastapi_toolsets.fixtures as fixtures_package
from fastapi_toolsets.fixtures import (
    Context,
    FixtureRegistry,
    LoadStrategy,
    load_fixtures,
    load_fixtures_by_context,
)
from fastapi_toolsets.fixtures import registry as registry_module
from fastapi_toolsets.fixtures import utils as utils_module
from fastapi_toolsets.fixtures.utils import (
    _get_primary_key,
    _get_table_chain,
    _instance_to_dict,
    _instance_to_dict_for_cls,
)

from .conftest import (
    Challenge,
    ChallengeStandard,
    IntRole,
    Permission,
    Role,
    RoleCreate,
    RoleCrud,
    User,
    UserCrud,
)


class AppContext(str, Enum):
    """A user-defined str enum of contexts."""

    STAGING = "staging"
    DEMO = "demo"


class PlainEnumContext(Enum):
    """A user-defined plain enum (no str mixin) sharing a value with AppContext."""

    STAGING = "staging"


_ADMIN_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()
_ALICE_ID = uuid.uuid4()
_BOB_ID = uuid.uuid4()


def _register(
    registry: FixtureRegistry,
    name: str,
    rows: Sequence[DeclarativeBase] = (),
    **options: Any,
) -> None:
    """Register a fixture *name* returning *rows*."""
    registry.register(lambda: list(rows), name=name, **options)


def _role(name: str) -> Role:
    return Role(id=uuid.uuid4(), name=name)


def _user(name: str, **columns: Any) -> User:
    uid = columns.pop("id", uuid.uuid4())
    return User(id=uid, username=name, email=f"{name}@test.com", **columns)


def _challenge(cid: uuid.UUID, title: str, difficulty: str) -> ChallengeStandard:
    return ChallengeStandard(
        id=cid, title=title, challenge_type="standard", difficulty=difficulty
    )


async def _load(
    session: AsyncSession,
    name: str,
    rows: Sequence[DeclarativeBase],
    strategy: LoadStrategy = LoadStrategy.MERGE,
) -> list[DeclarativeBase]:
    """Load a one-fixture registry and return the rows loaded for *name*."""
    registry = FixtureRegistry()
    _register(registry, name, rows)
    return (await load_fixtures(session, registry, name, strategy=strategy))[name]


async def _rows(session: AsyncSession, model: type, key: str) -> dict[Any, Any]:
    """Every row of *model* in the database, keyed by the column *key*."""
    result = await session.execute(select(model))
    return {getattr(row, key): row for row in result.scalars()}


def _people() -> FixtureRegistry:
    """Roles and users, with a testing variant of the users fixture."""
    registry = FixtureRegistry()
    _register(
        registry,
        "roles",
        [Role(id=_ADMIN_ID, name="admin"), Role(id=_USER_ID, name="user")],
    )
    alice = _user("alice", id=_ALICE_ID, role_id=_ADMIN_ID)
    bob = _user("bob", id=_BOB_ID, role_id=_ADMIN_ID)
    _register(registry, "users", [alice, bob], depends_on=["roles"])
    _register(
        registry, "users", [_user("tester", role_id=_USER_ID)], contexts=["testing"]
    )
    return registry


class TestPackage:
    """The enums and the lazily imported package exports."""

    def test_enums_are_closed_str_enums(self):
        expected = ["base", "production", "development", "testing"]
        assert [c.value for c in Context] == expected
        assert [s.value for s in LoadStrategy] == ["insert", "merge", "skip_existing"]
        assert Context.BASE == "base" and LoadStrategy.MERGE == "merge"
        with pytest.raises(TypeError):

            class MyContext(Context):  # ty: ignore[subclass-of-final-class]
                STAGING = "staging"

    def test_exports_are_imported_lazily(self):
        assert fixtures_package.FixtureRegistry is registry_module.FixtureRegistry
        assert fixtures_package.load_fixtures is utils_module.load_fixtures
        assert fixtures_package.load_fixtures_by_context is load_fixtures_by_context
        with pytest.raises(AttributeError, match="no attribute 'nope'"):
            assert fixtures_package.nope


class TestRegister:
    """register() and include_registry() store fixtures with their metadata."""

    def test_register_as_bare_decorator_or_with_options(self):
        registry = FixtureRegistry()

        @registry.register
        def roles():
            return []

        @registry.register(name="people", depends_on=["roles"], contexts=["testing"])
        def users():
            return []

        assert [f.name for f in registry.get_all()] == ["roles", "people"]
        first, second = registry.get("roles"), registry.get("people")
        assert first.func is roles and first.depends_on == []
        assert first.contexts == ["base"]
        assert second.func is users and second.depends_on == ["roles"]
        assert second.contexts == ["testing"]

    @pytest.mark.parametrize(
        ("defaults", "declared", "expected"),
        [
            (None, None, ["base"]),
            ([Context.TESTING], None, ["testing"]),
            ([Context.DEVELOPMENT, "custom"], None, ["development", "custom"]),
            ([AppContext.STAGING], None, ["staging"]),
            ([Context.TESTING], [Context.PRODUCTION], ["production"]),
            (None, [Context.TESTING, "x", AppContext.DEMO], ["testing", "x", "demo"]),
        ],
        ids=[
            "base",
            "registry-default",
            "several-defaults",
            "custom-enum-default",
            "explicit-overrides-default",
            "mixed-enum-and-str",
        ],
    )
    def test_fixture_contexts(self, defaults, declared, expected):
        """Declared contexts win over the registry defaults, which win over BASE."""
        registry = FixtureRegistry(contexts=defaults)
        options = {} if declared is None else {"contexts": declared}

        _register(registry, "data", **options)

        assert registry.get("data").contexts == expected

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ([Context.BASE], [Context.BASE]),
            ([Context.BASE, Context.TESTING], [Context.TESTING, "staging"]),
        ],
        ids=["same", "partial-overlap"],
    )
    def test_same_name_with_overlapping_contexts_is_rejected(self, first, second):
        registry = FixtureRegistry()
        _register(registry, "items", contexts=first)
        other = FixtureRegistry()
        _register(other, "items", contexts=second)

        with pytest.raises(ValueError, match="overlapping contexts"):
            _register(registry, "items", contexts=second)
        with pytest.raises(ValueError, match="overlapping contexts"):
            registry.include_registry(other)

    def test_include_registry_merges_fixtures_with_their_metadata(self):
        main, dev, empty = FixtureRegistry(), FixtureRegistry(), FixtureRegistry()
        _register(main, "roles")
        _register(dev, "roles", contexts=[Context.DEVELOPMENT])
        _register(dev, "users", depends_on=["roles"], contexts=["testing", "dev"])

        main.include_registry(dev)
        main.include_registry(empty)

        assert [(f.name, f.contexts, f.depends_on) for f in main.get_all()] == [
            ("roles", ["base"], []),
            ("roles", ["development"], []),
            ("users", ["testing", "dev"], ["roles"]),
        ]


class TestLookup:
    """get(), get_variants(), get_by_context(), obj() and field()."""

    @pytest.mark.parametrize(
        "lookup",
        [
            lambda r: r.get("ghost"),
            lambda r: r.get_variants("ghost"),
            lambda r: r.get_load_variants("ghost", Context.BASE),
            lambda r: r.get_dependencies("ghost"),
            lambda r: r.obj("ghost", "id", 1),
            lambda r: r.resolve_dependencies("ghost"),
        ],
        ids=[
            "get",
            "get_variants",
            "get_load_variants",
            "get_dependencies",
            "obj",
            "resolve_dependencies",
        ],
    )
    def test_unknown_name_raises_key_error(self, lookup):
        with pytest.raises(KeyError, match="'ghost' not found"):
            lookup(FixtureRegistry())

    def test_get_variants_filters_by_context_and_always_keeps_base(self):
        registry = FixtureRegistry()
        _register(registry, "items", contexts=[Context.BASE])
        _register(registry, "items", contexts=[Context.TESTING, "staging"])
        _register(registry, "items", contexts=[AppContext.DEMO])

        def contexts(*filters: str | Enum) -> list[list[str]]:
            return [v.contexts for v in registry.get_variants("items", *filters)]

        assert contexts() == [["base"], ["testing", "staging"], ["demo"]]
        assert contexts(Context.TESTING) == [["base"], ["testing", "staging"]]
        assert contexts(AppContext.STAGING, "demo") == contexts()
        assert contexts(Context.PRODUCTION) == [["base"]]
        with pytest.raises(ValueError, match="has 3 context variants"):
            registry.get("items")

    def test_get_load_variants_falls_back_to_every_variant(self):
        """A name with no variant for the context (nor for BASE) still loads (#337)."""
        registry = FixtureRegistry()
        _register(registry, "env", contexts=["staging"])
        _register(registry, "env", contexts=["demo"])

        def contexts(*filters: str) -> list[list[str]]:
            return [v.contexts for v in registry.get_load_variants("env", *filters)]

        assert contexts("demo") == [["demo"]]
        assert contexts("production") == [["staging"], ["demo"]]
        assert contexts() == [["staging"], ["demo"]]

    @pytest.mark.parametrize(
        ("contexts", "expected"),
        [
            ((), {"base_data"}),
            ((Context.TESTING,), {"base_data", "test_data"}),
            (("staging",), {"base_data", "staging_data"}),
            ((AppContext.STAGING,), {"base_data", "staging_data"}),
            ((PlainEnumContext.STAGING,), {"base_data", "staging_data"}),
            ((AppContext.DEMO,), {"base_data"}),
            ((Context.TESTING, Context.PRODUCTION), {"base_data", "test_data", "prod"}),
        ],
        ids=[
            "none",
            "builtin",
            "string",
            "str-enum",
            "plain-enum",
            "unknown",
            "several",
        ],
    )
    def test_get_by_context_always_includes_base(self, contexts, expected):
        """Enum members and strings with the same value name the same context."""
        registry = FixtureRegistry()
        _register(registry, "base_data")
        _register(registry, "test_data", contexts=[Context.TESTING])
        _register(registry, "prod", contexts=[Context.PRODUCTION])
        _register(registry, "staging_data", contexts=[AppContext.STAGING])

        names = {f.name for f in registry.get_by_context(*contexts)}

        assert names == expected

    def test_obj_and_field_return_the_first_match_across_variants(self):
        registry = _people()

        assert cast(Role, registry.obj("roles", "id", _ADMIN_ID)).name == "admin"
        first = cast(User, registry.obj("users", "role_id", _ADMIN_ID))
        tester = cast(User, registry.obj("users", "username", "tester"))
        assert (first.username, tester.role_id) == ("alice", _USER_ID)
        assert registry.field("roles", "name", "user") == _USER_ID
        assert registry.field("users", "id", _BOB_ID, field="email") == "bob@test.com"

    @pytest.mark.parametrize(
        ("lookup", "message"),
        [
            (
                lambda r: r.obj("roles", "name", "nobody"),
                "name=nobody found in fixture",
            ),
            (lambda r: r.obj("roles", "id", "not-a-uuid"), "id=not-a-uuid"),
            (lambda r: r.field("roles", "name", "nobody"), "name=nobody"),
        ],
        ids=["obj", "obj-wrong-type", "field"],
    )
    def test_no_match_raises_stop_iteration(self, lookup, message):
        with pytest.raises(StopIteration, match=f"No object with {message}"):
            lookup(_people())


class TestDependencies:
    """Dependency resolution by name and by context."""

    @pytest.mark.parametrize(
        ("graph", "names", "expected"),
        [
            ({"roles": [], "users": ["roles"]}, ["users"], ["roles", "users"]),
            (
                {"roles": [], "perms": [], "users": ["roles", "perms"]},
                ["users"],
                ["roles", "perms", "users"],
            ),
            (
                {"base": [], "middle": ["base"], "top": ["middle"]},
                ["top"],
                ["base", "middle", "top"],
            ),
            (
                {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]},
                ["d"],
                ["a", "b", "c", "d"],
            ),
            ({"roles": [], "users": ["roles"]}, ["users", "roles"], ["roles", "users"]),
        ],
        ids=["simple", "several", "transitive", "diamond", "already-resolved"],
    )
    def test_resolve_dependencies_puts_dependencies_first(self, graph, names, expected):
        registry = FixtureRegistry()
        for name, depends_on in graph.items():
            _register(registry, name, depends_on=depends_on)

        assert registry.resolve_dependencies(*names) == expected

    def test_dependencies_are_the_union_across_variants(self):
        registry = FixtureRegistry()
        _register(registry, "roles")
        _register(registry, "tags")
        _register(registry, "items", depends_on=["roles"])
        _register(registry, "items", depends_on=["roles", "tags"], contexts=["testing"])

        assert registry.get_dependencies("items") == ["roles", "tags"]
        assert registry.resolve_dependencies("items") == ["roles", "tags", "items"]

    @pytest.mark.parametrize(
        ("graph", "match"),
        [
            ({"a": ["b"], "b": ["a"]}, "Circular dependency detected: a"),
            ({"a": ["ghost"]}, "'ghost' not found"),
        ],
        ids=["circular", "unknown-dependency"],
    )
    def test_resolve_dependencies_errors(self, graph, match):
        registry = FixtureRegistry()
        for name, depends_on in graph.items():
            _register(registry, name, depends_on=depends_on)

        with pytest.raises((ValueError, KeyError), match=match):
            registry.resolve_dependencies("a")

    def test_resolve_context_dependencies_dedups_names_and_keeps_base(self):
        registry = FixtureRegistry()
        _register(registry, "roles")
        _register(registry, "test_users", depends_on=["roles"], contexts=["testing"])
        _register(registry, "staging_roles", contexts=[AppContext.STAGING])
        _register(registry, "items", contexts=[Context.BASE])
        _register(registry, "items", contexts=[Context.TESTING])

        resolve = registry.resolve_context_dependencies

        assert resolve(Context.TESTING) == ["roles", "test_users", "items"]
        assert resolve(Context.BASE, Context.TESTING) == [
            "roles",
            "test_users",
            "items",
        ]
        assert resolve(AppContext.STAGING) == ["roles", "staging_roles", "items"]
        assert resolve(PlainEnumContext.STAGING) == ["roles", "staging_roles", "items"]
        assert resolve("production") == ["roles", "items"]


class TestLoadFixtures:
    """load_fixtures() writes rows with each strategy and returns refreshed rows."""

    @pytest.mark.anyio
    async def test_load_resolves_dependencies_and_returns_refreshed_rows(
        self, db_session
    ):
        registry = FixtureRegistry()
        admin = _role("admin")
        _register(registry, "roles", [admin])
        alice = _user("alice", role_id=admin.id)
        _register(registry, "users", [alice], depends_on=["roles"])

        result = await load_fixtures(db_session, registry, "users")

        assert list(result) == ["roles", "users"]
        assert [cast(Role, r).name for r in result["roles"]] == ["admin"]
        user = cast(User, result["users"][0])
        assert user.role is not None and user.role.name == "admin"
        assert user.is_active is True
        assert await RoleCrud.count(db_session) == 1
        assert await UserCrud.count(db_session) == 1

    @pytest.mark.anyio
    async def test_empty_fixture_loads_nothing(self, db_session):
        registry = FixtureRegistry()
        _register(registry, "nothing")

        assert await load_fixtures(db_session, registry, "nothing") == {"nothing": []}

    @pytest.mark.anyio
    async def test_rows_of_several_models_are_grouped_per_model(self, db_session):
        admin = _role("admin")
        rows = [admin, _user("alice", role_id=admin.id), _role("user")]

        loaded = await _load(db_session, "seed", rows, LoadStrategy.INSERT)

        assert sorted(type(r).__name__ for r in loaded) == ["Role", "Role", "User"]
        assert set(await _rows(db_session, Role, "name")) == {"admin", "user"}
        alice = (await _rows(db_session, User, "username"))["alice"]
        assert alice.role_id == admin.id

    @pytest.mark.anyio
    async def test_merge_updates_existing_rows_and_the_rows_already_loaded(
        self, db_session
    ):
        rid = uuid.uuid4()

        first = await _load(db_session, "roles", [Role(id=rid, name="admin")])
        second = await _load(db_session, "roles", [Role(id=rid, name="superadmin")])

        assert cast(Role, second[0]).name == "superadmin"
        assert cast(Role, first[0]).name == "superadmin"
        assert await RoleCrud.count(db_session) == 1

    @pytest.mark.anyio
    async def test_merge_only_touches_the_columns_each_row_provides(self, db_session):
        """Rows omitting a nullable column keep its stored value on re-merge."""
        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        editor = await RoleCrud.create(db_session, RoleCreate(name="editor"))
        ids = [uuid.uuid4() for _ in range(3)]
        initial = [
            _user(name, id=uid, role_id=admin.id, notes=name)
            for name, uid in zip(["alice", "bob", "carol"], ids)
        ]
        await _load(db_session, "users", initial, LoadStrategy.INSERT)

        await _load(
            db_session,
            "users",
            [
                _user("alice", id=ids[0], role_id=editor.id, notes="updated"),
                _user("bob", id=ids[1], role_id=editor.id),
                _user("carol", id=ids[2], notes="updated"),
            ],
        )

        rows = await _rows(db_session, User, "username")
        assert (rows["alice"].role_id, rows["alice"].notes) == (editor.id, "updated")
        assert (rows["bob"].role_id, rows["bob"].notes) == (editor.id, "bob")
        assert (rows["carol"].role_id, rows["carol"].notes) == (admin.id, "updated")

    @pytest.mark.anyio
    async def test_insert_keeps_each_rows_own_column_set(self, db_session):
        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        rows = [
            _user("all_set", role_id=admin.id, notes="full"),
            _user("only_role", role_id=admin.id),
            _user("only_notes", notes="partial"),
            _user("null_notes", notes=None),
        ]

        loaded = await _load(db_session, "users", rows, LoadStrategy.INSERT)

        stored = await _rows(db_session, User, "username")
        assert len(loaded) == 4
        assert [
            (stored[u.username].role_id, stored[u.username].notes) for u in rows
        ] == [
            (admin.id, "full"),
            (admin.id, None),
            (None, "partial"),
            (None, None),
        ]

    @pytest.mark.anyio
    async def test_insert_fails_on_existing_rows(self, db_session):
        rid = uuid.uuid4()
        await _load(
            db_session, "roles", [Role(id=rid, name="admin")], LoadStrategy.INSERT
        )

        with pytest.raises(IntegrityError):
            await _load(
                db_session, "roles", [Role(id=rid, name="admin")], LoadStrategy.INSERT
            )

        assert await RoleCrud.count(db_session) == 1

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "strategy", [LoadStrategy.INSERT, LoadStrategy.SKIP_EXISTING]
    )
    async def test_generated_primary_keys_are_written_back(self, db_session, strategy):
        """Keyless rows get a default or sequence value, mixed with keyed rows."""
        explicit = uuid.uuid4()
        roles = [Role(id=explicit, name="admin"), Role(name="user")]

        loaded = await _load(db_session, "roles", roles, strategy)
        int_roles = await _load(
            db_session, "int_roles", [IntRole(name="a"), IntRole(name="b")], strategy
        )

        by_name = {cast(Role, r).name: cast(Role, r).id for r in loaded}
        assert by_name["admin"] == explicit and len(by_name) == 2
        assert isinstance(by_name["user"], uuid.UUID)
        ids = [cast(IntRole, r).id for r in int_roles]
        assert all(isinstance(i, int) for i in ids) and len(set(ids)) == 2

    @pytest.mark.anyio
    async def test_skip_existing_only_inserts_new_rows(self, db_session):
        admin = await RoleCrud.create(db_session, RoleCreate(name="admin"))
        uid = uuid.uuid4()
        existing = _user("alice", id=uid, role_id=admin.id, notes="keep")
        await _load(db_session, "users", [existing], LoadStrategy.INSERT)
        strategy = LoadStrategy.SKIP_EXISTING

        loaded = await _load(
            db_session,
            "users",
            [_user("alice-updated", id=uid), _user("bob", notes="new")],
            strategy,
        )
        bob_id = cast(User, loaded[0]).id
        again = await _load(db_session, "users", [_user("bob2", id=bob_id)], strategy)

        rows = await _rows(db_session, User, "username")
        assert [cast(User, u).username for u in loaded] == ["bob"] and again == []
        assert (rows["alice"].role_id, rows["alice"].notes) == (admin.id, "keep")
        assert rows["bob"].notes == "new"

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("strategy", "reloaded"),
        [(LoadStrategy.MERGE, 2), (LoadStrategy.SKIP_EXISTING, 0)],
    )
    async def test_composite_key_only_model(self, db_session, strategy, reloaded):
        """A key-only table has nothing to update, and is matched on the key tuple."""

        def permissions() -> list[DeclarativeBase]:
            return [Permission(subject="post", action=a) for a in ("read", "write")]

        first = await _load(db_session, "permissions", permissions(), strategy)
        second = await _load(db_session, "permissions", permissions(), strategy)

        assert [cast(Permission, p).action for p in first] == ["read", "write"]
        assert len(second) == reloaded
        assert len(await _rows(db_session, Permission, "action")) == 2


class TestLoadFixturesByContext:
    """load_fixtures_by_context() picks the variants for the requested contexts."""

    @staticmethod
    def _registry() -> FixtureRegistry:
        registry = FixtureRegistry()
        admin = _role("admin")
        _register(registry, "roles", [admin])
        tester = _user("tester", role_id=admin.id)
        testing = {"depends_on": ["roles"], "contexts": ["testing"]}
        _register(registry, "test_users", [tester], **testing)
        staging = [AppContext.STAGING]
        _register(registry, "staging_roles", [_role("staging")], contexts=staging)
        _register(registry, "demo_roles", [_role("demo")], contexts=["demo"])
        return registry

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("contexts", "roles", "users"),
        [
            ((Context.BASE,), {"admin"}, 0),
            ((Context.TESTING,), {"admin"}, 1),
            ((Context.BASE, Context.TESTING), {"admin"}, 1),
            (("staging",), {"admin", "staging"}, 0),
            ((AppContext.STAGING,), {"admin", "staging"}, 0),
            ((PlainEnumContext.STAGING,), {"admin", "staging"}, 0),
            ((AppContext.DEMO, Context.TESTING), {"admin", "demo"}, 1),
        ],
        ids=[
            "base",
            "testing",
            "base-and-testing",
            "string",
            "str-enum",
            "plain-enum",
            "several",
        ],
    )
    async def test_loads_base_and_the_requested_contexts(
        self, db_session, contexts, roles, users
    ):
        result = await load_fixtures_by_context(db_session, self._registry(), *contexts)

        assert set(await _rows(db_session, Role, "name")) == roles
        assert len(await _rows(db_session, User, "username")) == users
        assert sum(len(rows) for rows in result.values()) == len(roles) + users

    @pytest.mark.anyio
    async def test_variants_of_a_name_are_merged_for_the_loaded_contexts(
        self, db_session
    ):
        registry = FixtureRegistry()
        _register(registry, "roles", [_role("admin")], contexts=[Context.BASE])
        _register(registry, "roles", [_role("tester")], contexts=[Context.TESTING])
        _register(registry, "roles", [_role("demo")], contexts=["demo"])

        by_context = await load_fixtures_by_context(db_session, registry, "testing")
        by_name = await load_fixtures(db_session, registry, "roles")

        assert {cast(Role, r).name for r in by_context["roles"]} == {"admin", "tester"}
        names = {cast(Role, r).name for r in by_name["roles"]}
        assert names == {"admin", "tester", "demo"}
        assert len(await _rows(db_session, Role, "name")) == 3

    @pytest.mark.anyio
    async def test_dependency_without_a_variant_for_the_context_loads_all(
        self, db_session
    ):
        """A dependency registered only for other contexts still loads (#337)."""
        registry = FixtureRegistry()
        _register(registry, "env", [_role("staging-only")], contexts=["staging"])
        production = {"depends_on": ["env"], "contexts": ["production"]}
        _register(registry, "prod", [_role("prod")], **production)

        result = await load_fixtures_by_context(
            db_session, registry, Context.PRODUCTION
        )

        assert [cast(Role, r).name for r in result["env"]] == ["staging-only"]
        assert set(await _rows(db_session, Role, "name")) == {"staging-only", "prod"}


class TestJoinedTableInheritance:
    """Rows of a joined-table child are split between the root and child tables."""

    @pytest.mark.anyio
    @pytest.mark.parametrize("strategy", list(LoadStrategy))
    async def test_rows_are_written_to_both_tables(self, db_session, strategy):
        rows = [
            _challenge(uuid.uuid4(), "Alpha", "easy"),
            _challenge(uuid.uuid4(), "Beta", "hard"),
        ]

        loaded = await _load(db_session, "challenges", rows, strategy)

        stored = await _rows(db_session, ChallengeStandard, "title")
        assert [cast(Challenge, c).title for c in loaded] == ["Alpha", "Beta"]
        alpha = stored["Alpha"]
        assert (alpha.difficulty, alpha.challenge_type) == ("easy", "standard")
        assert (alpha.points, stored["Beta"].difficulty) == (0, "hard")

    @pytest.mark.anyio
    async def test_merge_updates_existing_rows_in_both_tables(self, db_session):
        """MERGE on a joined-table child upserts both tables (#345)."""
        cid = uuid.uuid4()
        await _load(db_session, "challenges", [_challenge(cid, "Original", "easy")])

        await _load(db_session, "challenges", [_challenge(cid, "Updated", "hard")])

        row = (await _rows(db_session, ChallengeStandard, "id"))[cid]
        assert (row.title, row.difficulty) == ("Updated", "hard")

    @pytest.mark.anyio
    async def test_skip_existing_leaves_existing_rows_alone(self, db_session):
        cid = uuid.uuid4()
        strategy = LoadStrategy.SKIP_EXISTING
        first = [_challenge(cid, "First", "easy")]
        await _load(db_session, "challenges", first, strategy)
        db_session.expunge_all()

        loaded = await _load(
            db_session, "challenges", [_challenge(cid, "Overwrite", "hard")], strategy
        )

        row = (await _rows(db_session, ChallengeStandard, "id"))[cid]
        assert loaded == [] and (row.title, row.difficulty) == ("First", "easy")


class TestHelpers:
    """Unit tests for the row-extraction helpers (no database)."""

    @pytest.mark.parametrize(
        ("instance", "expected"),
        [
            (Role(id=_ADMIN_ID, name="admin"), {"id": _ADMIN_ID, "name": "admin"}),
            (Role(id=None, name="admin"), {"name": "admin"}),
            (IntRole(id=None, name="admin"), {"name": "admin"}),
            (
                User(id=_USER_ID, username="u", email="e", role_id=None),
                {"role_id": None},
            ),
            (User(id=_USER_ID, username="u", email="e"), {}),
            (User(id=_USER_ID, username="u", email="e", notes=None), {"notes": None}),
            (User(id=_USER_ID, username="u", email="e", notes="hi"), {"notes": "hi"}),
        ],
        ids=[
            "explicit-values",
            "none-with-callable-default",
            "none-with-autoincrement",
            "none-on-nullable-fk",
            "omitted-nullable",
            "none-on-nullable",
            "value-on-nullable",
        ],
    )
    def test_instance_to_dict_skips_unset_and_generated_columns(
        self, instance, expected
    ):
        """None is kept only where the database would not generate a value."""
        extracted = _instance_to_dict(instance)

        if isinstance(instance, User):
            assert extracted.pop("id") == _USER_ID and "is_active" not in extracted
            assert extracted.pop("username") == "u" and extracted.pop("email") == "e"
        assert extracted == expected

    def test_table_chain_and_per_table_columns(self):
        inst = _challenge(uuid.uuid4(), "t", "easy")

        assert _get_table_chain(Role) == [Role]
        assert _get_table_chain(ChallengeStandard) == [Challenge, ChallengeStandard]
        root, child = Challenge, ChallengeStandard
        assert set(_instance_to_dict_for_cls(inst, root)) == {
            "id",
            "title",
            "challenge_type",
        }
        assert set(_instance_to_dict_for_cls(inst, child)) == {"id", "difficulty"}

    @pytest.mark.parametrize(
        ("instance", "expected"),
        [
            (Role(id=_ADMIN_ID, name="admin"), _ADMIN_ID),
            (IntRole(name="member"), None),
            (Permission(subject="post", action="read"), ("post", "read")),
            (Permission(subject="post"), None),
            (Permission(subject=None, action=None), None),
        ],
        ids=[
            "single",
            "single-unset",
            "composite",
            "composite-partial",
            "composite-unset",
        ],
    )
    def test_get_primary_key(self, instance, expected):
        assert _get_primary_key(instance) == expected
