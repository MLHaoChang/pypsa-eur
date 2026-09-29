"""
Phase 3 end-to-end QA: participants and value flows (Edge Investment Case P3
plan, "Phase 3 e2e QA gate"; oracles V1–V6 in "Fixtures and oracles").

Every template case runs through the routes: POST the template → price and
save its drafts (solver-config route) → PUT the value-flow config (If-Match)
→ solve on the session → GET `/api/results/value_flows`. On each: status ok,
every period's four conservation checks True, and the ledger reconciled to
`cost_breakdown` to the cent — by the route's own check AND by a bridge the
driver rebuilds from `/results/cost_breakdown`, `/results/billing`, the LP
rows and the export price on the network.

  A. V1 `single_owner` (the templates test's E2E table, the P1 edge site with
     a TOU/demand/fixed/feed-in/levy tariff, an export price, a firm
     connection fee, a dispatch PPA and a lease): the commodity line equals
     Σ w·p·mc of the grid-side generator and the fee line the
     `network_capacity` row, to the cent.
  B. V2 `btm_ppa`: an `as_consumed_btm` PPA on `pv` and an EaaS on `bess`
     with the developer, `bess` re-assigned to the developer (a disclosed
     edit), a second site PV kept by the site → the export split of BOTH
     export sources (export price and the tariff's feed-in item) equals the
     per-interval hand formula; the PPA and EaaS lines equal their hand
     formulas; the developer pays the PV/BESS costs; the site pays the bill,
     the PPA and the EaaS. Plus the E2E `btm_ppa` cases (with and without a
     saved PPA). The V2 project is saved for H.
  C. V3 `landlord_tenant` (E2E, with and without a saved lease): the landlord
     pays the leased assets' costs and receives the lease (= annual payment ×
     represented hours / 8760 to the cent); the tenant (= `site_party`) pays
     the lease and the bill.
  D. V4 `dso_developer` (E2E, the drafted DR priced, DSR on the load's bus):
     DSO → developer availability = rate × MW × hours / 8760 and activation =
     rate × Σ w·DSR on the bus, each to the cent (the P2 C-style formula).
  E. `energy_hub` (E2E, two members): after the solve the allocation key is
     switched through the value-flows route to each of the four keys (no
     re-solve: the ledger never enters the LP) → the ledger still closes, each
     billed item is split in full, and the keyed items of
     `contracted_capacity` and `fixed_shares` split by hand to the cent.
  F. V5: the self-authored three-member hub (fixtures/investment_case/hub):
     the group bill and the per-member metered energy equal the working, and
     every member's share of every shared item under each of the four keys
     equals the stdlib arithmetic to the cent.
  G. V6: a net cost energy item on a three-member group with an export Link,
     through the routes → the LP row `energy_net_group` equals the billed
     item to the cent, the objective gap is 0, the billing gap's energy kind
     is fully attributed with no gate; under `single_owner` the ledger closes.
  H. Corruptions on the real V1 ledger: a swapped payer/payee and a dropped
     source each turn coverage and reconciliation False.
  I. Bundle round trip of the V2 project: save → load and bundle export →
     import keep `value_flows` and the recomputed ledger identical.

(The P1 and P2 drivers — `qa_commercial_lp.py`, `qa_billing_contracts.py` —
run beside this one in `tests/run_qa_drivers.py`.)

Run: python tests/qa_value_flows.py   (from pypsa-gui/backend; also run by
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

import copy  # noqa: E402
import queue  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tests import qa_support  # noqa: E402  (ordering is the point)

from models.commercial import Tariff  # noqa: E402
from services.commercial import connection as _conn  # noqa: E402
from services.commercial import hub_allocation as HA  # noqa: E402
from services.commercial import participants as P  # noqa: E402
from services.commercial.cost_rows import commercial_cost_terms  # noqa: E402
from services.commercial.lp_bindings import same_party  # noqa: E402
from services.solver_service import run_simulation  # noqa: E402

PASS = 0
FAIL = 0
CENT = 0.005
HOURS_PER_YEAR = 8760.0                   # the edge fixture's modelled year, 2030
PREFIX = "_qa_vf_"
PROJECTS: list[str] = []
STASH: dict = {}                          # scenario B → scenario I


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {msg}" if msg else ""))


def _cent(a, b) -> bool:
    return a is not None and b is not None and abs(a - b) < CENT


def _why(r) -> str:
    """A route's answer, only when it is not a success (a PASS stays one line)."""
    return "" if 200 <= r.status_code < 300 else f"{r.status_code} {r.text[:300]}"


def _project(tag: str) -> str:
    name = f"{PREFIX}{tag}"
    if name not in PROJECTS:
        PROJECTS.append(name)
    return name


def _lines(payload: dict, p: str = "_") -> list[dict]:
    return payload["periods"][p]["lines"]


def _site_view(ln: dict, party: str) -> float:
    """+ amount when `party` pays, − when it receives, 0 otherwise."""
    if same_party(ln["payer"], party):
        return float(ln["amount"])
    if same_party(ln["payee"], party):
        return -float(ln["amount"])
    return 0.0


# ── through the routes ─────────────────────────────────────────────────────


