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


# ===========================================================================
# Templates v2 — dynamic parameters beyond H, provenance per FIELD
# ===========================================================================
#
# Everything above this line is the increment-2 contract and must keep passing
# unchanged: that IS the compatibility assertion. The v1 flat unit form stays
# legal — a flat unit carries exactly one parameter (h_s), so its one tag is
# already per-field. What v2 forbids is a unit-level tag sitting over MANY
# fields, because the classic case39 reactances are datasheet while the
# saturation and subtransient values are assumed, and one tag launders the
# second into the first.

import copy

import yaml

from gridspine.ingest.pandapower_source import RES_LEDGER, load_case39_res, registry_from_net
from gridspine.templates.unit_params import (
    MODEL_PARAMS,
    SYNCHRONOUS_MODELS,
    load_unit_templates,
    provenance_counts,
)

N_SYNC, N_RES = 10, 5


def _genrou_unit(**over):
    """A complete, physically ordered GENROU unit; tests break one thing at a time."""
    p = {
        "h_s": 30.0, "d": 0.0,
        "xd": 0.3, "xq": 0.28, "xd_p": 0.07, "xq_p": 0.17, "xd_pp": 0.05, "xl": 0.035,
        "t_do_p": 6.5, "t_qo_p": 1.5, "t_do_pp": 0.05, "t_qo_pp": 0.05,
        "s1": 0.05, "s12": 0.3,
    }
    src = {k: "datasheet" for k in p}
    src.update({"xd_pp": "assumed", "t_do_pp": "assumed", "t_qo_pp": "assumed",
                "s1": "assumed", "s12": "assumed", "d": "assumed"})
    unit = {
        "model": "GENROU", "mbase_mva": 100.0, "include_in_inertia": True,
        "params": {k: {"value": v, "source": src[k]} for k, v in p.items()},
    }
    unit = copy.deepcopy(unit)
    for k, v in over.items():
        if k == "params":
            unit["params"].update(v)
        else:
            unit[k] = v
    return unit


def _write(tmp_path, units):
    f = tmp_path / "u.yaml"
    f.write_text(yaml.safe_dump({"units": units}, sort_keys=False))
    return f


# --- the default file -------------------------------------------------------

def test_default_templates_cover_the_synchronous_fleet_and_the_res_sites():
    t = load_unit_templates()
    assert len(t.units) == N_SYNC + N_RES
    reg = registry_from_net(load_case39_res())
    assert set(t.units.index) == set(reg.index)
    assert set(t.units["model"]) == {"GENROU", "GENSAL", "inverter"}
    assert (t.units.loc[t.units["model"] == "inverter"].index.str.match(r"^[WS]_")).all()


def test_default_h_params_view_is_unchanged_for_ranking_and_the_ledger():
    """The frame ranking/ and the driver's ledger line read: exactly the ten
    synchronous units, `source` == the provenance of h_s, still all datasheet."""
    params = load_unit_params()
    assert len(params) == N_SYNC
    assert not params.index.str.match(r"^[WS]_").any()
    assert (params["source"] == "datasheet").all()
    t = load_unit_templates()
    h = t.params[t.params["param"] == "h_s"].set_index("unit_id")["source"]
    assert params["source"].to_dict() == h.to_dict()


def test_default_reactances_are_datasheet_and_saturation_is_assumed():
    """The laundering the per-field tag exists to prevent, asserted on the data."""
    p = load_unit_templates().params
    for datasheet in ("xd", "xq", "xd_p", "xl", "t_do_p"):
        assert (p.loc[p["param"] == datasheet, "source"] == "datasheet").all(), datasheet
    for assumed in ("s1", "s12", "xd_pp", "t_do_pp", "t_qo_pp"):
        assert (p.loc[p["param"] == assumed, "source"] == "assumed").all(), assumed


def test_per_field_assumed_tags_surface_in_the_provenance_count():
    counts = provenance_counts(load_unit_templates())
    assert set(counts.index) == {"measured", "datasheet", "assumed"}
    assert counts["assumed"] > 0
    assert counts["datasheet"] > counts["assumed"] * 0  # both present, count is per (unit, param)
    assert counts.sum() == len(load_unit_templates().params)


def test_default_h_params_view_carries_the_wide_columns():
    params = load_unit_params()
    for col in ("xd", "xq", "xd_p", "xl", "t_do_p", "s1", "s12"):
        assert col in params.columns, col
    genrou = params[params["model"] == "GENROU"]
    assert genrou[["xd", "xq_p", "t_qo_p"]].notna().all().all()
    gensal = params[params["model"] == "GENSAL"]
    assert len(gensal) >= 1
    assert gensal[["xq_p", "t_qo_p"]].isna().all().all(), "GENSAL has no q-axis transient"


def test_default_res_units_carry_fault_parameters_and_ledger_capacities():
    """`mbase_mva` for an inverter is the RES_LEDGER installed MW read as MVA —
    the same ledgered assumption the RAW writer's MBASE fallback makes."""
    t = load_unit_templates()
    cap = {e["name"]: e["p_mw"] for e in RES_LEDGER}
    inv = t.units[t.units["model"] == "inverter"]
    assert set(inv.index) == set(cap)
    for unit_id, row in inv.iterrows():
        assert row["mbase_mva"] == cap[unit_id]
        assert not row["include_in_inertia"]
    p = t.params[t.params["unit_id"].isin(cap)]
    assert set(p["param"]) == set(MODEL_PARAMS["inverter"]) == {"k_sc", "rx_sc"}


