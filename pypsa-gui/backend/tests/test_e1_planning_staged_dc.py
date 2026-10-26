"""E1a: the staged data center built for any entered planning years.

Workspace reconciliation §3.3 and §13 (owner Q3 = B; the stage spacing is
owner decision E1-stages, 2026-10-21): the create body's ``planning``
(start and end year, optional investment dates) builds the example for
those years. The gatekeeper's conditions (2026-10-21):

1. weights only through ``auto_discount_periods``: Σ ipw.years equals the
   case's ``analysis_years``, and the default years are byte-identical;
2. the default build is unchanged (network canonically, sidecars by bytes);
3. growth values the example has no figure for are placeholders: assumed
   ledger rows (the flow warns), ``unknown`` in the peak check and the
   scorecard, a flag in the investment case, and the run is allowed;
4. the investment-case horizon is the planning horizon;
5. a path read by year holds its first point before it, ledgered;
6. a refused planning entry is a typed ``planning_invalid``.
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_templates as T  # noqa: E402

TID = "eh_datacenter_staged"
NATIVE = T.Planning((2027, 2030, 2035), 2045)
#: Shifted and uneven: periods of 3, 11 and 10 years.
UNEVEN = {"start_year": 2026, "end_year": 2050, "dates": [2029, 2040]}


def _plan(raw):
    return T.planning_spec(TID, raw)


# ── 1. the planning entry ───────────────────────────────────────────────────

def test_the_native_planning_is_the_examples_years():
    assert T.NATIVE_PLANNING[TID] == NATIVE
    assert NATIVE.start_year == 2027 and NATIVE.analysis_years == 18
    assert NATIVE.period_years == {2027: 3, 2030: 5, 2035: 10}


def test_start_and_end_only_keep_the_examples_spacing_shifted():
    """Owner decision E1-stages: +0 / +3 / +8 from the start year; a stage at
    or after the end year is dropped."""
    assert _plan({"start_year": 2028, "end_year": 2040}) == T.Planning((2028, 2031, 2036), 2040)
    assert _plan({"start_year": 2028, "end_year": 2034}) == T.Planning((2028, 2031), 2034)
    assert _plan({"start_year": 2028, "end_year": 2031}) == T.Planning((2028,), 2031)
    assert _plan({"start_year": 2027, "end_year": 2045}) == NATIVE


def test_dates_are_the_later_investment_periods():
    assert _plan(UNEVEN) == T.Planning((2026, 2029, 2040), 2050)
    assert _plan({"start_year": 2025, "end_year": 2060, "dates": []}) == T.Planning((2025,), 2060)


@pytest.mark.parametrize("raw, field", [
    ("2030", "planning"),
    ({"end_year": 2040}, "planning.start_year"),
    ({"start_year": 2030}, "planning.end_year"),
    ({"start_year": 2030, "end_year": 2040, "extra": 1}, "planning.extra"),
    ({"start_year": 2024, "end_year": 2040}, "planning.start_year"),
    ({"start_year": 2030, "end_year": 2061}, "planning.end_year"),
    ({"start_year": 2030.5, "end_year": 2040}, "planning.start_year"),
    ({"start_year": True, "end_year": 2040}, "planning.start_year"),
    ({"start_year": 2030, "end_year": 2030}, "planning.end_year"),
    ({"start_year": 2030, "end_year": 2028}, "planning.end_year"),
    ({"start_year": 2030, "end_year": 2050, "dates": [2035, 2033]}, "planning.dates"),
    ({"start_year": 2030, "end_year": 2050, "dates": [2030]}, "planning.dates"),
    ({"start_year": 2030, "end_year": 2050, "dates": [2050]}, "planning.dates"),
    ({"start_year": 2030, "end_year": 2050, "dates": [2032, 2034, 2036]}, "planning.dates"),
    ({"start_year": 2030, "end_year": 2050, "dates": "2035"}, "planning.dates"),
])
def test_a_refused_entry_names_its_field(raw, field):
    with pytest.raises(T.PlanningError) as exc:
        _plan(raw)
    assert exc.value.field == field
    assert str(exc.value).startswith(field)


def test_a_single_year_on_a_staged_example_is_refused_with_the_reason():
    with pytest.raises(T.PlanningError, match="single-year example"):
        _plan({"start_year": 2030, "end_year": 2030})


# ── 2. weights: Σ ipw.years == analysis_years (the gatekeeper's first test) ──

def test_the_period_years_sum_to_the_cases_analysis_years_at_uneven_years():
    p = _plan(UNEVEN)
    n = T.BUILDERS[TID](planning=p)
    ipw = n.investment_period_weightings
    assert list(n.investment_periods) == [2026, 2029, 2040]
    assert list(ipw["years"]) == [3.0, 11.0, 10.0]
    # Objective weights are the years; the solve's auto-discount derives the PV.
    assert list(ipw["objective"]) == list(ipw["years"])
    fin = T.solver_config(TID, p)["finance"]
    assert ipw["years"].sum() == fin["analysis_years"] == p.end_year - p.start_year
    assert T.solver_config(TID, p)["auto_discount_periods"] is True


def test_each_periods_week_stands_for_one_year_so_nyears_is_equal():
    """PyPSA refuses an overnight cost on unequal per-period ``nyears``; each
    period's week weighs 8,760 h whatever the period's length."""
    n = T.BUILDERS[TID](planning=_plan(UNEVEN))
    hours = n.snapshot_weightings["generators"].groupby(level=0).sum()
    assert np.allclose(hours.to_numpy(), 8760.0)


# ── 3. the default build is unchanged ──────────────────────────────────────

def _canonical(n) -> dict:
    out = {"snapshots": [str(s) for s in n.snapshots],
           "periods": list(map(int, n.investment_periods)),
           "ipw": n.investment_period_weightings.to_dict(),
           "sw": n.snapshot_weightings.to_dict()}
    for c in n.iterate_components():
        out[c.name] = c.static.sort_index(axis=0).sort_index(axis=1).to_json()
        for attr, df in c.dynamic.items():
            if not df.empty:
                out[f"{c.name}_t.{attr}"] = df.sort_index(axis=1).to_json()
    return out


def test_the_native_planning_builds_the_default_network():
    assert _canonical(T.BUILDERS[TID](planning=NATIVE)) == _canonical(T.BUILDERS[TID]())


@pytest.mark.parametrize("planning", [None, NATIVE])
def test_the_default_sidecars_are_the_shipped_bytes(tmp_path, planning):
    T.write_sidecars(tmp_path, TID, planning)
    for name in (*T.SIDECAR_FILES, T.ISLAND_FILE):
        assert (tmp_path / name).read_bytes() == (TPL_DIR / TID / name).read_bytes(), name


# ── 4. the investment-case horizon is the planning horizon ─────────────────

def test_the_case_operates_over_the_periods_covered_years():
    p = _plan(UNEVEN)
    fin = T.solver_config(TID, p)["finance"]
    assert fin["financial_close"] == "2025-01-01"
    cods = set(fin["cod_by_asset"].values())
    assert cods == {"2026-01-01"}
    cod = int(next(iter(cods))[:4])
    covered = [y for period, span in p.period_years.items() for y in range(period, period + span)]
    assert list(range(cod, cod + fin["analysis_years"])) == covered == list(range(2026, 2050))
    # Every candidate of the plan has a degradation entry (the engine never reads 0).
    n = T.BUILDERS[TID](planning=p)
    new = [a for a in [*n.generators.index, *n.storage_units.index] if "_new_" in a]
    assert sorted(new) == sorted(f"{k}_new_{y}" for y in p.years for k in ("genset", "bess"))
    assert set(new) <= set(fin["degradation_by_asset"])


def test_the_tariff_is_valid_from_the_start_year():
    p = _plan({"start_year": 2025, "end_year": 2040})
    tariff = T.solver_config(TID, p)["commercial"]["import_tariff"]
    assert tariff["valid_from"] == "2025-01-01"


def test_a_start_before_the_tariffs_year_holds_it_with_a_ledger_row():
    """Condition 5 on the tariff: its rates hold from the start year, and the
    ledger says so (assumed, so the flow lists it to confirm)."""
    p = _plan({"start_year": 2025, "end_year": 2040})
    assert T.solver_config(TID, p)["commercial"]["import_tariff"]["valid_from"] == "2025-01-01"
    row = next(r for r in T.template_meta(TID, p)["ledger"]
               if r["item"] == "import tariff before its year")
    assert row["source"] == "assumed" and row["note"].startswith("assumed, held from 2027")
    for later in ({"start_year": 2027, "end_year": 2040}, {"start_year": 2029, "end_year": 2040}):
        assert not any(r["item"] == "import tariff before its year"
                       for r in T.template_meta(TID, _plan(later))["ledger"])


def test_the_held_tariff_is_an_assumed_warning_in_the_flow(client, tmp_projects_dir):
    r = _create(client, "dc_e1_tariff", {"planning": {"start_year": 2025, "end_year": 2040}})
    assert r.status_code == 200, r.text
    body = client.get("/api/projects/dc_e1_tariff/flow").json()
    held = [(s["section"], i) for s in body["sections"] for i in s["items"]
            if (i.get("target") or {}).get("entity") == "import tariff before its year"]
    assert len(held) == 1
    section, item = held[0]
    assert section == "commercial" and item["severity"] == "warning"
    assert "(assumed)" in item["message"]


def test_the_ledger_says_the_pack_money_year_is_not_re_dated():
    meta = T.template_meta(TID, _plan(UNEVEN))
    row = next(r for r in meta["ledger"] if r["item"] == "money year")
    assert "2020" in row["value"] and "does not re-date" in row["note"]


# ── 5. growth values the example has no figure for ─────────────────────────

def test_the_native_years_carry_no_placeholder():
    """No ``growth_inputs`` block means every growth value is the example's
    own (the shipped sidecar stays byte-identical)."""
    from services.adequacy import site_peak as SP

    for meta in (T.template_meta(TID), T.template_meta(TID, NATIVE)):
        assert "growth_inputs" not in meta
        assert not any(r["item"].startswith("placeholder") for r in meta["ledger"])
        assert SP.unset_periods(meta) == {}


def test_shifted_years_list_every_growth_value_with_its_stage_value():
    meta = T.template_meta(TID, _plan({"start_year": 2028, "end_year": 2040}))
    assert meta["growth_inputs"] == [
        {"kind": "it_mw", "year": y, "value": v, "set": False, "example_value": v,
         "source": "unset"} for y, v in ((2028, 12.0), (2031, 24.0), (2036, 36.0))] + [
        {"kind": "energized_mw", "year": y, "value": v, "set": False, "example_value": v,
         "source": "unset"} for y, v in ((2028, 15.0), (2031, 55.0), (2036, 55.0))]


def test_a_shifted_year_is_a_placeholder_with_the_examples_value_by_stage():
    meta = T.template_meta(TID, _plan({"start_year": 2027, "end_year": 2045,
                                       "dates": [2030, 2038]}))
    rows = {(g["kind"], g["year"]): g for g in meta["growth_inputs"]}
    # The years the example has keep its value; 2038 is the third stage's placeholder.
    assert rows[("it_mw", 2030)]["set"] is True
    assert rows[("it_mw", 2038)] == {"kind": "it_mw", "year": 2038, "value": 36.0, "set": False,
                                    "example_value": 36.0, "source": "unset"}
    assert rows[("energized_mw", 2038)]["set"] is False
    placeholders = [r for r in meta["ledger"] if r["item"].startswith("placeholder")]
    assert {r["item"] for r in placeholders} == {"placeholder it_mw 2038",
                                                 "placeholder energized_mw 2038"}
    for r in placeholders:
        assert r["source"] == "assumed" and "not set" in r["note"]
    # The energization the flow lists carries the year's placeholder, assumed.
    energ = {e["year"]: e for e in meta["energization"]}
    assert energ[2038]["source"] == "assumed" and "placeholder" in energ[2038]["note"]


def test_an_example_year_in_another_stage_says_why_its_value_is_not_used():
    """dates [2035]: 2035 is the example's third stage but this planning's
    second, so it takes the second stage's 24 MW; the note says so, and says
    the check stays unknown (setting a placeholder is a follow-up)."""
    meta = T.template_meta(TID, _plan({"start_year": 2027, "end_year": 2045, "dates": [2035]}))
    row = next(r for r in meta["ledger"] if r["item"] == "placeholder it_mw 2035")
    assert row["value"] == 24.0
    assert "the example's 2035 value is not used: this planning puts 2035 in stage 2, so it " \
           "takes 24 MW" in row["note"]
    assert "IT load" in row["label"] and "it load" not in row["note"]
    assert "stays unknown" in row["note"] and "E1-set-growth-placeholders" in row["note"]
    other = T.template_meta(TID, _plan({"start_year": 2027, "end_year": 2045,
                                        "dates": [2030, 2038]}))
    row = next(r for r in other["ledger"] if r["item"] == "placeholder it_mw 2038")
    assert "the example has no IT load for 2038; it takes its stage 3 value, 36 MW" in row["note"]


def test_the_growth_flag_format_is_pinned():
    """``growth_placeholder:<kind>_<year>``, one per unset (kind, year), in
    year order: E1b-E1d follow it (gatekeeper review of #540)."""
    from routers import results as R

    meta = {"growth_inputs": [
        {"kind": "it_mw", "year": 2038, "set": False},
        {"kind": "energized_mw", "year": 2031, "set": False},
        {"kind": "it_mw", "year": 2030, "set": True}]}
    flags = R._growth_flags_of(meta)
    assert flags == ("growth_placeholder:energized_mw_2031", "growth_placeholder:it_mw_2038")
    import re

    assert all(re.fullmatch(r"growth_placeholder:[a-z0-9_]+_\d{4}", f) for f in flags)


def test_an_example_year_keeps_its_value_set_when_only_the_horizon_moves():
    """2027 / 2030 → 2033: both years are the example's, so their IT and
    energization values are its own (set), though 2030 now stands for 3 years."""
    meta = T.template_meta(TID, _plan({"start_year": 2027, "end_year": 2033}))
    assert all(g["set"] for g in meta["growth_inputs"])
    assert [g["year"] for g in meta["growth_inputs"]] == [2027, 2030, 2027, 2030]
    assert meta["period_years"] == {"2027": 3, "2030": 3}


def test_a_dc_load_above_a_placeholder_cap_is_unknown_not_refused():
    """Only a set energization can refuse a dc_load peak (DC-01); against a
    placeholder the period is unknown and the create goes ahead."""
    from services.adequacy import site_peak as SP

    p = _plan({"start_year": 2028, "end_year": 2040})
    phases = [{"cod": f"{y}-01-01", "it_mw": 60.0} for y in p.years]
    spec = T.dc_load_spec(TID, {"phases": phases}, planning=p)        # not refused
    n = T.BUILDERS[TID](load_spec=spec, planning=p)
    rows = SP.peak_check(n, T.island_config(TID, p),
                         unset=SP.unset_periods(T.template_meta(TID, p, load_spec=spec)))
    assert all(r["headroom_mw"] is None and r["unset"] == ["energized_mw"] for r in rows)
    with pytest.raises(Exception, match="dc_load.phases"):          # the same load, native years
        T.dc_load_spec(TID, {"phases": [{"cod": f"{y}-01-01", "it_mw": 60.0}
                                        for y in (2027, 2030, 2035)]})


def test_the_peak_check_reads_unknown_for_a_placeholder_period():
    from services.adequacy import site_peak as SP

    p = _plan({"start_year": 2027, "end_year": 2045, "dates": [2030, 2038]})
    n = T.BUILDERS[TID](planning=p)
    unset = SP.unset_periods(T.template_meta(TID, p))
    assert unset == {"2038": ["it_mw", "energized_mw"]}
    rows = {r["period"]: r for r in SP.peak_check(n, T.island_config(TID, p), unset=unset)}
    assert rows["2038"]["headroom_mw"] is None and rows["2038"]["unset"] == ["it_mw", "energized_mw"]
    assert rows["2030"]["headroom_mw"] is not None and rows["2030"]["unset"] == []


# ── 6. it builds, passes the checks and solves at uneven years ─────────────

def _solve(n, cfg) -> None:
    from services.adequacy.energization import apply_energization
    from services.solver.process_uc import planning_uc_kwargs

    test = n.copy()
    test.snapshots.name = "snapshot"
    apply_energization(test)
    status, condition = test.optimize(solver_name="highs", multi_investment_periods=True,
                                      **planning_uc_kwargs(test, None))
    assert (status, condition) == ("ok", "optimal")


@pytest.mark.parametrize("raw", [UNEVEN, {"start_year": 2025, "end_year": 2060, "dates": []}],
                         ids=["2026-2029-2040_to_2050", "2025_to_2060"])
def test_it_passes_the_template_rules_and_solves(raw):
    from services.process_template_rules import refusals

    p = _plan(raw)
    n = T.BUILDERS[TID](planning=p)
    cfg = T.solver_config(TID, p)
    assert refusals(n, cfg) == []
    _solve(n, cfg)


# ── 7. the AI-training load on the entered periods ─────────────────────────

def test_dc_load_takes_the_entered_periods():
    from services.dc_load_profile import DcLoadError

    p = _plan({"start_year": 2028, "end_year": 2040})
    phases = [{"cod": f"{y}-01-01", "it_mw": it} for y, it in zip(p.years, (12.0, 24.0, 36.0))]
    spec = T.dc_load_spec(TID, {"phases": phases}, planning=p)
    assert [ph.cod.year for ph in spec.phases] == [2028, 2031, 2036]
    with pytest.raises(DcLoadError, match=r"\[2028, 2031, 2036\]"):
        T.dc_load_spec(TID, {}, planning=p)          # the preset's phases are the native years


def test_with_dc_load_the_it_mw_is_set_and_only_energization_is_a_placeholder():
    p = _plan({"start_year": 2028, "end_year": 2040})
    phases = [{"cod": f"{y}-01-01", "it_mw": it} for y, it in zip(p.years, (10.0, 20.0, 30.0))]
    spec = T.dc_load_spec(TID, {"phases": phases}, planning=p)
    meta = T.template_meta(TID, p, load_spec=spec)
    it = [g for g in meta["growth_inputs"] if g["kind"] == "it_mw"]
    assert [(g["year"], g["value"], g["set"], g["source"]) for g in it] == [
        (2028, 10.0, True, "dc_load"), (2031, 20.0, True, "dc_load"), (2036, 30.0, True, "dc_load")]
    assert all(not g["set"] for g in meta["growth_inputs"] if g["kind"] == "energized_mw")


# ── 8. create from the template with ``planning`` ──────────────────────────

SHIFTED = {"start_year": 2027, "end_year": 2045, "dates": [2030, 2038]}


def _create(client, name, body):
    return client.post(f"/api/projects/from_template/{TID}", params={"name": name}, json=body)


def _project_dir(bound_ctx, client):
    from services import network_lengths

    with bound_ctx(client):
        return network_lengths.active_project_dir()


def test_create_builds_the_entered_years_and_writes_their_sidecars(
        client, tmp_projects_dir, bound_ctx):
    import pypsa

    r = _create(client, "dc_e1", {"planning": SHIFTED})
    assert r.status_code == 200, r.text
    d = _project_dir(bound_ctx, client)
    n = pypsa.Network(str(d / "network.nc"))
    assert list(n.investment_periods) == [2027, 2030, 2038]
    assert list(n.investment_period_weightings["years"]) == [3.0, 8.0, 7.0]
    p = _plan(SHIFTED)
    for name, want in ((T.META_FILE, T.template_meta(TID, p)),
                       (T.SOLVER_FILE, T.solver_config(TID, p)),
                       (T.ISLAND_FILE, T.island_config(TID, p))):
        assert json.loads((d / name).read_text()) == json.loads(json.dumps(want)), name
    cfg = client.get("/api/simulation/solver_config").json()
    assert cfg["finance"]["analysis_years"] == 18 and cfg["auto_discount_periods"] is True


def test_the_flow_warns_on_each_placeholder_and_the_scorecard_reads_unknown(
        client, tmp_projects_dir, bound_ctx):
    """The gatekeeper's condition-3 test: entered years, no dc_load → the
    flow carries the placeholder warnings, the scorecard's peak and ramp rows
    read unknown for them, and the solve runs."""
    import pypsa

    from services.adequacy import grid_readiness as G

    assert _create(client, "dc_e1_flow", {"planning": SHIFTED}).status_code == 200
    body = client.get("/api/projects/dc_e1_flow/flow").json()
    items = {i["code"]: (s["section"], i) for s in body["sections"] for i in s["items"]}
    for kind, section in (("it_mw", "loads"), ("energized_mw", "connection")):
        sec, item = items[f"assumed_placeholder {kind} 2038"]
        assert sec == section and item["severity"] == "warning"
        assert "placeholder" in item["message"]
    d = _project_dir(bound_ctx, client)
    n = pypsa.Network(str(d / "network.nc"))
    rows = {r["id"]: r for r in G.grid_readiness(d, n, "eu_rfg_dcc_ce")["rows"]}
    assert rows["energization_peak:2038"]["verdict"] == "unknown"
    assert "not set" in rows["energization_peak:2038"]["note"]
    assert rows["energization_peak:2030"]["verdict"] != "unknown"
    # The ramp row is one row for the site (not per period) and reads the
    # AI-training envelope, which a create without dc_load does not have.
    assert rows["ramp_rate"]["verdict"] == "unknown"
    # The run is allowed: the placeholder network solves.
    _solve(n, T.solver_config(TID, _plan(SHIFTED)))


def test_the_investment_case_lists_each_placeholder(client, tmp_projects_dir, bound_ctx):
    from routers import results as R
    from services.finance.baseline_text import flag_text

    assert _create(client, "dc_e1_ic", {"planning": SHIFTED}).status_code == 200
    with bound_ctx(client):
        flags = R._ic_growth_flags()
    assert flags == ("growth_placeholder:it_mw_2038", "growth_placeholder:energized_mw_2038")
    assert flag_text(flags[0]).startswith("The IT load in 2038 is a placeholder")
    assert flag_text(flags[1]).startswith("The energized import in 2038 is a placeholder")


def test_the_default_create_has_no_placeholder_flag(client, tmp_projects_dir, bound_ctx):
    from routers import results as R

    assert _create(client, "dc_native", None).status_code == 200
    with bound_ctx(client):
        assert R._ic_growth_flags() == ()


@pytest.mark.parametrize("body, field", [
    ({"planning": {"start_year": 2030, "end_year": 2030}}, "planning.end_year"),
    ({"planning": {"start_year": 2030}}, "planning.end_year"),
    ({"planning": {"start_year": 2030, "end_year": 2050, "dates": [2029]}}, "planning.dates"),
])
def test_a_refused_planning_is_a_typed_422_and_creates_nothing(
        client, tmp_projects_dir, body, field):
    r = _create(client, "dc_e1_bad", body)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == detail["error_kind"] == "planning_invalid" and detail["field"] == field
    assert client.get("/api/projects/dc_e1_bad/eh_template").status_code in (403, 404)


def test_dc_load_at_the_native_years_is_refused_against_entered_years(client, tmp_projects_dir):
    r = _create(client, "dc_e1_dc", {"planning": {"start_year": 2028, "end_year": 2040},
                                     "dc_load": {}})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == detail["error_kind"] == "dc_load_invalid" and "[2028, 2031, 2036]" in detail["message"]


def test_an_unknown_parameter_beside_planning_is_refused(client, tmp_projects_dir):
    # E1d gave the single-year data center native years, so ``planning`` is
    # one of its parameters now; any other key is still refused.
    r = client.post("/api/projects/from_template/eh_datacenter", params={"name": "dc_single"},
                    json={"planning": {"start_year": 2028, "end_year": 2040}, "growth": 1})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == detail["error_kind"] == "template_params_invalid" and detail["field"] == "growth"


@pytest.mark.live_solve
def test_the_ic_report_has_one_sentence_per_placeholder(reset_backend, monkeypatch, tmp_path):
    """Entered years, no dc_load: solved as run_simulation solves it, the
    case carries ``_ic_growth_flags``' flags (as ``_ic_build_case`` adds
    them), and the IC report lists each flag and one sentence for it."""
    import dataclasses
    import queue
    import threading

    import routers.results as R
    import routers.simulation as sim_router
    from models.finance import FinanceInputs
    from routers.projects import _solver_config_from_dict
    from services.finance.baseline_text import flag_text
    from services.finance.engine import run_case
    from services.finance.report import _project_payload
    from services.pypsa_service import PyPSAService
    from services.results import baseline as Bl
    from services.results.finance_case import baseline_plan, build_finance_case
    from services.solver import island as I
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    from services.adequacy.island_config import SIDECAR_NAME, load_island_config

    p = _plan(SHIFTED)
    T.write_sidecars(tmp_path, TID, p)
    isl = load_island_config(tmp_path / SIDECAR_NAME)
    isl["cuts"] = []
    monkeypatch.setattr(I, "load_project_island", lambda: isl)
    install_network_into_backend(T.BUILDERS[TID](planning=p))
    cfg = _solver_config_from_dict(T.solver_config(TID, p))
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    state: dict = {}
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: state.update(k))
    assert status in ("ok", "optimal"), (status, cond)
    Bl.clear_cache()
    plan = baseline_plan(n, cfg)
    base = Bl.solve_baseline(n, cfg, plan) if plan is not None and plan.needed else None
    case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance),
                              result_df=R._result_df, lost_load=state.get("last_lost_load"),
                              baseline=base)
    growth = R._growth_flags_of(T.template_meta(TID, p))
    assert growth == ("growth_placeholder:it_mw_2038", "growth_placeholder:energized_mw_2038")
    case = dataclasses.replace(case, flags=case.flags + growth)
    payload = _project_payload(run_case(case), case)
    assert set(growth) <= set(payload["flags"])
    notes = payload["counterfactual"]["notes"]
    for f in growth:
        assert notes.count(flag_text(f)) == 1, notes


