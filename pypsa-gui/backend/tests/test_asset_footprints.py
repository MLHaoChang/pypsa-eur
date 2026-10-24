"""Optional site-only dimensions: validation, persistence and lifecycle."""
import copy

import pytest

from services import site_service as ss


def document(value):
    return {"version": 1, "sites": [], "asset_footprints": {"Generator:G": value}}


@pytest.mark.parametrize("value", [
    None, [], {}, {"width_m": 12}, {"depth_m": 6},
    {"width_m": 0, "depth_m": 6}, {"width_m": -1, "depth_m": 6},
    {"width_m": True, "depth_m": 6}, {"width_m": "12", "depth_m": 6},
    {"width_m": float("nan"), "depth_m": 6},
    {"width_m": 12, "depth_m": float("inf")},
    {"width_m": 12, "depth_m": 6, "height_m": None},
    {"width_m": 12, "depth_m": 6, "height_m": 0},
    {"width_m": 100001, "depth_m": 6},
])
def test_invalid_dimensions_refuse(value):
    with pytest.raises(ss.SitesInvalid, match="asset_footprints"):
        ss.validate_sites(document(value))


@pytest.mark.parametrize("key", ["Line:L", "Bus:B", "Load:L", "Carrier:AC", "Generator:", "G"])
def test_only_equipment_keys_are_allowed(key):
    doc = document({"width_m": 12, "depth_m": 6})
    doc["asset_footprints"] = {key: {"width_m": 12, "depth_m": 6}}
    with pytest.raises(ss.SitesInvalid, match="asset_footprints"):
        ss.validate_sites(doc)


def test_dimensions_round_trip_without_a_site_or_network(tmp_path):
    doc = document({"width_m": 12, "depth_m": 6, "height_m": 3})
    before = copy.deepcopy(doc)
    ss.validate_sites(doc)
    ss.write_sites(tmp_path, doc)
    assert ss.read_sites(tmp_path) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["sites.json"]


def test_rename_and_delete_footprint_do_not_change_other_classes():
    doc = document({"width_m": 12, "depth_m": 6})
    doc["asset_footprints"]["Store:G"] = {"width_m": 4, "depth_m": 2}
    assert ss.rename_component(doc, "Generator", "G", "new")
    assert "Generator:G" not in doc["asset_footprints"]
    assert doc["asset_footprints"]["Generator:new"] == {"width_m": 12, "depth_m": 6}
    assert ss.remove_asset_footprint(doc, "Generator", "new")
    assert doc["asset_footprints"] == {"Store:G": {"width_m": 4, "depth_m": 2}}


def test_non_mapping_footprint_table_refuses():
    with pytest.raises(ss.SitesInvalid, match="asset_footprints"):
        ss.validate_sites({"version": 1, "sites": [], "asset_footprints": []})


def test_owned_http_round_trip_and_invalid_write_preserve_prior_state(client, api_project):
    name = api_project('equipment_envelope')
    url = f'/api/projects/{name}/sites'
    doc = document({'width_m': 12, 'depth_m': 6})
    assert client.put(url, json=doc).status_code == 200
    assert client.get(url).json() == doc
    invalid = document({'width_m': -1, 'depth_m': 6})
    response = client.put(url, json=invalid)
    assert response.status_code == 422
    assert 'asset_footprints' in response.json()['detail']
    assert client.get(url).json() == doc


@pytest.mark.parametrize('cascade', [False, True])
def test_native_delete_and_recreate_does_not_inherit_dimensions(tmp_path, install_network, monkeypatch, cascade):
    import pypsa

    from services.network_buses import apply_delete_bus_cascade
    from services.network_crud import _delete_component
    from services.pypsa_service import PyPSAService

    n = pypsa.Network()
    n.add('Bus', 'B')
    n.add('Generator', 'G', bus='B', p_nom=10)
    install_network(n)
    monkeypatch.setattr(PyPSAService.get_active_context(), 'storage_dir', tmp_path)
    ss.write_sites(tmp_path, document({'width_m': 12, 'depth_m': 6}))
    if cascade:
        apply_delete_bus_cascade('B')
        n.add('Bus', 'B')
    else:
        _delete_component('Generator', 'generators', 'G')
    n.add('Generator', 'G', bus='B', p_nom=50)
    assert ss.read_sites(tmp_path)['asset_footprints'] == {}


@pytest.mark.parametrize('bad_table', [None, [], 'corrupt'])
def test_corrupt_optional_metadata_never_turns_native_deletion_into_failure(tmp_path, install_network, monkeypatch, bad_table):
    import pypsa

    from services.network_crud import _delete_component
    from services.pypsa_service import PyPSAService

    n = pypsa.Network()
    n.add('Bus', 'B')
    n.add('Generator', 'G', bus='B', p_nom=10)
    install_network(n)
    monkeypatch.setattr(PyPSAService.get_active_context(), 'storage_dir', tmp_path)
    doc = document({'width_m': 12, 'depth_m': 6})
    doc['asset_footprints'] = bad_table
    ss.write_sites(tmp_path, doc)
    _delete_component('Generator', 'generators', 'G')
    assert 'G' not in n.generators.index
