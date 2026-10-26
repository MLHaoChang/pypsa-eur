"""The campus study's current-distortion screen, ``campus_tdd.csv`` (datacenter readiness 12, part 1)."""
import pandas as pd
import pytest
import yaml

from gridspine.drivers.campus_study import (
    CAMPUS_YAML,
    SK_FROM_NETWORK,
    TDD_CSV,
    tdd_campus,
)
from gridspine.static import current_tdd as td
from tests.gridspine.test_harmonics_campus import _hub, _run


def _rated_load(run):
    units = yaml.safe_load((run / CAMPUS_YAML).read_text())["campus"]["units"]
    return sum(u["p_mw"]["value"] / u["pf"]["value"] for u in units.values() if u["kind"] == "load")


def test_the_csv_holds_the_pcc_row_with_i_l_from_the_rated_load(tmp_path):
    run, _ = _run(tmp_path, _hub(rated=(15.0, 5.0)))
    out = tdd_campus(run)
    on_disk = pd.read_csv(run / TDD_CSV)
    assert list(on_disk["level"]) == ["poc"] and len(out) == 1
    row = out.iloc[0]
    i_l = _rated_load(run)
    assert i_l > 0 and row["i_l_mva"] == pytest.approx(i_l, rel=1e-12)
    assert row["sk_mva"] == 2000.0 and row["sk_mva_source"] == SK_FROM_NETWORK
    assert row["isc_il"] == pytest.approx(2000.0 / i_l, rel=1e-12)
    assert row["standard"] == "ieee519-2014" and row["verdict"] in ("pass", "fail")
    assert td.I_L_NOTE in row["note"] and td.STRICTER in row["note"]
    assert bool(row["assumed"]) is True and row["generation_units"] == []


def test_a_missing_unit_input_blocks_a_pass(tmp_path):
    run, _ = _run(tmp_path, _hub())                             # pump has no rated_mva
    row = tdd_campus(run).iloc[0]
    assert row["no_input_units"] == ["pump"] and row["verdict"] in ("unknown", "fail")


def test_no_load_unit_is_unknown(tmp_path):
    def drop_loads(spec):
        units = spec["campus"]["units"]
        for name in [k for k, u in units.items() if u["kind"] == "load"]:
            units.pop(name)
    run, _ = _run(tmp_path, _hub(rated=(15.0, 5.0)), drop_loads)
    row = tdd_campus(run).iloc[0]
    assert row["verdict"] == "unknown" and pd.isna(row["i_l_mva"])
    assert "I_L is unknown" in row["note"]


def test_a_profile_without_the_standard_is_unknown(tmp_path):
    run, _ = _run(tmp_path, _hub(rated=(15.0, 5.0)))
    row = tdd_campus(run, profile="us_ieee2800_nerc").iloc[0]
    assert row["verdict"] == "unknown" and "names no current-distortion standard" in row["note"]

