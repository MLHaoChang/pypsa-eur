"""The island checks over worker processes (``gridspine.static.parallel``):
the same tables in the same order at any worker count, a worker's error
raised with its traceback, and a stop that leaves no worker behind."""
import json
import multiprocessing
import time

import pandas as pd
import pytest

from gridspine.static import frequency as F
from gridspine.static import parallel as P
from tests.gridspine.test_frequency_events import hourly, island_tables, ups_campus


def _context(base):
    return {"base": base}


def _square(c, x):
    return c["base"] + x * x


def _fail_on_three(c, x):
    if x == 3:
        raise ValueError(f"item {x} is bad")
    return x


def _slow(c, x):
    time.sleep(0.2)
    return x


def test_the_map_keeps_the_input_order_at_any_worker_count():
    items = list(range(12))
    serial = P.ordered_map(_square, items, _context, (100,), n_workers=1)
    assert serial == [100 + x * x for x in items]
    assert P.ordered_map(_square, items, _context, (100,), n_workers=3) == serial


def test_a_worker_s_error_is_raised_with_its_traceback_and_no_worker_is_left():
    with pytest.raises(ValueError, match="item 3 is bad") as exc:
        P.ordered_map(_fail_on_three, range(8), _context, (0,), n_workers=2)
    assert "_fail_on_three" in str(exc.value.__cause__)         # the worker's own traceback
    assert multiprocessing.active_children() == []


def test_a_stop_ends_the_map_and_its_workers():
    calls = []

    def should_stop():
        calls.append(1)
        return len(calls) > 2                         # past the first poll: the workers are running
    with pytest.raises(P.Stopped):
        P.ordered_map(_slow, range(40), _context, (0,), n_workers=2, should_stop=should_stop)
    assert multiprocessing.active_children() == []
    with pytest.raises(P.Stopped):                    # serially: asked before each item
        P.ordered_map(_slow, range(3), _context, (0,), n_workers=1, should_stop=lambda: True)


def test_the_worker_count(monkeypatch, caplog):
    monkeypatch.setenv(P.WORKERS_ENV, "3")
    assert P.workers() == 3
    monkeypatch.setenv(P.WORKERS_ENV, "abc")
    assert P.workers() == 1 and "not a whole number" in caplog.text   # warned, run serially, not failed
    monkeypatch.delenv(P.WORKERS_ENV)
    monkeypatch.setattr(P.os, "sched_getaffinity", lambda pid: set(range(16)), raising=False)
    assert P.workers() == P.MAX_DEFAULT_WORKERS                 # never more than 4 by default
    monkeypatch.setattr(P.os, "sched_getaffinity", lambda pid: {0, 1}, raising=False)
    assert P.workers() == 1                                     # one CPU left to the backend
    monkeypatch.setattr(P.os, "sched_getaffinity", lambda pid: set(range(16)), raising=False)
    monkeypatch.setattr(P.sys, "frozen", True, raising=False)
    monkeypatch.setenv(P.WORKERS_ENV, "4")
    assert P.workers() == 1                                     # a packaged app runs serially, whatever the env
    assert P.used(4, 2) == 2 and P.used(4, 0) == 1 and P.used(1, 9) == 1


def _fail_from_two(c, x):
    if x == 2:
        time.sleep(0.5)                               # fails after item 5 has
        raise ValueError("item 2")
    if x == 5:
        raise ValueError("item 5")
    return x


def test_the_first_failure_in_input_order_is_raised_as_serially():
    for n in (1, 3):
        with pytest.raises(ValueError, match="item 2"):
            P.ordered_map(_fail_from_two, range(8), _context, (0,), n_workers=n)
    assert multiprocessing.active_children() == []


def test_the_study_and_the_cuts_are_the_same_tables_at_any_worker_count():
    camp = ups_campus(transfers=False, scenarios=("seamless", "ups_bridged", "planned"))
    ih, iu = island_tables()
    hours = [(2030, 0), (2030, 1)]
    one = F.campus_frequency_study(camp, hourly(), hours, island_unit_hourly=iu, t_end=10.0, workers=1)
    two = F.campus_frequency_study(camp, hourly(), hours, island_unit_hourly=iu, t_end=10.0, workers=2)
    for name in ("events", "losses", "bridge", "planned", "ups"):
        assert getattr(one, name).to_csv(index=False) == getattr(two, name).to_csv(index=False), name
    assert not one.events["ok"].all()                            # so the cuts have events to invert
    sweep = F.e1_every_hour(camp, hourly(), ih, iu)
    cuts = [F.propose_cuts(camp, hourly(), one.events, sweep, island_hourly=ih, island_unit_hourly=iu, workers=w)
            for w in (1, 2)]
    assert len(cuts[0]) and cuts[0].to_csv(index=False) == cuts[1].to_csv(index=False)


def test_the_driver_writes_the_same_bytes_at_any_worker_count(tmp_path):
    from gridspine.drivers import campus_study as D
    from tests.gridspine.test_campus_island_frequency_driver import campus_spec, write_run

    names = (D.FREQ_E1_HOURLY_CSV, D.FREQ_EVENTS_CSV, D.FREQ_LOSSES_CSV, D.FREQ_BRIDGE_CSV, D.FREQ_PLANNED_CSV,
             D.FREQ_UPS_CSV, D.FREQ_SUMMARY_JSON, D.FREQ_CUTS_CSV, D.CAMPUS_MANIFEST)
    out = {}
    for n in (1, 2):
        run = write_run(tmp_path / f"run{n}", campus_spec(), selected=tuple((h, "max_island_dp_up") for h in (0, 1, 2)))
        D.island_frequency_campus(run, workers=n)
        D.cuts_campus(run, workers=n)
        out[n] = {name: (run / name).read_bytes() for name in names if (run / name).exists()}
        # the count used, never more than the items, beside the manifest (hashed elsewhere), not in it
        assert json.loads((run / D.WORKERS_JSON).read_text()) == {
            "island_frequency": P.used(n, len(D.island_hours(run))),
            "cuts": P.used(n, len(F.cut_events(pd.read_csv(run / D.FREQ_EVENTS_CSV),
                                               pd.read_csv(run / D.FREQ_E1_HOURLY_CSV))))}
        assert n == 1 or json.loads((run / D.WORKERS_JSON).read_text())["island_frequency"] == 2   # three hours, two workers
    assert len(out[1][D.FREQ_CUTS_CSV]) > 100 and out[1] == out[2]