def _export_price_ref(client, tag: str, n) -> dict | None:
    price = n.links_t["ic_export_price"]["export"]
    r = client.post("/api/library/series", json={
        "name": f"qa_vf_px_{tag}", "timestamps": [t.isoformat() for t in n.snapshots],
        "values": [float(v) for v in price], "meta": {"source": "qa driver"}})
    return r.json() if r.status_code in (200, 201) else None


def _through_routes(label: str, tag: str, n, commercial: dict, template: str, *,
                    solver: dict | None = None, n_drafts: int | None = None,
                    fill: dict | None = None, edit=None):
    """template → drafts priced and confirmed → value-flows route → solve →
    `/results/value_flows`. Returns (network, cfg, payload, template body) or
    None when a route refused (already a FAIL)."""
    from tests.test_value_flow_templates import FILL

    fill = fill or FILL
    client = qa_support.client()
    qa_support.install_network(n, name=_project(tag))
    if "export_link" in commercial and "ic_export_price" in n.links_t:
        ref = _export_price_ref(client, tag, n)
        _step(f"{label}: export price stored in the Library", ref is not None)
        commercial["export_price_ref"] = ref
    r = client.put("/api/simulation/solver_config", json={"commercial": commercial})
    _step(f"{label}: config route accepts the commercial config", r.status_code == 200,
          _why(r))
    if solver:
        r = client.put("/api/simulation/solver_config", json=solver)
        _step(f"{label}: config route accepts the solver patch", r.status_code == 200,
              _why(r))

    r = client.post("/api/simulation/value_flows/template", json={"template": template})
    _step(f"{label}: template route builds {template}", r.status_code == 200, _why(r))
    if r.status_code != 200:
        return None
    body = r.json()
    if n_drafts is not None:
        _step(f"{label}: {n_drafts} draft contract(s)", len(body["draft_contracts"]) == n_drafts,
              f"{[d['id'] for d in body['draft_contracts']]}")
    _step(f"{label}: no dsr_not_enabled note",
          not any(x.startswith("dsr_not_enabled") for x in body["notes"]), f"{body['notes']}")
    if body["draft_contracts"]:
        stored = copy.deepcopy(client.get("/api/simulation/solver_config").json()["commercial"])
        stored.pop("value_flows", None)
        stored["contracts"] = [*stored["contracts"],
                               *({**d, **fill[d["type"]]} for d in body["draft_contracts"])]
        r = client.put("/api/simulation/solver_config", json={"commercial": stored})
        _step(f"{label}: the priced drafts are confirmed", r.status_code == 200, _why(r))
    vf = copy.deepcopy(body["config"])
    if edit is not None:
        vf = edit(vf)
    tag_ = client.get("/api/simulation/commercial/value_flows").json()["digest"]
    r = client.put("/api/simulation/commercial/value_flows", json={"value_flows": vf},
                   headers={"If-Match": tag_})
    _step(f"{label}: value-flows route stores the config", r.status_code == 200, _why(r))
    if r.status_code != 200:
        return None

    ctx = qa_support.session_context()
    cfg = ctx.solver_state["solver_config"]
    status, cond = run_simulation(cfg, ctx.network, ctx.mutation_lock, threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **kw: None)
    _step(f"{label}: solve is optimal", status in ("ok", "optimal"), f"{status}/{cond}")
    r = client.get("/api/results/value_flows")
    _step(f"{label}: /results/value_flows answers 200", r.status_code == 200,
          _why(r))
    if r.status_code != 200:
        return None
    return ctx.network, cfg, r.json(), body


def _independent_bridge(client, n, cfg, payload, p: str = "_") -> tuple[float, float, dict]:
    """(ledger side, bridge side, terms) of check 4, rebuilt from the routes on a
    flat network: participants' net outflow to externals = cost_breakdown total
    − every commercial_cost_terms item + external bill items + external
    connection fees + one-external contract lines − export-price revenue."""
    ids = [x["id"] for x in payload["participants"]]
    ext = payload["externals"]

    def side(party):
        if party is None:
            return None
        if any(same_party(party, i) for i in ids):
            return "internal"
        return "external" if any(same_party(party, e) for e in ext) else None

    lhs = 0.0
    for ln in _lines(payload, p):
        a, b = side(ln["payer"]), side(ln["payee"])
        if a == "internal" and b == "external":
            lhs += ln["amount"]
        elif a == "external" and b == "internal":
            lhs -= ln["amount"]

    terms: dict[str, float] = {}
    cb = client.get("/api/results/cost_breakdown").json()
    terms["cost_breakdown_total"] = float(cb["total"])
    items = commercial_cost_terms(n, cfg.commercial)["items"]
    terms["lp_rows"] = -float(sum(cx + ox for _l, _p, cx, ox in items))
    billing = client.get("/api/results/billing").json()
    payees = payload["provenance"]["tariff_payees"]
    per_item = ((billing.get("per_period") or {}).get(p) or {}).get("per_item_sampled") or {}
    terms["bill_external"] = float(sum(v for k, v in per_item.items()
                                       if side(payees.get(k)) == "external"))
    conn_payee = (cfg.commercial.get("value_flows") or {}).get("connection_fee_payee") or "dso"
    fee = 0.0
    if side(conn_payee) == "external":
        if n.meta.get(_conn.META_FEE):
            fee += sum(cx + ox for lab, _p, cx, ox in items if lab == "network_capacity")
        fixed = n.meta.get(_conn.META_FIXED_FEE)
        if fixed:
            fee += float(fixed.get("eur") or 0.0)
    terms["connection_external"] = fee
    contracts = 0.0
    for ln in billing["contracts"]["lines"]:
        if ln["value_stream"] in P._VOLUME_ONLY:
            continue
        a, b = side(ln["payer"]), side(ln["payee"])
        if a == "internal" and b == "external":
            contracts += ln["amount"]
        elif a == "external" and b == "internal":
            contracts -= ln["amount"]
    terms["contracts_external"] = contracts
    exp = 0.0
    if cfg.commercial.get("export_link") and cfg.commercial.get("export_price_ref"):
        link = cfg.commercial["export_link"]
        w = n.snapshot_weightings.objective.to_numpy(dtype=float)
        exp = float((w * n.links_t.p0[link].to_numpy(dtype=float)
                     * n.links_t["ic_export_price"][link].to_numpy(dtype=float)).sum())
    terms["export_revenue"] = -exp
    return lhs, float(sum(terms.values())), terms


