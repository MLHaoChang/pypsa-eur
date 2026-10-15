"""Recommended GFM settings through the campus driver (E2E demo plan
2026-10-14 §7.2). A run directory written by hand, as the frequency driver's
tests do: the cases are the island-hour E1/E2/E3/E5 rows; E4 is never run;
the result is written beside the frequency check; a run without one refuses."""
import json

import pytest

from gridspine.drivers import campus_study as D
from gridspine.schema.contracts import ContractError
from gridspine.static import frequency as F
from tests.gridspine.test_campus_island_frequency_driver import campus_spec, write_run


def test_the_recommendation_is_written_and_never_runs_e4(tmp_path, monkeypatch):
    run = write_run(tmp_path / "run", campus_spec(scenarios=("seamless", "ups_bridged")))
    D.island_frequency_campus(run, every_hour=False)

    def no_e4(*a, **k):
        raise AssertionError("E4 has no GFM: it is never a case")
    monkeypatch.setattr(F, "e4_ups_bridged", no_e4)
    out = D.gfm_settings_campus(run, max_sims=200)
    assert json.loads((run / D.GFM_SETTINGS_JSON).read_text()) == json.loads(json.dumps(out))
    assert "BESS1" in out["settings"]
    assert out["cases"] and all(c["key"].split()[-1] in ("E1", "E2", "E3", "E5") for c in out["cases"])
    assert 0 < out["simulations"] <= 200
    assert out["label"].startswith("heuristic screening")
    assert any(x.startswith("E4") for x in out["excluded"])


def test_it_needs_the_frequency_check(tmp_path):
    run = write_run(tmp_path / "run", campus_spec())
    with pytest.raises(ContractError, match="island_frequency_campus"):
        D.gfm_settings_campus(run)


def test_the_tuning_margin_is_its_own_key_never_gfm_margin(tmp_path):
    from tests.gridspine.test_island_schema import t
    s = campus_spec()
    s["campus"]["island"]["gfm_margin"] = t(0.95)            # I2b's capacity margin
    run = write_run(tmp_path / "a", s)
    D.island_frequency_campus(run, every_hour=False)
    assert D.gfm_settings_campus(run, max_sims=60)["margin"] == pytest.approx(0.1)
    s["campus"]["island"]["gfm_tuning_margin"] = t(0.2)
    run = write_run(tmp_path / "b", s)
    D.island_frequency_campus(run, every_hour=False)
    assert D.gfm_settings_campus(run, max_sims=60)["margin"] == pytest.approx(0.2)


def test_the_tuning_margin_must_be_below_one(tmp_path):
    from tests.gridspine.test_island_schema import t
    s = campus_spec()
    s["campus"]["island"]["gfm_tuning_margin"] = t(1.0)
    with pytest.raises(ContractError, match=r"gfm_tuning_margin must be in \[0, 1\)"):
        D.load_run_campus(write_run(tmp_path / "run", s))


def test_the_bounds_are_ledgered_as_assumed(tmp_path):
    run = write_run(tmp_path / "run", campus_spec())
    D.island_frequency_campus(run, every_hour=False)
    out = D.gfm_settings_campus(run, max_sims=60)
    text = " ".join(out["ledger"])
    for word in ("droop", "H", "tau_f", "i_max", "assumed"):
        assert word in text
    assert any("1–10 %" in x or "1-10 %" in x for x in out["ledger"])


def test_a_stopped_search_writes_nothing(tmp_path):
    """GFM-b review (#303): the driver passes the stop through; a stopped
    search raises and leaves no recommendation behind."""
    from gridspine.static import gfm_tuning as T

    run = write_run(tmp_path / "run", campus_spec(scenarios=("seamless", "ups_bridged")))
    D.island_frequency_campus(run, every_hour=False)
    n = [0]

    def stop():
        n[0] += 1
        return n[0] > 2
    with pytest.raises(T.SearchStopped):
        D.gfm_settings_campus(run, max_sims=200, should_stop=stop)
    assert not (run / D.GFM_SETTINGS_JSON).exists()
