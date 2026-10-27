"""The scorecard's voltage-unbalance row (DC grid readiness 12, part 2)."""
import json

import pytest

from harness import grid_readiness_calculations as R
from services.adequacy import grid_readiness as G
from tests.test_grid_readiness import PROFILE, _harmonic_site, _rows


def _profile_dir(tmp_path, **over):
    from gridspine.templates.grid_codes import load_grid_code

    p = load_grid_code(PROFILE, raw=True)
    p.update(over)
    codes = tmp_path / "codes"
    codes.mkdir()
    (codes / "with_unbalance.yaml").write_text(json.dumps(p))
    return codes


LIMIT = {"value": 1.0, "clause": "unbalance clause", "source": "code"}


def _row(tmp_path, n, limit=LIMIT):
    over = {} if limit is None else {"unbalance_limit_pct": limit}
    codes = _profile_dir(tmp_path, **over)
    return _rows(G.grid_readiness(tmp_path, n, "with_unbalance", profile_dirs=(codes,)))["voltage_unbalance"]


def _site(share, rated=20.0, sk=1400.0):
    n = _harmonic_site(rated=rated, sk=sk)
    n.links["single_phase_share"] = [None, share]
    return n


def test_without_an_unbalance_limit_the_row_is_unknown(tmp_path):
    row = _row(tmp_path, _site(0.5), limit=None)
    assert row["verdict"] == "unknown" and row["limit"] is None and row["project_value"] is None
    assert row["note"] == "The grid code sets no unbalance limit"


def test_u2_is_the_unbalanced_load_over_s_sc2_with_the_c_factor(tmp_path):
    row = _row(tmp_path, _site(0.5))
    expected = 100.0 * 0.5 * 20.0 / (1400.0 / 1.1)
    assert row["project_value"] == pytest.approx(expected, abs=1e-4)
    assert row["limit"] == 1.0 and row["verdict"] == "pass"
    assert row["unit"] == "%\u00a0of\u00a0positive\u00a0sequence"   # wraps as one unit
    assert row["note"].startswith("Screening upper bound: 10 MVA of single-phase load")
    assert "equal negative- and positive-sequence impedance" in row["note"]
    assert "IEC 60909 voltage factor" in row["note"]
    assert R.disposition(row) == ("checked", "pass")
    assert row["unbalance"]["margin_pct"] == pytest.approx(1.0 - expected, rel=1e-9)
    assert row["unbalance"]["band"] == "PoC S_k''" and row["unbalance"]["label"].startswith("screening")


def test_over_the_limit_warns(tmp_path):
    row = _row(tmp_path, _site(1.0, rated=20.0, sk=1000.0))
    assert row["project_value"] > 1.0 and row["verdict"] == "warn"
    assert R.disposition(row) == ("checked", "warn")


def test_a_unit_without_its_share_is_unknown_and_points_at_it(tmp_path):
    n = _harmonic_site()
    row = _row(tmp_path, n)
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"].startswith("compressor has no Single-phase share or Rated apparent power; "
                                  "a unit without them blocks a pass.")
    assert "At least" not in row["note"] and "Screening upper bound" not in row["note"]
    assert row["target"]["entity"] == "compressor" and row["target"]["field"] == "single_phase_share"


def test_a_partial_sum_is_said_as_a_floor_without_a_number(tmp_path):
    n = _site(0.5)
    n.add("Link", "pump", bus0="mv", bus1="nh3", p_nom=1.0)
    n.links.loc["pump", ["eh_process", "rated_mva"]] = [True, 5.0]
    row = _row(tmp_path, n)
    floor = 100.0 * 0.5 * 20.0 / (1400.0 / 1.1)
    assert row["verdict"] == "unknown" and row["project_value"] is None
    assert row["note"].startswith("pump has no Single-phase share")
    assert f"At least {floor:.2g} % from the units with inputs." in row["note"]
    assert row["target"]["entity"] == "pump"


def test_a_floor_already_over_the_limit_warns_without_a_number(tmp_path):
    """A unit without inputs only adds to u2, so a floor already over the limit is the rule's otherwise
    verdict, still with no project value and the floor said in words."""
    n = _site(1.0, rated=20.0, sk=1000.0)
    n.add("Link", "pump", bus0="mv", bus1="nh3", p_nom=1.0)
    n.links.loc["pump", ["eh_process", "rated_mva"]] = [True, 5.0]
    row = _row(tmp_path, n)
    floor = 100.0 * 1.0 * 20.0 / (1000.0 / 1.1)
    assert floor > LIMIT["value"]
    assert row["verdict"] == G.RULE_UNBALANCE["otherwise"] == "warn" and row["project_value"] is None
    assert f"At least {floor:.2g} % from the units with inputs." in row["note"]
    assert row["target"]["entity"] == "pump"


def test_a_share_outside_zero_to_one_is_unknown_not_an_error(tmp_path):
    row = _row(tmp_path, _site(2.0))
    assert row["verdict"] == "unknown" and "single_phase_share" in row["note"]


def test_no_fault_level_is_unknown(tmp_path):
    n = _site(0.5)
    n.buses["eh_sk_mva"] = [float("nan")] * 3
    row = _row(tmp_path, n)
    assert row["verdict"] == "unknown" and row["target"]["field"] == "eh_sk_mva"
