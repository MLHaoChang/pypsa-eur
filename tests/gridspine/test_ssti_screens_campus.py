# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The SSTI and sub-synchronous screens of a prepared campus run (grid-compliance GC-08)."""

import pandas as pd
import pytest

from gridspine.drivers import campus_study as cs
from gridspine.static import ssti_uif as su
from gridspine.static import subsync_scan as ss
from tests.gridspine.test_harmonics_campus import _hub, _run


def _hub_with_gas():
    """The test hub with a gas generator on the MV bus, which the draft takes as a genset (GAS)."""
    n = _hub()
    n.add("Generator", "gas", bus="mv", carrier="gas", p_nom=8.0)
    n.generators_t.p["gas"] = 0.0
    return n


def _profile_dir(tmp_path, **keys):
    import yaml

    from gridspine.templates.grid_codes import load_grid_code

    p = load_grid_code("generic_assumed", raw=True)
    p.pop("name", None)
    p.update(keys)
    d = tmp_path / "codes"
    d.mkdir()
    (d / "gc08_test.yaml").write_text(yaml.safe_dump(p, sort_keys=False))
    return (d,)


def test_the_step_writes_both_screens(tmp_path):
    run, _ = _run(tmp_path, _hub_with_gas())
    out = cs.ssti_screens_campus(run)
    uif = pd.read_csv(run / cs.SSTI_UIF_CSV)
    assert set(uif["machine"]) == {"GAS"}
    assert set(uif["case"]) == {"max", "min"}
    assert uif["assumed"].all() and (uif["threshold"] == su.DEFAULT_THRESHOLD).all()
    assert out.uif.verdict in ("flag", "clear")
    assert out.uif_threshold == []
    scan = pd.read_csv(run / cs.SUBSYNC_SCAN_CSV)
    assert scan["f_hz"].min() == 5.0 and scan["f_hz"].max() == 45.0
    res = pd.read_csv(run / cs.SUBSYNC_RESONANCE_CSV)
    assert set(res["kind"]) == {ss.SERIES, ss.SSCI}
    assert set(res["verdict"]) == {"not evaluated"}
    # the campus enters no series capacitor: never clear
    assert out.subsync_verdict == "not evaluated"
    assert (
        set(uif["label"])
        == set(res["label"])
        == {"screening; binding answer is the EMT study"}
    )


def test_the_profile_keys_reach_both_screens(tmp_path):
    run, _ = _run(tmp_path, _hub_with_gas())
    dirs = _profile_dir(
        tmp_path,
        ssti_uif_threshold={"value": 0.25, "clause": "TRGC 19", "source": "code"},
        subsync_torsional_clearance_hz={
            "value": 2.0,
            "clause": "TRGC 20",
            "source": "assumed",
        },
    )
    out = cs.ssti_screens_campus(run, "gc08_test", dirs)
    assert (out.uif.rows["threshold"] == 0.25).all() and not out.uif.rows[
        "assumed"
    ].any()
    assert out.uif_threshold == [
        {"key": "ssti_uif_threshold", "clause": "TRGC 19", "source": "code"}
    ]
    assert (out.subsync.resonance["clearance_hz"] == 2.0).all()
    # a clearance that is not the code's is assumed
    assert out.subsync.resonance["assumed"].all()
    assert out.subsync_clearance[0]["source"] == "assumed"


def test_without_a_genset_the_uif_is_not_evaluated(tmp_path):
    run, _ = _run(tmp_path, _hub())
    out = cs.ssti_screens_campus(run)
    assert out.uif.verdict == "not evaluated" and out.uif.notes == [su.NO_MACHINES]
    assert pd.read_csv(run / cs.SSTI_UIF_CSV).empty


@pytest.mark.parametrize(
    ("verdicts", "worst"),
    [
        (["clear", "flag", "check"], "flag"),
        (["clear", "check", "not evaluated"], "check"),
        (["clear", "not evaluated"], "not evaluated"),
        (["clear", "clear"], "clear"),
        ([], "not evaluated"),  # no series row compared nothing: never clear
    ],
)
def test_the_subsync_verdict_is_the_worst_series_row(
    tmp_path, monkeypatch, verdicts, worst
):
    run, _ = _run(tmp_path, _hub())
    real = ss.subsync_scan

    def fake(*a, **kw):
        study = real(*a, **kw)
        rows = [{"case": "max", "kind": ss.SERIES, "verdict": v} for v in verdicts]
        rows.append({"case": "max", "kind": ss.SSCI, "verdict": "not evaluated"})
        study.resonance = pd.DataFrame(rows, columns=list(ss.RESONANCE_COLUMNS))
        return study

    monkeypatch.setattr(ss, "subsync_scan", fake)
    assert cs.ssti_screens_campus(run).subsync_verdict == worst


def test_the_judged_threshold_and_clearance(tmp_path):
    run, _ = _run(tmp_path, _hub())
    out = cs.ssti_screens_campus(run)
    assert (out.threshold, out.clearance_hz) == (
        su.DEFAULT_THRESHOLD,
        ss.DEFAULT_CLEARANCE_HZ,
    )
    dirs = _profile_dir(
        tmp_path,
        ssti_uif_threshold={"value": 0.3, "clause": "TRGC 19", "source": "code"},
        subsync_torsional_clearance_hz={
            "value": 1.5,
            "clause": "TRGC 20",
            "source": "code",
        },
    )
    out = cs.ssti_screens_campus(
        str(run), "gc08_test", dirs
    )  # a str run dir is taken too
    assert (out.threshold, out.clearance_hz) == (0.3, 1.5)
    # both are the code's: nothing is assumed
    assert not out.subsync.resonance["assumed"].any()
