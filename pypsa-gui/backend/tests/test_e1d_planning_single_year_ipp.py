"""E1d: the single-year examples and the IPP built for any entered planning years.

``eh_datacenter``, ``eh_h2_hub``, ``eh_microgrid`` and ``eh_ipp_hybrid`` take a
create body's ``planning`` {start_year, end_year}: one period, the start year,
which dates the modelled week (or the IPP's hourly year); the end year is the
horizon. The gatekeeper's E1d plan conditions (GK B) and owner E1D-IPP-1:

1. the microgrid and the IPP refuse start == end (their investment case runs
   to the end year); the data center and the H2 hub take it;
2. the IPP's money year is the start year: the example's 2030 illustrative
   money and the observed 2024 prices are taken as start-year money with no
   conversion; the finance ``currency_year`` and every series' ``currency_year``
   note move together; the case stays nominal at the same escalation;
3. the re-dating is ledgered ("re-dated from 2030", "from 2024"), weekdays and
   holidays falling as in the source year; the tariff's ``valid_from`` and the
   PPA base year move too;
4. the IPP closes two years before the start, the microgrid one; the COD is the
   start year; the years the case covers equal its ``analysis_years``;
5. a create at entered years mints the IPP's series for those years and pins
   them, and the pins resolve; the default create is unchanged (golden bytes);
6. an invalid planning entry is a typed 422 on each template.
"""
from __future__ import annotations

import json
import pathlib
import queue
import sys
import threading

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_templates as T  # noqa: E402

DC, H2, MG, IPP = "eh_datacenter", "eh_h2_hub", "eh_microgrid", "eh_ipp_hybrid"
IDS = (DC, H2, MG, IPP)
#: Templates with an investment case to the end year.
CASED = (MG, IPP)
SHIFTED = {"start_year": 2033, "end_year": 2051}
#: A leap start year: the IPP's hourly year drops 29 Feb.
LEAP = {"start_year": 2032, "end_year": 2050}


def _plan(tid, raw):
    return T.planning_spec(tid, raw)


def _ipp():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_eh_ipp_e1d", TPL_DIR / "eh_ipp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _ipp()


def _canonical(n) -> dict:
    out = {"snapshots": [str(s) for s in n.snapshots],
           "periods": list(map(int, n.investment_periods)),
           "sw": n.snapshot_weightings.to_dict()}
    for c in n.iterate_components():
        out[c.name] = c.static.sort_index(axis=0).sort_index(axis=1).to_json()
        for attr, df in c.dynamic.items():
            if not df.empty:
                out[f"{c.name}_t.{attr}"] = df.sort_index(axis=1).to_json()
    return out


# ── 1 and 6. the planning entry ─────────────────────────────────────────────

@pytest.mark.parametrize("tid", IDS)
def test_each_takes_one_period_at_the_entered_start_year(tid):
    assert T.SINGLE_YEAR[tid] is (tid in CASED)
    assert _plan(tid, SHIFTED) == T.Planning((2033,), 2051)
    native = T.NATIVE_PLANNING[tid]
    assert native.years == (2030,)
    assert _plan(tid, {"start_year": 2030, "end_year": native.end_year}) == native


@pytest.mark.parametrize("tid", CASED)
def test_a_cased_example_refuses_start_equal_to_end(tid):
    with pytest.raises(T.PlanningError, match="must be after the start year") as exc:
        _plan(tid, {"start_year": 2033, "end_year": 2033})
    assert exc.value.field == "planning.end_year"


@pytest.mark.parametrize("tid", (DC, H2))
def test_an_uncased_example_takes_start_equal_to_end(tid):
    assert _plan(tid, {"start_year": 2033, "end_year": 2033}) == T.Planning((2033,), 2033)


@pytest.mark.parametrize("raw, field", [
    ({"start_year": 2033, "end_year": 2051, "dates": [2040]}, "planning.dates"),
    ({"start_year": 2033, "end_year": 2031}, "planning.end_year"),
    ({"start_year": 2033}, "planning.end_year"),
    ({"start_year": "2033", "end_year": 2051}, "planning.start_year"),
])
@pytest.mark.parametrize("tid", IDS)
def test_an_invalid_entry_is_refused_naming_its_field(tid, raw, field):
    with pytest.raises(T.PlanningError) as exc:
        _plan(tid, raw)
    assert exc.value.field == field


# ── 5. the default build is unchanged ───────────────────────────────────────

