"""The campus-run speed package's proof: a full campus Run, then the
recommended GFM settings, on three sites, its run directory copied out for
``campus_island_equivalence.py`` to compare with another commit's.

* ``i7_hand_hub``: the I7 hub (``test_campus_island_cuts_e2e``), a closed
  tie, so the every-hour E1 sweep has steps across the PCC; failing events,
  so the cuts and the GFM search run;
* ``eh_microgrid_staged``: the open-tie microgrid (504 hours, 36 island
  hours, seamless), the slow case the package is for;
* ``eh_datacenter_staged``: ``ups_bridged``, so E4's pickup and its cache.

Skipped unless ``CAMPUS_EQUIV_OUT`` names the directory to write; run once
per commit and worker count (``GRIDSPINE_WORKERS``), e.g.

    CAMPUS_EQUIV_OUT=/tmp/eq/head-w4 GRIDSPINE_WORKERS=4 \\
        pytest -m slow tests/test_campus_island_equivalence.py
    python tests/campus_island_equivalence.py /tmp/eq/base-w1 /tmp/eq/head-w4

The conftest pins ``GRIDSPINE_WORKERS`` to 1; the count here is read from
``CAMPUS_EQUIV_WORKERS`` (default 1) so the run says which it used.
"""
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from services import campus_electrical_service as ce
from services import campus_island_tuning as cit
from services import project_registry
from tests import test_campus_electrical_service as _ces

user_and_db = _ces.user_and_db
OUT = os.environ.get("CAMPUS_EQUIV_OUT")
pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not OUT, reason="set CAMPUS_EQUIV_OUT to write the runs to compare")]


def _run_and_copy(row, d, name, monkeypatch):
    monkeypatch.setenv("GRIDSPINE_WORKERS", os.environ.get("CAMPUS_EQUIV_WORKERS", "1"))
    ce.draft(row)
    times, t0 = [], time.time()

    def progress(stage, done, total):
        times.append((stage, round(time.time() - t0, 1)))
    ce.run(row, {}, progress=progress)
    run_dir = ce.campus_dir(row) / "run"
    t1 = time.time()
    cit.recommend(run_dir)
    times.append(("gfm_settings", round(time.time() - t1, 1)))
    dst = Path(OUT) / name
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(run_dir, dst)
    (Path(OUT) / f"{name}.times.json").write_text(json.dumps(times))


@pytest.mark.parametrize("tid", ["eh_microgrid_staged", "eh_datacenter_staged"])
def test_template(tid, reset_backend, monkeypatch, tmp_path, user_and_db):
    from tests.test_eh_staged_hubs import T, _solve

    n, _cfg, _state = _solve(tid, monkeypatch, tmp_path)
    db, user = user_and_db
    row = project_registry.create_root(db, user, tid)
    d = project_registry.ensure_project_dir(row)
    n.export_to_netcdf(str(d / "network.nc"))
    (d / "island_config.json").write_text(json.dumps(T.ISLAND_CONFIG[tid]))
    _run_and_copy(row, d, tid, monkeypatch)


def test_i7_hand_hub(user_and_db, monkeypatch):
    from tests.test_campus_island_cuts_e2e import _network, _sidecar

    db, user = user_and_db
    row = project_registry.create_root(db, user, "Cut Hub")
    d = project_registry.ensure_project_dir(row)
    _network().export_to_netcdf(str(d / "network.nc"))
    (d / "island_config.json").write_text(json.dumps(_sidecar()))
    _run_and_copy(row, d, "i7_hand_hub", monkeypatch)
