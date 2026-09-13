"""
The outage-rate provenance ledger.

`resolve_outage_params` resolves a rate three ways, and two of the three
explain themselves: the carrier library ships its own citation, and `missing`
is the absence of a claim. `asset` does not — and it is the one that carries
every condition-based reliability number. These tests pin the two properties
that make the ledger worth having: it REFUSES a record that is not provenance,
and it can be CONTRADICTED by the network it describes.
"""
from __future__ import annotations

import json

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from services import chat_tools as T
from services.adequacy import asset_health as H


def _entry(**overrides) -> dict:
    base = {
        "component": "generators",
        "name": "gas1",
        "outage_rate_value": 0.031,
        "outage_rate_basis": "EFORd",
        "mttr_hours": 48.0,
        "method": "inspection",
        "source_ref": "drone-survey R-118",
        "measured_at": "2030-03-14",
        "confidence": "medium",
        "note": "conductor damage on span 14",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None or k in overrides}


def _network(rate: float | None = 0.031) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "B1")
    n.add("Carrier", "gas")
    n.add("Generator", "gas1", bus="B1", carrier="gas", p_nom=100.0)
    n.add("Generator", "gas2", bus="B1", carrier="gas", p_nom=100.0)
    n.generators["outage_rate_value"] = float("nan")
    n.generators["outage_rate_basis"] = ""
    n.generators["mttr_hours"] = float("nan")
    if rate is not None:
        n.generators.loc["gas1", "outage_rate_value"] = rate
        n.generators.loc["gas1", "outage_rate_basis"] = "EFORd"
        n.generators.loc["gas1", "mttr_hours"] = 48.0
    return n


# ── What the ledger refuses ────────────────────────────────────────────────


def test_a_record_with_no_method_is_not_provenance():
    with pytest.raises(H.AssetHealthValidationError, match="method"):
        H.validate_entries([_entry(method=None)])


def test_a_record_with_no_date_is_not_a_measurement():
    with pytest.raises(H.AssetHealthValidationError, match="measured_at"):
        H.validate_entries([_entry(measured_at=None)])


def test_a_free_text_date_is_refused():
    """'last spring' is not a date the report can age."""
    with pytest.raises(H.AssetHealthValidationError, match="ISO date"):
        H.validate_entries([_entry(measured_at="last spring")])


def test_a_record_carrying_no_number_is_refused():
    with pytest.raises(H.AssetHealthValidationError,
                       match="provenance for no number"):
        H.validate_entries([_entry(outage_rate_value=None, mttr_hours=None)])


@pytest.mark.parametrize("rate", [-0.1, 1.0, 1.5])
def test_a_rate_outside_the_probability_domain_is_refused(rate):
    with pytest.raises(H.AssetHealthValidationError, match=r"\[0, 1\)"):
        H.validate_entries([_entry(outage_rate_value=rate)])


def test_an_unknown_method_is_refused():
    with pytest.raises(H.AssetHealthValidationError, match="method must be"):
        H.validate_entries([_entry(method="vibes")])


def test_an_unknown_basis_is_refused():
    """
    FOR and EFORd are not interchangeable, and a third label would imply a
    conversion the model cannot do.
    """
    with pytest.raises(H.AssetHealthValidationError, match="outage_rate_basis"):
        H.validate_entries([_entry(outage_rate_basis="EFOR")])


def test_a_component_without_outage_columns_is_refused():
    with pytest.raises(H.AssetHealthValidationError, match="no outage columns"):
        H.validate_entries([_entry(component="buses")])


def test_a_misspelled_key_is_refused_not_dropped():
    """A silently-dropped `mttr_hrs` reads as a repair time that was recorded."""
    with pytest.raises(H.AssetHealthValidationError, match="unknown keys"):
        H.validate_entries([_entry(mttr_hrs=48.0)])


def test_two_records_for_one_asset_are_refused():
    with pytest.raises(H.AssetHealthValidationError, match="duplicate entry"):
        H.validate_entries([_entry(), _entry(measured_at="2031-01-01")])


def test_expert_judgement_is_an_allowed_method():
    """Naming an estimate honestly must be easier than laundering it."""
    assert "expert_judgement" in H.VALID_METHODS
    assert H.validate_entries([_entry(method="expert_judgement")])


def test_a_valid_entry_normalises_every_optional_field():
    [entry] = H.validate_entries([{
        "component": "lines", "name": "L1", "mttr_hours": 12.0,
        "method": "sensor", "measured_at": "2030-01-01",
    }])
    assert entry["outage_rate_value"] is None
    assert entry["confidence"] is None
    assert entry["source_ref"] is None


# ── Persistence ────────────────────────────────────────────────────────────


def test_save_then_load_round_trips(tmp_path):
    saved = H.save_asset_health(tmp_path, [_entry()])
    assert saved["version"] == 1
    loaded = H.load_asset_health(tmp_path)
    assert loaded["entries"] == saved["entries"]
    assert json.loads((tmp_path / H.SIDECAR_NAME).read_text())["__schema__"] == 1


def test_a_rejected_save_leaves_the_previous_ledger_untouched(tmp_path):
    H.save_asset_health(tmp_path, [_entry()])
    before = (tmp_path / H.SIDECAR_NAME).read_bytes()
    with pytest.raises(H.AssetHealthValidationError):
        H.save_asset_health(tmp_path, [_entry(method="vibes")])
    assert (tmp_path / H.SIDECAR_NAME).read_bytes() == before


