"""The campus study's flicker screen, ``campus_flicker.csv`` (grid-to-process issue 17, PR 2)."""
import math

import pandas as pd
import pytest

from gridspine.drivers.campus_study import (
    FLICKER_CSV,
    SK_FROM_NETWORK,
    SK_TYPICAL,
    flicker_campus,
)
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_harmonics_campus import _hub, _run


def _flicker_hub(sk=2000.0, step=(20.0, None), rate=(2.0, 6.0)):
    n = _hub(sk=sk)
    n.links["flicker_step_mva"] = [math.nan, *(math.nan if s is None else s for s in step)]
    n.links["flicker_changes_per_min"] = [math.nan, *rate]
    return n


def test_the_csv_holds_the_poc_and_every_unit(tmp_path):
    run, _ = _run(tmp_path, _flicker_hub())
    out = flicker_campus(run)
    on_disk = pd.read_csv(run / FLICKER_CSV)
    assert list(on_disk["level"]) == ["poc", "unit", "unit"]
    assert list(out.loc[out["level"] == "unit", "unit"]) == ["compressor", "pump"]
    poc = out.iloc[0]
    assert poc["sk_mva"] == 2000.0 and poc["sk_mva_source"] == SK_FROM_NETWORK
    assert poc["no_input_units"] == ["pump"]                    # step missing: kept, never skipped
    assert poc["verdict"] == "unknown"
    pump = out[out["unit"] == "pump"].iloc[0]
    assert pump["note"] == "no input: Largest power step not set"
    compressor = out[out["unit"] == "compressor"].iloc[0]
    assert compressor["d_pct"] == pytest.approx(1.0, rel=1e-12)
    assert bool(poc["assumed"]) is True                         # the demo limits are assumed


def test_the_typical_fallback_is_said_and_assumed(tmp_path):
    run, _ = _run(tmp_path, _flicker_hub(sk=math.nan))
    out = flicker_campus(run)
    assert (out["sk_mva_source"] == SK_TYPICAL).all() and out["assumed"].all()
    assert all("no verdict here is a pass to rely on" in s for s in out["note"])


def test_doubling_the_campus_fault_level_halves_pst(tmp_path):
    def edit(k):
        def f(spec):
            spec["campus"]["pcc"]["sk_min_mva"]["value"] = k
        return f
    hub = _flicker_hub(step=(20.0, 5.0))
    a = flicker_campus(_run(tmp_path / "a", hub, edit(1000.0))[0]).iloc[0]
    b = flicker_campus(_run(tmp_path / "b", hub, edit(2000.0))[0]).iloc[0]
    assert abs(b["pst"] - a["pst"] / 2) < 1e-9


def test_a_changed_project_is_refused(tmp_path):
    run, path = _run(tmp_path, _flicker_hub())
    _flicker_hub(sk=3000.0).export_to_netcdf(str(path))
    with pytest.raises(ContractError, match="prepare the run again"):
        flicker_campus(run)
