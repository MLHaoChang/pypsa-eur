"""The island sidecar ``island_config.json`` (plan 2026-10-07 campus island, I1).

The schema lives in gridspine (``gridspine/schema/island.py``); pypsa-gui only
loads the file and hands it over, so the hub and the campus file can never
disagree about what a field means.

Oracles:
* the Data Center template's sidecar validates through gridspine, and every
  name in it is a component of the template network, of the kind it claims;
* its genset pickup blocks cover the template's peak critical load, computed
  here from the template's own load profiles;
* the committed JSON is what the builder writes (no drift);
* a project made from the template receives the file, and a bundle carries it.
"""
import json
import pathlib

import pytest

from gridspine.producers.campus import draft_campus
from gridspine.schema.contracts import ContractError
from gridspine.schema.island import check_island

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"


def _templates():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_eh_templates_island", TPL_DIR / "eh_templates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = _templates()


def test_the_loader_validates_through_gridspine(tmp_path):
    from services.adequacy import island_config as IC
    (tmp_path / IC.SIDECAR_NAME).write_text(json.dumps(T.ISLAND_CONFIG["eh_datacenter"]))
    cfg = IC.load_island_config(tmp_path / IC.SIDECAR_NAME)
    assert cfg["units"]["bess_new"]["control"] == "gfm_droop"
    bad = json.loads(json.dumps(T.ISLAND_CONFIG["eh_datacenter"]))
    bad["units"]["bess_new"]["control"] = "gfm_magic"
    (tmp_path / IC.SIDECAR_NAME).write_text(json.dumps(bad))
    with pytest.raises(ContractError, match="gfm_magic"):
        IC.load_island_config(tmp_path / IC.SIDECAR_NAME)


def test_a_project_without_the_sidecar_has_no_island_config(tmp_path):
    from services.adequacy import island_config as IC
    assert IC.load_island_config(tmp_path / IC.SIDECAR_NAME) is None


def test_the_datacenter_sidecar_names_components_of_the_template_and_their_kinds():
    from services.adequacy import island_config as IC
    n = T.build_eh_datacenter()
    cfg = IC.validate(T.ISLAND_CONFIG["eh_datacenter"])
    tables = {"bess": "storage_units", "ups": "storage_units", "genset": "generators",
              "pv": "generators", "wind": "generators", "load": "loads"}
    for name, block in cfg["units"].items():
        assert name in getattr(n, tables[block["kind"]]).index, (name, block["kind"])
    # the draft agrees with every kind the sidecar claims (it refuses otherwise)
    n.generators["p_nom_opt"] = n.generators["p_nom"]
    n.storage_units["p_nom_opt"] = n.storage_units["p_nom"].where(n.storage_units["p_nom"] > 0, 20.0)
    n.generators.loc["genset_new", "p_nom_opt"] = 10.0
    n.links["p_nom_opt"] = n.links["p_nom"]
    draft = draft_campus(n, island=cfg)
    assert draft.spec["campus"]["island"]["scenarios"] == ["ups_bridged"]


def test_the_datacenter_pickup_blocks_cover_its_peak_critical_load():
    from services.adequacy import island_config as IC
    n = T.build_eh_datacenter()
    cfg = IC.validate(T.ISLAND_CONFIG["eh_datacenter"])
    critical = [name for name, b in cfg["units"].items() if b["kind"] == "load" and b["critical"]]
    assert set(critical) == {"it_load", "cooling"}              # plan Q1: IT plus its cooling
    peak = float(n.loads_t.p_set[critical].sum(axis=1).max())
    check_island(cfg, critical_peak_mw=peak)


@pytest.mark.parametrize("tid", sorted(T.ISLAND_CONFIG))
def test_the_committed_island_sidecar_matches_the_builder(tid, tmp_path):
    T.write_sidecars(tmp_path, tid)
    assert (TPL_DIR / tid / T.ISLAND_FILE).read_text() == (tmp_path / T.ISLAND_FILE).read_text()


# ── the H2 hub (no genset, no UPS: its batteries form the island) ────────────

H2_IDS = ("eh_h2_hub", "eh_h2_hub_staged")


@pytest.mark.parametrize("tid", H2_IDS)
def test_the_h2_hub_sidecar_names_its_units_and_its_batteries_form_the_island(tid):
    from gridspine.producers.campus import DEFAULT_ISLAND
    from gridspine.schema.island import GFM_CONTROLS
    from services.adequacy import island_config as IC

    n = T.BUILDERS[tid]()
    cfg = IC.validate(T.ISLAND_CONFIG[tid])
    tables = {"bess": "storage_units", "pv": "generators", "wind": "generators",
              "fuel_cell": "links", "load": "loads"}
    for name, block in cfg["units"].items():
        assert name in getattr(n, tables[block["kind"]]).index, (name, block["kind"])
    # every battery (existing and candidate) forms the island and rides it through
    bess = sorted(n.storage_units.index)
    assert sorted(u for u, b in cfg["units"].items() if b["kind"] == "bess") == bess
    assert all(cfg["units"][b]["control"] in GFM_CONTROLS for b in bess)
    assert sorted(cfg["requirements"]["ride_through_storage"]) == bess
    assert cfg["requirements"]["scenarios"] == ["seamless"]
    # the critical load is the process load; the electrolyser and the H2 offtake are not islanded
    assert [u for u, b in cfg["units"].items() if b["kind"] == "load"] == ["process_load"]
    # the fuel cell's block is gridspine's drafted default, so draft and sidecar agree
    raw = T.ISLAND_CONFIG[tid]["units"]["fuel_cell"]
    assert {k: (v["value"] if isinstance(v, dict) else v) for k, v in raw.items() if k != "kind"} \
        == DEFAULT_ISLAND["fuel_cell"]
    assert {v["source"] for v in raw.values() if isinstance(v, dict)} == {"assumed"}


