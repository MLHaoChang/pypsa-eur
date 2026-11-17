"""
Parameter catalogue completeness (spec expert-catalogue §10.1, issue 01a-1).

Every entry carries what every view needs; ids are unique, derived the same
way every time and pinned by `catalogue.ids.txt` (GK B: a derived id that
moves fails here, and the fix is an explicit `id=` equal to the old one).
"""

import re

import pytest

from services.asset_schema import catalogue as C
from services.asset_schema import entries as E
from services.asset_schema.asset_types import ASSET_TYPES, CATEGORIES
from services.asset_schema.fields import FieldSpec, fields_for, known_components
from services.asset_schema.functions import FUNCTION_IDS, USE_LEVELS

ENTRIES = C.entries()


def _pinned_ids() -> list[str]:
    return [ln.strip() for ln in C.IDS_FILE.read_text("utf-8").splitlines() if ln.strip() and not ln.startswith("#")]


def _model_names() -> set[str]:
    """Model names a planner would read as code: snake_case or letter+digit columns (p_nom, bus0)."""
    return {e.model_name for e in ENTRIES if re.search(r"[_0-9]", e.model_name)}


def _mentions(text: str, names: set[str]) -> list[str]:
    return sorted(n for n in names if re.search(r"(?<![\w])" + re.escape(n) + r"(?![\w])", text))


def test_one_entry_per_field_row():
    assert len(ENTRIES) == sum(len(fields_for(c)) for c in known_components())


@pytest.mark.parametrize("e", ENTRIES, ids=lambda e: e.id)
def test_entry_is_complete(e):
    assert e.plain_name and e.plain_name.strip() == e.plain_name
    assert e.group in E.GROUP_IDS
    assert e.depth in E.DEPTHS
    assert e.spec.kind
    assert 0 < len(e.short_help) <= 140, e.short_help
    assert isinstance(e.functions, dict)
    assert set(e.functions) <= set(FUNCTION_IDS)
    assert set(e.functions.values()) <= set(USE_LEVELS)
    assert e.default["source"] in ("library", "engine", "template", "none")


def test_essentials_is_todays_guided_set():
    """`depth: essentials` replaces `key_param`: the Guided face lists exactly the fields it listed before
    the move, read from the recording taken on master (not from the table under test)."""
    import json

    from tests.test_catalogue_legacy_schema_pin import FIXTURE

    recorded = json.loads(FIXTURE.read_text("utf-8"))
    before = {(c, f["key"]) for c, p in recorded.items() for g in p["groups"] for f in g["fields"] if f["key_param"]}
    now = {(e.scope, e.model_name) for e in ENTRIES if e.depth == "essentials"}
    assert now == before


def test_plain_names_carry_no_unit_and_no_model_name():
    raw = _model_names()
    units = {e.spec.unit_display for e in ENTRIES if e.spec.unit_display}
    for e in ENTRIES:
        assert not _mentions(e.plain_name, raw), e.id
        assert not _mentions(e.short_help, raw), e.id
        for u in units:
            assert f"({u}" not in e.plain_name and f" in {u}" not in e.plain_name, e.id


def test_plain_names_unique_within_scope_and_group():
    seen: dict[tuple[str, str, str], str] = {}
    for e in ENTRIES:
        k = (e.scope, e.group, e.plain_name.lower())
        assert k not in seen, f"{e.id} and {seen[k]} share a plain name in one group"
        seen[k] = e.id


def test_ids_unique_case_insensitively():
    ids = [e.id.lower() for e in ENTRIES]
    assert len(ids) == len(set(ids))


def test_ids_are_scope_dot_slug():
    for e in ENTRIES:
        assert re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", e.id), e.id
        assert e.id.split(".")[0] == E.scope_slug(e.scope)


def test_ids_pinned_by_ids_txt():
    """Append-only: an id may leave the file only when some entry lists it in `former_ids`."""
    pinned = set(_pinned_ids())
    now = {e.id for e in ENTRIES}
    former = {f for e in ENTRIES for f in e.spec.former_ids}
    missing = sorted(pinned - now - former)
    assert not missing, (
        f"entry ids moved: {missing}. A plain-name change re-derives the id; set `id=` on the FieldSpec row "
        "to the old id (or list it in `former_ids` if the entry really is new).")
    new = sorted(now - pinned)
    assert not new, f"new entry ids {new}: append them to services/asset_schema/catalogue.ids.txt"


def test_ids_txt_is_sorted_and_has_no_duplicates():
    ids = _pinned_ids()
    assert ids == sorted(set(ids))