def _four_checks(label: str, client, n, cfg, payload: dict) -> None:
    """Status ok; every period's four checks True; reconciliation to the cent by
    the route's check and by the driver's own bridge."""
    _step(f"{label}: status ok, conservation_ok True",
          payload.get("status") == "ok" and payload.get("conservation_ok") is True,
          f"status {payload.get('status')}, ok {payload.get('conservation_ok')}, "
          f"flags {[f for f in payload.get('flags', []) if 'not_established' in f or 'incomplete' in f]}")
    if payload.get("status") != "ok":
        return
    need = ("double_entry", "internal_nets_to_zero", "coverage", "reconciliation")
    for p, per in payload["periods"].items():
        checks = {c["name"]: c for c in per["conservation"]["checks"]}
        bad = {k: checks.get(k, {}).get("detail") for k in need
               if checks.get(k, {}).get("ok") is not True}
        _step(f"{label}: period {p} — all four checks True", not bad
              and per["conservation"]["ok"] is True, f"{bad}")
        rec = checks.get("reconciliation", {}).get("detail")
        diff = rec.get("difference") if isinstance(rec, dict) else None
        _step(f"{label}: period {p} — route reconciliation to the cent",
              diff is not None and abs(diff) < CENT, f"{rec}")
        lhs, rhs, terms = _independent_bridge(client, n, cfg, payload, p)
        _step(f"{label}: period {p} — ledger = cost_breakdown bridge rebuilt from the routes "
              "(to the cent)", _cent(lhs, rhs) and isinstance(rec, dict)
              and _cent(rhs, rec.get("bridge")),
              f"ledger {lhs:.4f}, bridge {rhs:.4f}, route bridge "
              f"{rec.get('bridge') if isinstance(rec, dict) else None}; "
              + ", ".join(f"{k} {v:.2f}" for k, v in terms.items()))
    _step(f"{label}: no ledger_incomplete / not_established flag",
          not any(f.startswith(("ledger_incomplete", "input_not_established"))
                  for f in payload["flags"]), f"{payload['flags']}")


def _e2e(case: str, label: str):
    """One case of the templates test's E2E table, through the routes."""
    from tests.test_value_flow_reconciliation import _network
    from tests.test_value_flow_templates import _HUB, E2E, _v1, _v1_hub

    name, contracts, hub, solver, n_drafts = E2E[case]
    n = _v1_hub() if hub else _network()
    commercial = _v1(copy.deepcopy(contracts), **(_HUB if hub else {}))
    if hub:
        commercial.pop("connection")
    got = _through_routes(label, case, n, commercial, name, solver=solver, n_drafts=n_drafts)
    if got is None:
        return None
    n, cfg, payload, body = got
    _four_checks(label, qa_support.client(), n, cfg, payload)
    status = payload["provenance"]["template"]["status"]
    _step(f"{label}: stored config reads as neither edited nor stale", status == [],
          f"{status}")
    if body["draft_contracts"]:
        did = body["draft_contracts"][0]["id"]
        _step(f"{label}: the confirmed draft {did} settles in the ledger",
              any(ln["contract_id"] == did and ln["amount"] is not None
                  for ln in _lines(payload)))
    return got


# ── A: V1 single_owner ─────────────────────────────────────────────────────


