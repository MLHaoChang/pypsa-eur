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
converter-coupled assets (inverter-based generators and storage; under a set
architecture, not the units behind it). The row states the capacity it used
and where it comes from. A stated capacity below those assets, or a converter
not sized yet, never passes: it reads unknown (GK A 30, Q2/Q3; unknown,
not warn, so the verdict stays the declared rule's, checked results S9). One predicate
(``dc_architecture.behind_architecture``) serves 03 and 05 (R1).
"""

from __future__ import annotations

import json
import pathlib
import sys

import pypsa
import pytest

from services import dc_architecture as A

from services.adequacy import grid_readiness as G
from services.adequacy import scr_gate as S

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

PROFILE = "eu_rfg_dcc_ce"
UNSET_ROWS = (
    BACKEND / "tests" / "fixtures" / "grid_readiness_unset_architecture_rows.json"
)


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
    r = client.post(
        "/api/projects/from_template/eh_datacenter",
        params={"name": name},
        json={"dc_load": dc_load},
    )
    assert r.status_code == 200, r.text
    d = pathlib.Path(
        project_registry.project_dir(project_row(r.json().get("name", name)))
    )
    n = pypsa.Network()
    n.import_from_netcdf(str(d / "network.nc"))
    return d, n


def _bess(**kw) -> dict:
    """The AC project on battery strings, its IT load scaled so the batteries mode is accepted."""
    pre = T.dc_load_preset("eh_datacenter")
    return {
        **pre,
        "phases": [{**p, "it_mw": p["it_mw"] * 0.6} for p in pre["phases"]],
        "architecture": "ac",
        "backup": {"mode": "bess"},
        **kw,
    }


def _rows(d, n) -> dict:
    return {r["id"]: r for r in G.grid_readiness(d, n, PROFILE)["rows"]}


# ── 03: services behind the power architecture ─────────────────────────────


def _with_strings_island(d):
    """The single-year create copies the example's island sidecar, without the
    strings (reported to DW-B, walkthrough 01): write the backup mode's own."""
    island = T.island_config(
        "eh_datacenter", load_spec=T.dc_load_spec("eh_datacenter", _bess())
    )
    (d / "island_config.json").write_text(json.dumps(island))


def test_an_ac_lv_ups_site_on_gensets_gets_no_frequency_support_from_its_ups_side(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    d, n = _dc_project(
        tmp_path, monkeypatch, client, project_row, "dc_ac", {"architecture": "ac"}
    )
    row = _rows(d, n)["frequency_support"]
    assert row["verdict"] == "warn"
    assert row["project_value"] == "voltage control (Rooftop PV)"
    assert "AC (LV UPS)" in row["note"] and A.SERVICE_WHY["ac"] in row["note"]
    # The units behind the architecture are named as not credited.
    assert "BESS new" in row["note"] and "UPS battery" in row["note"]


def test_a_grid_facing_battery_string_still_offers_frequency_support_and_is_named(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    d, n = _dc_project(
        tmp_path, monkeypatch, client, project_row, "dc_ac_bess", _bess()
    )
    _with_strings_island(d)
    row = _rows(d, n)["frequency_support"]
    assert row["verdict"] == "pass"
    assert (
        "grid forming (Backup BESS 1, " in row["project_value"]
        and " · " in row["project_value"]
    )
    assert "more" not in row["project_value"]  # every unit listed, none folded away
    assert "AC (LV UPS)" in row["note"]


def test_an_unset_architecture_leaves_the_support_rows_byte_identical(tmp_path):
    d = tmp_path / "p"
    n = T.BUILDERS["eh_datacenter"]()
    T.write_sidecars(d, "eh_datacenter")
    rows = _rows(d, n)
    pinned = json.loads(UNSET_ROWS.read_text())  # master 76f0be706's output
    assert {k: rows[k] for k in pinned} == pinned


def test_the_reactive_range_counts_no_unit_behind_the_architecture(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    d, n = _dc_project(
        tmp_path, monkeypatch, client, project_row, "dc_ac_q", {"architecture": "ac"}
    )
    row = _rows(d, n)["reactive_range"]
    assert "bess_new" not in row["source"] and "bess_new" not in row["note"]
    assert "rooftop_pv" in row["source"]


def test_the_compare_service_columns_read_the_scorecards_value(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    d, n = _dc_project(
        tmp_path, monkeypatch, client, project_row, "dc_ac_cmp", {"architecture": "ac"}
    )
    out = A.compare_project(d, T, energy_price_per_mwh=80.0)
    ac = next(v for v in out["variants"] if v["architecture"] == "ac")
    assert ac["services"]["counted"] == []
    row = _rows(d, n)["frequency_support"]
    assert ac["verdicts"]["frequency_support"] == {
        "verdict": row["verdict"],
        "project_value": row["project_value"],
        "assumed_from_architecture": [],
        "conditional": [],
        "sources": row["sources"],
        "value_rest": row["value_rest"],
    }
    vc = ac["verdicts"]["voltage_control"]
    # Voltage control here is the rooftop PV's own, named as such, not the architecture's.
    assert (
        vc["verdict"] == "pass"
        and vc["project_value"] == "voltage control (Rooftop PV)"
    )
    for v in out["variants"]:  # never Pass on frequency from the UPS side
        assert v["verdicts"]["frequency_support"]["verdict"] != "pass"


def test_each_architectures_voltage_cell_names_only_voltage_control(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    # DC walk 05 shots: the voltage cell repeated the frequency services and
    # missed the architecture's own assumed voltage control.
    d, _ = _dc_project(
        tmp_path,
        monkeypatch,
        client,
        project_row,
        "dc_vc_cells",
        {"architecture": "ac"},
    )
    out = A.compare_project(d, T, energy_price_per_mwh=80.0)
    for v in out["variants"]:
        vc = v["verdicts"]["voltage_control"]
        for word in ("frequency", "grid forming", "inertia"):
            assert word not in vc["project_value"], (v["architecture"], vc)
        assert [r["service"] for r in vc["sources"]] == ["voltage_control"]


def test_an_unset_architecture_compares_with_the_scorecards_values(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    # GK A 30 ask 3: with nothing stored, the compare's "ac" default does not
    # move units behind an architecture, so its cells read the scorecard's rows.
    dc_load = {k: v for k, v in _bess().items() if k != "architecture"}
    d, n = _dc_project(
        tmp_path, monkeypatch, client, project_row, "dc_unset_cmp", dc_load
    )
    from services.adequacy import dc_backup

    assert not A.is_set((dc_backup._meta(d).get("dc_load") or {}).get("spec"))
    out = A.compare_project(d, T, energy_price_per_mwh=80.0)
    ac = next(v for v in out["variants"] if v["architecture"] == "ac")
    row = _rows(d, n)["frequency_support"]
    cell = ac["verdicts"]["frequency_support"]
    assert (cell["verdict"], cell["project_value"]) == (
        row["verdict"],
        row["project_value"],
    )
    assert "sources" not in row and cell["sources"] == []  # no units named, as before


def test_03_and_05_leave_out_the_same_units(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    d, n = _dc_project(tmp_path, monkeypatch, client, project_row, "dc_same", _bess())
    _with_strings_island(d)
    island = json.loads((d / "island_config.json").read_text())
    behind = A.behind_architecture(n, island, "ac")
    assert set(behind) == {"ups_battery", "bess_new"}
    offer = G.grid_facing_offer(n, island, "ac")
    _, _, payload, _ = S.evaluate_network_scr_gate(n, behind=behind)
    assert offer["behind"] == behind
    assert payload["buses"][0]["ibr_excluded"] == sorted(behind)
    assert not set(behind) & set(offer["units"]) and not set(behind) & set(
        payload["buses"][0]["ibr_assets"]
    )


def test_without_an_architecture_nothing_is_behind():
    n = T.BUILDERS["eh_datacenter"]()
    assert (
        A.behind_architecture(n, {"units": {"ups_battery": {"kind": "ups"}}}, None)
        == []
    )


@pytest.mark.parametrize(
    "unit, why", [("ups_battery", "kind ups"), ("bess_new", "critical bus")]
)
def test_each_rule_of_the_predicate_puts_a_unit_behind(unit, why):
    n = T.BUILDERS["eh_datacenter"]()
    island = {
        "units": {
            "ups_battery": {"kind": "ups"},
            "bess_new": {"kind": "bess"},
            "rooftop_pv": {"kind": "pv"},
            "it_load": {"kind": "load"},
        }
    }
    if why == "kind ups":
        n.storage_units.at["ups_battery", "bus"] = (
            "dc_mv"  # off the critical bus: kind alone
        )
    behind = A.behind_architecture(n, island, "ac")
    assert unit in behind and "rooftop_pv" not in behind and "it_load" not in behind


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
    n = _site(critical_bess=40.0)
    _, status, payload, _ = S.evaluate_network_scr_gate(n, behind={"ups"})
    assert status == "ok"
    row = payload["buses"][0]
    assert row["ibr_mva"] == pytest.approx(
        49.0
    )  # PV 4 + strings 45; not the UPS, not the genset
    assert row["ibr_source"] == "derived"
    assert row["ibr_assets"] == {"pv": 4.0, "strings": 45.0}
    assert payload["min_scr"] == pytest.approx(250.0 / 49.0)
    # No architecture (Q1): the critical bus is only a reliability tag, so every converter counts.
    _, _, payload, _ = S.evaluate_network_scr_gate(n)
    assert payload["buses"][0]["ibr_mva"] == pytest.approx(89.0)


def test_a_stated_capacity_wins_and_reports_the_converters_it_leaves_out():
    _, _, payload, _ = S.evaluate_network_scr_gate(_site(ibr=30.0))
    row = payload["buses"][0]
    assert row["ibr_mva"] == 30.0 and row["ibr_source"] == "stated"
    assert row["ibr_derived_mva"] == pytest.approx(49.0)


def test_with_two_pocs_only_the_converters_on_each_poc_bus_count():
    n = _site()
    n.buses.at["mv", "eh_poc"] = True
    n.buses.at["mv", "eh_sk_mva"] = 200.0
    gate, status, _, note = S.evaluate_network_scr_gate(n)
    assert (
        gate is None and status == "not_established"
    )  # nothing on the grid bus itself
    assert "eh_ibr_mva" in note
    n.buses.at["grid", "eh_ibr_mva"] = 20.0
    _, status, payload, _ = S.evaluate_network_scr_gate(n)
    mv = next(r for r in payload["buses"] if r["bus"] == "mv")
    assert status == "ok" and mv["ibr_assets"] == {"pv": 4.0, "strings": 45.0}


def test_an_extendable_asset_reads_its_solved_size_and_a_fixed_one_its_p_nom():
    n = _site()
    n.storage_units.at["strings", "p_nom_extendable"] = True
    n.storage_units.at["strings", "p_nom_opt"] = 12.0
    n.generators.at["pv", "p_nom_opt"] = 99.0  # stale: pv is not extendable
    row = S.evaluate_network_scr_gate(n)[2]["buses"][0]
    assert (
        row["ibr_assets"] == {"pv": 4.0, "strings": 12.0} and row["ibr_unsized"] == []
    )
    n.storage_units.at["strings", "p_nom_opt"] = 0.0
    row = S.evaluate_network_scr_gate(n)[2]["buses"][0]
    assert row["ibr_assets"] == {"pv": 4.0, "strings": 0.0} and row["ibr_unsized"] == [
        "strings"
    ]


@pytest.mark.parametrize(
    "where, value",
    [
        (("generators", "pv", "p_nom"), float("nan")),
        (("generators", "pv", "p_nom"), float("inf")),
        (("storage_units", "strings", "p_nom"), -1.0),
        (("buses", "grid", "eh_ibr_mva"), float("inf")),
        (("buses", "grid", "eh_ibr_mva"), -5.0),
    ],
)
def test_a_non_finite_rating_is_unknown_never_zero(where, value):
    n = _site()
    frame, name, col = where
    getattr(n, frame).at[name, col] = value
    gate, status, _, _ = S.evaluate_network_scr_gate(n)
    assert gate is None and status == "not_established"


def test_a_blank_stated_capacity_is_derived():
    n = _site()
    n.buses.at["grid", "eh_ibr_mva"] = float("nan")
    assert S.evaluate_network_scr_gate(n)[2]["buses"][0]["ibr_source"] == "derived"


def test_the_dc_template_derives_its_inverter_capacity():
    n = T.BUILDERS["eh_datacenter"]()
    assert n.buses["eh_ibr_mva"].isna().all()


def test_battery_strings_lower_the_scr_and_the_row_states_the_capacity(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    d1, n1 = _dc_project(
        tmp_path, monkeypatch, client, project_row, "dc_g", {"architecture": "ac"}
    )
    d2, n2 = _dc_project(tmp_path, monkeypatch, client, project_row, "dc_b", _bess())
    g, b = _rows(d1, n1)["scr"], _rows(d2, n2)["scr"]
    assert g["project_value"] == pytest.approx(250.0 / 4.0) and g["verdict"] == "pass"
    strings = sum(
        float(n2.storage_units.at[s, "p_nom"])
        for s in n2.storage_units.index
        if s.startswith("backup_bess")
    )
    assert b["project_value"] == pytest.approx(250.0 / (4.0 + strings), rel=1e-3)
    assert (
        "Inverter capacity" in b["note"]
        and "derived" in b["note"]
        and "assumed" in b["note"]
    )
    assert "behind the AC (LV UPS)" in b["note"] and "UPS battery" in b["note"]


def test_an_unset_architecture_counts_every_converter_and_caps_an_unsized_one(tmp_path):
    d = tmp_path / "p"
    n = T.BUILDERS["eh_datacenter"]()
    T.write_sidecars(d, "eh_datacenter")
    row = _rows(d, n)["scr"]
    assert row["project_value"] == pytest.approx(
        250.0 / 44.0, rel=1e-3
    )  # PV 4 + UPS 40; bess_new unsized
    assert (
        row["verdict"] == "unknown"
        and "not sized yet" in row["note"]
        and "BESS new" in row["note"]
    )
    n.storage_units.at["bess_new", "p_nom_opt"] = 10.0
    row = _rows(d, n)["scr"]
    assert row["verdict"] == "pass" and row["project_value"] == pytest.approx(
        250.0 / 54.0, rel=1e-3
    )


def test_a_project_saved_before_the_change_keeps_its_stated_capacity_and_verdict(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    d, n = _dc_project(
        tmp_path, monkeypatch, client, project_row, "dc_old_g", {"architecture": "ac"}
    )
    n.buses.at["grid", "eh_ibr_mva"] = 30.0  # what the template stored before
    n.buses.at["dc_mv", "eh_ibr_mva"] = 25.0
    row = _rows(d, n)["scr"]
    assert (
        row["project_value"] == pytest.approx(250.0 / 30.0, rel=1e-3)
        and row["verdict"] == "pass"
    )
    assert "stated" in row["note"] and "below" not in row["note"]


def test_a_stated_capacity_below_the_converters_says_it_predates_them_and_never_passes(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row
):
    d, n = _dc_project(tmp_path, monkeypatch, client, project_row, "dc_old", _bess())
    n.buses.at["grid", "eh_ibr_mva"] = 30.0  # a project saved before the derivation
    row = _rows(d, n)["scr"]
    assert row["project_value"] == pytest.approx(250.0 / 30.0, rel=1e-3)
    assert row["verdict"] == "unknown"  # 8.3 would pass; the stale value withholds it
    assert "stated" in row["note"] and "IBR capacity field" in row["note"]
    assert (
        "below the converter-coupled assets" in row["note"]
        and "predates them" in row["note"]
    )


def test_a_bad_asset_rating_does_not_block_a_stated_capacity():
    n = _site(ibr=30.0)
    n.generators.at["pv", "p_nom"] = float("nan")
    gate, status, payload, _ = S.evaluate_network_scr_gate(n)
    assert status == "ok" and payload["buses"][0]["ibr_source"] == "stated"
    assert payload["buses"][0]["ibr_derived_mva"] is None


def test_pumped_hydro_is_not_a_converter_and_a_stated_zero_reads_blank():
    n = _site(ibr=0.0)
    n.add("StorageUnit", "phs", bus="mv", carrier="PHS", p_nom=100.0)
    row = S.evaluate_network_scr_gate(n)[2]["buses"][0]
    assert row["ibr_source"] == "derived" and "phs" not in row["ibr_assets"]


def test_an_unknown_scr_names_the_input_it_needs(tmp_path):
    d = tmp_path / "p"
    n = T.BUILDERS["eh_datacenter"]()
    T.write_sidecars(d, "eh_datacenter")
    n.buses.at["grid", "eh_sk_mva"] = float("nan")
    row = _rows(d, n)["scr"]
    assert row["verdict"] == "unknown" and "IBR capacity field" in row["note"]


def test_a_network_unit_on_the_critical_bus_is_behind_even_outside_the_island_but_never_a_load():
    n = T.BUILDERS["eh_datacenter"]()
    assert "bess_new" in A.behind_architecture(
        n, {"units": {}}, "ac"
    )  # not in the island
    assert "bess_new" not in A.behind_architecture(
        n, {"units": {"bess_new": {"kind": "load"}}}, "ac"
    )


def test_with_two_pocs_a_converter_on_neither_leaves_every_derived_capacity_unknown(
    tmp_path,
):
    n = _site()
    n.add("Bus", "poc2")
    n.buses.at["poc2", "eh_poc"] = True
    n.buses.at["poc2", "eh_sk_mva"] = 300.0
    n.add("Generator", "wind2", bus="poc2", carrier="wind", p_nom=50.0)
    n.buses.at["grid", "eh_ibr_mva"] = (
        20.0  # grid stated; poc2 would derive 50 from wind2
    )
    gate, status, _, note = S.evaluate_network_scr_gate(n)
    assert gate is None and status == "not_established"
    assert (
        "pv" in note and "strings" in note and "eh_ibr_mva" in note
    )  # on mv: on neither PoC bus
    assert S.unattributed_converters(n) == ["pv", "strings"]
    n.buses.at["poc2", "eh_ibr_mva"] = 50.0  # stated on both: the gate runs
    assert S.evaluate_network_scr_gate(n)[1] == "ok"


def test_a_solved_size_says_it_is_from_the_last_solve(tmp_path):
    d = tmp_path / "p"
    n = T.BUILDERS["eh_datacenter"]()
    T.write_sidecars(d, "eh_datacenter")
    n.storage_units.at["bess_new", "p_nom_opt"] = 10.0
    note = _rows(d, n)["scr"]["note"]
    assert "BESS new 10 (from the last solve)" in note and "Rooftop PV 4 (" not in note


def test_a_real_scr_below_three_warns_even_when_a_cap_also_holds(tmp_path):
    d = tmp_path / "p"
    n = T.BUILDERS["eh_datacenter"]()
    T.write_sidecars(d, "eh_datacenter")
    n.buses.at["grid", "eh_sk_mva"] = (
        100.0  # 100 / 44 = 2.3; bess_new unsized (a cap holds)
    )
    row = _rows(d, n)["scr"]
    assert row["verdict"] == "warn" and "not sized yet" in row["note"]
    n.buses.at["grid", "eh_ibr_mva"] = (
        40.0  # stated below the 44 MVA of converters: 2.5
    )
    row = _rows(d, n)["scr"]
    assert row["verdict"] == "warn" and "predates them" in row["note"]


def test_a_cap_on_any_poc_withholds_the_pass_not_only_on_the_lowest_scr_one(tmp_path):
    # GK A 30 ask 2: PoC grid is stated 25 MVA (SCR 10). PoC poc2 is stated
    # 10 MVA against 100 MVA of converters on its own bus: it shows 40, really 4.
    d = tmp_path / "p"
    n = T.BUILDERS["eh_datacenter"]()
    T.write_sidecars(d, "eh_datacenter")
    n.buses.at["grid", "eh_ibr_mva"] = 25.0
    n.add("Bus", "poc2")
    n.buses.at["poc2", "eh_poc"] = True
    n.buses.at["poc2", "eh_sk_mva"] = 400.0
    n.buses.at["poc2", "eh_ibr_mva"] = 10.0
    n.add("Generator", "wind2", bus="poc2", carrier="wind", p_nom=100.0)
    row = _rows(d, n)["scr"]
    assert row["project_value"] == pytest.approx(10.0, rel=1e-3)  # the lowest, at grid
    assert row["verdict"] == "unknown"
    assert "poc2" in row["note"] and "predates them" in row["note"]
    n.buses.at["poc2", "eh_ibr_mva"] = 100.0  # stated at the converters: the Pass holds
    assert _rows(d, n)["scr"]["verdict"] == "pass"
