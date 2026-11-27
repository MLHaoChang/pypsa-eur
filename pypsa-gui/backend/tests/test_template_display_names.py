"""Template display names (expert-catalogue 01a-2b, T1; GK B2 D1–D7).

A template ships a plain name per component, keyed on (template id,
component class, component id) and served on the network meta payload.
The id stays the model's own; the name never enters the network, a
sidecar or a digest.
"""

from __future__ import annotations

import json

import pytest

import project_templates.eh_templates as eh
from routers import projects as projects_router
from services import template_display_names as tdn

COVERED = {
    "eh_ammonia_staged",
    "eh_concentrator_staged",
    "eh_datacenter",
    "eh_datacenter_staged",
    "eh_h2_hub",
    "eh_h2_hub_staged",
    "eh_ipp_hybrid",
    "eh_microgrid",
    "eh_microgrid_staged",
    "eh_site",
}
EXEMPT = {"3bus", "ieee14", "belgium", "ieee39"}


# ── D1: every template is covered or exempt ─────────────────────────────────


def test_every_template_is_covered_or_exempt():
    templates = set(projects_router._TEMPLATE_DEFAULT_NAMES)
    # D1: the builder registry is the source. TEMPLATE_META carries every
    # covered template but eh_site (built from its own module); a template
    # added to either registry alone fails here, by name.
    builder = set(eh.TEMPLATE_META) | {"eh_site"}
    assert builder == COVERED, sorted(builder ^ COVERED)
    assert set(tdn.DISPLAY_NAMES) == COVERED
    assert set(tdn.EXEMPT) == EXEMPT
    assert not COVERED & EXEMPT
    assert templates == COVERED | EXEMPT, sorted(templates ^ (COVERED | EXEMPT))
    assert all(reason.strip() for reason in tdn.EXEMPT.values())


# ── The lookup rules ────────────────────────────────────────────────────────


def test_a_placeholder_row_gives_its_value_back():
    assert (
        tdn.name_for("eh_site", "Generator", "genset_new_2030") == "New genset (2030)"
    )
    assert tdn.name_for("eh_site", "Generator", "genset_7") == "Genset 7"
    # Outside the planning bounds, or not a number: no name.
    assert tdn.name_for("eh_site", "Generator", "genset_new_2061") is None
    assert tdn.name_for("eh_site", "Generator", "genset_new_2024") is None
    assert tdn.name_for("eh_site", "Generator", "genset_new_x") is None
    assert tdn.name_for("eh_site", "Generator", "genset_0") is None


def test_the_same_id_under_another_class_gets_no_name():
    assert tdn.name_for("eh_h2_hub", "Link", "electrolyser") == "Electrolyser"
    assert tdn.name_for("eh_h2_hub", "Bus", "electrolyser") is None
    assert tdn.name_for("eh_h2_hub", "Generator", "electrolyser") is None
    # A placeholder row stays in its class too.
    assert tdn.name_for("eh_site", "Generator", "genset_3") == "Genset 3"
    assert tdn.name_for("eh_site", "Bus", "genset_3") is None
    assert tdn.name_for("eh_site", "StorageUnit", "genset_new_2030") is None


@pytest.mark.parametrize(
    "tid", [None, "", "ieee14", "no_such_template", 7, ["eh_h2_hub"]]
)
def test_no_template_falls_back_to_the_id(tid):
    assert tdn.name_for(tid, "Link", "electrolyser") is None
    assert tdn.label_for(tid, "Link", "electrolyser") == "electrolyser"


def test_a_named_component_reads_its_name_and_an_unnamed_one_its_id():
    assert tdn.label_for("eh_h2_hub", "Link", "electrolyser") == "Electrolyser"
    assert tdn.label_for("eh_h2_hub", "Link", "my_link") == "my_link"


# ── The meta payload, through the real routes ───────────────────────────────


def _create(client, tmp_path, monkeypatch, tid: str, name: str) -> str:
    src = tmp_path / "tpl" / tid
    src.mkdir(parents=True)
    eh.BUILDERS[tid]().export_to_netcdf(str(src / "network.nc"))
    eh.write_sidecars(src, tid)
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path / "tpl")
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": name})
    assert r.status_code == 200, r.text
    return r.json().get("name", name)


def _names(client, name: str | None = None) -> dict:
    url = f"/api/projects/{name}/network/meta" if name else "/api/network/meta"
    r = client.get(url)
    assert r.status_code == 200, r.text
    return r.json()["display_names"]


def _add_generator(client, gid: str, bus: str = "hub") -> None:
    r = client.post(
        "/api/network/generators", json={"name": gid, "bus": bus, "p_nom": 1.0}
    )
    assert r.status_code in (200, 201), r.text


def test_a_template_project_serves_its_names_on_both_routes(
    client, tmp_projects_dir, tmp_path, monkeypatch
):
    name = _create(client, tmp_path, monkeypatch, "eh_h2_hub", "Hub")
    active, scoped = _names(client), _names(client, name)
    assert active == scoped
    assert active["Link"]["electrolyser"] == "Electrolyser"
    assert active["Bus"]["h2"] == "Hydrogen"
    # Only the components the network has.
    n_ids = {cid for row in active.values() for cid in row}
    assert "solar_new_2030" not in n_ids


def test_the_meta_payload_gains_one_key(
    client, tmp_projects_dir, tmp_path, monkeypatch
):
    """D5: the one change to the payload is the added key."""
    _create(client, tmp_path, monkeypatch, "eh_h2_hub", "Hub")
    assert set(client.get("/api/network/meta").json()) == {
        "name",
        "loaded_project",
        "snapshot_count",
        "bus_count",
        "display_names",
    }


def test_an_unbound_network_serves_no_names(client):
    assert _names(client) == {}