def scenario_a() -> None:
    print("\n[A] V1 single_owner through the routes (+ H corruptions on its ledger)")
    got = _e2e("single_owner", "A")
    if got is None:
        return
    n, cfg, payload, _body = got
    lines = _lines(payload)
    sources = {ln["source"] for ln in lines}
    _step("A: bill, connection, export price, contract and asset sources present",
          {"bill", "connection", "export_price", "contract", "asset"} <= sources, f"{sources}")
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    grid = [ln for ln in lines if ln["asset"] == "grid_supply"]
    hand = float((w * n.generators_t.p["grid_supply"].to_numpy(dtype=float)
                  * float(n.generators.at["grid_supply", "marginal_cost"])).sum())
    got_grid = sum(_site_view(ln, "site") for ln in grid)
    _step("A: commodity line site → market = Σ w·p·mc of grid_supply (to the cent)",
          bool(grid) and all(same_party(ln["payee"], "market") or same_party(ln["payer"],
                                                                                "market")
                             for ln in grid)
          and any("commodity_from_grid_side_generator" in ln["flags"] for ln in grid)
          and _cent(got_grid, hand), f"{got_grid:.4f} vs {hand:.4f}")
    cb = qa_support.client().get("/api/results/cost_breakdown").json()
    row = (cb.get("commercial") or {}).get("network_capacity")
    fee = [ln for ln in lines if ln["source"] == "connection"]
    _step("A: connection fee line site → dso = the network_capacity row (to the cent)",
          bool(fee) and all(ln["payer"] == "site" and ln["payee"] == "dso" for ln in fee)
          and _cent(sum(ln["amount"] for ln in fee), row),
          f"{[ln['amount'] for ln in fee]} vs {row}")
    scenario_h_corruptions(n, cfg)


# ── H: corruptions on the real ledger ──────────────────────────────────────


def scenario_h_corruptions(n, cfg) -> None:
    import routers.results as R
    from services.results.value_flows import value_flow_ledger

    got = value_flow_ledger(n, cfg, result_df=R._result_df)
    _step("H: the V1 ledger is rebuilt beside the route", got is not None)
    if got is None:
        return
    inputs, vf, ledger, res = got
    _step("H: the uncorrupted ledger passes", res.ok is True)

    def check(ledger_):
        r = P.check_conservation(ledger_, inputs, vf)
        checks = {c["name"]: c for c in r.periods["_"].checks}
        return r.ok, checks["coverage"], checks["reconciliation"]

    swapped = copy.deepcopy(ledger)
    ln = next((x for x in swapped.periods["_"] if x.tariff_item == "energy"
               and x.amount), None)
    _step("H: an import-energy bill line to swap exists", ln is not None)
    if ln is not None:
        ln.payer, ln.payee = ln.payee, ln.payer
        ok, cov, rec = check(swapped)
        _step("H: a swapped direction is detected (coverage and reconciliation False)",
              ok is False and cov["ok"] is False and rec["ok"] is False,
              f"ok {ok}; coverage {cov['detail'][:1]}; reconciliation {rec['detail']}")

    dropped = copy.deepcopy(ledger)
    before = len(dropped.periods["_"])
    dropped.periods["_"] = [x for x in dropped.periods["_"] if x.asset != "bess"]
    ok, cov, rec = check(dropped)
    _step("H: a dropped source (the BESS's cost lines) is detected "
          "(coverage and reconciliation False)",
          before > len(dropped.periods["_"]) and ok is False and cov["ok"] is False
          and rec["ok"] is False,
          f"dropped {before - len(dropped.periods['_'])}; ok {ok}; "
          f"coverage {cov['detail'][:1]}; reconciliation {rec['detail']}")


# ── B: V2 btm_ppa ──────────────────────────────────────────────────────────


PPA_BTM = {"type": "ppa", "id": "ppa_btm", "kind": "as_consumed_btm", "price": 20.0,
           "tenor_years": 15, "seller": "SunCo", "buyer": "site", "asset_ids": ["pv"]}
EAAS = {"type": "eaas", "id": "eaas1", "provider": "SunCo", "customer": "site",
        "fee_eur_per_mwh": 15.0, "tenor_years": 10, "asset_ids": ["bess"]}


def _bess_to_developer(vf: dict) -> dict:
    for o in vf["asset_owners"]:
        if o["asset_id"] == "bess":
            o["owner"] = "SunCo"
    return vf


