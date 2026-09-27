"""
Phase 1 end-to-end QA: the commercial layer on a 15-minute edge site
(Edge Investment Case plan, "Phase 1 e2e QA gate").

Scenarios, each through the real routes (Library, solver config, project
save/load, bundle export/import) with `run_simulation` on the session's live
network:

  A. DE-style tariff: TOU energy (15-min) + a firm connection with an annual
     capacity fee + an export price from the Library on a `poc→grid` Link.
  B. US-style tariff: TOU energy + a monthly demand charge with a ratchet seeded
     from meter history.

Asserted for each: the LP energy/demand cost equals the billing engine's rating
of the same dispatch, per item; the objective gap is 0; the user's PoC Links are
untouched after the solve (the transforms were undone); the evening peak import
is shaved against a plain solve (B); the rows and the gap are identical after a
project save → load; a bundle round-trip keeps the commercial config and its
Library pins (no `library_issues`).

Run: python tests/qa_commercial_lp.py   (from pypsa-gui/backend; also run by
tests/run_qa_drivers.py).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import queue  # noqa: E402
import threading  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tests import qa_support  # noqa: E402  (ordering is the point)

from models.commercial import Tariff  # noqa: E402
from services.commercial import lp_bindings as L  # noqa: E402
from services.commercial.tariff_engine import rate  # noqa: E402
from services.results.cost_breakdown import compute_cost_breakdown  # noqa: E402
from services.results.objective_decomposition import compute_objective_decomposition  # noqa: E402
from services.solver_service import SolverConfig, run_simulation  # noqa: E402
from tests.fixtures.investment_case.edge_15min import build_edge_15min  # noqa: E402

PASS = 0
FAIL = 0
PROJECTS = ("_qa_commercial_de", "_qa_commercial_us", "_qa_commercial_de_imported")

TOU = {"id": "energy_tou", "kind": "energy", "unit": "per_kwh", "periods": [
    {"name": "night", "rate": 0.06, "start_hour": 0, "end_hour": 6},
    {"name": "peak", "rate": 0.32, "start_hour": 17, "end_hour": 21},
    {"name": "day", "rate": 0.18}]}
DEMAND = {"id": "demand", "kind": "demand", "unit": "per_kw_month",
          "periods": [{"name": "all", "rate": 14.0}], "measured_on": "import",
          "ratchet": {"lookback_months": 11, "share": 0.8}}
FEE = {"kind": "firm", "import_cap_mw": 70.0, "available_from": "2030-01-01",
       "capacity_fee": {"id": "cap_fee", "kind": "capacity", "unit": "per_kw_year",
                        "periods": [{"name": "all", "rate": 45.0}]}}
HISTORY = {f"2029-{m:02d}": 25_000.0 for m in range(2, 13)}


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {msg}" if msg else ""))


def _approx(a, b, rel=1e-6) -> bool:
    return a is not None and b is not None and abs(a - b) <= rel * max(abs(b), 1.0)


def _tariff(*items) -> dict:
    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
            "items": list(items)}


def _site(with_export: bool):
    n = build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 0.0
    n.generators.loc["pv", "p_nom"] = 90.0
    if with_export:
        n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC",
              marginal_cost=0.5)
        n.generators.loc["grid_supply", "p_min_pu"] = -1.0
    return n


def _links_snapshot(n):
    return n.links[["p_nom", "p_nom_extendable", "p_nom_max", "capital_cost",
                    "marginal_cost", "p_max_pu"]].copy(), n.links_t.marginal_cost.copy()


def _solve(cfg, label):
    ctx = qa_support.session_context()
    ctx.solver_state["solver_config"] = cfg
    n = ctx.network
    sink: dict = {}
    status, condition = run_simulation(cfg, n, ctx.mutation_lock, threading.Event(),
                                       queue.SimpleQueue(),
                                       state_update=lambda **kw: sink.update(kw))
    _step(f"{label}: solve is optimal", status in ("ok", "optimal"), f"{status}/{condition}")
    return n, sink


def _gap(n, cfg):
    cb = compute_cost_breakdown(n, cfg)
    return compute_objective_decomposition(n, cb)["gap_pct"], cb


def _dispatch(n, utc: bool = False):
    """The solved PoC dispatch; `utc=True` when the config names a timezone
    (then the snapshots are UTC and the engine needs an aware index)."""
    exp = n.links_t.p0["export"].to_numpy() if "export" in n.links_t.p0.columns else 0.0
    idx = n.snapshots.tz_localize("UTC") if utc else n.snapshots
    return pd.DataFrame({"import_mw": n.links_t.p0["import"].to_numpy(), "export_mw": exp},
                        index=idx)


def _evening_peak(n) -> float:
    hour = pd.DatetimeIndex(n.snapshots).hour
    return float(n.links_t.p0["import"][(hour >= 17) & (hour < 21)].max())


def _reload_and_compare(name, cfg, cb_before, label):
    client = qa_support.client()
    qa_support.save_project(name)
    r = client.get(f"/api/projects/{name}")
    _step(f"{label}: project reloads", r.status_code == 200, str(r.status_code))
    ctx = qa_support.session_context()
    gap2, cb2 = _gap(ctx.network, ctx.solver_state["solver_config"])
    _step(f"{label}: gap 0 after reload", gap2 is not None and abs(gap2) < 1e-6, f"{gap2}")
    same = all(_approx((cb2["commercial"] or {}).get(k), v, 1e-9)
               for k, v in (cb_before["commercial"] or {}).items()
               if isinstance(v, float))
    _step(f"{label}: commercial rows identical after reload", same)
    _step(f"{label}: total identical after reload", _approx(cb2["total"], cb_before["total"], 1e-9))


def scenario_de() -> None:
    print("\n[A] DE: TOU energy + firm connection fee + Library export price")
    client = qa_support.client()
    n0 = _site(with_export=True)
    qa_support.install_network(n0, name=PROJECTS[0])
    qa_support.save_project(PROJECTS[0])
    idx = n0.snapshots.tz_localize("UTC")
    price = [40.0 + 30.0 * np.sin(i / 96 * 2 * np.pi) for i in range(len(idx))]
    ref = client.post("/api/library/series", json={
        "name": "qa_da_price", "timestamps": [t.isoformat() for t in idx], "values": price,
        "meta": {"source": "qa driver"}}).json()
    commercial = {"poc_link": "import", "export_link": "export", "export_price_ref": ref,
                  "timezone": "UTC", "import_tariff": _tariff(TOU), "connection": FEE}
    r = client.put("/api/simulation/solver_config", json={"commercial": commercial})
    _step("A: config route accepts and materialises the export price", r.status_code == 200,
          r.text[:200])
    cfg = qa_support.session_context().solver_state["solver_config"]
    before_links = _links_snapshot(qa_support.session_context().network)
    n, sink = _solve(cfg, "A")
    after_links = _links_snapshot(n)
    _step("A: user's Links untouched after the solve",
          before_links[0].equals(after_links[0]) and before_links[1].equals(after_links[1]))
    billed = rate(_dispatch(n, utc=True), Tariff.model_validate(_tariff(TOU)), step_hours=0.25,
                  timezone="UTC")
    w = n.snapshot_weightings.objective
    lp_energy = float((w * n.links_t.p0["import"] * n.links_t[L.ENERGY_PRICE_ATTR]["import"]).sum())
    _step("A: LP TOU energy == billing engine", _approx(lp_energy, billed.per_item["energy_tou"]),
          f"{lp_energy:.2f} vs {billed.per_item['energy_tou']:.2f}")
    gap, cb = _gap(n, cfg)
    _step("A: objective gap 0", gap is not None and abs(gap) < 1e-6, f"{gap}")
    rows = cb["commercial"]
    _step("A: capacity fee row reported", (rows.get("network_capacity") or 0) > 0,
          f"{rows.get('network_capacity')}")
    _step("A: export revenue row reported (negative cost)", (rows.get("energy_export") or 0) < 0,
          f"{rows.get('energy_export')}")
    _step("A: terms published", (sink.get("last_commercial_terms") or {}).get("poc_link") == "import")
    _reload_and_compare(PROJECTS[0], cfg, cb, "A")

    print("\n[A'] bundle export → import keeps bindings and Library pins")
    r = client.get(f"/api/projects/{PROJECTS[0]}/bundle")
    _step("A': bundle exported", r.status_code == 200)
    r = client.post(f"/api/projects/import_bundle?name={PROJECTS[2]}",
                    files={"file": ("b.zip", r.content, "application/zip")})
    body = r.json() if r.status_code in (200, 201) else {}
    _step("A': bundle imported", r.status_code in (200, 201), r.text[:200])
    _step("A': no library issues in the same org", body.get("library_issues") == [],
          f"{body.get('library_issues')}")
    cfg2 = qa_support.session_context().solver_state["solver_config"]
    carried = ((cfg2.commercial or {}).get("export_price_ref") or {}).get("hash")
    _step("A': commercial config carried (same pinned Library version)", carried == ref["hash"])


def scenario_us() -> None:
    print("\n[B] US: TOU energy + ratcheted monthly demand charge")
    plain = _site(with_export=False)
    qa_support.install_network(plain, name=PROJECTS[1])
    qa_support.save_project(PROJECTS[1])
    n_plain, _ = _solve(SolverConfig(), "B-plain")
    plain_peak = _evening_peak(n_plain)
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU, DEMAND),
                  "meter_history_peaks_kw": HISTORY}
    cfg = SolverConfig(commercial=commercial)
    n, sink = _solve(cfg, "B")
    _step("B: evening peak shaved", _evening_peak(n) < 0.99 * plain_peak,
          f"{_evening_peak(n):.2f} vs {plain_peak:.2f} MW")
    billed = rate(_dispatch(n), Tariff.model_validate(_tariff(TOU, DEMAND)), step_hours=0.25,
                  timezone=None, meter_history=HISTORY)
    peaks = n.meta[L.META_DEMAND]
    lp_demand = sum(v["eur_per_mw"] * v["billed_mw"] for v in peaks.values())
    _step("B: LP demand charge == billing engine (ratcheted)",
          _approx(lp_demand, billed.per_item["demand"]),
          f"{lp_demand:.2f} vs {billed.per_item['demand']:.2f}")
    gap, cb = _gap(n, cfg)
    _step("B: objective gap 0", gap is not None and abs(gap) < 1e-6, f"{gap}")
    _step("B: demand peaks in the published terms",
          bool((sink.get("last_commercial_terms") or {}).get("demand_peaks")))
    _reload_and_compare(PROJECTS[1], cfg, cb, "B")


def main() -> int:
    for fn in (scenario_de, scenario_us):
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
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
