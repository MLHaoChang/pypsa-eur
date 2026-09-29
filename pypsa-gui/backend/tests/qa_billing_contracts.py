"""
Phase 2 end-to-end QA: the billing pass and contracts (Edge Investment Case
P2 plan, "Phase 2 e2e QA gate").

  A. R1 (REopt `leap_year`) imported through the URDB importer route into the
     Library → attached by `import_tariff_ref` through the solver-config route
     → the REopt testset's fixed loads rated → parity to the cent for 2023 and
     2024 (both branches: TOU and the Feb-28/29 facility case).
  B. R2 and R3′ imported likewise; R3 (cases 2, 3), R4a and R4b rated from
     their hand translations — each to the cent. Engine timing on a 15-minute
     year with 8 items is recorded (bound 10 s).
  C. The P1 US site (America/New_York) with a pay-as-produced PPA, a CfD on a
     Library reference price and a DR contract on an active DSR bus → solve →
     `/results/billing` → every settlement line equals its hand formula to the
     cent; the gap per item kind is fully attributed.
  D. A `changes_dispatch` PPA with a Library tariff (`import_tariff_ref`) and a
     CfD on a Library reference price reconciles (gap 0) before and after
     save → load, and after bundle export → import with the Library pins; the
     PPA line equals its hand formula, and the billing gap per item kind stays
     fully attributed after each round trip.
  E. The H1–H3 hand-rated bills (DE, NL, US C&I; fixtures/investment_case/bills,
     arithmetic in `h_bills_arithmetic.py`, stdlib only) rated to the cent.

Run: python tests/qa_billing_contracts.py   (from pypsa-gui/backend; also run
by tests/run_qa_drivers.py).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import json  # noqa: E402
import queue  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tests import qa_support  # noqa: E402  (ordering is the point)

from models.commercial import Tariff  # noqa: E402
from services.commercial.tariff_engine import rate  # noqa: E402
from services.results.cost_breakdown import compute_cost_breakdown  # noqa: E402
from services.results.objective_decomposition import compute_objective_decomposition  # noqa: E402
from services.solver_service import run_simulation  # noqa: E402
from tests.fixtures.investment_case.edge_15min import build_edge_15min  # noqa: E402

PASS = 0
FAIL = 0
CENT = 0.005
ORACLES = pathlib.Path(__file__).resolve().parent / "fixtures" / "investment_case" / "oracles"
PROJECTS = ("_qa_billing_a", "_qa_billing_c", "_qa_billing_d", "_qa_billing_d_imported")
US_TZ = "America/New_York"


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {msg}" if msg else ""))


def _cent(a, b) -> bool:
    return a is not None and b is not None and abs(a - b) < CENT


def _json(name: str) -> dict:
    return json.loads((ORACLES / name).read_text())


def _gap_attributed(bill: dict, kinds: set[str]) -> tuple[bool, str]:
    """The billing gap per item kind is non-empty, holds `kinds`, raises no
    gate and leaves nothing unattributed (never vacuous on an empty gap)."""
    periods = (bill.get("gap") or {}).get("periods") or {}
    seen = {k for per in periods.values() for k in per}
    unexplained = [(p, k, v["unattributed_pct"]) for p, per in periods.items()
                   for k, v in per.items()
                   if v["unattributed_pct"] is None or v["unattributed_pct"] > 1e-6]
    gates = (bill.get("gap") or {}).get("gates")
    ok = bool(periods) and kinds <= seen and gates == [] and not unexplained
    return ok, f"kinds {sorted(seen)}, gates {gates}, unexplained {unexplained}"


def _hourly(year: int, kw: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range(f"{year}-01-01", periods=len(kw), freq="h")
    return pd.DataFrame({"import_mw": kw / 1000.0, "export_mw": 0.0}, index=idx)


def _import(client, name: str, urdb: dict, valid_from: str, **extra):
    r = client.post("/api/library/items/tariff/import_urdb", json={
        "urdb_response": urdb, "name": name, "valid_from": valid_from, **extra})
    return r


def _attach(client, ref: dict) -> dict | None:
    r = client.put("/api/simulation/solver_config", json={"commercial": {
        "poc_link": "import", "import_tariff_ref": ref}})
    if r.status_code != 200:
        return None
    return qa_support.session_context().solver_state["solver_config"].commercial


# ── A ──────────────────────────────────────────────────────────────────────


def scenario_a() -> None:
    print("\n[A] R1 through the URDB importer → Library → import_tariff_ref → REopt parity")
    client = qa_support.client()
    qa_support.install_network(build_edge_15min(), name=PROJECTS[0])
    qa_support.save_project(PROJECTS[0])
    r = _import(client, "qa_r1", _json("r1_leap_year.urdb.json"), "2023-01-01")
    _step("A: R1 imports with no refusals", r.status_code == 200 and r.json()["refusals"] == [],
          r.text[:200])
    commercial = _attach(client, r.json()["ref"])
    _step("A: attached through the config route (resolved inline)",
          commercial is not None and commercial["import_tariff"]["name"] == "qa_r1")
    tariff = Tariff.model_validate(commercial["import_tariff"])
    tou = 31 * 24 + 29 * 24 + 3 * 24 + 16            # REopt's 1-based loads_kw index
    facility = [31 * 24 + 27 * 24 + 8, 31 * 24 + 28 * 24 + 8]
    for year, energy, demand, fac in ((2023, 2.8, 180.5, 361.0), (2024, 3.6, 280.5, 180.5)):
        kw = np.zeros(8760)
        kw[tou - 1] = 10.0
        res = rate(_hourly(year, kw), tariff, step_hours=1.0, timezone=None)
        _step(f"A: {year} TOU energy to the cent", _cent(res.per_item["energy"], energy),
              f"{res.per_item['energy']}")
        _step(f"A: {year} TOU + facility demand to the cent",
              _cent(res.per_item["demand"] + res.per_item["demand_tou"], demand))
        kw = np.zeros(8760)
        for h in facility:
            kw[h - 1] = 10.0
        res = rate(_hourly(year, kw), tariff, step_hours=1.0, timezone=None)
        _step(f"A: {year} Feb-28/29 facility case to the cent",
              _cent(res.per_item["demand"] + res.per_item["demand_tou"], fac))


# ── B ──────────────────────────────────────────────────────────────────────


def scenario_b() -> None:
    print("\n[B] R2, R3′ through the importer; R3, R4a, R4b by hand translation; timing")
    client = qa_support.client()
    r = _import(client, "qa_r2", _json("r2_tiered_tou_demand.urdb.json"), "2017-01-01")
    _step("B: R2 imports", r.status_code == 200, r.text[:200])
    t2 = Tariff.model_validate(_attach(client, r.json()["ref"])["import_tariff"])
    scen = _json("r2_tiered_tou_demand.reopt.json")
    tiers = scen["ElectricTariff"]["urdb_response"]["demandratestructure"][0]
    peak = scen["ElectricLoad"]["annual_kwh"] / 8760
    res = rate(_hourly(2017, np.full(8760, peak)), t2, step_hours=1.0, timezone=None)
    want = 12 * (tiers[0]["max"] * tiers[0]["rate"] + (peak - tiers[0]["max"]) * tiers[1]["rate"])
    _step("B: R2 tiered demand to the cent", _cent(res.per_item["demand"], want),
          f"{res.per_item['demand']:.4f} vs {want:.4f}")

    r = _import(client, "qa_r3p", _json("r3prime.urdb.json"), "2022-01-01", cyclic_year=True)
    _step("B: R3′ imports (cyclic_year disclosed)",
          r.status_code == 200 and "cyclic_year_set_by_importer" in r.json()["notes"])
    t3p = Tariff.model_validate(_attach(client, r.json()["ref"])["import_tariff"])
    kw = np.ones(8760)
    kw[7] = 100.0
    res = rate(_hourly(2022, kw), t3p, step_hours=1.0, timezone=None)
    _step("B: R3′ cyclic range to the cent",
          _cent(res.per_item["demand"], 100 * (10.5 + 0.35 * 10.5 * 5 + 0.35 * 11.5 * 6)))

    rates = [10, 10, 20, 50, 20, 10, 20, 20, 20, 20, 20, 5]
    kw = np.full(8760, 100.0)
    kw[21], kw[2402], kw[4087], kw[8332] = 200.0, 400.0, 500.0, 300.0
    for fixture, peaks in (("r3_case2.tariff.json", [300, 300, 300, 400, 300, 500] + [300] * 6),
                           ("r3_case3.tariff.json", [225, 225, 225, 400, 300, 500] + [375] * 6)):
        res = rate(_hourly(2022, kw), Tariff.model_validate(_json(fixture)), step_hours=1.0,
                   timezone=None)
        _step(f"B: R3 {fixture[:8]} to the cent",
              _cent(res.per_item["demand"], sum(p * r for p, r in zip(peaks, rates))))

    blended = Tariff.model_validate(_json("r4_blended.tariff.json"))
    scen = _json("r4_no_techs.reopt.json")
    kwh = scen["ElectricLoad"]["annual_kwh"]
    t = scen["ElectricTariff"]
    res = rate(_hourly(2017, np.full(8760, kwh / 8760)), blended, step_hours=1.0, timezone=None)
    _step("B: R4a blended energy and demand to the cent",
          _cent(res.per_item["energy"], t["blended_annual_energy_rate"] * kwh)
          and _cent(res.per_item["demand"], 12 * t["blended_annual_demand_rate"] * kwh / 8760))
    idx = pd.date_range("2017-01-01", "2017-12-31 23:59", freq="15min")
    res = rate(pd.DataFrame({"import_mw": 0.001, "export_mw": 0.0}, index=idx), blended,
               step_hours=0.25, timezone=None)
    _step("B: R4b 15-min to the cent",
          _cent(res.per_item["energy"], 876.0) and _cent(res.per_item["demand"], 120.0))

    # Engine timing: a 15-minute year, 8 items (spec §16 risk 1).
    items = [
        {"id": "e", "kind": "energy", "unit": "per_kwh", "periods": [
            {"name": "peak", "rate": 0.3, "start_hour": 17, "end_hour": 21},
            {"name": "off", "rate": 0.1}]},
        {"id": "wt", "kind": "energy", "unit": "per_kwh", "measured_on": "import",
         "tiers": [{"threshold": 0, "rate": 0}, {"threshold": 50_000, "rate": 0}],
         "periods": [{"name": "pk", "rate": 0, "start_hour": 8, "end_hour": 20,
                      "tier_rates": [0.02, 0.03]}, {"name": "op", "rate": 0,
                                                    "tier_rates": [0.01, 0.015]}]},
        {"id": "d", "kind": "demand", "unit": "per_kw_month", "periods": [
            {"name": "pk", "rate": 8.0, "start_hour": 14, "end_hour": 20},
            {"name": "all", "rate": 4.0}]},
        {"id": "fac", "kind": "demand", "unit": "per_kw_month",
         "periods": [{"name": "facility", "rate": 10.0}],
         "ratchet": {"lookback_months": 11, "share": 0.5, "cyclic_year": True}},
        {"id": "fm", "kind": "fixed", "unit": "per_month", "periods": [{"name": "a", "rate": 20.0}]},
        {"id": "fd", "kind": "fixed", "unit": "per_day", "periods": [{"name": "a", "rate": 1.0}]},
        {"id": "cap", "kind": "capacity", "unit": "per_kw_year",
         "periods": [{"name": "a", "rate": 30.0}]},
        {"id": "fit", "kind": "energy", "unit": "per_kwh", "measured_on": "export",
         "direction": "revenue", "periods": [{"name": "a", "rate": 0.05}]},
    ]
    big = Tariff.model_validate({"id": "big", "name": "big", "jurisdiction": "US",
                                 "valid_from": "2030-01-01", "items": items})
    idx = pd.date_range("2030-01-01", periods=35040, freq="15min")
    rng = np.random.default_rng(0)
    dispatch = pd.DataFrame({"import_mw": rng.uniform(0.1, 1.0, len(idx)),
                             "export_mw": rng.uniform(0.0, 0.2, len(idx))}, index=idx)
    t0 = time.perf_counter()
    res = rate(dispatch, big, step_hours=0.25, timezone=None, capacity_kw=1000.0)
    took = time.perf_counter() - t0
    _step("B: engine on a 15-min year with 8 items under 10 s",
          took < 10.0 and res.total_supported is not None, f"{took:.2f} s")


# ── C ──────────────────────────────────────────────────────────────────────


def _solve(cfg, label):
    ctx = qa_support.session_context()
    ctx.solver_state["solver_config"] = cfg
    n = ctx.network
    status, condition = run_simulation(cfg, n, ctx.mutation_lock, threading.Event(),
                                       queue.SimpleQueue(), state_update=lambda **kw: None)
    _step(f"{label}: solve is optimal", status in ("ok", "optimal"), f"{status}/{condition}")
    return n


def _gap(n, cfg):
    cb = compute_cost_breakdown(n, cfg)
    return compute_objective_decomposition(n, cb)["gap_pct"], cb


def _us_site():
    n = build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 50.0      # the PV runs
    return n


TOU = {"id": "energy", "kind": "energy", "unit": "per_kwh", "periods": [
    {"name": "night", "rate": 0.06, "start_hour": 0, "end_hour": 6},
    {"name": "peak", "rate": 0.40, "start_hour": 17, "end_hour": 21},
    {"name": "day", "rate": 0.18}]}
DEMAND = {"id": "demand", "kind": "demand", "unit": "per_kw_month",
          "periods": [{"name": "all", "rate": 14.0}], "measured_on": "import"}


def _us_tariff() -> dict:
    return {"id": "us", "name": "us", "jurisdiction": "US", "valid_from": "2029-01-01",
            "items": [TOU, DEMAND]}


def _price_ref(client, name: str, n) -> dict:
    idx = n.snapshots.tz_localize("UTC")
    price = [45.0 + 35.0 * np.sin(i / 96 * 2 * np.pi) for i in range(len(idx))]
    return client.post("/api/library/series", json={
        "name": name, "timestamps": [t.isoformat() for t in idx], "values": price,
        "meta": {"source": "qa driver"}}).json()


def scenario_c() -> None:
    print("\n[C] US site: PPA + CfD + DR → solve → /results/billing to the cent")
    client = qa_support.client()
    n0 = _us_site()
    qa_support.install_network(n0, name=PROJECTS[1])
    qa_support.save_project(PROJECTS[1])
    ref = _price_ref(client, "qa_ref_price_c", n0)
    contracts = [
        {"type": "ppa", "id": "ppa", "kind": "pay_as_produced", "price": 32.0,
         "tenor_years": 10, "seller": "Solar LLC", "buyer": "site", "asset_ids": ["pv"]},
        {"type": "cfd", "id": "cfd", "strike": 55.0, "reference_price": ref, "tenor_years": 15,
         "asset_ids": ["pv"], "generator_owner": "site", "counterparty": "State"},
        {"type": "dr", "id": "dr", "availability_eur_per_mw_year": 20_000.0,
         "activation_eur_per_mwh": 300.0, "load_ids": ["site_load"], "counterparty": "TSO",
         "contracted_mw": 5.0}]
    commercial = {"poc_link": "import", "timezone": US_TZ, "import_tariff": _us_tariff(),
                  "contracts": contracts}
    r = client.put("/api/simulation/solver_config", json={
        "commercial": commercial, "dsr_price_eur_per_mwh": 150.0, "dsr_share_of_load": 0.2,
        "dsr_buses": ["site"]})
    _step("C: config route accepts the contracts and the DSR", r.status_code == 200,
          r.text[:200])
    cfg = qa_support.session_context().solver_state["solver_config"]
    n = _solve(cfg, "C")
    r = client.get("/api/results/billing")
    _step("C: /results/billing answers", r.status_code == 200, str(r.status_code))
    out = r.json()
    lines = {}
    for ln in out["contracts"]["lines"]:
        lines.setdefault((ln["contract_id"], ln["value_stream"]), []).append(ln)
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    pv = n.generators_t.p["pv"].to_numpy(dtype=float)
    _step("C: the PV runs (Σ w·pv > 0)", float((w * pv).sum()) > 1.0,
          f"{float((w * pv).sum()):.3f} MWh")
    (ppa,) = [x for (cid, _), v in lines.items() if cid == "ppa" for x in v]
    _step("C: PPA = price × Σ w·pv to the cent", _cent(ppa["amount"], 32.0 * float((w * pv).sum())),
          f"{ppa['amount']}")
    px = np.array([45.0 + 35.0 * np.sin(i / 96 * 2 * np.pi) for i in range(len(w))])
    (cfd,) = [x for (cid, _), v in lines.items() if cid == "cfd" for x in v]
    _step("C: CfD = Σ (strike − ref)·w·pv to the cent",
          _cent(cfd["amount"], float(((55.0 - px) * w * pv).sum())), f"{cfd['amount']}")
    dr = {vs: v[0] for (cid, vs), v in lines.items() if cid == "dr"}
    hours = float(w.sum())
    avail = [v for k, v in dr.items() if "avail" in k]
    act = [v for k, v in dr.items() if "activ" in k]
    _step("C: DR availability = rate × MW × hours / 8760 to the cent",
          bool(avail) and _cent(avail[0]["amount"], 20_000.0 * 5.0 * hours / 8760.0),
          f"{avail[0]['amount'] if avail else None}")
    dsr = n.buses_t["ic_dsr_p"]["site"].to_numpy(dtype=float) if "ic_dsr_p" in n.buses_t \
        else None
    _step("C: DR activation = rate × Σ w·DSR on the bus to the cent",
          bool(act) and dsr is not None and _cent(act[0]["amount"],
                                                  300.0 * float((w * dsr).sum())),
          f"{act[0]['amount'] if act else None}; DSR MWh {float((w * dsr).sum()) if dsr is not None else None}")
    _step("C: the DSR dispatches (Σ w·DSR > 0)",
          dsr is not None and float((w * dsr).sum()) > 1.0,
          f"{float((w * dsr).sum()) if dsr is not None else None} MWh")
    ok, detail = _gap_attributed(out, {"energy", "demand", "contracts"})
    _step("C: the gap per item kind (energy, demand, contracts) is fully attributed", ok, detail)
    # The DSR slack is a transient LP term like a VOLL slack: its cost is the
    # objective decomposition's documented residual, not a cost row. The
    # residual must be exactly that cost (price × Σ w·DSR) and nothing else.
    cb = compute_cost_breakdown(n, cfg)
    dec = compute_objective_decomposition(n, cb)
    dsr_cost = 150.0 * float((w * dsr).sum()) if dsr is not None else None
    _step("C: the objective's residual is exactly the DSR slack cost",
          dsr_cost is not None and abs(dec["residual_gap_eur"] - dsr_cost) < 0.01,
          f"residual {dec['residual_gap_eur']:.2f} vs DSR {dsr_cost}")


# ── D ──────────────────────────────────────────────────────────────────────


def scenario_d() -> None:
    print("\n[D] changes_dispatch PPA + Library tariff + Library ref price: save/load, bundle")
    client = qa_support.client()
    n0 = _us_site()
    qa_support.install_network(n0, name=PROJECTS[2])
    qa_support.save_project(PROJECTS[2])
    r = client.put("/api/library/items/tariff/qa_us_tariff",
                   json={"payload": _us_tariff(), "meta": {"source": "qa driver"}})
    tariff_ref = r.json()
    ref = _price_ref(client, "qa_ref_price_d", n0)
    commercial = {"poc_link": "import", "timezone": US_TZ, "import_tariff_ref": tariff_ref,
                  "contracts": [
                      {"type": "ppa", "id": "ppa", "kind": "pay_as_produced", "price": 20.0,
                       "tenor_years": 10, "seller": "Solar LLC", "buyer": "site",
                       "asset_ids": ["pv"], "changes_dispatch": True},
                      {"type": "cfd", "id": "cfd", "strike": 55.0, "reference_price": ref,
                       "tenor_years": 15, "asset_ids": ["pv"], "generator_owner": "site",
                       "counterparty": "State"}]}
    r = client.put("/api/simulation/solver_config", json={"commercial": commercial})
    _step("D: config route resolves the Library tariff", r.status_code == 200, r.text[:200])
    cfg = qa_support.session_context().solver_state["solver_config"]
    n = _solve(cfg, "D")
    gap, cb = _gap(n, cfg)
    _step("D: objective gap 0", gap is not None and abs(gap) < 1e-6, f"{gap}")
    row = (cb["commercial"] or {}).get("ppa_settlement")
    bill = client.get("/api/results/billing").json()
    (line,) = [ln for ln in bill["contracts"]["lines"] if ln["contract_id"] == "ppa"]
    _step("D: the dispatch PPA's line equals its row", _cent(line["amount"], row),
          f"{line['amount']} vs {row}")
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    pv = n.generators_t.p["pv"].to_numpy(dtype=float)
    hand = 20.0 * float((w * pv).sum())
    _step("D: the PPA line = price × Σ w·pv to the cent (hand formula)",
          float((w * pv).sum()) > 1.0 and _cent(line["amount"], hand),
          f"{line['amount']} vs {hand}")
    ok, detail = _gap_attributed(bill, {"energy", "demand", "contracts"})
    _step("D: the billing gap per item kind is fully attributed", ok, detail)
    kinds_before = {p: sorted(per) for p, per in bill["gap"]["periods"].items()}

    qa_support.save_project(PROJECTS[2])
    r = client.get(f"/api/projects/{PROJECTS[2]}")
    _step("D: project reloads", r.status_code == 200)
    ctx = qa_support.session_context()
    gap2, cb2 = _gap(ctx.network, ctx.solver_state["solver_config"])
    _step("D: gap 0 after reload", gap2 is not None and abs(gap2) < 1e-6, f"{gap2}")
    _step("D: ppa_settlement identical after reload",
          _cent((cb2["commercial"] or {}).get("ppa_settlement"), row))
    bill2 = client.get("/api/results/billing").json()
    ok, detail = _gap_attributed(bill2, {"energy", "demand", "contracts"})
    _step("D: billing gap per item kind attributed after reload", ok, detail)
    _step("D: the same gap kinds after reload",
          {p: sorted(per) for p, per in bill2["gap"]["periods"].items()} == kinds_before)

    r = client.get(f"/api/projects/{PROJECTS[2]}/bundle")
    _step("D: bundle exported", r.status_code == 200)
    r = client.post(f"/api/projects/import_bundle?name={PROJECTS[3]}",
                    files={"file": ("b.zip", r.content, "application/zip")})
    body = r.json() if r.status_code in (200, 201) else {}
    _step("D: bundle imported with no library issues",
          r.status_code in (200, 201) and body.get("library_issues") == [], r.text[:200])
    cfg3 = qa_support.session_context().solver_state["solver_config"]
    c3 = cfg3.commercial or {}
    _step("D: the tariff pin is carried", (c3.get("import_tariff_ref") or {}).get("hash")
          == tariff_ref["hash"])
    cfd3 = next((c for c in c3.get("contracts") or [] if c.get("id") == "cfd"), {})
    _step("D: the reference-price pin is carried",
          (cfd3.get("reference_price") or {}).get("hash") == ref["hash"])
    ctx = qa_support.session_context()
    gap3, _ = _gap(ctx.network, cfg3)
    _step("D: gap 0 after the bundle round trip", gap3 is not None and abs(gap3) < 1e-6,
          f"{gap3}")
    bill3 = client.get("/api/results/billing").json()
    ok, detail = _gap_attributed(bill3, {"energy", "demand", "contracts"})
    _step("D: billing gap per item kind attributed after the bundle round trip", ok, detail)
    (line3,) = [ln for ln in bill3["contracts"]["lines"] if ln["contract_id"] == "ppa"]
    _step("D: the PPA line is unchanged after the bundle round trip",
          _cent(line3["amount"], line["amount"]), f"{line3['amount']} vs {line['amount']}")


# ── E ──────────────────────────────────────────────────────────────────────


def scenario_e() -> None:
    print("\n[E] H1–H3 hand-rated bills (DE, NL, US C&I) to the cent")
    from tests.test_tariff_engine_core import _load

    for name in ("h1_de_rlm.json", "h2_nl_business.json", "h3_us_ci.json"):
        raw, tariff, df = _load(name)
        res = rate(df, tariff, step_hours=raw["step_hours"], timezone=raw["timezone"],
                   meter_history=raw.get("meter_history"))
        exp = raw["expected"]
        bad = {k: (res.per_item.get(k), v) for k, v in exp["per_item"].items()
               if not _cent(res.per_item.get(k), v)}
        _step(f"E: {name} complete, every item and the total to the cent",
              res.complete and not bad and _cent(res.total, exp["total"]),
              f"total {res.total} vs {exp['total']}; off {bad}; flags {res.flags}")


def main() -> int:
    for fn in (scenario_a, scenario_b, scenario_c, scenario_d, scenario_e):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — a crashed scenario is a failure
            import traceback
            traceback.print_exc()
            _step(f"{fn.__name__} ran without crashing", False, f"{type(exc).__name__}: {exc}")
    try:
        qa_support.delete_project(*PROJECTS)
    except Exception:  # noqa: BLE001
        pass
    print()
    print("=" * 60)
    print(f"Total: {PASS + FAIL}  Pass: {PASS}  Fail: {FAIL}")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
