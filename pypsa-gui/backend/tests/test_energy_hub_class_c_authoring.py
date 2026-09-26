"""
P15 — Class-C authoring: shipped profile packs + the pack listing route.

Before P15 ``profile_pack`` ids resolved against ``tests/fixtures/`` — shipped
code read from the test tree, so a frozen build (which never bundles tests)
could not resolve a single pack. The packs now live in ``backend/data/``,
the spec ships that directory, and the bundle check expects it.
"""
from __future__ import annotations

import json
import pathlib
import sys

from services.adequacy import stress as ST

BACKEND = pathlib.Path(__file__).resolve().parents[1]
DATA = BACKEND / "data" / "eh_class_c"
SPEC = BACKEND.parent / "pypsa-gui.spec"


def test_profile_packs_resolve_from_the_shipped_data_dir():
    assert ST.PROFILE_PACK_DIR == DATA
    assert "tests" not in ST.PROFILE_PACK_DIR.parts
    pack = ST.load_synthetic_profile_pack("synth_dunkelflaute")
    assert pack["kind"] == "profiles"
    assert (DATA / "synth_dunkelflaute.json").is_file()
    assert not (BACKEND / "tests" / "fixtures" / "eh_class_c").exists()


def test_list_profile_packs_summarises_every_pack():
    packs = ST.list_profile_packs()
    ids = [p["id"] for p in packs]
    assert ids == sorted(ids)
    assert {"synth_dunkelflaute", "synth_mild_snap"} <= set(ids)
    snap = next(p for p in packs if p["id"] == "synth_mild_snap")
    assert snap == {
        "id": "synth_mild_snap",
        "name": "Synthetic mild cold snap (2h)",
        "frequency_per_year": 0.2,
        "snapshots": 2,
        "loads": ["l"],
        "generators": ["wind1"],
        "provenance": "synthetic_fixture_v1",
    }


def test_a_broken_pack_is_listed_with_its_error_not_hidden(tmp_path, monkeypatch):
    (tmp_path / "ok_pack.json").write_text(json.dumps(
        {"name": "ok", "loads_p_set": {"l": [1.0]}}))
    (tmp_path / "bad_pack.json").write_text("{not json")
    (tmp_path / "Not-An-Id.json").write_text("{}")
    monkeypatch.setattr(ST, "PROFILE_PACK_DIR", tmp_path)
    packs = {p["id"]: p for p in ST.list_profile_packs()}
    assert set(packs) == {"ok_pack", "bad_pack"}
    assert packs["ok_pack"]["snapshots"] == 1
    assert "unreadable" in packs["bad_pack"]["error"]


def test_the_spec_ships_the_profile_pack_dir():
    text = SPEC.read_text(encoding="utf-8")
    assert '(str(BACKEND / "data" / "eh_class_c"), "data/eh_class_c")' in text


def test_the_bundle_check_expects_a_profile_pack():
    sys.path.insert(0, str(BACKEND / "smoke"))
    try:
        import check_bundle
    finally:
        sys.path.pop(0)
    assert "synth_dunkelflaute.json" in check_bundle.EXPECTED


def test_the_frozen_layout_resolves_the_same_relative_path():
    """Under PyInstaller ``pathex=[BACKEND]`` puts ``services/`` at the bundle
    root, so ``parents[2]`` of stress.py is that root and ``data/eh_class_c``
    must be where the spec writes it."""
    rel = ST.PROFILE_PACK_DIR.relative_to(
        pathlib.Path(ST.__file__).resolve().parents[2])
    assert rel.as_posix() == "data/eh_class_c"


def test_profile_packs_route(client, install_network, tmp_projects_dir):
    from tests.test_adequacy_http import _network
    install_network(_network(), name="PP1")
    client.post("/api/projects/PP1", params={"force": True, "rebind": True})
    r = client.get("/api/projects/PP1/stress_profile_packs")
    assert r.status_code == 200, r.text
    ids = [p["id"] for p in r.json()["packs"]]
    assert "synth_dunkelflaute" in ids
    # A scenario naming a shipped pack saves; an unknown pack is a 422 whose
    # message the editor shows verbatim.
    sc = {"id": "df", "kind": "profiles", "frequency_per_year": 0.1,
          "profile_pack": "synth_dunkelflaute"}
    assert client.put("/api/projects/PP1/stress_scenarios",
                      json={"scenarios": [sc]}).status_code == 200
    r = client.put("/api/projects/PP1/stress_scenarios",
                   json={"scenarios": [sc | {"profile_pack": "nope"}]})
    assert r.status_code == 422
    assert "unknown synthetic profile_pack 'nope'" in r.json()["detail"]


# ── parametric multipliers: an explicit 0 is a value, not "unset" ───────────


import pytest  # noqa: E402


def test_zero_availability_multiplier_is_applied_not_read_as_one():
    """[0, 1.5] allows 0 — a full renewables drought. `x or 1.0` read it as
    1.0, so the scenario ran with NO stress at all."""
    from tests.test_energy_hub_class_c_profiles import _network_with_wind

    sc = {"id": "df0", "kind": "parametric", "frequency_per_year": 0.1,
          "renewable_availability_multiplier": 0.0}
    ST._validate([sc])
    n = _network_with_wind()
    undo = ST._parametric_mutate(sc)(n)
    assert n.generators_t.p_max_pu["wind1"].tolist() == [0.0, 0.0]
    undo()
    assert n.generators_t.p_max_pu["wind1"].tolist() == [1.0, 1.0]


@pytest.mark.parametrize("field,value,needle", [
    ("electrical_load_multiplier", 0, "load multiplier 0 outside"),
    ("electrical_load_multiplier", "abc", "must be a number"),
    ("renewable_availability_multiplier", "abc", "must be a number"),
    ("renewable_availability_multiplier", float("nan"), "outside"),
])
def test_bad_multipliers_are_validation_errors(field, value, needle):
    sc = {"id": "x", "kind": "parametric", "frequency_per_year": 0.1,
          field: value}
    with pytest.raises(ST.StressValidationError, match=needle):
        ST._validate([sc])


def test_absent_or_null_multiplier_means_one():
    sc = {"id": "x", "kind": "parametric", "frequency_per_year": 0.1,
          "electrical_load_multiplier": None}
    ST._validate([sc])
    assert ST._multipliers(sc) == (1.0, 1.0)


def test_non_object_scenario_is_a_422_not_a_500(client, install_network,
                                                tmp_projects_dir):
    from tests.test_adequacy_http import _network
    install_network(_network(), name="PP2")
    client.post("/api/projects/PP2", params={"force": True, "rebind": True})
    for bad in (["not-a-dict"], [{"id": "a", "kind": "parametric",
                                  "frequency_per_year": 0.1,
                                  "electrical_load_multiplier": "abc"}]):
        r = client.put("/api/projects/PP2/stress_scenarios",
                       json={"scenarios": bad})
        assert r.status_code == 422, r.text