@pytest.mark.parametrize("tid", IDS)
def test_the_native_planning_builds_the_default_network(tid):
    assert _canonical(T.BUILDERS[tid](planning=T.NATIVE_PLANNING[tid])) \
        == _canonical(T.BUILDERS[tid]())


@pytest.mark.parametrize("native", [False, True], ids=["none", "native"])
@pytest.mark.parametrize("tid", IDS)
def test_the_default_sidecars_are_the_shipped_bytes(tmp_path, tid, native):
    T.write_sidecars(tmp_path, tid, T.NATIVE_PLANNING[tid] if native else None)
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written
    for p in written:
        rel = p.relative_to(tmp_path)
        assert p.read_bytes() == (TPL_DIR / tid / rel).read_bytes(), rel


# ── the dated week and year ─────────────────────────────────────────────────

@pytest.mark.parametrize("tid", (DC, H2, MG))
def test_the_week_is_the_examples_week_in_the_start_year(tid):
    n0 = T.BUILDERS[tid]()
    n = T.BUILDERS[tid](planning=_plan(tid, SHIFTED))
    assert len(n.snapshots) == len(n0.snapshots) == T.HOURS
    assert str(n.snapshots[0]) == "2033-07-01 00:00:00"
    assert list(n.investment_periods) == []


@pytest.mark.parametrize("raw", [SHIFTED, LEAP], ids=["2033", "leap-2032"])
def test_the_ipp_year_is_the_start_year_without_29_february(raw):
    n = T.BUILDERS[IPP](planning=_plan(IPP, raw))
    idx = n.snapshots
    assert len(idx) == 8760 and set(idx.year) == {raw["start_year"]}
    assert not ((idx.month == 2) & (idx.day == 29)).any()
    # The example's values, re-dated by hour of year.
    n0 = T.BUILDERS[IPP]()
    for attr in ("p_max_pu",):
        assert (n.generators_t[attr].to_numpy() == n0.generators_t[attr].to_numpy()).all()
    assert (M.price_shape("base", raw["start_year"]).to_numpy()
            == M.price_shape("base").to_numpy()).all()
    for name in M.OBSERVED:
        assert (M.observed_series(name, raw["start_year"]).to_numpy()
                == M.observed_series(name).to_numpy()).all()


@pytest.mark.parametrize("raw", [SHIFTED, LEAP], ids=["2033", "leap-2032"])
@pytest.mark.parametrize("tid", IDS)
def test_it_passes_the_template_rules_and_the_profile_qa(tid, raw):
    from services import timeseries_qa as Q
    from services.process_template_rules import refusals

    p = _plan(tid, raw)
    n = T.BUILDERS[tid](planning=p)
    assert refusals(n, T.solver_config(tid, p)) == []
    assert Q.check_timeseries_quality(n) == Q.check_timeseries_quality(T.BUILDERS[tid]())


# ── 4. close, COD and the case's years ──────────────────────────────────────

@pytest.mark.parametrize("raw", [None, SHIFTED, LEAP], ids=["golden", "2033", "leap-2032"])
@pytest.mark.parametrize("tid, lead", [(MG, 1), (IPP, 2)])
def test_close_cod_and_the_cases_years(tid, lead, raw):
    p = T.NATIVE_PLANNING[tid] if raw is None else _plan(tid, raw)
    fin = T.solver_config(tid, None if raw is None else p)["finance"]
    start = p.start_year
    assert fin["financial_close"] == f"{start - lead}-01-01"
    assert set(fin["cod_by_asset"].values()) == {f"{start}-01-01"}
    assert sum(p.period_years.values()) == fin["analysis_years"] == p.end_year - start


# ── 2 and 3. the IPP's money year and its re-dating ─────────────────────────

@pytest.mark.parametrize("raw", [SHIFTED, LEAP], ids=["2033", "leap-2032"])
def test_the_ipp_money_year_moves_with_every_series_note(raw):
    year = raw["start_year"]
    p = _plan(IPP, raw)
    cfg = T.solver_config(IPP, p)
    fin, base = cfg["finance"], T.solver_config(IPP)["finance"]
    assert fin["currency_year"] == year and fin["price_basis"] == "nominal"
    assert fin["escalation"] == base["escalation"]
    assert fin["solve_ppa"]["target_year"] == fin["analysis_years"]
    for s in M.series_meta(year)["series"]:
        assert f"currency_year:{year}" in s["notes"], s["name"]
        assert not any(n.startswith("currency_year:") and n != f"currency_year:{year}"
                       for n in s["notes"]), s["name"]
    assert cfg["commercial"]["import_tariff"]["valid_from"] == f"{year}-01-01"
    assert {c["base_year"] for c in cfg["commercial"]["contracts"]} == {year}


