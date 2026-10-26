"""The campus study's harmonic screen, ``campus_harmonics.csv`` (grid-to-process issue 17)."""
import math

import pandas as pd
import pytest
import yaml

from gridspine.drivers.campus_study import (
    CAMPUS_YAML,
    HARMONICS_CSV,
    SK_CAMPUS,
    SK_FROM_NETWORK,
    SK_TYPICAL,
    draft_from_project,
    harmonics_campus,
    prepare_campus,
)
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_campus_hourly import solved_hub


def _hub(sk=2000.0, rated=(15.0, None)):
    n = solved_hub()
    n.add("Bus", "nh3", carrier="NH3")
    for name in ("compressor", "pump"):
        n.add("Link", name, bus0="mv", bus1="nh3", p_nom=5.0)
        n.links_t.p0[name] = 5.0
        n.links_t.p1[name] = -5.0
    n.links["eh_process"] = [False, True, True]
    n.links["rated_mva"] = [math.nan, *(math.nan if r is None else r for r in rated)]
    n.links["harmonic_source"] = ["", "6-pulse", "12-pulse"]
    n.buses.loc["grid", "eh_sk_mva"] = sk
    return n


def _run(tmp_path, n, edit=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "network.nc"
    n.export_to_netcdf(str(path))
    spec = draft_from_project(path).spec
    if edit:
        edit(spec)
    prepare_campus(tmp_path / "run", spec, path)
    return tmp_path / "run", path


def test_the_csv_holds_the_poc_and_every_unit(tmp_path):
    run, _ = _run(tmp_path, _hub())
    out = harmonics_campus(run)
    on_disk = pd.read_csv(run / HARMONICS_CSV)
    assert list(on_disk["level"]) == ["poc", "unit", "unit"]
    assert list(out.loc[out["level"] == "unit", "unit"]) == ["compressor", "pump"]
    poc = out.iloc[0]
    assert poc["sk_mva"] == 2000.0
    assert poc["sk_mva_source"] == SK_FROM_NETWORK
    assert poc["no_input_units"] == ["pump"]                    # rated_mva missing: kept, never skipped
    pump = out[out["unit"] == "pump"].iloc[0]
    assert pump["verdict"] == "unknown" and pump["note"].startswith("no input")
    assert bool(poc["assumed"]) is True                         # demo spectrum, no code limit


def test_the_typical_fallback_is_said_and_assumed(tmp_path):
    run, _ = _run(tmp_path, _hub(sk=math.nan))
    out = harmonics_campus(run)
    assert (out["sk_mva_source"] == SK_TYPICAL).all()
    assert out["assumed"].all()
    assert all("no verdict here is a pass to rely on" in s for s in out["note"])


def test_an_edited_campus_fault_level_is_the_campus_description(tmp_path):
    def edit(spec):
        spec["campus"]["pcc"]["sk_min_mva"]["value"] = 1234.0
    run, _ = _run(tmp_path, _hub(), edit)
    out = harmonics_campus(run)
    assert out.iloc[0]["sk_mva"] == 1234.0
    assert out.iloc[0]["sk_mva_source"] == SK_CAMPUS


def test_doubling_the_campus_fault_level_halves_the_poc_distortion(tmp_path):
    def edit(k):
        def f(spec):
            spec["campus"]["pcc"]["sk_min_mva"]["value"] = k
        return f
    a = harmonics_campus(_run(tmp_path / "a", _hub(rated=(15.0, 5.0)), edit(1000.0))[0]).iloc[0]
    b = harmonics_campus(_run(tmp_path / "b", _hub(rated=(15.0, 5.0)), edit(2000.0))[0]).iloc[0]
    assert b["thd_pct"] == pytest.approx(a["thd_pct"] / 2, rel=1e-9)


def test_a_changed_project_is_refused(tmp_path):
    run, path = _run(tmp_path, _hub())
    _hub(sk=3000.0).export_to_netcdf(str(path))
    with pytest.raises(ContractError, match="prepare the run again"):
        harmonics_campus(run)


def test_the_campus_yaml_is_not_rewritten(tmp_path):
    run, _ = _run(tmp_path, _hub())
    before = (run / CAMPUS_YAML).read_text()
    harmonics_campus(run)
    assert (run / CAMPUS_YAML).read_text() == before
    assert yaml.safe_load(before)["campus"]["pcc"]["pypsa_name"] == "grid"
