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
    assert m["time_basis"] == "hours_per_year"
    assert m["eue_mwh"] > 0
    names = [r["name"] for r in out["per_mode"]]
    assert set(names) == {"thermal1", "thermal2"}
    assert out["fleet"]["units"] == 2
    assert out["fleet"]["must_take"] == 1          # the wind farm
    # VoLL comes from the live solver config; default config has voll=0 →
    # € fields are zero but ΔEUE still ranks.
    assert all(r["criticality_eur_per_year"] == 0.0 for r in out["per_mode"])
    assert out["voll_eur_per_mwh"] == 0.0
