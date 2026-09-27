"""
P14 — Energy Hub network tagging (plan 2026-09-25 P14; B11 / R5).

`eh_*` columns are custom (not PyPSA attributes). Before P14 a first write was
silently DROPPED by the D21 whitelist (PUT/POST) or refused by `_bulk`, so the
GUI could not tag an import Link, a PoC bus or a critical bus at all.
"""
from __future__ import annotations

import io

import pandas as pd
import pypsa
import pytest

from models.energy_hub import (
    EH_CONVERSION_ROLES,
    EH_CUSTOM_COLUMNS,
    EH_IMPORT_ROLES,
    EH_LINK_ROLES,
    ImportOverlaySpec,
)
from services.adequacy import archetypes as A
from services.adequacy import eh_columns as EC

BULK = "/api/network/_bulk"
BUS = {"name": "hub", "v_nom": 1.0, "carrier": "AC"}
LINK = {"name": "imp", "bus0": "grid", "bus1": "hub", "p_nom": 100.0}


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "hub", carrier="AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Load", "l", bus="hub", p_set=10.0)
    n.add("Link", "imp", bus0="grid", bus1="hub", p_nom=100.0)
    return n


# ── one shared role vocabulary (B11 / R5) ───────────────────────────────────


def test_every_role_consumer_uses_the_shared_vocabulary():
    from services.adequacy import levers, redundancy
    assert set(redundancy._IMPORT_ROLES) == set(EH_IMPORT_ROLES)
    assert tuple(redundancy._CONVERSION_ROLES) == tuple(EH_CONVERSION_ROLES)
    assert levers.IMPORT_ROLES == EH_IMPORT_ROLES
    assert "" in EH_LINK_ROLES
    assert set(EH_IMPORT_ROLES) | set(EH_CONVERSION_ROLES) <= set(EH_LINK_ROLES)
    assert "eh_n1_conversion" in EH_LINK_ROLES          # written by redundancy


def test_selection_rule_1_is_grid_import_only():
    n = _net()
    n.links["eh_role"] = ""
    n.links.at["imp", "eh_role"] = "eh_import"
    links, rule = A.select_import_links_with_rule(
        n, ImportOverlaySpec(import_carriers=[]))
    assert rule != "eh_role" and links == []


# ── CRUD: whitelisted keys create the column; others still dropped ──────────


def test_put_creates_a_bool_column_with_false_for_other_rows(
        client, install_network):
    n = _net()
    install_network(n)
    r = client.put("/api/network/buses/grid", json={
        "name": "grid", "v_nom": 1.0, "carrier": "AC", "eh_poc": True})
    assert r.status_code == 200, r.text
    assert n.buses["eh_poc"].dtype == bool
    assert bool(n.buses.at["grid", "eh_poc"]) is True
    assert bool(n.buses.at["hub", "eh_poc"]) is False


def test_put_link_role_and_float_columns(client, install_network):
    n = _net()
    install_network(n)
    r = client.put("/api/network/links/imp", json={**LINK, "eh_role": "grid_import"})
    assert r.status_code == 200, r.text
    assert n.links.at["imp", "eh_role"] == "grid_import"
    r = client.put("/api/network/buses/hub", json={
        **BUS, "eh_sk_mva": 250.0, "eh_ibr_mva": 40.0, "eh_critical": True})
    assert r.status_code == 200, r.text
    assert float(n.buses.at["hub", "eh_sk_mva"]) == 250.0
    assert pd.isna(n.buses.at["grid", "eh_sk_mva"])
    assert n.buses["eh_critical"].dtype == bool


def test_post_creates_whitelisted_columns(client, install_network):
    n = _net()
    install_network(n)
    r = client.post("/api/network/buses", json={
        "name": "crit", "v_nom": 1.0, "carrier": "AC", "eh_critical": True})
    assert r.status_code in (200, 201), r.text
    assert n.buses["eh_critical"].dtype == bool
    assert bool(n.buses.at["crit", "eh_critical"]) is True