def test_the_planned_series_fit_the_planned_case_and_not_the_examples(tmp_path):
    """The basis check the create runs (`_mint_template_series`) bites: the entered
    year's series pass against its case and clash with the example's."""
    from services.library import template_series as TS

    p = _plan(IPP, SHIFTED)
    M.write_price_series(tmp_path, p)
    series = TS.read(tmp_path)
    assert series
    assert TS.basis_conflicts(series, T.solver_config(IPP, p)["finance"]) == []
    assert TS.basis_conflicts(series, T.solver_config(IPP)["finance"])


def test_the_ipp_ledger_states_the_money_year_and_the_re_dating():
    rows = {r["item"]: r for r in T.template_meta(IPP, _plan(IPP, SHIFTED))["ledger"]}
    period = rows["planning period"]
    assert period["source"] == "entered"
    assert "2033" in period["value"] and "to 2051" in period["value"]
    assert "financial close 2031, 18 operating years" in period["note"]
    money = rows["money year"]
    assert money["value"] == "2033 EUR, no conversion" and money["source"] == "assumed"
    assert "2030 money" in money["note"] and "observed 2024" in money["note"]
    illustrative = rows["illustrative prices and profiles re-dated from 2030"]
    assert "weekday and holiday alignment is 2030's" in illustrative["note"]
    observed = rows["observed prices re-dated from 2024 to 2033"]
    assert "alignment is 2024's" in observed["note"]
    assert rows["tariff and PPA dates"]["value"] == "valid from 2033-01-01, PPA base year 2033"
    assert "18 years" in rows["finance"]["value"]


@pytest.mark.parametrize("tid", (DC, H2, MG))
def test_the_single_year_ledger_states_the_entered_period(tid):
    rows = {r["item"]: r for r in T.template_meta(tid, _plan(tid, SHIFTED))["ledger"]}
    assert rows["planning period"]["value"] == "2033 → 2051 (entered)"
    assert rows["planning period"]["source"] == "entered"


# ── create from the template with ``planning`` ──────────────────────────────

@pytest.mark.parametrize("tid", IDS)
def test_create_builds_the_entered_year_and_writes_its_sidecars(
        tid, client, tmp_projects_dir, bound_ctx):
    import pypsa

    from services import network_lengths

    r = client.post(f"/api/projects/from_template/{tid}", params={"name": f"{tid}_e1d"},
                    json={"planning": SHIFTED})
    assert r.status_code == 200, r.text
    assert r.json().get("solver_config_issues", []) == []
    with bound_ctx(client):
        d = network_lengths.active_project_dir()
    n = pypsa.Network(str(d / "network.nc"))
    assert set(n.snapshots.year) == {2033}
    p = _plan(tid, SHIFTED)
    assert json.loads((d / T.META_FILE).read_text()) \
        == json.loads(json.dumps(T.template_meta(tid, p)))
    cfg = json.loads((d / T.SOLVER_FILE).read_text())
    want = json.loads(json.dumps(T.solver_config(tid, p)))
    assert cfg.get("finance") == want.get("finance")


@pytest.mark.parametrize("tid", (DC, H2))
def test_create_takes_start_equal_to_end_on_an_uncased_example(tid, client, tmp_projects_dir):
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": f"{tid}_one"},
                    json={"planning": {"start_year": 2033, "end_year": 2033}})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("raw, field", [
    ({"start_year": 2033, "end_year": 2051, "dates": [2040]}, "planning.dates"),
    ({"start_year": 2033, "end_year": 2031}, "planning.end_year"),
])
@pytest.mark.parametrize("tid", IDS)
def test_create_refuses_an_invalid_entry_with_nothing_created(tid, raw, field, client,
                                                              tmp_projects_dir):
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": f"{tid}_bad"},
                    json={"planning": raw})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == detail["error_kind"] == "planning_invalid"
    assert detail["field"] == field
    assert client.get(f"/api/projects/{tid}_bad").status_code == 404


@pytest.mark.parametrize("tid", CASED)
def test_create_refuses_start_equal_to_end_on_a_cased_example(tid, client, tmp_projects_dir):
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": f"{tid}_one"},
                    json={"planning": {"start_year": 2033, "end_year": 2033}})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "planning_invalid" and detail["field"] == "planning.end_year"


