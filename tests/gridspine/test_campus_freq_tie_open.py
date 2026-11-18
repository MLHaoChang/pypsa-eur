"""
The island frequency check at hours whose solved PCC tie is open (study
harness 40, owner decision SH40-BRIDGE-2).

With the tie open the solve imports nothing, so there is no islanding step:
E1 is not run and reads "not applicable: tie open" with an empty ``ok``, and
the grid-forming bridge is not judged at that hour. E2, E3 and E5 are judged
exactly as at any hour. The tie state is read from the run's own
``campus_pcc.csv`` (``tie_open``), never from the campus file.

The run directory is written by hand (``test_campus_island_frequency_driver``),
with one empty grid-forming battery (e(t−1) = 0) and a cold genset, so a
judged bridge fails.
"""

import json
import math

import pandas as pd
import pytest

from gridspine.drivers import campus_study as D
from gridspine.schema.island import ISLAND_UNIT_HOURLY_CSV
from gridspine.static import frequency as F
from tests.gridspine.test_campus_island_frequency_driver import campus_spec, write_run

NA = "not applicable: tie open"


def empty_battery_run(path, tie_open, scenarios=("seamless",), spec=None):
    """
    Hours 0 and 1 are island hours; BESS1 holds nothing at either, the
    genset is cold. ``tie_open`` is the pcc file's column (None: no column).
    """
    run = write_run(
        path,
        spec or campus_spec(scenarios=scenarios),
        genset_online=0.0,
        selected=((0, "max_island_dp_up"), (1, "max_island_dp_up")),
    )
    pcc = pd.read_csv(run / "campus_pcc.csv")
    if tie_open is not None:
        pcc["tie_open"] = list(tie_open)
    pcc.to_csv(run / "campus_pcc.csv", index=False)
    unit = pd.read_csv(run / ISLAND_UNIT_HOURLY_CSV)
    bess = pd.DataFrame(
        [
            {
                "period": 2030,
                "hour": h,
                "unit_id": "BESS1",
                "soc_start_mwh": 0.0,
                "online": 1.0,
                "headroom_up_mw": 10.0,
                "headroom_down_mw": 10.0,
                "p_ups_it_mw": math.nan,
            }
            for h in sorted(unit["hour"].unique())
        ]
    )
    pd.concat([unit, bess], ignore_index=True).to_csv(
        run / ISLAND_UNIT_HOURLY_CSV, index=False
    )
    return run


def _events(run):
    return pd.read_csv(run / D.FREQ_EVENTS_CSV)


def test_an_open_tie_hour_is_not_judged_and_a_closed_one_is(tmp_path):
    run = empty_battery_run(tmp_path / "run", tie_open=[True, False, False])
    summary = D.island_frequency_campus(run)
    ev = _events(run)
    e1 = ev[(ev["event"] == "E1") & ev["i_max_pu"].isna()].set_index("hour")
    # hour 0: tie open: not run, ok empty, the reason in binding
    assert (
        e1.at[0, "binding"] == NA
        and pd.isna(e1.at[0, "ok"])
        and pd.isna(e1.at[0, "nadir_hz"])
    )
    assert not F.failed(e1.at[0, "ok"]) and not F.passed(e1.at[0, "ok"])
    # hour 1: tie closed: judged
    assert e1.at[1, "binding"] != NA and str(e1.at[1, "ok"]) in ("True", "False")
    assert not ev[
        (ev["hour"] == 0) & (ev["event"] == "E1") & ev["i_max_pu"].notna()
    ].shape[0]  # no Q8 E1 rerun
    bridge = pd.read_csv(run / D.FREQ_BRIDGE_CSV)
    assert bridge["hour"].tolist() == [1] and bridge["unit"].tolist() == ["BESS1"]
    assert summary["bridge_failing_hours"] == [
        [2030, 1]
    ]  # the empty battery fails where judged
    assert summary["tie_open_hours"] == [[2030, 0]]
    assert summary["e1_bridge_note"] is None
    assert summary["failing_hours"] == [
        [2030, h]
        for h in sorted({int(h) for h, ok in zip(ev["hour"], ev["ok"]) if F.failed(ok)})
    ]
    # the every-hour E1 marks hour 0 the same way
    sweep = pd.read_csv(run / D.FREQ_E1_HOURLY_CSV).set_index("hour")
    assert sweep.at[0, "binding"] == NA and pd.isna(sweep.at[0, "ok"])
    assert sweep.at[1, "binding"] != NA
    assert [2030, 0] not in summary["e1_failing_hours"]