def scenario_b() -> None:
    print("\n[B] V2 btm_ppa: as-consumed PPA + EaaS, export split by hand")
    from tests.test_value_flow_reconciliation import _network
    from tests.test_value_flow_templates import _v1

    n = _network()
    prof = 0.5 + 0.5 * np.cos(np.arange(len(n.snapshots)) / 96 * 2 * np.pi)
    n.add("Generator", "pv2", bus="site", carrier="solar", p_nom=15.0,
          p_max_pu=pd.Series(np.clip(prof, 0, 1), index=n.snapshots), marginal_cost=0.0)
    commercial = _v1([copy.deepcopy(PPA_BTM), copy.deepcopy(EAAS)])
    got = _through_routes("B", "v2", n, commercial, "btm_ppa", n_drafts=0,
                          edit=_bess_to_developer)
    if got is not None:
        n, cfg, payload, body = got
        client = qa_support.client()
        _four_checks("B", client, n, cfg, payload)
        roles = {x["id"]: x["role"] for x in payload["participants"]}
        _step("B: participants are the site (offtaker) and SunCo (developer)",
              roles == {"site": "offtaker", "SunCo": "developer"}, f"{roles}")
        _step("B: the bess re-assignment is disclosed as template_edited",
              payload["provenance"]["template"]["status"] == ["template_edited"],
              f"{payload['provenance']['template']['status']}")
        lines = _lines(payload)
        w = n.snapshot_weightings.objective.to_numpy(dtype=float)
        exp = n.links_t.p0["export"].to_numpy(dtype=float)
        price = n.links_t["ic_export_price"]["export"].to_numpy(dtype=float)
        g1 = np.clip(n.generators_t.p["pv"].to_numpy(dtype=float), 0, None)
        g2 = np.clip(n.generators_t.p["pv2"].to_numpy(dtype=float), 0, None)
        tot = g1 + g2
        share = np.where(tot > 0, g1 / np.where(tot > 0, tot, 1.0), 0.0)
        both = int(((exp > 1e-6) & (np.abs(price) > 0) & (tot > 0)).sum())
        _step("B: intervals with export, an export price and the feed-in item exist",
              both > 0, f"{both} intervals")
        _step("B: the shares really vary (pv and pv2 both run while exporting)",
              bool(((share > 0.05) & (share < 0.95) & (exp > 1e-6)).any()))
        hand_px = float((w * exp * price * share).sum())
        hand_fi = float((w * exp * 1000.0 * 0.01 * share).sum())
        got_px = sum(ln["amount"] for ln in lines if ln["source"] == "export_price"
                     and ln["payee"] == "SunCo")
        got_fi = sum(ln["amount"] for ln in lines if ln["tariff_item"] == "feed_in"
                     and ln["payee"] == "SunCo")
        _step("B: developer's export-price share = Σ w·exp·price·pv/(pv+pv2) (to the cent)",
              _cent(got_px, hand_px) and hand_px > 1.0, f"{got_px:.4f} vs {hand_px:.4f}")
        _step("B: developer's feed-in share = Σ w·exp·1000·0.01·pv/(pv+pv2) (to the cent)",
              _cent(got_fi, hand_fi) and hand_fi > 0.01, f"{got_fi:.4f} vs {hand_fi:.4f}")
        site_px = sum(ln["amount"] for ln in lines if ln["source"] == "export_price"
                      and ln["payee"] == "site")
        tot_px = float((w * exp * price).sum())
        _step("B: the site keeps the rest of the export price",
              _cent(site_px + got_px, tot_px), f"{site_px + got_px:.4f} vs {tot_px:.4f}")
        # The PPA as consumed: generation less its pro-rata share of export.
        pv = n.generators_t.p["pv"].to_numpy(dtype=float)
        total = pv + n.generators_t.p["pv2"].to_numpy(dtype=float)
        sh = np.divide(pv, total, out=np.zeros_like(pv), where=total > 0)
        consumed = pv - np.minimum(pv, np.clip(exp, 0, None) * sh)
        hand_ppa = 20.0 * float((w * consumed).sum())
        ppa = [ln for ln in lines if ln["contract_id"] == "ppa_btm"]
        _step("B: site → developer PPA = 20 × Σ w·(pv − its export share) (to the cent)",
              bool(ppa) and all(ln["payer"] == "site" and ln["payee"] == "SunCo" for ln in ppa)
              and _cent(sum(ln["amount"] for ln in ppa), hand_ppa),
              f"{sum(ln['amount'] for ln in ppa):.4f} vs {hand_ppa:.4f}")
        dis = np.clip(n.storage_units_t.p["bess"].to_numpy(dtype=float), 0, None)
        hand_eaas = 15.0 * float((w * dis).sum())
        eaas = [ln for ln in lines if ln["contract_id"] == "eaas1"]
        _step("B: site → developer EaaS = 15 × Σ w·BESS discharge (to the cent)",
              bool(eaas) and all(ln["payer"] == "site" and ln["payee"] == "SunCo"
                                 for ln in eaas)
              and _cent(sum(ln["amount"] for ln in eaas), hand_eaas) and hand_eaas > 1.0,
              f"{sum(ln['amount'] for ln in eaas):.4f} vs {hand_eaas:.4f}")
        costs = [ln for ln in lines if ln["source"] == "asset" and ln["asset"] in ("pv", "bess")]
        _step("B: the developer pays every PV and BESS cost line",
              {ln["asset"] for ln in costs} == {"pv", "bess"}
              and all(ln["payer"] == "SunCo" for ln in costs),
              f"{sorted({(ln['asset'], ln['payer']) for ln in costs})}")
        pv2 = [ln for ln in lines if ln["source"] == "asset" and ln["asset"] == "pv2"]
        _step("B: the site's own PV (pv2) stays with the site",
              all(ln["payer"] == "site" for ln in pv2))
        imports = [ln for ln in lines if ln["source"] == "bill" and ln["tariff_item"] != "feed_in"]
        _step("B: the site pays every import bill line",
              bool(imports) and all(ln["payer"] == "site" for ln in imports))
        dev = payload["periods"]["_"]["by_participant"].get("SunCo") or {}
        _step("B: the developer's totals are established",
              dev.get("net") is not None and dev.get("received") and dev.get("paid"),
              f"{dev}")
        qa_support.save_project(_project("v2"))
        STASH["v2"] = {"payload": payload, "value_flows": copy.deepcopy(
            cfg.commercial["value_flows"])}
    for case in ("btm_ppa", "btm_ppa_drafted"):
        _e2e(case, f"B/{case}")


# ── C: V3 landlord_tenant ──────────────────────────────────────────────────


