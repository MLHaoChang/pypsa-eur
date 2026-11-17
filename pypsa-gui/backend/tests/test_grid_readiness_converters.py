"""
DC grid-readiness walkthrough 03 and 05: what the scorecard credits to the
site's converters.

03: under an explicitly set power architecture, a unit behind it (the UPS, or
any unit on a critical bus the architecture feeds) reaches the grid only
through the architecture's own converters, so its island-study services are
not credited at the PoC: the architecture's own (assumed) services stand in.
The frequency-support row and the compare's two service columns name the
architecture and the unit each credited service comes from.

05: the SCR's inverter capacity is the one stated on the PoC bus
(``eh_ibr_mva``) when there is one, else derived from the network's
converter-coupled assets (inverter-based generators and storage, not those
behind the UPS on a critical bus). The row states the capacity it used and
where it comes from, and says when a stated capacity is below the converter
assets the network now holds.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pypsa
import pytest

from services.adequacy import grid_readiness as G
from services.adequacy import scr_gate as S

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

PROFILE = "eu_rfg_dcc_ce"


def _dc_project(tmp_path, monkeypatch, client, project_row, name, dc_load):
    """A DC project created through the route (the backup mode's strings and
    island sidecar as a user gets them), and its saved network."""
    from routers import projects as projects_router
    from services import project_registry

    src = tmp_path / "eh_datacenter"
    if not src.exists():
        src.mkdir()
        T.BUILDERS["eh_datacenter"]().export_to_netcdf(str(src / "network.nc"))
        T.write_sidecars(src, "eh_datacenter")
        (tmp_path / "eh_templates.py").write_text(pathlib.Path(T.__file__).read_text())
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    r = client.post("/api/projects/from_template/eh_datacenter", params={"name": name},
                    json={"dc_load": dc_load})
    assert r.status_code == 200, r.text
    d = pathlib.Path(project_registry.project_dir(project_row(r.json().get("name", name))))
    n = pypsa.Network()
    n.import_from_netcdf(str(d / "network.nc"))
    return d, n


def _bess(**kw) -> dict:
    """The AC project on battery strings, its IT load scaled so the batteries mode is accepted."""
    pre = T.dc_load_preset("eh_datacenter")
    return {**pre, "phases": [{**p, "it_mw": p["it_mw"] * 0.6} for p in pre["phases"]],
            "architecture": "ac", "backup": {"mode": "bess"}, **kw}


def _rows(d, n) -> dict:
    return {r["id"]: r for r in G.grid_readiness(d, n, PROFILE)["rows"]}


# ── 03: services behind the power architecture ─────────────────────────────


def test_an_ac_lv_ups_site_on_gensets_gets_no_frequency_support_from_its_ups_side(
        client, tmp_path, monkeypatch, tmp_projects_dir, project_row):
    d, n = _dc_project(tmp_path, monkeypatch, client, project_row, "dc_ac",
                       {"architecture": "ac"})
    row = _rows(d, n)["frequency_support"]
    assert row["verdict"] == "warn"
    assert "grid forming" not in row["project_value"]
    assert "AC (LV UPS)" in row["note"]
    # The units behind the architecture are named as not credited.
    assert "bess_new" in row["note"] and "ups_battery" in row["note"]


def test_a_grid_facing_battery_string_still_offers_frequency_support_and_is_named(
        client, tmp_path, monkeypatch, tmp_projects_dir, project_row):
    d, n = _dc_project(tmp_path, monkeypatch, client, project_row, "dc_ac_bess",
                       _bess())
    row = _rows(d, n)["frequency_support"]
    assert row["verdict"] == "pass"
    assert "backup_bess" in row["project_value"]
    assert "AC (LV UPS)" in row["note"]


def test_an_unset_architecture_credits_the_island_units_as_before(tmp_path):
    d = tmp_path / "p"
    n = T.BUILDERS["eh_datacenter"]()
    T.write_sidecars(d, "eh_datacenter")
    row = _rows(d, n)["frequency_support"]
    assert row["verdict"] == "pass" and "grid forming" in row["project_value"]


def test_the_reactive_range_counts_no_unit_behind_the_architecture(
        client, tmp_path, monkeypatch, tmp_projects_dir, project_row):
    d, n = _dc_project(tmp_path, monkeypatch, client, project_row, "dc_ac_q",
                       {"architecture": "ac"})
    row = _rows(d, n)["reactive_range"]
    assert "bess_new" not in row["source"] and "bess_new" not in row["note"]


def test_the_compare_service_columns_agree_with_the_ac_rows(
        client, tmp_path, monkeypatch, tmp_projects_dir, project_row):
    from services import dc_architecture as A

    d, n = _dc_project(tmp_path, monkeypatch, client, project_row, "dc_ac_cmp",
                       {"architecture": "ac"})
    out = A.compare_project(d, T, energy_price_per_mwh=80.0)
    ac = next(v for v in out["variants"] if v["architecture"] == "ac")
    assert ac["services"]["counted"] == []
    assert ac["verdicts"]["frequency_support"]["verdict"] == "warn"
    vc = ac["verdicts"]["voltage_control"]
    # Voltage control here is the rooftop PV's own, named as such, not the architecture's.
    assert vc["verdict"] == "pass" and "rooftop_pv" in vc["project_value"]
    row = _rows(d, n)["frequency_support"]
    assert ac["verdicts"]["frequency_support"]["project_value"] == row["project_value"]


# ── 05: the SCR's inverter capacity ────────────────────────────────────────


def _site(ibr=None, critical_bess=0.0):
    n = pypsa.Network()
    n.set_snapshots(range(2))
    for b in ("grid", "mv", "it"):
        n.add("Bus", b)
    n.buses["eh_poc"] = False
    n.buses["eh_critical"] = False
    n.buses["eh_sk_mva"] = float("nan")
    n.buses["eh_ibr_mva"] = float("nan")
    n.buses.at["grid", "eh_poc"] = True
    n.buses.at["it", "eh_critical"] = True
    n.buses.at["grid", "eh_sk_mva"] = 250.0
    if ibr is not None:
        n.buses.at["grid", "eh_ibr_mva"] = ibr
    n.add("Link", "grid_import", bus0="grid", bus1="mv", p_nom=40.0)
    n.add("Link", "tx", bus0="mv", bus1="it", p_nom=80.0)
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=500.0)
    n.add("Generator", "pv", bus="mv", carrier="solar", p_nom=4.0)
    n.add("Generator", "genset", bus="mv", carrier="gas", p_nom=10.0)
    n.add("StorageUnit", "strings", bus="mv", carrier="battery", p_nom=45.0)
    if critical_bess:
        n.add("StorageUnit", "ups", bus="it", carrier="battery", p_nom=critical_bess)
    return n


def test_without_a_stated_capacity_the_gate_derives_it_from_inverters_and_storage():
    _, status, payload, _ = S.evaluate_network_scr_gate(_site(critical_bess=40.0))
    assert status == "ok"
    row = payload["buses"][0]
    assert row["ibr_mva"] == pytest.approx(49.0)          # PV 4 + strings 45; not the UPS, not the genset
    assert row["ibr_source"] == "derived"
    assert row["ibr_assets"] == {"pv": 4.0, "strings": 45.0}
    assert payload["min_scr"] == pytest.approx(250.0 / 49.0)


def test_a_stated_capacity_wins_and_reports_the_converters_it_leaves_out():
    _, _, payload, _ = S.evaluate_network_scr_gate(_site(ibr=30.0))
    row = payload["buses"][0]
    assert row["ibr_mva"] == 30.0 and row["ibr_source"] == "stated"
    assert row["ibr_derived_mva"] == pytest.approx(49.0)


def test_two_pocs_without_stated_capacities_are_not_derived():
    n = _site()
    n.buses.at["mv", "eh_poc"] = True
    n.buses.at["mv", "eh_sk_mva"] = 200.0
    gate, status, _, note = S.evaluate_network_scr_gate(n)
    assert gate is None and status == "not_established"
    assert "eh_ibr_mva" in note


def test_the_dc_template_derives_its_inverter_capacity():
    n = T.BUILDERS["eh_datacenter"]()
    assert n.buses["eh_ibr_mva"].isna().all()


def test_battery_strings_lower_the_scr_and_the_row_states_the_capacity(
        client, tmp_path, monkeypatch, tmp_projects_dir, project_row):
    d1, n1 = _dc_project(tmp_path, monkeypatch, client, project_row, "dc_g", {"architecture": "ac"})
    d2, n2 = _dc_project(tmp_path, monkeypatch, client, project_row, "dc_b",
                         _bess())
    g, b = _rows(d1, n1)["scr"], _rows(d2, n2)["scr"]
    assert b["project_value"] < g["project_value"]
    strings = sum(float(n2.storage_units.at[s, "p_nom"]) for s in n2.storage_units.index
                  if s.startswith("backup_bess"))
    assert b["project_value"] == pytest.approx(250.0 / (4.0 + strings), rel=1e-3)
    assert "inverter capacity" in b["note"] and "derived" in b["note"] and "assumed" in b["note"]


def test_a_stated_capacity_below_the_converters_says_it_predates_them(
        client, tmp_path, monkeypatch, tmp_projects_dir, project_row):
    d, n = _dc_project(tmp_path, monkeypatch, client, project_row, "dc_old",
                       _bess())
    n.buses.at["grid", "eh_ibr_mva"] = 30.0                  # a project saved before the derivation
    row = _rows(d, n)["scr"]
    assert row["project_value"] == pytest.approx(250.0 / 30.0, rel=1e-3)
    assert "stated" in row["note"] and "IBR capacity" in row["note"]
    assert "below the converter-coupled assets" in row["note"]