@pytest.mark.parametrize("body,needle", [
    ({**LINK, "eh_role": "not_a_role"}, "eh_role"),
    ({**BUS, "name": "hub", "eh_sk_mva": -5.0}, "eh_sk_mva"),
    ({**BUS, "name": "hub", "eh_poc": "maybe"}, "eh_poc"),
])
def test_invalid_eh_values_are_422(client, install_network, body, needle):
    install_network(_net())
    url = ("/api/network/links/imp" if "bus0" in body
           else "/api/network/buses/hub")
    r = client.put(url, json=body)
    assert r.status_code == 422, r.text
    assert needle in r.text


def test_non_whitelisted_custom_keys_are_still_dropped(client, install_network):
    n = _net()
    install_network(n)
    r = client.put("/api/network/buses/hub", json={**BUS, "eh_not_a_tag": 1})
    assert r.status_code == 200
    assert "eh_not_a_tag" not in n.buses.columns


# ── bulk ────────────────────────────────────────────────────────────────────


def test_bulk_creates_the_whitelisted_column(client, install_network):
    n = _net()
    install_network(n)
    r = client.patch(BULK, json={"component_class": "Bus", "names": ["hub"],
                                 "updates": {"eh_critical": True}})
    assert r.status_code == 200, r.text
    assert n.buses["eh_critical"].dtype == bool
    assert bool(n.buses.at["hub", "eh_critical"]) is True
    r = client.patch(BULK, json={"component_class": "Link", "names": ["imp"],
                                 "updates": {"eh_role": "grid_import"}})
    assert r.status_code == 200, r.text
    assert n.links.at["imp", "eh_role"] == "grid_import"


def test_bulk_refuses_an_unknown_role_and_unknown_columns(client, install_network):
    install_network(_net())
    r = client.patch(BULK, json={"component_class": "Link", "names": ["imp"],
                                 "updates": {"eh_role": "nope"}})
    assert r.status_code == 422, r.text
    r = client.patch(BULK, json={"component_class": "Bus", "names": ["hub"],
                                 "updates": {"eh_not_a_tag": True}})
    assert r.status_code == 400


# ── persistence: dtype survives netCDF and Excel ────────────────────────────


def test_netcdf_round_trip_keeps_bool_and_role_dtypes(tmp_path):
    from services.pypsa_service import PyPSAService
    n = _net()
    n.buses["eh_poc"] = pd.Series([None, True], index=n.buses.index, dtype=object)
    n.links["eh_role"] = "grid_import"
    path = tmp_path / "eh.nc"
    PyPSAService.export_network_to_netcdf(n, path)       # object-bool → bool
    m = pypsa.Network()
    PyPSAService.import_network_from_netcdf(m, path)
    assert m.buses["eh_poc"].dtype == bool
    assert m.buses["eh_poc"].tolist() == [False, True]
    assert m.links.at["imp", "eh_role"] == "grid_import"


def test_excel_import_normalises_eh_columns(client, install_network):
    import openpyxl
    install_network(_net())
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "buses"
    ws.append(["name", "carrier", "eh_poc", "eh_critical"])
    ws.append(["grid", "AC", "TRUE", None])
    ws.append(["hub", "AC", None, 1])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/io/import/excel", files={"file": (
        "n.xlsx", buf.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200, r.text
    # Read back through the API: the import replaced the session's network.
    rows = {b["name"]: b for b in client.get("/api/network/buses").json()}
    assert rows["grid"]["eh_poc"] is True and rows["hub"]["eh_poc"] is False
    assert rows["grid"]["eh_critical"] is False
    assert rows["hub"]["eh_critical"] is True


def test_normaliser_is_idempotent_and_only_touches_present_columns():
    n = _net()
    EC.normalise_eh_columns(n)
    assert "eh_poc" not in n.buses.columns          # never invents columns
    n.buses["eh_critical"] = ["yes", "0"]
    EC.normalise_eh_columns(n)
    EC.normalise_eh_columns(n)
    assert n.buses["eh_critical"].tolist() == [True, False]


def test_whitelist_shape():
    assert set(EH_CUSTOM_COLUMNS) == {"Bus", "Link"}
    assert set(EH_CUSTOM_COLUMNS["Bus"]) == {
        "eh_poc", "eh_critical", "eh_sk_mva", "eh_ibr_mva"}
    assert set(EH_CUSTOM_COLUMNS["Link"]) == {"eh_role"}


# ── readiness preflight: predicts what the driver then does ─────────────────


