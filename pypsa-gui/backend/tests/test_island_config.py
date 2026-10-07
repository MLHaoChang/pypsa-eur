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
    assert draft.spec["campus"]["island"]["scenarios"] == ["ups_bridged", "seamless"]


def test_the_datacenter_pickup_blocks_cover_its_peak_critical_load():
    from services.adequacy import island_config as IC
    n = T.build_eh_datacenter()
    cfg = IC.validate(T.ISLAND_CONFIG["eh_datacenter"])
    critical = [name for name, b in cfg["units"].items() if b["kind"] == "load" and b["critical"]]
    assert set(critical) == {"it_load", "cooling"}              # plan Q1: IT plus its cooling
    peak = float(n.loads_t.p_set[critical].sum(axis=1).max())
    check_island(cfg, critical_peak_mw=peak)


def test_the_committed_island_sidecar_matches_the_builder(tmp_path):
    T.write_sidecars(tmp_path, "eh_datacenter")
    assert (TPL_DIR / "eh_datacenter" / T.ISLAND_FILE).read_text() == (tmp_path / T.ISLAND_FILE).read_text()


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