def test_a_missing_or_corrupt_file_reads_as_empty(tmp_path):
    assert H.load_asset_health(tmp_path)["entries"] == []
    (tmp_path / H.SIDECAR_NAME).write_text("{not json")
    assert H.load_asset_health(tmp_path)["entries"] == []


# ── The reconciliation ─────────────────────────────────────────────────────


def test_an_unexplained_override_is_reported():
    """
    The finding the module exists for: a rate that beats the carrier library,
    with nothing saying where it came from.
    """
    report = H.provenance_report(_network(), [])
    assert report["counts"]["unsourced"] == 1
    assert report["unsourced"][0]["name"] == "gas1"
    assert "no recorded source" in report["unsourced"][0]["reason"]


def test_a_carrier_default_is_not_an_unexplained_override():
    """
    The library ships its own citation, so demanding provenance for it would
    make the report noise.
    """
    report = H.provenance_report(_network(rate=None), [])
    assert report["counts"]["unsourced"] == 0


def test_a_matched_record_is_sourced():
    report = H.provenance_report(_network(), H.validate_entries([_entry()]))
    assert report["counts"]["sourced"] == 1
    assert report["counts"]["unsourced"] == 0
    assert report["sourced"][0]["method"] == "inspection"
    assert report["sourced"][0]["measured_at"] == "2030-03-14"


def test_drift_is_detected_when_the_network_moves_under_the_ledger():
    """
    The property that makes the ledger provenance rather than decoration: it
    can be contradicted by the thing it describes.
    """
    entries = H.validate_entries([_entry()])
    report = H.provenance_report(_network(rate=0.09), entries)
    assert report["counts"]["drifted"] == 1
    row = report["drifted"][0]
    assert row["recorded"] == pytest.approx(0.031)
    assert row["on_network"] == pytest.approx(0.09)


def test_clearing_the_rate_also_counts_as_drift():
    """A record describing an override that no longer exists is stale too."""
    entries = H.validate_entries([_entry()])
    report = H.provenance_report(_network(rate=None), entries)
    assert report["counts"]["drifted"] == 1
    assert report["drifted"][0]["on_network"] is None


def test_a_float_round_trip_is_not_drift():
    """JSON → DataFrame → compare must not report every untouched entry."""
    entries = H.validate_entries([_entry(outage_rate_value=0.1 + 0.2)])
    report = H.provenance_report(_network(rate=0.30000000000000004), entries)
    assert report["counts"]["drifted"] == 0
    assert report["counts"]["sourced"] == 1


def test_a_record_for_an_absent_asset_is_orphaned():
    entries = H.validate_entries([_entry(name="renamed_away")])
    report = H.provenance_report(_network(), entries)
    assert report["counts"]["orphaned"] == 1
    assert report["orphaned"][0]["name"] == "renamed_away"


def test_the_report_writes_nothing():
    n = _network()
    before = n.generators["outage_rate_value"].copy()
    H.provenance_report(n, H.validate_entries([_entry()]))
    pd.testing.assert_series_equal(n.generators["outage_rate_value"], before)


# ── The chat tools ─────────────────────────────────────────────────────────


def test_tools_are_registered_and_tiered():
    from services import chat_service
    from services import chat_tools_schema as S

    assert {"get_asset_health", "record_asset_health"} <= set(T.DISPATCHERS)
    assert {t["name"] for t in S.TOOLS} >= {"get_asset_health",
                                            "record_asset_health"}
    assert chat_service._safety_tier_for("get_asset_health") == "read"
    assert chat_service._safety_tier_for("record_asset_health") == "write"


def test_record_then_read_reconciles_against_the_live_network(
        api_project, install_network):
    name = api_project("health-demo")
    install_network(_network(), name=name)

    T.record_asset_health(name, [_entry()])
    out = T.get_asset_health(name)
    counts = out["provenance"]["counts"]
    assert counts["sourced"] == 1
    assert {r["name"] for r in out["provenance"]["sourced"]} == {"gas1"}
    # `gas2` carries no asset-level rate, so it resolves through the carrier
    # library and is not an unexplained override. Only the asset tier needs a
    # source; demanding one for the library would make the report noise.
    assert counts["unsourced"] == 0
    assert counts["drifted"] == 0 and counts["orphaned"] == 0


def test_reading_a_project_that_is_not_in_the_foreground_reports_no_provenance(
        api_project, install_network):
    """
    Reconciling an on-disk ledger against somebody else's network would answer
    a question nobody asked, and look authoritative doing it.
    """
    other = api_project("other-project")
    install_network(_network(), name=other)
    mine = api_project("health-demo")
    install_network(_network(), name=other)   # foreground stays on `other`

    T.record_asset_health(mine, [_entry()])
    out = T.get_asset_health(mine)
    assert out["provenance"] is None
    assert "not the project in the foreground" in out["note"]
    assert out["entries"], "the ledger itself is still served"


def test_the_write_tool_rejects_a_bad_batch_with_422(api_project):
    name = api_project("health-demo")
    with pytest.raises(HTTPException) as exc:
        T.record_asset_health(name, [_entry(method="vibes")])
    assert exc.value.status_code == 422


def test_the_ledger_never_writes_a_rate_onto_the_network(
        api_project, install_network):
    """The split that lets the network contradict the ledger."""
    name = api_project("health-demo")
    n = _network(rate=None)
    install_network(n, name=name)
    T.record_asset_health(name, [_entry()])
    from services.pypsa_service import PyPSAService
    live = PyPSAService.get_network()
    assert not (live.generators["outage_rate_value"] > 0).any()
