"""DC 05 wiring: every accepted utilisation kind builds, ai_training is pinned to
master's literal values, stored specs stay byte-equal, the compute block is inert,
and a dropped waste_heat_export is reported (GK A W1, W2, W4, W5, W6)."""
from __future__ import annotations

import hashlib
import json
import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

from models.flex_archetypes import DataCentreLoadSpec
from services import dc_load_profile as P

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "project_templates"))
import eh_templates as T  # noqa: E402

#: A master-era spec: none of the DC 05 inputs, waste_heat_export at its default.
MASTER_ERA = {
    "phases": [{"cod": "2027-01-01", "it_mw": 100}, {"cod": "2028-01-01", "it_mw": 250}],
    "utilisation": "ai_training", "pue_at_design": 1.3, "pue_temperature_slope_per_k": 0.02,
    "redundancy": "N+1", "critical_share": 0.75, "curtailable_share": 0.0,
}
#: Master's sha256 of the same dump with the key still present.
MASTER_DUMP_SHA = "f0ddb7acf4a7c6e959b98ab11e2072861ce5e47f894b0f57fbe08def26e682b5"

IDX = pd.date_range("2027-07-01", periods=24, freq="h")
HOURS = [0, 4, 8, 12, 16, 20]
#: master's hourly_profile (MW) at those hours for IT 100 and 250 MW, ai_training.
MASTER_FACILITY = {
    100.0: [120.683333333, 120.683333333, 120.683333333, 124.595203909, 126.997907378, 120.683333333],
    250.0: [301.708333333, 301.708333333, 301.708333333, 311.488009773, 317.494768445, 301.708333333],
}


def spec(**over):
    return DataCentreLoadSpec.model_validate({**MASTER_ERA, **over})


@pytest.mark.parametrize("it", [100.0, 250.0])
def test_ai_training_hourly_series_is_masters_literal_values(it):
    got = P.hourly_profile(spec(), IDX, it)[HOURS]
    assert got == pytest.approx(MASTER_FACILITY[it], abs=1e-8)


def test_master_era_spec_dump_is_master_minus_the_dead_key():
    d = spec().model_dump(mode="json")
    assert "waste_heat_export" not in d and "workload_mix" not in d and "compute" not in d
    assert "ignored_inputs" not in d
    with_key = {**d, "waste_heat_export": False}
    assert hashlib.sha256(json.dumps(with_key, sort_keys=True).encode()).hexdigest() == MASTER_DUMP_SHA


@pytest.mark.parametrize("kind", ["ai_training", "inference", "mixed", "flat"])
def test_every_accepted_kind_builds_through_the_template_check(kind):
    s = T.dc_load_spec("eh_datacenter", {"utilisation": kind})
    assert s.utilisation == kind


def test_kinds_change_the_series_ai_training_does_not():
    base = P.utilisation(spec(), IDX)
    for kind in ("inference", "mixed", "flat"):
        assert not np.allclose(P.utilisation(spec(utilisation=kind), IDX), base)


def test_waste_heat_export_true_is_dropped_and_reported():
    s = spec(waste_heat_export=True)
    assert s.model_dump(mode="json") == spec().model_dump(mode="json")  # hash cannot move
    rows = P.ledger(s, None)
    assert any("waste heat export is not modelled" in r["item"] for r in rows)
    assert not any("waste heat" in r["item"] for r in P.ledger(spec(waste_heat_export=False), None))


def test_compute_block_counts_must_match_phases():
    with pytest.raises(Exception, match="one count per phase"):
        spec(compute={"kw_per_accelerator": 1.5, "utilisation": 0.8, "accelerator_counts": [1000]})


def test_compute_block_leaves_the_lp_load_unchanged():
    comp = {"kw_per_accelerator": 1.5, "utilisation": 0.8, "accelerator_counts": [1000, 9999999]}
    a, b = spec(), spec(compute=comp)
    assert np.array_equal(P.hourly_profile(a, IDX, 100.0), P.hourly_profile(b, IDX, 100.0))
    from services import dc_it_energy as E
    ea = E.it_energy(a, IDX, 1.0)
    eb = E.it_energy(b, IDX, 1.0)
    assert ea.periods.equals(eb.periods)
    assert eb.phases[0]["compute_it_mw"] == pytest.approx(1.2)


def _fingerprint(n, tid, tmp_path):
    from types import SimpleNamespace

    from services.workflow_tools import _network_input_fingerprint

    return _network_input_fingerprint(SimpleNamespace(
        network=n, solver_state={"solver_config": T.solver_config(tid)},
        user_ts={}, storage_dir=str(tmp_path)))


@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
def test_compute_block_feeds_no_solve_input(tmp_path, tid):
    n_ph = len(T.dc_load_spec(tid, {}).phases)
    comp = {"kw_per_accelerator": 1.5, "utilisation": 0.8, "accelerator_counts": [10000.0] * n_ph}
    other = {**comp, "accelerator_counts": [123456.0] * n_ph}
    a = T.dc_load_spec(tid, {"compute": comp})
    b = T.dc_load_spec(tid, {"compute": other})
    c = T.dc_load_spec(tid, {})
    nets = [T.BUILDERS[tid](load_spec=s) for s in (a, b, c)]
    fps = [_fingerprint(n, tid, tmp_path) for n in nets]
    assert fps[0] == fps[1] == fps[2]
    for i, s in enumerate((a, b, c)):
        T.write_sidecars(tmp_path / str(i), tid, load_spec=s)
    for name in (T.SOLVER_FILE, *([T.ISLAND_FILE] if (tmp_path / "0" / T.ISLAND_FILE).exists() else [])):
        assert (tmp_path / "0" / name).read_bytes() == (tmp_path / "1" / name).read_bytes() \
            == (tmp_path / "2" / name).read_bytes()


def test_form_word_map_keys_equal_the_engine_utilisation_values():
    """The Workload select's keys (frontend dcLoadForm.ts) are the schema's values."""
    import re
    from typing import get_args

    from models.flex_archetypes import UtilisationKind

    ts = (pathlib.Path(__file__).resolve().parents[2] / "frontend/src/pages/hubDesign/dcLoadForm.ts").read_text()
    block = ts.split("export const UTILISATION_OPTIONS")[1].split("/** The assumed shape inputs")[0]
    keys = re.findall(r"\['(\w+)', '[^']+'\]", block)
    assert keys == list(get_args(UtilisationKind))


def test_mismatch_threshold_is_the_same_in_form_and_engine():
    import re

    from services.dc_compute import MISMATCH_NOTE_ABOVE

    ts = (pathlib.Path(__file__).resolve().parents[2] / "frontend/src/pages/hubDesign/dcLoadForm.ts").read_text()
    assert float(re.search(r"MISMATCH_NOTE_ABOVE = ([\d.]+)", ts).group(1)) == MISMATCH_NOTE_ABOVE


def test_a_client_cannot_write_ignored_inputs_and_false_says_nothing():
    assert spec(ignored_inputs=["waste_heat_export"]).ignored_inputs == []
    assert spec(waste_heat_export=False).ignored_inputs == []
    assert spec(waste_heat_export=True).ignored_inputs == ["waste_heat_export"]
