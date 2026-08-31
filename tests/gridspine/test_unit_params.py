import pytest

from gridspine.schema.contracts import ContractError
from gridspine.templates.unit_params import load_unit_params


def test_default_load_covers_case39_units():
    from gridspine.ingest.pandapower_source import load_case39, registry_from_net
    params = load_unit_params()
    reg = registry_from_net(load_case39())
    assert set(reg.index) == set(params.index)


def test_columns_and_source_tags():
    params = load_unit_params()
    assert {"h_s", "mbase_mva", "source", "include_in_inertia"} <= set(params.columns)
    assert params["source"].isin(["measured", "datasheet", "assumed"]).all()
    assert (~params.loc[params.index.str.startswith("SLK_"), "include_in_inertia"]).all()


def test_bad_source_tag_rejected(tmp_path):
    bad = tmp_path / "u.yaml"
    bad.write_text("units:\n  G_X: {h_s: 5.0, mbase_mva: 100.0, source: guessed, include_in_inertia: true}\n")
    with pytest.raises(ContractError, match="source"):
        load_unit_params(bad)


def test_nonpositive_h_rejected(tmp_path):
    bad = tmp_path / "u.yaml"
    bad.write_text("units:\n  G_X: {h_s: 0.0, mbase_mva: 100.0, source: assumed, include_in_inertia: true}\n")
    with pytest.raises(ContractError, match="h_s"):
        load_unit_params(bad)


def test_duplicate_unit_ids_rejected(tmp_path):
    # YAML mappings dedupe keys, so build the duplicate through the same
    # from_dict path the loader uses by feeding a list-shaped units block.
    bad = tmp_path / "u.yaml"
    bad.write_text("units: [G_X, G_X]\n")
    with pytest.raises(ContractError):
        load_unit_params(bad)


def test_templates_imports_no_engine():
    # yaml/pandas only — no pypsa, pandapower, or producers/ import,
    # and never the unsafe full yaml.load.
    import gridspine.templates.unit_params as mod

    src = open(mod.__file__, encoding="utf-8").read()
    for banned in ("import pypsa", "import pandapower", "gridspine.producers", "yaml.load("):
        assert banned not in src