def test_slug_collision_is_refused_never_suffixed(monkeypatch):
    """#1127: two rows deriving the same id stop the build; nothing is renamed to make room."""
    twin = (FieldSpec("p_nom", "size", "Capacity", "x"), FieldSpec("p_set", "size", "Capacity", "y"))
    monkeypatch.setattr(C, "known_components", lambda: ["Generator"])
    monkeypatch.setattr(C, "fields_for", lambda c: twin)
    C.clear_caches()
    try:
        with pytest.raises(C.CatalogueError, match="generator.capacity"):
            C.entries()
    finally:
        C.clear_caches()


def test_case_only_collision_is_refused(monkeypatch):
    twin = (FieldSpec("a_x", "size", "Thing", "x", id="generator.thing"),
            FieldSpec("b_x", "size", "Other", "y", id="generator.THING"))
    monkeypatch.setattr(C, "known_components", lambda: ["Generator"])
    monkeypatch.setattr(C, "fields_for", lambda c: twin)
    C.clear_caches()
    try:
        with pytest.raises(C.CatalogueError):
            C.entries()
    finally:
        C.clear_caches()


def test_explicit_id_wins_over_the_derived_one():
    spec = FieldSpec("p_nom", "size", "Renamed capacity", "x", id="generator.capacity")
    assert E.entry_id_of("Generator", spec) == "generator.capacity"


def test_group_order_is_fixed():
    assert E.GROUP_IDS == ("general", "size_limits", "electrical", "operation", "running_cost", "investment",
                           "reliability", "replacement", "source")


def test_every_legacy_group_maps_to_a_plain_group():
    from services.asset_schema.fields import GROUPS
    assert set(E.LEGACY_GROUP) == set(GROUPS)


@pytest.mark.parametrize("model,scope,group", [
    ("x", "Transformer", "electrical"), ("x", "Bus", "general"), ("p_nom_extendable", "Generator", "investment"),
    ("mttr_hours", "Generator", "reliability"), ("build_year", "Generator", "replacement"),
    ("marginal_cost", "Generator", "running_cost"), ("p_nom", "Generator", "size_limits"),
])
def test_group_rules(model, scope, group):
    e = next(e for e in ENTRIES if e.scope == scope and e.model_name == model)
    assert e.group == group


def test_spec_rename_table_rows():
    """The §3.6 starting table, as UX reviews it (the full diff table is in the PR body)."""
    by = {(e.scope, e.model_name): e.plain_name for e in ENTRIES}
    assert by[("Generator", "p_nom")] == "Capacity"
    assert by[("Generator", "p_nom_extendable")] == "Let the optimiser size it"
    assert by[("Generator", "p_nom_min")] == "Smallest size"
    assert by[("Generator", "p_nom_max")] == "Largest size"
    assert by[("Generator", "p_max_pu")] == "Maximum output share"
    assert by[("Generator", "p_min_pu")] == "Minimum output share"
    assert by[("Generator", "marginal_cost")] == "Running cost"
    assert by[("Line", "s_nom")] == "Rating"
    assert by[("Transformer", "x")] == "Reactance"
    assert by[("Generator", "bus")] == "Connected to"


def test_functions_needs_where_a_missing_value_blocks_the_run():
    by = {(e.scope, e.model_name): e.functions for e in ENTRIES}
    assert by[("Generator", "bus")]["dispatch"] == "needs"
    assert by[("Generator", "p_nom_extendable")]["expansion"] == "needs"
    assert by[("Transformer", "x")]["load_flow"] == "needs"
    assert "load_flow" not in by[("Bus", "x")]            # the longitude


def test_defaults_shown_with_a_source_and_inf_kept_as_text():
    by = {(e.scope, e.model_name): e.default for e in ENTRIES}
    assert by[("Generator", "p_nom_max")] == {"value": "inf", "source": "engine"}
    assert by[("Generator", "bus")] == {"value": None, "source": "none"}
    assert by[("Generator", "efficiency")] == {"value": 1.0, "source": "engine"}


def test_asset_types_complete():
    ids = [t.id for t in ASSET_TYPES]
    assert len(ids) == len(set(ids))
    for t in ASSET_TYPES:
        assert t.category in CATEGORIES
        assert t.plain_name and t.plural and t.icon and t.match
        assert t.colour.startswith("--color-asset-") and "#" not in t.colour
        for m in t.match:
            assert m.component in known_components()
            for pattern in (m.carrier, m.generic_unless, m.far_carrier and m.far_carrier[1]):
                if pattern:
                    re.compile(pattern, re.I)


def test_model_names_map_covers_every_entry():
    names = C.model_names()
    assert sum(len(v) for v in names.values()) == len(ENTRIES)
    assert names["Generator"]["p_nom"] == "Capacity"