def scenario_c() -> None:
    print("\n[C] V3 landlord_tenant: the landlord pays the assets, receives the lease")
    for case, landlord, payment, leased in (
            ("landlord_tenant", "Leasing GmbH", 120_000.0, {"bess"}),
            ("landlord_tenant_drafted", "landlord", 50_000.0, {"pv", "bess"})):
        label = f"C/{case}"
        got = _e2e(case, label)
        if got is None:
            continue
        n, _cfg, payload, _body = got
        lines = _lines(payload)
        roles = {x["id"]: x["role"] for x in payload["participants"]}
        _step(f"{label}: {landlord} is the landlord, the site the tenant",
              roles == {landlord: "landlord", "site": "tenant"}, f"{roles}")
        hours = float(n.snapshot_weightings.objective.sum())
        lease = [ln for ln in lines if ln["value_stream"] == "lease"]
        _step(f"{label}: tenant → landlord lease = {payment:g} × hours / 8760 (to the cent)",
              bool(lease) and all(ln["payer"] == "site" and ln["payee"] == landlord
                                  for ln in lease)
              and _cent(sum(ln["amount"] for ln in lease), payment * hours / HOURS_PER_YEAR),
              f"{sum(ln['amount'] for ln in lease):.4f}")
        costs = [ln for ln in lines if ln["source"] == "asset" and ln["asset"] in leased]
        _step(f"{label}: the landlord pays every cost line of {sorted(leased)}",
              {ln["asset"] for ln in costs} == leased
              and all(ln["payer"] == landlord for ln in costs),
              f"{sorted({(ln['asset'], ln['payer']) for ln in costs})}")
        bill = [ln for ln in lines if ln["source"] == "bill" and ln["tariff_item"] != "feed_in"]
        _step(f"{label}: the tenant pays the bill",
              bool(bill) and all(ln["payer"] == "site" for ln in bill))


# ── D: V4 dso_developer ────────────────────────────────────────────────────


def scenario_d() -> None:
    print("\n[D] V4 dso_developer: DSO → developer DR to the cent")
    from tests.test_value_flow_templates import FILL

    got = _e2e("dso_developer", "D")
    if got is None:
        return
    n, _cfg, payload, body = got
    lines = _lines(payload)
    roles = {x["id"]: x["role"] for x in payload["participants"]}
    _step("D: the site is the developer and the DSO a participant",
          roles == {"site": "developer", "dso": "dso"}, f"{roles}")
    (draft,) = body["draft_contracts"]
    fill = FILL["dr"]
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    hours = float(w.sum())
    dsr = n.buses_t["ic_dsr_p"]["site"].to_numpy(dtype=float) if "ic_dsr_p" in n.buses_t \
        else None
    dsr_mwh = float((w * dsr).sum()) if dsr is not None else None
    _step("D: the DSR dispatches (Σ w·DSR > 0)", dsr_mwh is not None and dsr_mwh > 1.0,
          f"{dsr_mwh} MWh")
    dr = {ln["value_stream"]: ln for ln in lines if ln["contract_id"] == draft["id"]}
    avail, act = dr.get("dr_availability"), dr.get("dr_activation")
    hand_avail = fill["availability_eur_per_mw_year"] * fill["contracted_mw"] * hours \
        / HOURS_PER_YEAR
    _step("D: DSO → developer availability = rate × MW × hours / 8760 (to the cent)",
          avail is not None and avail["payer"] == "dso" and avail["payee"] == "site"
          and _cent(avail["amount"], hand_avail),
          f"{avail and avail['amount']} vs {hand_avail:.4f}")
    hand_act = fill["activation_eur_per_mwh"] * dsr_mwh if dsr_mwh is not None else None
    _step("D: DSO → developer activation = rate × Σ w·DSR on the bus (to the cent)",
          act is not None and act["payer"] == "dso" and act["payee"] == "site"
          and _cent(act["amount"], hand_act), f"{act and act['amount']} vs {hand_act}")
    dso = payload["periods"]["_"]["by_participant"].get("dso") or {}
    _step("D: the DSO's totals include both DR payments",
          dso.get("paid") is not None and dso["paid"] >= hand_avail + (hand_act or 0) - CENT,
          f"{dso}")


# ── E: energy_hub, all four keys through the route ─────────────────────────


