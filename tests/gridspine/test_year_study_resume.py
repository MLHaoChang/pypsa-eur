"""Follow-ups F3: the driver studies a SAVED dispatch without re-solving it.

The unit commitment is ~2 h for a year; everything after it (ranking, load
flow, screening, fault levels, bundles) is ~20 min and is what changes when
the static side changes — F1 fixed the engine path, F2 the ranking column, and
the v3 bundles had to be redone from the v3 dispatch. `run_year_study` is now
`dispatch_year` followed by `study_dispatch`, and `resume_from_dispatch` runs
the second half from another run's `dispatch.csv` and `loads.csv`.

The property that matters: resumed == composed. Same dispatch in, same
metrics, same selection, same `.raw` out — and the manifest names the dispatch
it came from by path and content hash, so a v4 bundle is traceable to the v3
solve. The fixture is small on purpose (48 h, two 24 h windows): one solve
here, then a resume from its output directory.
"""
import hashlib
import json

import pandas as pd
import pytest

from gridspine.drivers.year_study import (
    dispatch_year,
    resume_from_dispatch,
    run_year_study,
    study_dispatch,
)
from gridspine.schema.contracts import ContractError

from tests.gridspine.test_year_study import _raw_gen_p, _raw_load_p

HOURS, WINDOW, OVERLAP, K = 48, 24, 8, 1


@pytest.fixture(scope="module")
def composed(tmp_path_factory):
    out = tmp_path_factory.mktemp("composed")
    return out, run_year_study(out, hours=HOURS, k=K, window=WINDOW, overlap=OVERLAP)


@pytest.fixture(scope="module")
def resumed(composed, tmp_path_factory):
    src, _res = composed
    out = tmp_path_factory.mktemp("resumed")
    return out, resume_from_dispatch(src, out, k=K)


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_run_year_study_is_dispatch_year_then_study_dispatch(tmp_path):
    out = tmp_path / "split"
    net, registry, dispatch, loads = dispatch_year(out, hours=HOURS, window=WINDOW, overlap=OVERLAP)
    assert (out / "dispatch.csv").exists() and (out / "loads.csv").exists()
    res = study_dispatch(out, net, registry, dispatch, loads, k=K, window=WINDOW, overlap=OVERLAP)
    m = json.loads(res.artifacts["manifest"].read_text())
    assert (m["hours"], m["k"], m["window"], m["overlap"]) == (HOURS, K, WINDOW, OVERLAP)
    assert m["dispatch_source"] is None


def test_resumed_selection_and_metrics_equal_the_composed_run(composed, resumed):
    _src, a = composed
    _out, b = resumed
    assert list(a.selected["hour"]) == list(b.selected["hour"])
    assert list(a.selected["reasons"]) == list(b.selected["reasons"])
    ma = pd.read_csv(a.artifacts["metrics"]).set_index("hour")
    mb = pd.read_csv(b.artifacts["metrics"]).set_index("hour")
    pd.testing.assert_frame_equal(ma, mb, check_exact=False, rtol=1e-9, atol=1e-9)


def test_resumed_raw_files_are_byte_identical_to_the_composed_ones(composed, resumed):
    _src, a = composed
    _out, b = resumed
    for hour in a.selected["hour"]:
        hour = int(hour)
        assert a.artifacts[f"raw_{hour}"].read_bytes() == b.artifacts[f"raw_{hour}"].read_bytes(), hour


def test_resumed_raw_carries_the_hours_snapshot_not_the_native_peak(resumed):
    """The stage-order check of increment 2, on the resumed path: a resume that
    skipped `apply_snapshot` would still write every artifact."""
    out, res = resumed
    dispatch = pd.read_csv(res.artifacts["dispatch"])
    loads = pd.read_csv(res.artifacts["loads"])
    for hour in res.selected["hour"]:
        hour = int(hour)
        text = res.artifacts[f"raw_{hour}"].read_text()
        assert sum(_raw_load_p(text)) == pytest.approx(loads.loc[loads["hour"] == hour, "p_mw"].sum(), abs=0.05)
        want = dispatch[(dispatch["hour"] == hour) & dispatch["unit_id"].str.startswith("G_")].set_index("unit_id")["p_mw"]
        got = _raw_gen_p(text)[: len(want)]
        assert sum(got) == pytest.approx(want.sum(), abs=0.05), hour


def test_resumed_manifest_names_the_source_dispatch_by_path_and_hash(composed, resumed):
    src, _a = composed
    _out, b = resumed
    m = json.loads(b.artifacts["manifest"].read_text())
    ds = m["dispatch_source"]
    assert ds["path"] == str(src)
    assert ds["dispatch_sha256"] == _sha(src / "dispatch.csv")
    assert ds["loads_sha256"] == _sha(src / "loads.csv")
    assert ds["hours"] == HOURS
    assert m["hours"] == HOURS and m["k"] == K
    assert m["window"] is None and m["overlap"] is None
    # the resumed run's own copies are the same bytes
    assert _sha(b.artifacts["dispatch"]) == ds["dispatch_sha256"]
    assert _sha(b.artifacts["loads"]) == ds["loads_sha256"]


def test_resume_refuses_a_directory_without_a_dispatch(tmp_path):
    with pytest.raises(ContractError, match="dispatch.csv"):
        resume_from_dispatch(tmp_path / "nowhere", tmp_path / "out", k=K)


def test_resume_refuses_tables_whose_hours_disagree(composed, tmp_path):
    src, _a = composed
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "dispatch.csv").write_bytes((src / "dispatch.csv").read_bytes())
    loads = pd.read_csv(src / "loads.csv")
    loads[loads["hour"] < HOURS - 1].to_csv(bad / "loads.csv", index=False)
    with pytest.raises(ContractError):
        resume_from_dispatch(bad, tmp_path / "out", k=K)
