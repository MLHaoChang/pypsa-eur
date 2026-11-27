"""
Settings entries and the settings coverage rule (spec expert-catalogue §3.1, §3.5, issue 01a-2; GK B2 (7)
E1, E3, E5).

- Every FIELD_LABELS key is an entry or a named exception.
- Every body field of the settings write routes (enumerated from the app's OpenAPI table by method and
  path prefix) is covered by a settings entry's `writes`, is not offered with a reason, or is a line of
  the committed `settings_known_gaps.txt`, which only shrinks.
- The option lists stay in step with the code that reads them.
- Entries describe and do not enforce: a write the route refuses answers exactly as before 01a-2.
"""

from __future__ import annotations

import pathlib
import re
import typing

import pytest

from services.asset_schema import catalogue as C
from services.asset_schema import settings as S

BACKEND = pathlib.Path(__file__).resolve().parents[1]
FIELD_LABELS_TS = BACKEND.parent / "frontend" / "src" / "utils" / "fieldLabels.ts"
GAPS_FILE = BACKEND / "services" / "asset_schema" / "settings_known_gaps.txt"

#: Fail-closed floors (E3): a parse that finds less than this is broken, never vacuously green.
FIELD_LABELS_FLOOR = 40
ROUTES_FLOOR = 10
FIELDS_FLOOR = 150
#: E5: the committed gap list may only shrink; lower this when you remove lines, never raise it.
KNOWN_GAPS_CEILING = 189


# ── FIELD_LABELS ────────────────────────────────────────────────────────────


def field_label_keys(text: str) -> list[str]:
    body = re.search(r"FIELD_LABELS\s*:[^=]*=\s*\{(.*?)\n\}", text, flags=re.S)
    if not body:
        return []
    return re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", body.group(1), flags=re.M)


def test_field_labels_parse_finds_its_floor():
    keys = field_label_keys(FIELD_LABELS_TS.read_text("utf-8"))
    assert len(keys) >= FIELD_LABELS_FLOOR, keys
    assert field_label_keys("export const OTHER = {}") == []


def test_every_field_label_key_is_an_entry_or_a_named_exception():
    keys = set(field_label_keys(FIELD_LABELS_TS.read_text("utf-8")))
    models = {e.model_name for e in C.entries()}
    missing = sorted(keys - models - set(S.FIELD_LABEL_KEYS_NOT_ENTRIES))
    assert not missing, f"FIELD_LABELS keys with no catalogue entry: {missing}"
    stale = sorted(
        set(S.FIELD_LABEL_KEYS_NOT_ENTRIES)
        & (models | (set(S.FIELD_LABEL_KEYS_NOT_ENTRIES) - keys))
    )
    assert not stale, f"stale FIELD_LABEL_KEYS_NOT_ENTRIES rows: {stale}"


# ── Settings routes ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def openapi():
    import main

    return main.app.openapi()