def test_solve_ppa_price_carries_the_growth_placeholder_flags(install_network, monkeypatch):
    """A PPA price the chat tool solves on placeholder growth values says so
    in its own flags, as the investment case does (condition 3)."""
    import routers.results as R
    import routers.simulation as SIM
    from services import chat_tools
    from services.finance import engine as E
    from services.results import finance_case as FC
    from tests.fixtures.investment_case.sam import sam_case as S
    from tests.conftest import build_network
    from tests.test_investment_case_campus_route import _s2_inputs

    growth = ("growth_placeholder:it_mw_2038",)
    monkeypatch.setattr(R, "_ic_growth_flags", lambda: growth)
    monkeypatch.setattr(FC, "build_finance_case",
                        lambda n, cfg, fin, *, result_df, lost_load=None, owner=None,
                        extra_assets=(), baseline=None: S.to_finance_case("s1b", solve=True))
    monkeypatch.setattr(E, "solve_ppa", lambda case, pack=None, layers=None: {
        "solved_ppa_price": 55.0, "solve_ppa_status": "ok", "solved_ppa_price_money_year": 2026})
    install_network(build_network(solve=True))
    SIM.put_finance(SIM.FinanceIn(finance=_s2_inputs()), if_match=None)
    try:
        out = chat_tools.DISPATCHERS["solve_ppa_price"](target_irr=0.1, target_year=10)
    finally:
        SIM.put_finance(SIM.FinanceIn(finance=None), if_match=None)
    assert out["status"] == "ok"
    assert out["flags"] == list(growth)