def _study(n, pack, **kw):
    import queue
    import threading

    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig
    PyPSAService.set_network(n)
    return S.run_eh_study(n, pack, SolverConfig(voll=3000.0),
                          lock=PyPSAService.get_lock(),
                          stop_event=threading.Event(),
                          log_queue=queue.SimpleQueue(), **kw)


@pytest.mark.live_solve
@pytest.mark.parametrize("budget", [30, 4])
def test_readiness_exact_estimates_match_the_run(budget):
    from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
    from services.adequacy.eh_readiness import eh_readiness
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0)})
    ready = eh_readiness(_feeder_hub(), pack, budget_solves=budget, voll=3000.0)
    report = _study(_feeder_hub(), pack, budget_solves=budget)
    actual = {r.stage: r for r in report.pipeline.stages}
    for row in ready["stages"]:
        rec = actual[row["stage"]]
        if row["prediction"] == "not_requested":
            assert rec.status == "skipped", row
        elif row["basis"] == "exact" and row["prediction"] == "run":
            assert rec.status == "run", (row, rec)
            assert rec.solves_charged == row["solves"], (row, rec)
        elif row["prediction"] in ("skipped_budget", "not_established",
                                   "may_skip_budget"):
            assert rec.status == "skipped", (row, rec)
            assert rec.solves_charged == 0
    assert ready["class_b"]["k"] == report.sections["fmea_top"].payload["k_links"] \
        if report.completeness["fmea_top"] == "ok" else True
    if budget == 4:
        fmea = next(r for r in ready["stages"] if r["stage"] == "fmea_top")
        assert fmea["prediction"] == "skipped_budget"


def test_readiness_reports_the_boundary_and_dtc_derivability():
    from models.energy_hub import default_weak_flexible_pack
    from services.adequacy.eh_readiness import eh_readiness
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    ready = eh_readiness(_feeder_hub(), default_weak_flexible_pack(),
                         budget_solves=30, voll=3000.0)
    assert ready["import"] == {"rule": "eh_role", "links": ["import"],
                               "applied": True}
    assert ready["critical_buses"] == ["hub"]
    assert ready["dtc"]["derivable"] is True
    assert ready["mc_boundary"]["ok"] is True
    assert "remote" in ready["mc_boundary"]["removed_generators"]
    assert ready["class_b"]["k"] == 5
    assert ready["scr"]["status"] == "not_established"      # no eh_sk_mva
    assert ready["storage_units"] == 1


def test_readiness_explains_untagged_networks():
    from models.energy_hub import default_weak_flexible_pack
    from services.adequacy.eh_readiness import eh_readiness
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    n = _feeder_hub()
    n.links["eh_role"] = ""
    n.buses["eh_critical"] = False
    ready = eh_readiness(n, default_weak_flexible_pack(), budget_solves=30)
    assert ready["import"]["rule"] == "carrier"
    assert ready["dtc"]["derivable"] is False
    assert "eh_critical" in ready["dtc"]["reason"]
    assert ready["mc_boundary"]["ok"] is False
    assert "eh_role" in ready["mc_boundary"]["error"]


def test_readiness_http(client, install_network):
    from tests.test_energy_hub_frontier_fmea import _feeder_hub
    install_network(_feeder_hub())
    r = client.get("/api/results/eh_readiness",
                   params={"archetype": "off_grid", "budget_solves": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archetype"] == "off_grid"
    assert body["class_b"]["closed_import_links"] == ["import"]
    assert sum(row["solves"] for row in body["stages"]) == body["estimated_solves"]
    assert client.get("/api/results/eh_readiness",
                      params={"archetype": "nope"}).status_code == 422
    assert client.get("/api/results/eh_readiness", params={
        "archetype": "off_grid", "stages": "bogus"}).status_code == 422


# ── P14 gate: readiness mirrors the driver's skips and failures ─────────────


def _row(ready, stage):
    return next(r for r in ready["stages"] if r["stage"] == stage)


@pytest.mark.parametrize("voll", [None, 0.0])
def test_readiness_without_voll_predicts_frontier_and_fmea_not_established(voll):
    """The session default VOLL is 0: the frontier raises its config error
    and the Class-B sweep refuses, so neither may read as 'run'."""
    from models.energy_hub import AvailabilityTarget, default_strong_grid_pack
    from services.adequacy.eh_readiness import eh_readiness
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=5000.0)})
    ready = eh_readiness(_feeder_hub(), pack, budget_solves=30, voll=voll)
    for stage in ("frontier", "fmea_top"):
        row = _row(ready, stage)
        assert row["prediction"] == "not_established", row
        assert row["solves"] == 0 and "VOLL" in row["reason"]