def _series_ref(client) -> dict:
    return client.get("/api/simulation/solver_config").json()["commercial"]["export_price_ref"]


def test_the_ipp_create_mints_and_pins_the_entered_years_series(client, tmp_projects_dir,
                                                                 bound_ctx):
    from services import network_lengths
    from services.library import bundle_pins

    r = client.post(f"/api/projects/from_template/{IPP}", params={"name": "ipp_native"})
    assert r.status_code == 200, r.text
    native_ref = _series_ref(client)
    r = client.post(f"/api/projects/from_template/{IPP}", params={"name": "ipp_e1d"},
                    json={"planning": LEAP})
    assert r.status_code == 200, r.text
    assert r.json().get("solver_config_issues", []) == []
    com = client.get("/api/simulation/solver_config").json()["commercial"]
    ref = com["export_price_ref"]
    assert ref["id"] == M.SERIES["base"] and ref["hash"] != "template-placeholder"
    # Another year's dates are another series' content: a new version, not the example's.
    assert ref["hash"] != native_ref["hash"]
    assert com["import_price_ref"] == ref
    assert all(c["reference_price"] == ref for c in com["contracts"])
    assert client.get("/api/simulation/finance").json()["status"] == "ok"
    with bound_ctx(client):
        d = network_lengths.active_project_dir()
    pins, issues = bundle_pins.read_pins(d)
    cfg = json.loads((d / T.SOLVER_FILE).read_text())
    assert issues == [] and pins and pins == bundle_pins.collect_pins(cfg)
    flow = client.get("/api/projects/ipp_e1d/flow").json()
    assert "library_ref_stale" not in {i["code"] for s in flow["sections"] for i in s["items"]}
    # The pinned series resolve on the entered year's snapshots: the export is priced.
    pre = client.post("/api/simulation/preflight").json()
    assert pre["ok"] is True, pre["issues"]


# ── the IPP solves and its case builds at an entered year ───────────────────

@pytest.fixture
def _backend():
    from tests.conftest import _reset_backend_state

    _reset_backend_state()
    yield
    _reset_backend_state()


def _solve_ipp(planning):
    """As test_eh_ipp's ``_solve``, with the series and references the create binds
    for the entered year."""
    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict
    from services.commercial import lp_bindings as LP
    from services.commercial import settlement_inputs as SI
    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    cfg_dict = T.solver_config(IPP, planning)
    install_network_into_backend(T.BUILDERS[IPP](planning=planning))
    n = PyPSAService.get_network()
    da = M.price_shape("base", planning.start_year)
    assert LP.write_export_price(n, "grid_export", da) == 0
    assert LP.write_import_price(n, ["grid_import"], da) == 0
    SI.write_reference_series(
        n, {SI.contract_column(c["id"]): (da.to_numpy(), c["reference_price"])
            for c in cfg_dict["commercial"]["contracts"]}, frame=SI.REF_PRICE_ATTR)
    cfg = _solver_config_from_dict(cfg_dict)
    sim_router._state["solver_config"] = cfg
    state: dict = {}
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: state.update(k))
    assert status in ("ok", "optimal"), (status, cond)
    return n, cfg, state


def _run_case(n, cfg, state):
    import routers.results as R
    from models.finance import FinanceInputs
    from services.finance.engine import run_case
    from services.finance.packs.base import load_pack
    from services.results.finance_case import build_finance_case

    case = build_finance_case(n, cfg, FinanceInputs.model_validate(cfg.finance),
                              result_df=R._result_df, lost_load=state.get("last_lost_load"))
    pack = (load_pack(case.inputs.tax_pack_id, as_of=case.inputs.financial_close)
            if case.inputs.tax_pack_id else None)
    return case, run_case(case, pack)


@pytest.mark.live_solve
@pytest.mark.parametrize("raw", [LEAP, {"start_year": 2033, "end_year": 2038}],
                         ids=["leap-2032", "shorter-than-the-ppa"])
def test_the_ipp_solves_and_its_case_runs_at_an_entered_year(_backend, raw):
    """A leap start year (29 Feb dropped), and a case shorter than the PPA's tenor."""
    p = _plan(IPP, raw)
    n, cfg, state = _solve_ipp(p)
    assert set(n.snapshots.year) == {raw["start_year"]}
    case, res = _run_case(n, cfg, state)
    assert case.inputs.analysis_years == p.end_year - p.start_year
    assert res.metrics["project_pre_tax_npv"] is not None
