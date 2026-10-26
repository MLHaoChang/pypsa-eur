"""The harmonic impedance scan of a campus run: its cases, the PCC's normal
case and the three CSVs (datacenter grid readiness issue 12, part 3)."""
import pandas as pd
import pytest

from gridspine.drivers.campus_study import (
    HARMONIC_ENVELOPE_CSV,
    HARMONIC_RESONANCE_CSV,
    HARMONIC_SCAN_CSV,
    harmonic_scan_campus,
)
from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.static import harmonic_scan as hs
from gridspine.templates.grid_codes import load_grid_code, validate_profile
from tests.gridspine.test_campus import campus_spec, t
from tests.gridspine.test_harmonics_campus import _hub, _run


def test_max_and_min_come_from_the_grid_and_normal_is_not_invented():
    bus, cases, notes = hs.campus_cases(build_campus(campus_spec()))
    assert cases == {"max": (3000.0, 0.1), "min": (2000.0, 0.12)}
    assert notes == [hs.NO_NORMAL]


def test_the_normal_case_is_read_from_the_pcc():
    s = campus_spec()
    s["campus"]["pcc"].update(sk_normal_mva=t(2500.0), rx_normal=t(0.11))
    _, cases, notes = hs.campus_cases(build_campus(s))
    assert list(cases) == ["max", "min", "normal"] and cases["normal"] == (2500.0, 0.11)
    assert notes == []


@pytest.mark.parametrize("over, match", [
    ({"sk_normal_mva": t(2500.0)}, "come together"),
    ({"rx_normal": t(0.1)}, "come together"),
    ({"sk_normal_mva": t(3500.0), "rx_normal": t(0.1)}, "outside sk_min_mva..sk_max_mva"),
    ({"sk_normal_mva": t(1500.0), "rx_normal": t(0.1)}, "outside sk_min_mva..sk_max_mva"),
])
def test_a_bad_normal_case_is_refused(over, match):
    s = campus_spec()
    s["campus"]["pcc"].update(over)
    with pytest.raises(ContractError, match=match):
        build_campus(s)


def test_the_clearance_key_validates_like_the_other_single_limits():
    p = load_grid_code("generic_assumed", raw=True)
    p["harmonic_resonance_clearance_h"] = {"value": 0.3, "clause": "demo", "source": "assumed"}
    assert hs.clearance_from_profile(validate_profile("p", p))[0] == 0.3
    p["harmonic_resonance_clearance_h"] = {"value": 0.0, "clause": "demo", "source": "assumed"}
    with pytest.raises(ContractError, match="harmonic_resonance_clearance_h value must be a positive number"):
        validate_profile("p", p)
    p["harmonic_resonance_clearance_h"] = {"value": 0.3, "source": "assumed"}
    with pytest.raises(ContractError, match="harmonic_resonance_clearance_h has no clause"):
        validate_profile("p", p)


def _with_bank(spec):
    spec["campus"]["compensation"] = [{"name": "CAP1", "bus": "MV", "kind": "capacitor_bank",
                                       "q_mvar": t(10.0), "steps": 2}]


def test_the_run_writes_the_scan_the_envelope_and_the_resonances(tmp_path, monkeypatch):
    seen = {}
    real = hs.impedance_scan

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(hs, "impedance_scan", spy)
    run, _ = _run(tmp_path, _hub(), _with_bank)
    study = harmonic_scan_campus(run)

    scan = pd.read_csv(run / HARMONIC_SCAN_CSV)
    assert set(scan["case"]) == {"max", "min"}
    assert set(scan["cap_state"]) == {"CAP1=0/2", "CAP1=1/2", "CAP1=2/2"}
    assert len(scan) == len(study.scan)
    env = pd.read_csv(run / HARMONIC_ENVELOPE_CSV)
    assert list(env["h"]) == list(range(1, hs.H_MAX + 1))
    assert env["note"].str.contains("no normal case").all()
    res = pd.read_csv(run / HARMONIC_RESONANCE_CSV)
    assert list(res.columns) == list(hs.RESONANCE_COLUMNS)
    assert not res.empty and set(res["cap_state"]) <= {"CAP1=1/2", "CAP1=2/2"}
    assert res["assumed"].all()                                     # no clearance in the default profile
    # the 6-pulse and 12-pulse demo spectra, both counted
    assert seen["spectrum_orders"] == [5, 7, 11, 13, 17, 19, 23, 25]


def test_a_changed_project_is_refused(tmp_path):
    run, path = _run(tmp_path, _hub())
    path.write_bytes(path.read_bytes() + b"\0")
    with pytest.raises(ContractError, match="prepare the run again"):
        harmonic_scan_campus(run)
