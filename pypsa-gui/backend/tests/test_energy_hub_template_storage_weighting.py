"""
S0 of the campus island plan (2026-10-07): storage in the Energy Hub
templates integrates PHYSICAL hours.

Each template is one 168 h week standing for a year. PyPSA 1.1.2 reads
``snapshot_weightings.stores`` as the elapsed hours in the StorageUnit and
Store state-of-charge balance (``pypsa/optimization/constraints.py``), so
weighting it 8760/168 like the objective moved 52 hours of energy per
hourly dispatch. ``objective`` and ``generators`` keep the annual scaling;
``stores`` is the 1 h snapshot duration.

The oracle is a hand state-of-charge step: discharging p MW for one hourly
snapshot lowers the state of charge by p·1 h/η_dispatch.

The same file pins the Data Center UPS rating against the IT load it
protects (owner decision, folded into S0).
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

IDS = sorted(T.BUILDERS)
STORAGE_UNITS = [(tid, su) for tid in IDS for su in T.BUILDERS[tid]().storage_units.index]


def test_every_template_has_a_storage_unit_to_check():
    assert {tid for tid, _ in STORAGE_UNITS} == set(IDS)


@pytest.mark.parametrize("tid", IDS)
def test_stores_weight_is_the_snapshot_duration_and_annual_scaling_is_kept(tid):
    sw = T.BUILDERS[tid]().snapshot_weightings
    assert (sw["stores"] == 1.0).all()
    for col in ("objective", "generators"):
        assert np.allclose(sw[col], 8760.0 / 168)
        assert sw[col].sum() == pytest.approx(8760.0)


@pytest.mark.parametrize("tid,su", STORAGE_UNITS)
def test_one_snapshot_of_discharge_lowers_soc_by_p_over_eta(tid, su):
    """Force p MW of discharge at the first snapshot from a full, non-cyclic
    unit; nothing else may move its state of charge. p is chosen so that the
    old 52.14 h step would still be feasible, so a wrong weight shows as a
    wrong number rather than as an infeasible LP."""
    n = T.BUILDERS[tid]()
    s = n.storage_units
    if s.at[su, "p_nom_extendable"]:
        s.at[su, "p_nom_extendable"] = False
        s.at[su, "p_nom"] = 10.0
    p_nom, max_hours = float(s.at[su, "p_nom"]), float(s.at[su, "max_hours"])
    eta = float(s.at[su, "efficiency_dispatch"])
    e_nom = p_nom * max_hours
    p = 0.5 * e_nom * eta / T.WEIGHT
    s.at[su, "cyclic_state_of_charge"] = False
    s.at[su, "state_of_charge_initial"] = e_nom
    s.at[su, "p_min_pu"] = 0.0           # no charging
    s.at[su, "marginal_cost"] = -1_000.0  # dispatch whatever is allowed
    p_max_pu = np.zeros(len(n.snapshots))
    p_max_pu[0] = p / p_nom
    n.storage_units_t.p_max_pu[su] = p_max_pu

    status, cond = n.optimize(solver_name="highs")
    assert (status, cond) == ("ok", "optimal")
    assert float(n.storage_units_t.p_dispatch[su].iloc[0]) == pytest.approx(p)
    soc0 = float(n.storage_units_t.state_of_charge[su].iloc[0])
    assert e_nom - soc0 == pytest.approx(p * 1.0 / eta)


def test_store_energy_integrates_one_hour_per_snapshot():
    """The H2 hub's Store: e(t) − e(t−1) = −p(t)·1 h on a solved template."""
    n = T.build_eh_h2_hub()
    status, cond = n.optimize(solver_name="highs")
    assert (status, cond) == ("ok", "optimal")
    e = n.stores_t.e["h2_storage"].to_numpy()
    p = n.stores_t.p["h2_storage"].to_numpy()
    assert np.abs(p).max() > 1.0, "fixture must cycle the store"
    np.testing.assert_allclose(e - np.roll(e, 1), -p * 1.0, atol=1e-6)


# ── Data Center UPS ─────────────────────────────────────────────────────────


def _ups_protected_loads():
    """The Loads the UPS protects: the island sidecar's ``it_load`` list where
    the template ships one (plan I1), else the IT load the builder names."""
    unit = getattr(T, "ISLAND_CONFIG", {}).get("eh_datacenter", {}) \
        .get("units", {}).get("ups_battery", {})
    return unit.get("it_load", ["it_load"])


def test_ups_carries_the_it_peak_with_margin():
    n = T.build_eh_datacenter()
    loads = _ups_protected_loads()
    assert set(loads) <= set(n.loads.index)
    peak = float(n.loads_t.p_set[loads].sum(axis=1).max())
    assert peak > 30.0  # the ~36 MW IT load, not an empty selection
    p_nom = float(n.storage_units.at["ups_battery", "p_nom"])
    assert p_nom >= 1.05 * peak


def test_ups_energy_bridges_the_it_peak_until_the_gensets_are_on_line():
    """Plan Q1: the bridge is 600 s by default (I1's sidecar may set its
    own). The stored energy, after dispatch losses, covers the IT peak for
    that long."""
    n = T.build_eh_datacenter()
    req = getattr(T, "ISLAND_CONFIG", {}).get("eh_datacenter", {}) \
        .get("requirements", {})
    bridge_s = float(req.get("bridge_s", {}).get("value", 600.0))
    peak = float(n.loads_t.p_set[_ups_protected_loads()].sum(axis=1).max())
    s = n.storage_units.loc["ups_battery"]
    e_mwh = float(s["p_nom"]) * float(s["max_hours"])
    assert e_mwh * float(s["efficiency_dispatch"]) >= peak * bridge_s / 3600.0
