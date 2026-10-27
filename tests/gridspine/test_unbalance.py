"""The voltage-unbalance screen (datacenter grid readiness issue 12, part 2):
u2 = S_unbal / S_sc2 at the PoC, with S_sc2 from the negative-sequence network,
checked against a hand calculation in ohms."""
import math

import pandas as pd
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.static import island_sc as ISC
from gridspine.static import island_sc_seq as SEQ
from gridspine.static import unbalance as UB
from gridspine.templates.grid_codes import load_grid_code, validate_profile
from tests.gridspine.test_campus import t
from tests.gridspine.test_island_relays import feeder

UN = 20.0                                         # kV
X2 = 0.25                                         # the genset's x2, not its x''d (0.15)


def grid_feeder():
    """A 20 kV PCC at A (grid S_k'' 300/200 MVA, R/X 0.1) -> 1 km -> B -> 1 km -> C,
    with a 12.5 MVA genset at A whose x2 differs from its x''d."""
    s = feeder([], genset_at_a=True, b_unit=None)
    s["campus"]["units"]["G"]["island"]["x2_pu"] = t(X2)
    return build_campus(s)


def nets(campus, island=False):
    sources = ISC._sources(campus, None)
    return {band: SEQ.sequence_net(campus, sources, island=island, case=band) for band in UB.BANDS}


def z_parallel(a, b):
    return a * b / (a + b)


# Hand calculation, max case, in ohms at 20 kV.
Z_GRID = 1.1 * UN ** 2 / 300.0 * complex(0.1, 1.0) / math.hypot(0.1, 1.0)    # c_max Un^2 / Sk''
K_G = 1.1 / (1.0 + 0.15 * 0.6)                                              # IEC 60909 K_G, x''d 0.15, sin phi 0.6
Z_GEN2 = K_G * complex(0.07 * X2, X2) * UN ** 2 / 12.5                      # negative sequence: x2
Z_KM = complex(0.1, 0.1)                                                   # cable at 20 degC, one km
Z_P = z_parallel(Z_GRID, Z_GEN2)


def loads(*rows):
    return pd.DataFrame(rows, columns=["load", "poc", "s_mva", "single_phase_share"])


def test_s_sc2_is_the_negative_sequence_thevenin_power_by_hand():
    sn = nets(grid_feeder())["max"]
    assert UB.s_sc2_mva(sn, "C") == pytest.approx(UN ** 2 / abs(Z_P + 2 * Z_KM), rel=1e-9)
    assert UB.s_sc2_mva(sn, "A") == pytest.approx(UN ** 2 / abs(Z_P), rel=1e-9)


def test_the_screen_inverts_y2_not_y1():
    """With x2 != x''d the positive-sequence answer differs: the test would catch Y1."""
    z_gen1 = K_G * complex(0.07 * 0.15, 0.15) * UN ** 2 / 12.5
    s1 = UN ** 2 / abs(z_parallel(Z_GRID, z_gen1) + 2 * Z_KM)
    s2 = UB.s_sc2_mva(nets(grid_feeder())["max"], "C")
    assert abs(s2 - s1) / s1 > 0.05


def test_u2_matches_the_hand_calculation_for_one_poc():
    table = UB.unbalance_table(loads(("L1", "C", 4.0, 0.5)), nets(grid_feeder()), limit_pct=1.0)
    row = table.set_index("band").loc["max"]
    expected = 100.0 * 2.0 * abs(Z_P + 2 * Z_KM) / UN ** 2
    assert row["s_unbalanced_mva"] == pytest.approx(2.0)
    assert row["u2_pct"] == pytest.approx(expected, rel=1e-9)
    assert row["u2_own_pct"] == pytest.approx(expected, rel=1e-9)
    assert row["margin_pct"] == pytest.approx(1.0 - expected, rel=1e-9)
    assert row["verdict"] == "pass" and row["label"].startswith("screening")


def test_the_min_band_binds_and_both_are_shown():
    table = UB.unbalance_table(loads(("L1", "C", 4.0, 0.5)), nets(grid_feeder()), limit_pct=1.0)
    assert list(table["band"]) == ["min", "max"]
    by = table.set_index("band")
    assert by.at["min", "u2_pct"] > by.at["max", "u2_pct"]
    assert "min case binds" in by.at["min", "note"]


