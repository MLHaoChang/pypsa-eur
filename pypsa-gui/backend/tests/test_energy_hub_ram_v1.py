"""
P7 — RAM v1: COPT/MC expose library vs override rate provenance.

Plan: docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md Phase 7
Inventory: docs/superpowers/findings/2026-09-18-eh-p7-ram-v1-inventory.md

Acceptance: library loads; MC/COPT show library vs override provenance;
no claim of full RAM/CMMS.
"""
from __future__ import annotations

import time

import pandas as pd
import pypsa
import pytest

from services.pypsa_service import PyPSAService


def _network_with_library_and_override() -> pypsa.Network:
    """One gas unit on carrier_default; one gas unit with typed asset override."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "lib_gas", bus="b", carrier="gas",
          p_nom=60.0, marginal_cost=20.0)
    n.add("Generator", "typed_gas", bus="b", carrier="gas",
          p_nom=40.0, marginal_cost=30.0,
          outage_rate_value=0.12, outage_rate_basis="EFORd", mttr_hours=40.0)
    return n


def test_library_citation_helper_reads_carrier_defaults():
    from services.adequacy.occurrence import library_citation

    cite = library_citation("gas")
    assert cite and "NERC" in cite
    assert library_citation("wind") is None
    assert library_citation(None) is None


def test_copt_per_mode_exposes_rate_source_library_vs_asset():
    import routers.results as R

    PyPSAService.set_network(_network_with_library_and_override())
    out = R.get_copt()
    by = {r["name"]: r for r in out["per_mode"]}
    assert by["lib_gas"]["rate_source"] == "carrier_default"
    assert "NERC" in (by["lib_gas"].get("library_citation") or "")
    assert by["typed_gas"]["rate_source"] == "asset"
    assert not by["typed_gas"].get("library_citation")


def test_copt_fleet_units_provenance_lists_library_and_override():
    import routers.results as R

    PyPSAService.set_network(_network_with_library_and_override())
    out = R.get_copt()
    prov = {e["name"]: e for e in out["fleet"]["units_provenance"]}
    assert set(prov) == {"lib_gas", "typed_gas"}
    assert prov["lib_gas"]["rate_source"] == "carrier_default"
    assert "NERC" in prov["lib_gas"]["library_citation"]
    assert prov["typed_gas"]["rate_source"] == "asset"
    assert not prov["typed_gas"].get("library_citation")
    assert "RAM/CMMS" in (out.get("ram_note") or "")


def test_mc_result_exposes_units_provenance(client, reset_backend):
    """MC sibling payload carries the same rate-source ledger as COPT."""
    PyPSAService.set_network(_network_with_library_and_override())
    r = client.post("/api/results/mc", json={"draws": 4, "seed": 7})
    assert r.status_code == 200, r.text
    deadline = time.time() + 60
    body = None
    while time.time() < deadline:
        body = client.get("/api/results/mc").json()
        if body.get("status") in ("done", "failed", "aborted"):
            break
        time.sleep(0.05)
    assert body is not None and body.get("status") == "done", body
    result = body["result"]
    prov = {e["name"]: e for e in result["units_provenance"]}
    assert prov["lib_gas"]["rate_source"] == "carrier_default"
    assert "NERC" in prov["lib_gas"]["library_citation"]
    assert prov["typed_gas"]["rate_source"] == "asset"
    assert "RAM/CMMS" in (result.get("ram_note") or "")
