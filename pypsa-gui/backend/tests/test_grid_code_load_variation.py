"""Grid-code keys for load variation (asset-sizing loop AS-08)."""
import copy

import pytest

from gridspine.schema.contracts import ContractError
from gridspine.templates import grid_codes as G

NEW_KEYS = ("load_ramp_mw_per_min", "load_variation_limit", "lcl_ride_through_band", "stage1_d_lim_pct",
            "ride_through_transfer_counts")
SHIPPED = ("eu_rfg_dcc_ce", "generic_assumed", "us_ieee2800_nerc")


def _lim(**kw):
    return {"clause": "generic engineering", "source": "assumed", **kw}


def _profile(**over):
    p = copy.deepcopy(G.load_grid_code("generic_assumed", raw=True))
    for k in NEW_KEYS:
        p.pop(k, None)
    p.update(over)
    return p


@pytest.mark.parametrize("name", SHIPPED)
def test_shipped_profiles_load(name):
    assert G.load_grid_code(name)["name"] == name


@pytest.mark.parametrize("key,lim", [
    ("load_ramp_mw_per_min", _lim(value=30)),
    ("load_variation_limit", _lim(value=10, window_s=5)),
    ("lcl_ride_through_band", _lim(v_min=0.8, v_max=1.1, duration_s=1)),
    ("stage1_d_lim_pct", _lim(value=0.4)),
    ("ride_through_transfer_counts", _lim(value=False)),
])
def test_schema_accepts(key, lim):
    assert G.validate_profile("p", _profile(**{key: lim}))[key] == lim


@pytest.mark.parametrize("key,lim", [
    ("load_ramp_mw_per_min", _lim(value=0)),
    ("load_ramp_mw_per_min", _lim(value="fast")),
    ("load_variation_limit", _lim(value=10)),                       # no window
    ("load_variation_limit", _lim(value=10, window_s=0)),
    ("lcl_ride_through_band", _lim(v_min=1.2, v_max=1.1, duration_s=1)),
    ("lcl_ride_through_band", _lim(v_min=0.8, v_max=1.1)),          # no duration
    ("stage1_d_lim_pct", _lim(value=-1)),
    ("ride_through_transfer_counts", _lim(value="yes")),
    ("load_ramp_mw_per_min", {"value": 30}),                        # no clause or tag
])
def test_schema_rejects(key, lim):
    with pytest.raises(ContractError):
        G.validate_profile("p", _profile(**{key: lim}))


def test_missing_keys_fall_back_to_marked_default():
    lv = G.load_variation_limits(_profile())
    assert set(lv) == set(NEW_KEYS)
    for k, v in lv.items():
        assert v["defaulted"] is True
        assert v["source"] == "assumed"


def test_set_key_is_not_defaulted():
    lv = G.load_variation_limits(_profile(load_ramp_mw_per_min=_lim(value=7)))
    assert lv["load_ramp_mw_per_min"]["value"] == 7
    assert lv["load_ramp_mw_per_min"]["defaulted"] is False


def test_new_values_are_assumed_and_cited():
    for name in SHIPPED:
        p = G.load_grid_code(name)
        for k in NEW_KEYS:
            if k in p:
                assert p[k]["source"] == "assumed"
                assert p[k]["clause"]


def test_transfer_warning_follows_key():
    assert G.ride_through_transfer_warning(_profile(ride_through_transfer_counts=_lim(value=True))) is None
    for p in (_profile(ride_through_transfer_counts=_lim(value=False)), _profile()):
        w = G.ride_through_transfer_warning(p)
        assert w and "ride-through" in w