def test_readiness_mc_certify_follows_the_hub_boundary():
    from models.energy_hub import default_weak_flexible_pack
    from services.adequacy.eh_readiness import eh_readiness
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    ok = eh_readiness(_feeder_hub(), default_weak_flexible_pack(),
                      budget_solves=30, voll=3000.0)
    assert _row(ok, "mc_certify")["prediction"] == "run"
    n = _feeder_hub()
    n.links["eh_role"] = ""                     # carrier rule → boundary refused
    bad = eh_readiness(n, default_weak_flexible_pack(), budget_solves=30,
                       voll=3000.0)
    row = _row(bad, "mc_certify")
    assert bad["mc_boundary"]["ok"] is False
    assert row["prediction"] == "not_established"
    assert row["reason"] == bad["mc_boundary"]["error"]


def test_readiness_pack_apply_failure_fails_apply_pack_and_reaches_nothing():
    from models.energy_hub import default_weak_flexible_pack
    from services.adequacy.eh_readiness import eh_readiness

    n = _net()
    n.remove("Link", "imp")                     # weak_flexible needs an import
    ready = eh_readiness(n, default_weak_flexible_pack(), budget_solves=30,
                         voll=3000.0)
    assert ready["import"]["applied"] is False
    assert _row(ready, "apply_pack")["prediction"] == "fails"
    for row in ready["stages"]:
        if row["stage"] != "apply_pack" and row["prediction"] != "not_requested":
            assert row["prediction"] == "not_reached", row
    assert ready["estimated_solves"] == 0


def test_readiness_levers_use_the_levers_import_selector(monkeypatch):
    """The levers stage picks import Links with its own selector, not §6."""
    from models.energy_hub import default_strong_grid_pack
    from services.adequacy import levers as lev
    from services.adequacy.eh_readiness import eh_readiness
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    pack = default_strong_grid_pack().model_copy(
        update={"levers": default_strong_grid_pack().levers.model_copy(
            update={"import_cap": True, "storage_duration": True})})
    stages = ["apply_pack", "ens_solve", "levers"]
    both = eh_readiness(_feeder_hub(), pack, budget_solves=30, voll=3000.0,
                        stages=stages)
    assert _row(both, "levers")["solves"] == (
        len(lev.DEFAULT_STORAGE_HOURS) + len(lev.DEFAULT_IMPORT_CAPS_MW))
    monkeypatch.setattr(lev, "_import_link_ids", lambda n: [])
    storage_only = eh_readiness(_feeder_hub(), pack, budget_solves=30,
                                voll=3000.0, stages=stages)
    assert _row(storage_only, "levers")["solves"] == len(lev.DEFAULT_STORAGE_HOURS)


def test_readiness_budget_skip_after_an_upper_bound_is_only_may_skip():
    from models.energy_hub import default_weak_flexible_pack
    from services.adequacy.eh_readiness import eh_readiness
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    ready = eh_readiness(
        _feeder_hub(), default_weak_flexible_pack(), budget_solves=4,
        voll=3000.0, stages=["apply_pack", "ens_solve", "redundancy",
                             "levers", "dtc_stress"])
    assert _row(ready, "redundancy")["prediction"] == "run"
    assert _row(ready, "redundancy")["basis"] == "upper_bound"
    later = [_row(ready, s)["prediction"] for s in ("levers", "dtc_stress")]
    assert "skipped_budget" not in later
    assert "may_skip_budget" in later


def test_numpy_bools_survive_normalisation():
    import numpy as np
    n = _net()
    n.buses["eh_critical"] = pd.Series([True, None], index=n.buses.index,
                                       dtype=object)
    n.buses.at["grid", "eh_critical"] = np.True_
    EC.normalise_eh_columns(n)
    assert n.buses["eh_critical"].dtype == bool
    assert n.buses["eh_critical"].tolist() == [True, True]
    obj = pd.Series([True, None, np.True_, np.int64(0), np.int64(1)],
                    dtype=object)
    assert [EC._as_bool(v) for v in obj] == [True, False, True, False, True]


