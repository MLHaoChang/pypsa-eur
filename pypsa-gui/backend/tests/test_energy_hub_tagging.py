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
        elif row["prediction"] in ("skipped_budget", "not_established"):
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