@pytest.mark.parametrize("tid", H2_IDS)
def test_the_h2_hub_drafts_with_its_sidecar_and_ships_the_island_rows_off(tid):
    from gridspine.ingest.campus import build_campus
    from services.adequacy import island_config as IC

    n = T.BUILDERS[tid]()
    for frame in ("generators", "storage_units", "links"):
        df = getattr(n, frame)
        df["p_nom_opt"] = df["p_nom"].where(df["p_nom"] > 0, 10.0)
    spec = draft_campus(n, island=IC.validate(T.ISLAND_CONFIG[tid])).spec
    units = {u["pypsa_name"]: u for u in spec["campus"]["units"].values() if "pypsa_name" in u}
    assert units["fuel_cell"]["kind"] == "fuel_cell" and units["fuel_cell"]["island"]["control"] == "gfl_qu"
    assert units["site_battery"]["island"]["control"] == "gfm_droop"
    assert spec["campus"]["island"]["scenarios"] == ["seamless"]
    build_campus(spec)
    # owner (a): the plan is not sized to island, and the template says so
    assert not T.solver_config(tid).get("island_constraints")
    assert any("Island rows are off" in s for s in T.TEMPLATE_META[tid]["study_notes"])
    # both hubs ledger the sidecar, its unit data and what turning the rows on costs
    items = [r["item"] for r in T.TEMPLATE_META[tid]["ledger"]]
    assert {"island sidecar", "island unit data", "island rows"} <= set(items)
    rows = next(r for r in T.TEMPLATE_META[tid]["ledger"] if r["item"] == "island rows")
    assert rows["value"] == "off (island_constraints false)" and "24 h" in rows["note"]


def test_only_templates_with_island_data_ship_the_file(tmp_path):
    for tid in T.BUILDERS:
        out = tmp_path / tid
        T.write_sidecars(out, tid)
        assert (out / T.ISLAND_FILE).is_file() == (tid in T.ISLAND_CONFIG), tid


def test_the_template_and_bundle_allow_lists_carry_the_file():
    from routers.projects import _BUNDLE_FILES, _TEMPLATE_SIDECARS
    from services.adequacy import island_config as IC
    assert IC.SIDECAR_NAME == T.ISLAND_FILE == "island_config.json"
    assert IC.SIDECAR_NAME in _TEMPLATE_SIDECARS
    assert IC.SIDECAR_NAME in _BUNDLE_FILES


def test_a_build_without_gridspine_says_so_instead_of_failing_obscurely(tmp_path, monkeypatch):
    """The frozen desktop app may not carry gridspine (packaging rule, see
    test_packaging_requirements.OPTIONAL_AT_RUNTIME): the import is guarded,
    and using the sidecar then names what is missing."""
    from services.adequacy import island_config as IC
    monkeypatch.setattr(IC, "GRIDSPINE_AVAILABLE", False)
    monkeypatch.setattr(IC, "GRIDSPINE_IMPORT_ERROR", "No module named 'gridspine'")
    (tmp_path / IC.SIDECAR_NAME).write_text(json.dumps(T.ISLAND_CONFIG["eh_datacenter"]))
    with pytest.raises(IC.ContractError, match="gridspine"):
        IC.load_island_config(tmp_path / IC.SIDECAR_NAME)


# ── the single-year island microgrid (UX review N2) ──────────────────────────


def test_the_single_year_microgrid_ships_an_island_sidecar_that_matches_its_network():
    """Its Island section used to say "no island requirements yet. Paste an island config": the
    sidecar names the template's own units, validates, drafts into a campus, and the template's
    own solve is unchanged (rows off), as for the H2 hub."""
    from gridspine.ingest.campus import build_campus
    from services.adequacy import island_config as IC

    n = T.BUILDERS["eh_microgrid"]()
    cfg = IC.validate(T.ISLAND_CONFIG["eh_microgrid"])
    tables = {"bess": "storage_units", "pv": "generators", "wind": "generators",
              "genset": "generators", "load": "loads"}
    for name, block in cfg["units"].items():
        assert name in getattr(n, tables[block["kind"]]).index, (name, block["kind"])
    assert sorted(cfg["requirements"]["ride_through_storage"]) == sorted(n.storage_units.index)
    assert [u for u, b in cfg["units"].items() if b["kind"] == "load" and b["critical"]] == ["hospital_load"]
    for frame in ("generators", "storage_units", "links"):
        df = getattr(n, frame)
        df["p_nom_opt"] = df["p_nom"].where(df["p_nom"] > 0, 10.0)
    build_campus(draft_campus(n, island=cfg).spec)
    assert not T.solver_config("eh_microgrid").get("island_constraints")