def scenario_e() -> None:
    print("\n[E] energy_hub through the routes; the four keys switched by the route")
    got = _e2e("energy_hub", "E")
    if got is None:
        return
    n, cfg, payload, body = got
    client = qa_support.client()
    members = sorted((m["participant"] for m in body["config"]["hub_members"]),
                     key=lambda x: x.strip().casefold())
    tariff = Tariff.model_validate(cfg.commercial["import_tariff"])
    metered = {it.id for it in tariff.items if HA.is_metered(it)}
    peak_items = {it.id for it in tariff.items if HA.is_peak_item(it)}
    mw = {members[0]: 40.0, members[1]: 10.0}
    shares = {members[0]: 0.7, members[1]: 0.3}
    billing = client.get("/api/results/billing").json()
    per_item = billing["per_period"]["_"]["per_item_sampled"]
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    exp_rev = float((w * n.links_t.p0["export"].to_numpy(dtype=float)
                     * n.links_t["ic_export_price"]["export"].to_numpy(dtype=float)).sum())
    shared = {f"bill:{k}": v for k, v in per_item.items() if v}
    shared["export_price:export_price"] = -exp_rev
    for basis in ("contracted_capacity", "fixed_shares", "energy", "peak_contribution"):
        vf = copy.deepcopy(body["config"])
        for m in vf["hub_members"]:
            m["contracted_mw"] = mw[m["participant"]]
        vf["allocation"] = {"basis": basis, **({"shares": shares}
                                               if basis == "fixed_shares" else {})}
        tag = client.get("/api/simulation/commercial/value_flows").json()["digest"]
        r = client.put("/api/simulation/commercial/value_flows", json={"value_flows": vf},
                       headers={"If-Match": tag})
        _step(f"E/{basis}: value-flows route stores the key", r.status_code == 200,
              _why(r))
        r = client.get("/api/results/value_flows")
        if r.status_code != 200:
            _step(f"E/{basis}: /results/value_flows answers", False, str(r.status_code))
            continue
        out = r.json()
        _four_checks(f"E/{basis}", client, n, cfg, out)
        split: dict[str, dict[str, float]] = {}
        for ln in _lines(out):
            if ln["source"] != "allocation":
                continue
            member, sign = (ln["payer"], 1.0) if ln["payee"] == "site" else (ln["payee"], -1.0)
            split.setdefault(ln["source_id"], {})[member] = sign * ln["amount"]
        missing = {sid: (amount, sum(split.get(sid, {}).values()))
                   for sid, amount in shared.items()
                   if not _cent(sum(split.get(sid, {}).values()), amount)}
        _step(f"E/{basis}: every shared item is split in full among the members",
              not missing, f"{missing}")
        def method(sid: str) -> str:
            item = sid.split(":", 1)[1] if sid.startswith("bill:") else None
            if item in metered:
                return "allocation:metered"
            if basis == "peak_contribution":
                return "allocation:peak_contribution" if item in peak_items \
                    else "allocation:energy"
            return f"allocation:{basis}"

        wrong = {ln["source_id"]: ln["flags"] for ln in _lines(out)
                 if ln["source"] == "allocation" and method(ln["source_id"]) not in ln["flags"]}
        _step(f"E/{basis}: linear import items metered, peak items by peak, the rest keyed",
              not wrong and set(split) >= set(shared), f"{wrong}")
        if basis in ("contracted_capacity", "fixed_shares"):
            weights = mw if basis == "contracted_capacity" else shares
            tot = sum(weights.values())
            off = {}
            for sid, amount in shared.items():
                if sid.startswith("bill:") and sid.split(":", 1)[1] in metered:
                    continue
                for m in members:
                    hand = amount * weights[m] / tot
                    if not _cent(split.get(sid, {}).get(m), hand):
                        off[(sid, m)] = (split.get(sid, {}).get(m), hand)
            _step(f"E/{basis}: every keyed share = amount × key / Σ key (to the cent)",
                  not off, f"{off}")


# ── F: V5 hand splits ──────────────────────────────────────────────────────


def scenario_f() -> None:
    print("\n[F] V5: three-member hub, every share under each key to the cent")
    from tests.test_value_flow_allocation import MEMBERS, V5, _shares, _v5_inputs, _vf

    inputs, group, hp = _v5_inputs()
    bad = {k: (group.per_item_sampled.get(k), v) for k, v in V5["bill"].items()
           if not _cent(group.per_item_sampled.get(k), v)}
    _step("F: the group bill is the working bill (every item to the cent)", not bad, f"{bad}")
    bad = {(m, item): (hp.metered[item][m], v) for m in MEMBERS
           for item, v in V5["metered"][m].items() if not _cent(hp.metered[item][m], v)}
    _step("F: linear import energy metered per member (to the cent)",
          not bad and set(hp.metered) == {"energy", "levy"}, f"{bad}")
    _step("F: member energy (MWh) is the working's",
          all(abs(hp.energy_mwh[m] - V5["energy_mwh"][m]) < 1e-9 for m in MEMBERS))
    for basis in ("contracted_capacity", "fixed_shares", "energy", "peak_contribution"):
        vf = _vf(basis, shares=V5["keys"]["fixed_shares"] if basis == "fixed_shares" else None)
        ledger = P.build_ledger(inputs, vf)
        got = _shares(ledger)
        want = V5["expected"][basis]
        off = {(sid, m): (got.get(sid, {}).get(m), v) for sid, ms in want.items()
               for m, v in ms.items() if not _cent(got.get(sid, {}).get(m, 0.0), v)}
        n_shares = sum(len(ms) for ms in want.values())
        _step(f"F: {basis} — all {n_shares} hand shares to the cent",
              set(got) == set(want) and not off, f"{off}")
        res = P.check_conservation(ledger, inputs, vf)
        _step(f"F: {basis} — the ledger still passes every check", res.ok is True,
              f"{[c for c in res.periods['_'].checks if c['ok'] is not True]}")


# ── G: V6 group net import ─────────────────────────────────────────────────