def test_bulk_refused_batch_leaves_no_stray_column(client, install_network):
    n = _net()
    install_network(n)
    r = client.patch(BULK, json={"component_class": "Link", "names": ["imp"],
                                 "updates": {"eh_role": "nope"}})
    assert r.status_code == 422
    assert "eh_role" not in n.links.columns


@pytest.mark.parametrize("path", ["put", "bulk"])
def test_internal_roles_are_refused_through_the_api(client, install_network, path):
    install_network(_net())
    if path == "bulk":
        r = client.patch(BULK, json={"component_class": "Link", "names": ["imp"],
                                     "updates": {"eh_role": "eh_n1_conversion"}})
    else:
        r = client.put("/api/network/links/imp",
                       json={**LINK, "eh_role": "eh_n1_conversion"})
    assert r.status_code == 422, r.text
    assert "redundancy" in r.text


def test_readiness_route_computes_outside_the_network_lock(
        client, install_network, monkeypatch):
    from services.adequacy import eh_readiness as R
    from services.pypsa_service import PyPSAService
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    install_network(_feeder_hub())
    seen = {}
    real = R.eh_readiness

    def spy(network, *a, **kw):
        import threading
        lock = PyPSAService.get_lock()           # an RLock: probe off-thread

        def probe():
            got = lock.acquire(blocking=False)
            seen["free"] = got
            if got:
                lock.release()
        t = threading.Thread(target=probe)
        t.start()
        t.join()
        seen["shared"] = network is PyPSAService.get_network()
        return real(network, *a, **kw)

    monkeypatch.setattr(R, "eh_readiness", spy)
    r = client.get("/api/results/eh_readiness", params={"archetype": "off_grid"})
    assert r.status_code == 200, r.text
    assert seen == {"free": True, "shared": False}


# ── P24: additive readiness keys for the hub-design Site / Goal cards ───────

_PRE_P24_READINESS_KEYS = {
    "archetype", "pack_hash", "import", "critical_buses", "dtc", "scr",
    "storage_units", "class_b", "mc_boundary", "budget_solves",
    "estimated_solves", "stages", "warnings",
}
_P24_READINESS_KEYS = {"pack_defaults", "outage_units", "import_p_nom_mw"}


def _datacenter_readiness(client, install_network, **over):
    import json
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                           / "project_templates"))
    import eh_templates as TPL
    n = TPL.build_eh_datacenter()
    install_network(n)
    meta = TPL.TEMPLATE_META["eh_datacenter"]
    overrides = {**meta["pack_overrides"], **over}
    r = client.get("/api/results/eh_readiness", params={
        "archetype": meta["recommended_archetype"],
        "pack_overrides": json.dumps(overrides)})
    assert r.status_code == 200, r.text
    return r.json(), TPL.build_eh_datacenter(), meta, overrides


def test_readiness_p24_keys_are_additive(client, install_network):
    body, _n, _meta, _ov = _datacenter_readiness(client, install_network)
    assert set(body) == _PRE_P24_READINESS_KEYS | _P24_READINESS_KEYS
    # the pre-existing keys keep their shape and values on the template
    assert body["archetype"] == "weak_flexible"
    assert body["import"]["links"] == ["grid_import"]
    assert body["import"]["applied"] is True
    assert set(body["import"]) == {"rule", "links", "applied"}
    assert set(body["scr"]) == {"status", "note", "min_scr"}
    assert set(body["class_b"]) == {"k", "closed_import_links", "error"}
    assert sum(r["solves"] for r in body["stages"]) == body["estimated_solves"]