def test_the_path_scoped_route_serves_its_own_projects_names(
    client, tmp_projects_dir, tmp_path, monkeypatch
):
    """D3: B read while A is active gives B's names."""
    _create(client, tmp_path, monkeypatch, "eh_microgrid", "B")
    _create(client, tmp_path / "second", monkeypatch, "eh_h2_hub", "A")
    assert client.get("/api/network/meta").json()["loaded_project"] == "A"
    b = _names(client, "B")
    assert b["Link"]["subsea_tie"] == "Subsea cable to the mainland"
    assert "electrolyser" not in b.get("Link", {})
    assert _names(client)["Link"]["electrolyser"] == "Electrolyser"


@pytest.mark.parametrize(
    "body",
    [
        "{not json",
        "[1, 2]",
        '"eh_h2_hub"',
        json.dumps({"template_id": 7}),
        json.dumps({"template_id": ["eh_h2_hub"]}),
        json.dumps({"template_id": None}),
        json.dumps({"template_id": "no_such_template"}),
        json.dumps({}),
    ],
)
def test_a_bad_or_missing_record_serves_no_names_and_never_fails(
    client, tmp_projects_dir, project_storage_dir, tmp_path, monkeypatch, body
):
    """D4 and T1's old project: a corrupt metadata.json, a non-string or
    unknown id, or none recorded each give {} with a 200."""
    name = _create(client, tmp_path, monkeypatch, "eh_h2_hub", "Hub")
    (project_storage_dir(name) / "metadata.json").write_text(body)
    assert _names(client) == {}
    assert _names(client, name) == {}


def test_delete_and_re_add_keeps_the_name_and_another_class_gets_none(
    client, tmp_projects_dir, tmp_path, monkeypatch
):
    _create(client, tmp_path, monkeypatch, "eh_h2_hub", "Hub")
    assert _names(client)["Generator"]["wind_farm"] == "Wind farm"
    assert client.delete("/api/network/generators/wind_farm").status_code in (200, 204)
    assert "wind_farm" not in _names(client).get("Generator", {})
    _add_generator(client, "wind_farm")
    assert _names(client)["Generator"]["wind_farm"] == "Wind farm"
    # A Bus that takes a Generator's id gets no name.
    r = client.post("/api/network/buses", json={"name": "solar_park"})
    assert r.status_code in (200, 201), r.text
    assert "solar_park" not in _names(client).get("Bus", {})


def test_a_rename_drops_the_name_and_a_new_component_with_the_old_id_takes_it(
    client, tmp_projects_dir, tmp_path, monkeypatch
):
    """GK B2 (b): the renamed id is the user's own word."""
    _create(client, tmp_path, monkeypatch, "eh_h2_hub", "Hub")
    r = client.put(
        "/api/network/generators/wind_farm", json={"name": "my_wind", "bus": "hub"}
    )
    assert r.status_code == 200, r.text
    gens = _names(client).get("Generator", {})
    assert "my_wind" not in gens and "wind_farm" not in gens
    _add_generator(client, "wind_farm")
    assert _names(client)["Generator"]["wind_farm"] == "Wind farm"


@pytest.mark.parametrize("how", ["save_as", "save_a_copy", "fork"])
def test_the_names_survive_save_as_a_copy_and_a_fork(
    client, tmp_projects_dir, project_storage_dir, tmp_path, monkeypatch, how
):
    """D6: the template id travels, so the names read back after reopening."""
    name = _create(client, tmp_path, monkeypatch, "eh_h2_hub", "Hub")
    if how == "save_as":
        r = client.post("/api/projects/Hub2", params={"rebind": True})
    elif how == "save_a_copy":
        r = client.post("/api/projects/Hub2")
    else:
        r = client.post(f"/api/projects/{name}/scenarios", json={"name": "Hub2"})
    assert r.status_code in (200, 201), r.text
    meta = json.loads((project_storage_dir("Hub2") / "metadata.json").read_text())
    assert meta["template_id"] == "eh_h2_hub"
    assert client.post("/api/projects/Hub2/activate").status_code == 200
    assert _names(client, "Hub2")["Link"]["electrolyser"] == "Electrolyser"
    assert _names(client)["Link"]["electrolyser"] == "Electrolyser"


# ── D5: the names stay out of every digest ──────────────────────────────────


def test_the_names_stay_out_of_the_fingerprint_and_the_saved_bytes(
    client, tmp_projects_dir, project_storage_dir, tmp_path, monkeypatch
):
    """The fingerprint and save bytes are the same with the table emptied."""
    import types

    from services.pypsa_service import PyPSAService
    from services.workflow_tools import _network_input_fingerprint

    def fps():
        ctx = types.SimpleNamespace(
            network=PyPSAService.get_network(),
            solver_state={"solver_config": {}},
            user_ts={},
            storage_dir=None,
        )
        return _network_input_fingerprint(ctx), _network_input_fingerprint(
            ctx, exact=True
        )

    name = _create(client, tmp_path, monkeypatch, "eh_h2_hub", "Hub")
    assert _names(client)
    with_names = fps()
    assert client.post(f"/api/projects/{name}").status_code == 200
    saved = sorted(p.name for p in project_storage_dir(name).iterdir())
    # No saved file carries a display name (ids only).
    for path in project_storage_dir(name).rglob("*"):
        if path.is_file():
            blob = path.read_bytes()
            assert (
                b"Hydrogen storage" not in blob and b"Hydrogen offtake" not in blob
            ), path
    monkeypatch.setattr(tdn, "DISPLAY_NAMES", {})
    assert _names(client) == {}
    assert fps() == with_names
    assert sorted(p.name for p in project_storage_dir(name).iterdir()) == saved
