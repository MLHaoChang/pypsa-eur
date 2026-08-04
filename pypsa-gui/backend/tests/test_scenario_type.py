"""
`Project.scenario_type` as a real column, and the retirement of the `[type]`
prefix that carried it inside `scenario_description`.

Three things need pinning and none of them is the column itself:

  * the MIGRATION's backfill, because adding the column without lifting the
    existing tags leaves every scenario uncategorised AND still showing the
    marker as prose — strictly worse than before;
  * the REFUSAL of a tagged description on the way in, because storing both
    channels lets them disagree about one project and no rendering is right;
  * the LEGACY READ path, because a bundle exported before this change still
    carries the tag inline and must import with its category intact.
"""
import importlib.util
import pathlib

import pytest
import sqlalchemy as sa

from routers.projects import (
    _reject_legacy_tag,
    _scenario_fields_from_meta,
    _split_legacy_tag,
    _validated_scenario_type,
)


# ── the decoder ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "description, expected",
    [
        ("[stress] cold winter, no imports", ("stress", "cold winter, no imports")),
        ("[baseline] reference run", ("baseline", "reference run")),
        # The dialog wrote the tag even when the user typed nothing, so this is
        # the single most common stored value.
        ("[scenario]", ("scenario", None)),
        ("[scenario]   ", ("scenario", None)),
        # A bracketed word that is not one of ours is PROSE. Eating the user's
        # first word here would be unrecoverable.
        ("[draft] cut the gas fleet", (None, "[draft] cut the gas fleet")),
        ("[2030] high demand", (None, "[2030] high demand")),
        # Only at the start.
        ("see [stress] for the comparison", (None, "see [stress] for the comparison")),
        ("plain prose", (None, "plain prose")),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_split_legacy_tag(description, expected):
    assert _split_legacy_tag(description) == expected


def test_multiline_description_survives_the_split():
    assert _split_legacy_tag("[stress] line one\nline two") == ("stress", "line one\nline two")


def test_meta_prefers_the_explicit_column_over_an_inline_tag():
    # A bundle written after the split has both keys. The explicit one wins —
    # otherwise a description a user later edited to start with "[baseline]"
    # would silently re-categorise their project.
    assert _scenario_fields_from_meta(
        {"scenario_type": "stress", "scenario_description": "[baseline] confusing"}
    ) == {"scenario_type": "stress", "scenario_description": "[baseline] confusing"}


def test_meta_falls_back_to_the_inline_tag_for_an_old_bundle():
    assert _scenario_fields_from_meta(
        {"scenario_description": "[baseline] reference run"}
    ) == {"scenario_type": "baseline", "scenario_description": "reference run"}


# ── validation at the edge ───────────────────────────────────────────────────

def test_scenario_type_is_normalised():
    assert _validated_scenario_type("  STRESS ") == "stress"
    assert _validated_scenario_type(None) is None
    assert _validated_scenario_type("   ") is None


def test_unknown_scenario_type_is_refused_with_the_valid_set():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        _validated_scenario_type("sensitivity")
    assert excinfo.value.status_code == 400
    # The message must name what IS accepted — a bare rejection makes the
    # caller guess.
    assert "baseline" in excinfo.value.detail


def test_a_still_tagged_description_is_refused_and_says_what_to_send():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        _reject_legacy_tag("[stress] cold winter")
    assert excinfo.value.status_code == 400
    assert "scenario_type='stress'" in excinfo.value.detail
    assert "cold winter" in excinfo.value.detail


def test_prose_that_merely_starts_with_a_bracket_is_accepted():
    assert _reject_legacy_tag("[draft] cut the gas fleet") == "[draft] cut the gas fleet"


# ── the migration ────────────────────────────────────────────────────────────
# Driven directly against a SQLite table rather than through alembic's runner:
# the backfill is the part that can silently do nothing, and this exercises the
# real `upgrade`/`downgrade` bodies against real rows.

@pytest.fixture
def migration_module():
    # By path: `alembic.versions` is not an importable package (no
    # `__init__.py`, and `alembic` resolves to the installed library), and the
    # revision's module name starts with a digit.
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "0004_scenario_type.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0004", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(conn):
    conn.exec_driver_sql(
        "CREATE TABLE projects (id TEXT PRIMARY KEY, scenario_description TEXT)"
    )
    rows = [
        ("a", "[stress] cold winter"),
        ("b", "[scenario]"),
        ("c", "[draft] cut the gas fleet"),   # prose — must be left alone
        ("d", "plain notes"),
        ("e", None),
    ]
    for row_id, description in rows:
        conn.exec_driver_sql(
            "INSERT INTO projects (id, scenario_description) VALUES (?, ?)",
            (row_id, description),
        )


def _dump(conn) -> dict[str, tuple]:
    return {
        r[0]: (r[1], r[2])
        for r in conn.exec_driver_sql(
            "SELECT id, scenario_type, scenario_description FROM projects"
        ).all()
    }


def _run(module, direction: str, conn):
    """Invoke the migration body against `conn` via a stubbed alembic `op`."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(module, direction)()


def test_upgrade_lifts_the_tag_out_of_the_description(migration_module):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(migration_module, "upgrade", conn)
        got = _dump(conn)

    assert got["a"] == ("stress", "cold winter")
    # Tag-only descriptions become NULL, not the empty string: an empty
    # description and a missing one are the same thing to every reader.
    assert got["b"] == ("scenario", None)
    # Untouched — description AND category.
    assert got["c"] == (None, "[draft] cut the gas fleet")
    assert got["d"] == (None, "plain notes")
    assert got["e"] == (None, None)


def test_upgrade_is_idempotent(migration_module):
    # Re-running matches nothing, because the descriptions no longer start
    # with a recognised tag.
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(migration_module, "upgrade", conn)
        first = _dump(conn)
        # A second upgrade would re-add the column; only the backfill half is
        # re-runnable, which is the half that could corrupt data.
        conn.exec_driver_sql(
            "UPDATE projects SET scenario_description = scenario_description"
        )
        _run(migration_module, "downgrade", conn)
        _run(migration_module, "upgrade", conn)
        second = _dump(conn)

    assert first == second


def test_downgrade_restores_the_prefix_before_dropping_the_column(migration_module):
    # Order is the point: dropping first would discard every category with the
    # rollback still reporting success.
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        _run(migration_module, "upgrade", conn)
        _run(migration_module, "downgrade", conn)
        rows = dict(
            conn.exec_driver_sql(
                "SELECT id, scenario_description FROM projects"
            ).all()
        )
        columns = {
            c[1] for c in conn.exec_driver_sql("PRAGMA table_info(projects)").all()
        }

    assert "scenario_type" not in columns
    assert rows["a"] == "[stress] cold winter"
    assert rows["b"] == "[scenario]"
    assert rows["c"] == "[draft] cut the gas fleet"
    assert rows["d"] == "plain notes"
    assert rows["e"] is None


def test_downgrade_refuses_to_overflow_the_declared_column_width(migration_module):
    # `scenario_description` is String(500) and 500 is also the API's own cap,
    # so re-prefixing a description written at the limit pushes it to 511.
    # SQLite would take it silently; Postgres aborts the whole downgrade with
    # "value too long", stranding the database between two revisions during a
    # rollback. The category is dropped for those rows instead — it is
    # recoverable by re-labelling, and the description is the user's text.
    engine = sa.create_engine("sqlite://")
    at_limit = "x" * 500
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE projects (id TEXT PRIMARY KEY, scenario_description TEXT,"
            " scenario_type TEXT)"
        )
        conn.exec_driver_sql("INSERT INTO projects VALUES ('long', ?, 'stress')", (at_limit,))
        conn.exec_driver_sql("INSERT INTO projects VALUES ('short', 'ok', 'stress')")
        _run(migration_module, "downgrade", conn)
        rows = dict(
            conn.exec_driver_sql("SELECT id, scenario_description FROM projects").all()
        )

    assert len(rows["long"]) <= 500
    assert rows["long"] == at_limit          # the prose is untouched
    assert rows["short"] == "[stress] ok"    # the ordinary row still round-trips


def test_a_category_the_old_format_cannot_hold_is_dropped_not_mangled(migration_module):
    # A value added after this migration has nowhere to go in the prefix
    # encoding. Losing it on rollback is honest; smuggling it into the
    # description as "[sensitivity] …" would produce a string the upgrade path
    # then reads back as prose.
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE projects (id TEXT PRIMARY KEY, scenario_description TEXT,"
            " scenario_type TEXT)"
        )
        conn.exec_driver_sql(
            "INSERT INTO projects VALUES ('x', 'notes', 'sensitivity')"
        )
        _run(migration_module, "downgrade", conn)
        description = conn.exec_driver_sql(
            "SELECT scenario_description FROM projects WHERE id = 'x'"
        ).scalar()

    assert description == "notes"