def scenario_g() -> None:
    print("\n[G] V6: group net-import term through the routes")
    from tests.test_group_net_import import v6_commercial, v6_network

    got = _through_routes("G", "v6", v6_network(), v6_commercial(), "single_owner")
    if got is None:
        return
    n, cfg, payload, _body = got
    client = qa_support.client()
    p0 = n.links_t.p0
    net = (p0["import"] + p0["import_b"] + p0["import_c"] - p0["export"]).to_numpy()
    both = ((p0["export"] > 1e-6) & ((p0["import"] + p0["import_b"]) > 1e-6)).any()
    _step("G: the group nets (export while members import) and net-exports at times",
          bool(both) and (net < -1e-6).any() and (net > 1e-6).any())
    cb = client.get("/api/results/cost_breakdown").json()
    row = (cb.get("commercial") or {}).get("energy_net_group")
    billing = client.get("/api/results/billing").json()
    per = billing["per_period"]["_"]
    billed = per["per_item"].get("net_energy")
    _step("G: LP row energy_net_group = billed net_energy (to the cent)",
          _cent(row, billed) and row > 1.0,
          f"row {row} vs billed {billed} (sampled {per['per_item_sampled'].get('net_energy')})")
    dec = client.get("/api/results/objective_decomposition").json()
    _step("G: objective gap 0", dec.get("gap_pct") is not None and abs(dec["gap_pct"]) < 1e-6,
          f"{dec.get('gap_pct')}")
    energy = billing["gap_summary"]["periods"]["_"].get("energy") or {}
    _step("G: the billing gap's energy kind is fully attributed, no gate",
          billing["gap_summary"]["gates"] == [] and energy.get("unattributed_pct") is not None
          and energy["unattributed_pct"] <= 1e-6,
          f"gates {billing['gap_summary']['gates']}, energy {energy}")
    _step("G: no simultaneous_import_export flag",
          "simultaneous_import_export" not in (cb["commercial"].get("flags") or []))
    _four_checks("G", client, n, cfg, payload)


# ── I: bundle round trip ───────────────────────────────────────────────────


def _same_ledger(a: dict, b: dict) -> tuple[bool, str]:
    la, lb = _lines(a), _lines(b)
    if la == lb:
        return True, f"{len(la)} lines identical"
    if len(la) != len(lb):
        return False, f"{len(la)} vs {len(lb)} lines"
    diffs = [(x["source"], x["source_id"], x["amount"], y["amount"])
             for x, y in zip(la, lb) if x != y]
    return False, f"{len(diffs)} differ, e.g. {diffs[:3]}"


def scenario_i() -> None:
    print("\n[I] Bundle round trip of the V2 project: value_flows and the ledger identical")
    if "v2" not in STASH:
        _step("I: the V2 project was saved by B", False)
        return
    client = qa_support.client()
    name, imported = _project("v2"), _project("v2_imported")
    before, vf0 = STASH["v2"]["payload"], STASH["v2"]["value_flows"]
    r = client.get(f"/api/projects/{name}")
    _step("I: the V2 project reloads", r.status_code == 200, str(r.status_code))
    cfg = qa_support.session_context().solver_state["solver_config"]
    _step("I: value_flows identical after save → load",
          (cfg.commercial or {}).get("value_flows") == vf0)
    after = client.get("/api/results/value_flows")
    ok, detail = _same_ledger(before, after.json()) if after.status_code == 200 else \
        (False, str(after.status_code))
    _step("I: the ledger is identical after save → load", ok, detail)

    r = client.get(f"/api/projects/{name}/bundle")
    _step("I: bundle exported", r.status_code == 200, str(r.status_code))
    r = client.post(f"/api/projects/import_bundle?name={imported}",
                    files={"file": ("b.zip", r.content, "application/zip")})
    body = r.json() if r.status_code in (200, 201) else {}
    _step("I: bundle imported with no library issues",
          r.status_code in (200, 201) and body.get("library_issues") == [],
          _why(r) or f"library_issues {body.get('library_issues')}")
    cfg = qa_support.session_context().solver_state["solver_config"]
    vf3 = (cfg.commercial or {}).get("value_flows")
    _step("I: value_flows identical after the bundle round trip", vf3 == vf0,
          "" if vf3 == vf0 else f"{vf3} vs {vf0}")
    r = client.get("/api/results/value_flows")
    out = r.json() if r.status_code == 200 else {}
    _step("I: the imported ledger closes",
          out.get("status") == "ok" and out.get("conservation_ok") is True,
          f"{r.status_code} {out.get('conservation_ok')} {out.get('flags')}")
    ok, detail = _same_ledger(before, out) if out else (False, "no payload")
    _step("I: the recomputed ledger is identical after the bundle round trip", ok, detail)
    _step("I: the flags are identical after the bundle round trip",
          out.get("flags") == before.get("flags"),
          f"{out.get('flags')} vs {before.get('flags')}")


def main() -> int:
    started = time.monotonic()
    for fn in (scenario_a, scenario_b, scenario_c, scenario_d, scenario_e, scenario_f,
               scenario_g, scenario_i):
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
    print(f"Total: {PASS + FAIL}  Pass: {PASS}  Fail: {FAIL}  "
          f"({time.monotonic() - started:.0f} s)")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