def test_readiness_pack_defaults_follow_the_overridden_pack(
        client, install_network):
    from services.adequacy.eh_study_runner import (
        _PACK_FACTORY,
        apply_pack_overrides,
    )
    body, _n, meta, ov = _datacenter_readiness(client, install_network)
    pack = apply_pack_overrides(
        _PACK_FACTORY[meta["recommended_archetype"]](), ov)
    av = pack.availability
    assert body["pack_defaults"] == {
        "target_lole_h": av.target_lole_h,
        "ens_cap_permyriad": av.ens_cap_permyriad,
        "certification_metric": av.certification_metric,
    }
    # an override is what the study would run, so it is what readiness shows
    body2, *_ = _datacenter_readiness(client, install_network,
                                      target_lole_h=7.5)
    assert body2["pack_defaults"]["target_lole_h"] == 7.5
    assert body2["pack_defaults"]["target_lole_h"] != av.target_lole_h


def test_readiness_outage_units_count_via_resolve_outage_params(
        client, install_network):
    import json
    import math

    from services.adequacy.occurrence import resolve_outage_params
    from services.profile_shapes import _gen_category

    _body, n, _meta, _ov = _datacenter_readiness(client, install_network)
    # a rate without a repair time (and no carrier default to lend one) is
    # not usable outage data
    n.add("Generator", "odd_unit", bus=n.buses.index[0], carrier="mystery",
          p_nom=1.0)
    n.generators.at["odd_unit", "outage_rate_value"] = 0.1
    n.generators.at["odd_unit", "mttr_hours"] = float("nan")
    install_network(n)
    body = client.get("/api/results/eh_readiness", params={
        "archetype": "weak_flexible",
        "pack_overrides": json.dumps(_ov)}).json()
    by_class = {}
    for cls, attr in (("Generator", "generators"), ("Link", "links"),
                      ("StorageUnit", "storage_units")):
        p = resolve_outage_params(n, attr)
        by_class[cls] = int(sum(
            1 for name in p.index
            if math.isfinite(float(p.at[name, "rate"]))
            and math.isfinite(float(p.at[name, "mttr_hours"]))))
    ou = body["outage_units"]
    assert ou["by_class"] == by_class
    assert ou["count"] == sum(by_class.values())
    assert ou["count"] > 0
    assert ou["missing"] == []          # the template rates every thermal unit

    # untag a conventional generator's outage data: it is listed as missing
    gens = n.generators
    thermal = [g for g in gens.index
               if _gen_category(str(gens.at[g, "carrier"])) == "conventional"]
    assert thermal
    victim = thermal[0]
    n.generators.at[victim, "outage_rate_value"] = float("nan")
    n.generators.at[victim, "carrier"] = "diesel_unrated"
    install_network(n)
    r = client.get("/api/results/eh_readiness", params={
        "archetype": "weak_flexible",
        "pack_overrides": json.dumps(_ov)})
    ou2 = r.json()["outage_units"]
    assert {"class": "Generator", "name": victim} in ou2["missing"]
    assert ou2["count"] == ou["count"] - 1


def test_readiness_import_p_nom_mw_sums_the_import_links(
        client, install_network):
    body, n, _meta, ov = _datacenter_readiness(client, install_network)
    links = body["import"]["links"]
    assert links
    # the pack's import overlay (import_p_nom_mw = 40) is what would run
    assert body["import_p_nom_mw"] == pytest.approx(ov["import_p_nom_mw"])
    # ... also when it differs from the rating on the network itself
    raw = float(n.links.loc[links, "p_nom"].sum())
    capped, *_ = _datacenter_readiness(client, install_network,
                                       import_p_nom_mw=25.0)
    assert raw != 25.0
    assert capped["import_p_nom_mw"] == pytest.approx(25.0)

    from services.adequacy.eh_readiness import eh_readiness
    from tests.test_energy_hub_frontier_fmea import _feeder_hub
    from models.energy_hub import default_strong_grid_pack
    fh = _feeder_hub()
    ready = eh_readiness(fh, default_strong_grid_pack(), budget_solves=10)
    assert ready["import_p_nom_mw"] == pytest.approx(
        float(fh.links.loc[ready["import"]["links"], "p_nom"].sum()))
    none = eh_readiness(_net_no_import(), default_strong_grid_pack(),
                        budget_solves=10)
    assert none["import"]["links"] == []
    assert none["import_p_nom_mw"] is None


def _net_no_import() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "hub", carrier="AC")
    n.add("Load", "l", bus="hub", p_set=10.0)
    n.add("Generator", "g", bus="hub", carrier="gas", p_nom=20.0)
    return n