def _deref(schema, comps):
    while "$ref" in schema:
        schema = comps[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def body_fields(schema, comps, prefix="", depth=0) -> list[str]:
    """Dotted leaf paths of a request body: nested models are walked, a dict or scalar is a leaf."""
    schema = _deref(schema, comps)
    for alt in schema.get("anyOf", []) + schema.get("allOf", []):
        alt = _deref(alt, comps)
        if alt.get("properties"):
            return body_fields(alt, comps, prefix, depth)
    props = schema.get("properties")
    if not props or depth > 3:
        return [prefix] if prefix else []
    out: list[str] = []
    for key, sub in props.items():
        out += body_fields(sub, comps, f"{prefix}.{key}" if prefix else key, depth + 1)
    return out


def _archetype_params() -> list[str]:
    """The keys `POST …/profiles/loads/generate` accepts inside its free-form `params` dict."""
    from services.profile_gen import load_archetypes as LA

    keys = set(LA._COMMON)
    for _shape, defaults in LA.ARCHETYPES.values():
        keys |= set(defaults)
    return sorted(keys)


def settings_route_fields(spec) -> dict[str, list[str]]:
    """`"<METHOD> <path>"` → its body fields, for every write route under `SETTINGS_ROUTE_PREFIXES`."""
    comps = spec.get("components", {}).get("schemas", {})
    out: dict[str, list[str]] = {}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            m = method.upper()
            if not any(
                m == pm and path.startswith(pp) for pm, pp in S.SETTINGS_ROUTE_PREFIXES
            ):
                continue
            fields: list[str] = []
            for content in op.get("requestBody", {}).get("content", {}).values():
                fields += body_fields(content["schema"], comps)
            if f"{m} {path}" == S.LOAD_GENERATE and "params" in fields:
                fields.remove("params")
                fields += [f"params.{k}" for k in _archetype_params()]
            out[f"{m} {path}"] = fields or ["*"]
    return out


def covered() -> set[str]:
    return {w for s in S.SETTINGS for w in s.writes} | set(S.NOT_OFFERED_SETTINGS)


def gaps(route_fields: dict[str, list[str]]) -> list[str]:
    have = covered()
    return sorted(
        f"{route} {f}"
        for route, fields in route_fields.items()
        for f in fields
        if f"{route} {f}" not in have
    )


def _pinned_gaps() -> list[str]:
    return [
        ln.rstrip()
        for ln in GAPS_FILE.read_text("utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]


def check_gaps(now: list[str], pinned: list[str], ceiling: int) -> list[str]:
    """The reasons the committed gap list is out of step (empty when it is in step)."""
    problems = []
    if new := sorted(set(now) - set(pinned)):
        problems.append(
            f"new settings fields with no entry: {new}; add a settings.py entry (or a reason)"
        )
    if stale := sorted(set(pinned) - set(now)):
        problems.append(f"stale lines in settings_known_gaps.txt, remove them: {stale}")
    if len(pinned) > ceiling:
        problems.append(
            f"settings_known_gaps.txt has {len(pinned)} lines, over its ceiling {ceiling}"
        )
    if pinned != sorted(set(pinned)):
        problems.append("settings_known_gaps.txt must be sorted with no duplicates")
    return problems


def test_settings_routes_are_found_from_the_route_table(openapi):
    rf = settings_route_fields(openapi)
    assert len(rf) >= ROUTES_FLOOR, sorted(rf)
    assert sum(len(v) for v in rf.values()) >= FIELDS_FLOOR
    for method, prefix in S.SETTINGS_ROUTE_PREFIXES:
        assert any(r.startswith(f"{method} {prefix}") for r in rf), (method, prefix)


def test_every_entry_write_names_a_real_route_field(openapi):
    real = {
        f"{route} {f}"
        for route, fields in settings_route_fields(openapi).items()
        for f in fields
    }
    phantom = sorted(covered() - real)
    assert not phantom, f"settings writes that no route body has: {phantom}"


def test_known_gaps_are_exactly_the_uncovered_fields(openapi):
    problems = check_gaps(
        gaps(settings_route_fields(openapi)), _pinned_gaps(), KNOWN_GAPS_CEILING
    )
    assert not problems, "\n".join(problems)


def test_gap_check_fails_on_a_new_gap_a_stale_line_and_the_ceiling(openapi):
    """E5: red three ways."""
    rf = settings_route_fields(openapi)
    now, pinned = gaps(rf), _pinned_gaps()
    added = {**rf, S.SOLVER_CONFIG: rf[S.SOLVER_CONFIG] + ["a_new_field"]}
    assert any(
        "new settings fields" in p
        for p in check_gaps(gaps(added), pinned, KNOWN_GAPS_CEILING)
    )
    assert any(
        "stale lines" in p for p in check_gaps(now, pinned + ["PUT /api/x gone"], 10**6)
    )
    assert any(
        "over its ceiling" in p for p in check_gaps(now, pinned, len(pinned) - 1)
    )


# ── Options in step with their readers ──────────────────────────────────────


def test_eh_role_options_are_the_link_roles():
    from models.energy_hub import EH_LINK_ROLES

    assert tuple(k for k, _ in S.EH_ROLE_OPTIONS) == EH_LINK_ROLES


def test_control_modes_are_the_ones_the_island_study_reads():
    from services.adequacy.smoothing import CONTROL_WORDS

    assert {k for k, _ in S.CONTROL_MODES} == set(CONTROL_WORDS)


def test_island_scenarios_are_the_route_literal():
    from routers.campus_electrical import IslandStudyBody

    (inner,) = typing.get_args(IslandStudyBody.model_fields["scenarios"].annotation)[:1]
    assert tuple(k for k, _ in S.ISLAND_SCENARIOS) == typing.get_args(
        typing.get_args(inner)[0]
    )


def test_load_profile_entries_are_accepted_archetype_params():
    keys = {s.spec.key for s in S.SETTINGS if s.scope == "load_profile"}
    assert keys <= set(_archetype_params())


@pytest.mark.parametrize("st", S.SETTINGS, ids=lambda s: f"{s.scope}.{s.spec.key}")
def test_options_plain_is_parallel_to_options(st):
    assert len(st.spec.options_plain) == len(st.spec.options)
    if st.spec.kind == "enum":
        assert st.spec.options and all(st.spec.options_plain)


def test_settings_entries_are_never_essentials():
    """The Guided set is unchanged by 01a-2 (`test_essentials_is_todays_guided_set`)."""
    assert all(s.spec.depth != "essentials" for s in S.SETTINGS)


# ── E1: entries describe, routes enforce as before ──────────────────────────


def test_a_refused_load_growth_write_answers_as_before(client):
    r = client.put(
        "/api/simulation/solver_config",
        json={
            "multi_investment_periods": True,
            "investment_periods": [2030, 2035],
            "load_growth_pct_per_year": {"electrical": -150.0},
            "load_growth_base_year": 2030,
        },
    )
    assert r.status_code == 422
    assert r.json() == MASTER_REFUSAL


#: The body master d0d98072 answers for that write (recorded before 01a-2).
MASTER_REFUSAL = {
    "detail": {
        "code": "load_growth_invalid",
        "error_kind": "load_growth_invalid",
        "message": "Load growth (electrical): must be above -100 % per year (a fall of 100 % or more leaves no load)",
    }
}