def test_a_load_at_another_poc_adds_through_the_transfer_impedance():
    table = UB.unbalance_table(loads(("LB", "B", 3.0, 1.0), ("LC", "C", 2.0, 1.0)), nets(grid_feeder()), 1.0)
    row = table[(table["band"] == "max") & (table["poc"] == "C")].iloc[0]
    z_cc, z_cb = Z_P + 2 * Z_KM, Z_P + Z_KM
    assert row["u2_own_pct"] == pytest.approx(100.0 * 2.0 * abs(z_cc) / UN ** 2, rel=1e-9)
    assert row["u2_pct"] == pytest.approx(100.0 * (2.0 * abs(z_cc) + 3.0 * abs(z_cb)) / UN ** 2, rel=1e-9)
    assert "transfer" in row["note"]


def test_no_negative_sequence_path_is_unknown():
    """An island of converters only: their neg_seq sits outside Y2, so no S_sc2."""
    camp = build_campus(feeder([]))
    sn = nets(camp, island=True)
    assert UB.s_sc2_mva(sn["max"], "C") is None
    table = UB.unbalance_table(loads(("L1", "C", 4.0, 0.5)), sn, limit_pct=1.0)
    assert set(table["verdict"]) == {"unknown"}
    assert table["note"].str.contains("No negative-sequence path").all()


@pytest.mark.parametrize("delta, verdict", [(0.0, "pass"), (1e-6, "pass"), (-1e-6, "fail")])
def test_the_verdict_on_both_sides_of_the_limit(delta, verdict):
    u2 = UB.u2_pct(2.0, 200.0)
    assert u2 == pytest.approx(1.0)
    v, margin = UB.judge(u2, 1.0 + delta)
    assert v == verdict and margin == pytest.approx(delta, abs=1e-12)


def test_no_limit_is_unknown():
    assert UB.judge(0.5, None) == ("unknown", None)
    table = UB.unbalance_table(loads(("L1", "C", 4.0, 0.5)), nets(grid_feeder()), limit_pct=None)
    assert set(table["verdict"]) == {"unknown"} and table["note"].str.contains(UB.NO_LIMIT).all()


def test_a_load_without_its_share_blocks_a_pass_but_not_a_fail():
    n = nets(grid_feeder())
    table = UB.unbalance_table(loads(("L1", "C", 4.0, 0.5), ("L2", "C", 4.0, None)), n, limit_pct=10.0)
    assert set(table["verdict"]) == {"unknown"} and table.iloc[0]["no_input_loads"] == ["L2"]
    table = UB.unbalance_table(loads(("L1", "C", 400.0, 1.0), ("L2", "C", None, 0.5)), n, limit_pct=0.1)
    assert set(table["verdict"]) == {"fail"}


@pytest.mark.parametrize("share", [-0.1, 1.5])
def test_a_share_outside_zero_to_one_is_refused(share):
    with pytest.raises(ContractError, match="single_phase_share"):
        UB.unbalanced_mva(loads(("L1", "C", 4.0, share)))


def test_a_poc_not_in_the_network_is_refused():
    with pytest.raises(ContractError, match="not in the network"):
        UB.z2_transfer(nets(grid_feeder())["max"], ["Z"])


def _profile(**over):
    p = load_grid_code("generic_assumed", raw=True)
    p.update(over)
    return p


def test_the_profile_limit_is_optional_and_cited():
    assert UB.limit_from_profile(validate_profile("p", _profile())) == (None, [])
    p = validate_profile("p", _profile(unbalance_limit_pct={"value": 1.0, "clause": "c 1", "source": "code"}))
    assert UB.limit_from_profile(p) == (1.0, [{"key": "unbalance_limit_pct", "clause": "c 1", "source": "code"}])


@pytest.mark.parametrize("value", [0.0, -1.0, "x"])
def test_a_non_positive_profile_limit_is_refused(value):
    with pytest.raises(ContractError, match="unbalance_limit_pct"):
        validate_profile("p", _profile(unbalance_limit_pct={"value": value, "clause": "c", "source": "code"}))
