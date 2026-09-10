"""
GET /results/copt (Phase 2 Task 3): screening adequacy + the FMECA ranking
computed ON DEMAND from the current network — no solve required, zero LP
solves involved. 204 when the network has no occurrence-bearing electrical
generators (nothing to convolve).
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

import routers.results as R
from services.pypsa_service import PyPSAService
from tests.test_adequacy_copt import _network


def test_204_without_occurrence_data():
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=10.0)
    n.add("Generator", "g", bus="b", carrier="wind", p_nom=20.0)
    PyPSAService.set_network(n)
    resp = R.get_copt()
    assert getattr(resp, "status_code", 200) == 204


def test_endpoint_serves_metrics_and_ranked_modes():
    PyPSAService.set_network(_network())
    out = R.get_copt()
    assert out["engine"] == "copt"
    assert out["fidelity"] == "analytic_convolution"
    m = out["metrics"]
    # Residual [70, 100] vs fleet {60 q=.1, 40 q=.2}; weight 3.
    # LOLP(70)=0.28; LOLP(100)=P[cap<100]=1−0.72=0.28 → LOLE=0.28·6=1.68.
    assert m["lole_hours"] == pytest.approx(0.28 * 6.0)
    # This fixture is 2 snapshots at weight 3 — six modelled hours, not a
    # year — so the honest label is per-horizon. It asserted
    # "hours_per_year" while that string was hardcoded at the call site,
    # which is precisely the bug: the same label went onto a 168 h week's
    # 80.86 and onto the annualised 4216.05 for the same system, and
    # understating LOLE is the direction that invites a comparison against a
    # 3 h/yr standard.
    assert m["time_basis"] == "hours_per_horizon"
    assert m["horizon_years"] == pytest.approx(6.0 / 8760.0)
    assert m["eue_mwh"] > 0
    names = [r["name"] for r in out["per_mode"]]
    assert set(names) == {"thermal1", "thermal2"}
    assert out["fleet"]["units"] == 2
    assert out["fleet"]["must_take"] == 1          # the wind farm
    # Phase 12c-pre: no unit carries a profile into the fleet here, so the
    # disclosure is empty and the sentence absent — the shape is present.
    assert out["fleet"]["profile_units"] == []
    assert out["fleet"]["netted_beyond_cap"] == []
    assert out["fidelity_note"] is None
    # VoLL comes from the live solver config; default config has voll=0 →
    # € fields are zero but ΔEUE still ranks.
    assert all(r["criticality_eur_per_year"] == 0.0 for r in out["per_mode"])
    assert out["voll_eur_per_mwh"] == 0.0


def test_endpoint_reports_an_annual_basis_when_the_horizon_is_a_year():
    """The other direction, so the label is pinned as DERIVED rather than
    merely flipped to a different constant."""
    n = _network()
    n.snapshot_weightings.loc[:, :] = 8760.0 / len(n.snapshots)
    PyPSAService.set_network(n)
    m = R.get_copt()["metrics"]
    assert m["time_basis"] == "hours_per_year"
    assert m["horizon_years"] == pytest.approx(1.0)