def test_e2_e3_e5_are_judged_the_same_whatever_the_tie(tmp_path):
    closed = empty_battery_run(tmp_path / "closed", tie_open=None)
    opened = empty_battery_run(tmp_path / "open", tie_open=[True, True, True])
    D.island_frequency_campus(closed)
    D.island_frequency_campus(opened)
    rest = ["E2", "E3", "E5"]

    def pick(run):
        ev = _events(run)
        sel = ev[ev["event"].isin(rest)].reset_index(drop=True)
        return sel.assign(
            ok=sel["ok"].map(F.passed)
        )  # all judged; the file dtypes differ by the E1 rows

    pd.testing.assert_frame_equal(pick(closed), pick(opened), check_dtype=False)
    assert set(pick(opened)["event"]) >= {"E2", "E3"}


def test_every_island_hour_tie_open_reads_not_evaluated_never_pass(tmp_path):
    run = empty_battery_run(tmp_path / "run", tie_open=[True, True, True])
    summary = D.island_frequency_campus(run)
    assert (
        summary["tie_open_hours"] == [[2030, 0], [2030, 1]] == summary["island_hours"]
    )
    assert summary["e1_bridge_note"] == "not evaluated (tie open on every island hour)"
    assert (
        summary["bridge_failing_hours"] == []
        and pd.read_csv(run / D.FREQ_BRIDGE_CSV).empty
    )
    e1 = _events(run).loc[lambda d: d["event"] == "E1"]
    assert (e1["binding"] == NA).all() and e1["ok"].isna().all()
    assert tuple(summary) == D.FREQ_SUMMARY_KEYS
    assert json.loads((run / D.FREQ_SUMMARY_JSON).read_text()) == summary


def test_a_run_file_without_the_column_is_judged_as_before(tmp_path):
    run = empty_battery_run(tmp_path / "run", tie_open=None)
    summary = D.island_frequency_campus(run)
    assert summary["tie_open_hours"] == [] and summary["e1_bridge_note"] is None
    assert summary["bridge_failing_hours"] == [[2030, 0], [2030, 1]]
    assert not (_events(run)["binding"] == NA).any()


def test_the_campus_files_normally_open_flag_does_not_open_the_tie(tmp_path):
    """The tie state is the solve's (campus_pcc.csv), never the campus file's flag."""
    from tests.gridspine.test_island_schema import t

    s = campus_spec()
    s["campus"]["pcc"]["tie_normally_open"] = True
    s["campus"]["pcc"]["p_connection_mw"] = t(20.0)
    run = empty_battery_run(tmp_path / "run", tie_open=[False, False, False], spec=s)
    summary = D.island_frequency_campus(run)
    assert summary["tie_open_hours"] == [] and summary["bridge_failing_hours"] == [
        [2030, 0],
        [2030, 1],
    ]


# ── ok None: never failing, never passing ──────────────────────────────────


@pytest.mark.parametrize(
    "ok, failed, passed",
    [
        (True, False, True),
        (False, True, False),
        (None, False, False),
        (math.nan, False, False),
        ("True", False, True),
        ("False", True, False),
        ("", False, False),
        ("not applicable", False, False),
    ],
)
def test_ok_reads(ok, failed, passed):
    assert F.failed(ok) is failed and F.passed(ok) is passed


def test_the_cuts_skip_a_row_not_judged_in_memory_and_from_the_file(tmp_path):
    run = empty_battery_run(tmp_path / "run", tie_open=[True, True, True])
    D.island_frequency_campus(run)
    events, sweep = _events(run), pd.read_csv(run / D.FREQ_E1_HOURLY_CSV)
    assert not [r for r in F.cut_events(events, sweep) if r["event"] == "E1"]
    mem = pd.DataFrame(
        [
            {
                "period": 2030,
                "hour": 0,
                "event": "E1",
                "i_max_pu": math.nan,
                "ok": None,
                "binding": NA,
            }
        ]
    )
    assert F.cut_events(mem, mem.drop(columns=["i_max_pu"])) == []
    mem.loc[0, "ok"] = False
    assert len(F.cut_events(mem, None)) == 1


def test_the_gfm_settings_take_no_case_from_an_e1_not_run(tmp_path):
    run = empty_battery_run(tmp_path / "run", tie_open=[True, True, True])
    D.island_frequency_campus(run, every_hour=False)
    out = D.gfm_settings_campus(run, max_sims=40)
    assert out["cases"] and not [
        c for c in out["cases"] if c["key"].split()[-1] == "E1"
    ]


def test_no_island_hours_is_no_tie_open_note(tmp_path):
    """Nothing judged at all is not 'every island hour tie-open'."""
    run = write_run(tmp_path / "run", campus_spec(), selected=((0, "max_import"),))
    pcc = pd.read_csv(run / "campus_pcc.csv").assign(tie_open=True)
    pcc.to_csv(run / "campus_pcc.csv", index=False)
    summary = D.island_frequency_campus(run, every_hour=False)
    assert summary["island_hours"] == [] and summary["e1_bridge_note"] is None
