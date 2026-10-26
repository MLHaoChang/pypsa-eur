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
    assert "screening" in row["note"] and "Z2 = Z1" in row["note"]
    assert R.disposition(row) == ("checked", "pass")


def test_over_the_limit_warns(tmp_path):
    row = _row(tmp_path, _site(1.0, rated=20.0, sk=1000.0))
    assert row["project_value"] > 1.0 and row["verdict"] == "warn"
    assert R.disposition(row) == ("checked", "warn")


def test_a_unit_without_its_share_is_unknown_and_points_at_it(tmp_path):
    n = _harmonic_site()
    row = _row(tmp_path, n)
    assert row["verdict"] == "unknown" and "compressor" in row["note"]
    assert row["target"]["entity"] == "compressor" and row["target"]["field"] == "single_phase_share"


def test_a_share_outside_zero_to_one_is_unknown_not_an_error(tmp_path):
    row = _row(tmp_path, _site(2.0))
    assert row["verdict"] == "unknown" and "single_phase_share" in row["note"]


def test_no_fault_level_is_unknown(tmp_path):
    n = _site(0.5)
    n.buses["eh_sk_mva"] = [float("nan")] * 3
    row = _row(tmp_path, n)
    assert row["verdict"] == "unknown" and row["target"]["field"] == "eh_sk_mva"
