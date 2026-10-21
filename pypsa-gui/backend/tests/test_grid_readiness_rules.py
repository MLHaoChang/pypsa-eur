"""Checked results S9: every scorecard verdict on a number is the row's declared rule.

A row that judges a project value against its limit carries `rule` (a comparator
and the verdict otherwise) and sets its verdict through `_judge`, so a checker can
recompute it from the stored numbers. Rows whose verdict depends on inputs the
scorecard does not store are named below, not given a rule.
"""
from __future__ import annotations

import pytest

from services.adequacy import grid_readiness as G
from services.adequacy.scr_gate import PASS_SCR, classify_scr
from tests.test_grid_readiness import DC_IDS, PROFILE, TIDS, _project, _rows

# The warn band is the in-hour uplift (not stored); the CI rule; the band of concern.
NOT_SCALAR = ("energization_peak:", "lole", "oscillation")
CATEGORICAL = ("frequency_support", "dynamic:", "grid_former", "ride_through:")
RULED = {"scr": G.RULE_SCR, "ramp_rate": G.RULE_RAMP, "rapid_voltage_change": G.RULE_RVC,
         "reactive_range": G.RULE_REACTIVE}


def _named(row_id, prefixes):
    return any(row_id == p or (p.endswith(":") and row_id.startswith(p)) for p in prefixes)


def _cases():
    yield from ((tid, None) for tid in TIDS)
    yield from ((tid, {}) for tid in DC_IDS)


@pytest.mark.parametrize("tid, dc_load", list(_cases()))
def test_every_numeric_verdict_is_its_declared_rule_or_named(tmp_path, tid, dc_load):
    d, n = _project(tmp_path, tid, dc_load=dc_load)
    for row in G.grid_readiness(d, n, PROFILE)["rows"]:
        if row["id"] in RULED:
            assert row["rule"] == RULED[row["id"]], row["id"]
        if not isinstance(row["project_value"], (int, float)) or row["verdict"] == "unknown":
            continue
        if "rule" in row:
            assert G._judge(row["project_value"], row["limit"], row["rule"]) == row["verdict"], row["id"]
        else:
            assert _named(row["id"], NOT_SCALAR + CATEGORICAL), row["id"]


@pytest.mark.parametrize("scr", [0.5, 2.9, 2.999999, PASS_SCR - 5e-10, 3.0, PASS_SCR + 5e-10, 3.000001, 3.1, 12.0])
def test_the_scr_rule_agrees_with_the_scr_gate(scr):
    gate = "pass" if classify_scr(scr).scr == "pass" else "warn"
    assert G._judge(scr, PASS_SCR, G.RULE_SCR) == gate


def test_a_rounded_boundary_is_a_disagreement_a_checker_must_withhold(tmp_path):
    d, n = _project(tmp_path, "eh_datacenter")
    n.buses.at["grid", "eh_sk_mva"] = 89.988                     # IBR 30 MVA: SCR 2.9996
    row = _rows(G.grid_readiness(d, n, PROFILE))["scr"]
    assert row["verdict"] == "warn" and row["project_value"] == 3.0
    # Recomputed on the stored, rounded value the rule says pass: not a re-verdict, a withholding.
    assert G._judge(row["project_value"], row["limit"], row["rule"]) == "pass"


def test_an_unknown_comparator_is_refused():
    with pytest.raises(ValueError):
        G._judge(1.0, 1.0, {"comparator": "~", "otherwise": "warn", "tolerance": 0.0})