def test_default_classic_dataset_physics_ordering_holds():
    """xd > xd_p > xd_pp > xl > 0 on every synchronous machine — the guard
    the loader enforces, checked here on the data it ships."""
    params = load_unit_params()
    sync = params[params["model"].isin(SYNCHRONOUS_MODELS)]
    assert len(sync) == N_SYNC
    assert ((sync["xd"] > sync["xd_p"]) & (sync["xd_p"] > sync["xd_pp"])
            & (sync["xd_pp"] > sync["xl"]) & (sync["xl"] > 0)).all()


# --- the contract on hand-written files -------------------------------------

def test_v2_unit_round_trips(tmp_path):
    f = _write(tmp_path, {"G_X": _genrou_unit()})
    t = load_unit_templates(f)
    assert t.units.at["G_X", "model"] == "GENROU"
    assert len(t.params) == len(MODEL_PARAMS["GENROU"])
    assert load_unit_params(f).at["G_X", "h_s"] == 30.0
    assert load_unit_params(f).at["G_X", "source"] == "datasheet"


@pytest.mark.parametrize("field", ["xd", "s12"])
def test_missing_required_field_raises_rather_than_defaulting(tmp_path, field):
    """THE mutation target. Two cases on purpose: a defaulted xd=0 is ALSO
    caught by the reactance-ordering guard, so that case alone would pass for
    the wrong reason. A defaulted s12=0 satisfies every physics guard — only
    the required-field check can see it."""
    u = _genrou_unit()
    del u["params"][field]
    with pytest.raises(ContractError, match=field):
        load_unit_templates(_write(tmp_path, {"G_X": u}))


def test_unknown_param_for_the_model_is_rejected_not_ignored(tmp_path):
    """A typo'd field name would otherwise be silently dropped."""
    u = _genrou_unit(params={"xdd": {"value": 0.05, "source": "assumed"}})
    with pytest.raises(ContractError, match="xdd"):
        load_unit_templates(_write(tmp_path, {"G_X": u}))


def test_unknown_model_class_is_rejected(tmp_path):
    with pytest.raises(ContractError, match="model"):
        load_unit_templates(_write(tmp_path, {"G_X": _genrou_unit(model="GENCLS")}))


def test_unit_level_source_beside_params_is_rejected(tmp_path):
    """The laundering path: one tag over fourteen fields."""
    u = _genrou_unit()
    u["source"] = "datasheet"
    with pytest.raises(ContractError, match="per field"):
        load_unit_templates(_write(tmp_path, {"G_X": u}))


def test_bare_scalar_param_without_a_source_is_rejected(tmp_path):
    u = _genrou_unit()
    u["params"]["xd"] = 0.3
    with pytest.raises(ContractError, match="source"):
        load_unit_templates(_write(tmp_path, {"G_X": u}))


def test_bad_per_field_source_tag_is_rejected(tmp_path):
    u = _genrou_unit(params={"xd": {"value": 0.3, "source": "guessed"}})
    with pytest.raises(ContractError, match="source"):
        load_unit_templates(_write(tmp_path, {"G_X": u}))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "0.3", None])
def test_non_finite_or_non_numeric_value_is_rejected(tmp_path, value):
    u = _genrou_unit(params={"xd": {"value": value, "source": "datasheet"}})
    with pytest.raises(ContractError):
        load_unit_templates(_write(tmp_path, {"G_X": u}))


@pytest.mark.parametrize("field, value", [("xd_pp", 0.08), ("xl", 0.06), ("xd_p", 0.31)])
def test_reactance_ordering_violation_is_rejected(tmp_path, field, value):
    """xd > xd_p > xd_pp > xl > 0; each case breaks one inequality."""
    u = _genrou_unit(params={field: {"value": value, "source": "assumed"}})
    with pytest.raises(ContractError, match="xd"):
        load_unit_templates(_write(tmp_path, {"G_X": u}))


@pytest.mark.parametrize("field", ["t_do_p", "t_do_pp", "xd"])
def test_non_positive_reactance_or_time_constant_is_rejected(tmp_path, field):
    u = _genrou_unit(params={field: {"value": 0.0, "source": "assumed"}})
    with pytest.raises(ContractError, match=field):
        load_unit_templates(_write(tmp_path, {"G_X": u}))


def test_inverter_flagged_into_inertia_is_rejected(tmp_path):
    u = {
        "model": "inverter", "mbase_mva": 600.0, "include_in_inertia": True,
        "params": {"k_sc": {"value": 1.2, "source": "assumed"},
                   "rx_sc": {"value": 0.1, "source": "assumed"}},
    }
    with pytest.raises(ContractError, match="include_in_inertia"):
        load_unit_templates(_write(tmp_path, {"W_X": u}))


def test_inverter_is_absent_from_the_h_params_view(tmp_path):
    u = {
        "model": "inverter", "mbase_mva": 600.0, "include_in_inertia": False,
        "params": {"k_sc": {"value": 1.2, "source": "assumed"},
                   "rx_sc": {"value": 0.1, "source": "assumed"}},
    }
    f = _write(tmp_path, {"G_X": _genrou_unit(), "W_X": u})
    assert set(load_unit_templates(f).units.index) == {"G_X", "W_X"}
    assert set(load_unit_params(f).index) == {"G_X"}


def test_v1_flat_unit_loads_as_a_legacy_model_with_h_only(tmp_path):
    f = tmp_path / "u.yaml"
    f.write_text("units:\n  G_X: {h_s: 5.0, mbase_mva: 100.0, source: assumed, include_in_inertia: true}\n")
    t = load_unit_templates(f)
    assert t.units.at["G_X", "model"] == "legacy"
    assert t.params["param"].tolist() == ["h_s"]
    assert t.params["source"].tolist() == ["assumed"]
    assert load_unit_params(f).at["G_X", "source"] == "assumed"
