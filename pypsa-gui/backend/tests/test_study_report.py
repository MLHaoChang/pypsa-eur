"""
The study write-up.

`AdequacyReport` deliberately keeps the COPT screening and the sequential MC
as siblings — folding them in would grow the one shape every consumer parses
(spec §4). That is right for the wire and wrong for a client deliverable,
where the point is to put the screening, the proxy, the sampler and the
firm-capacity convention side by side.

So the fusion's whole job is to stop the fusion laundering anything, and these
tests are about the guards rather than the assembly: every section labelled,
the disclosures that must be carried, and the negative space stated.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pypsa
import pytest

from services import chat_tools as T
from services.adequacy import campaign as C
from services.adequacy import study_report as R


@pytest.fixture(autouse=True)
def _clean_campaign():
    C.reset()
    yield
    C.reset()


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=48, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add("Generator", "gas", bus="B1", carrier="gas", p_nom=200.0)
    return n


def _empty_read(section: str) -> dict:
    return {"status": "no_data", "kind": section, "message": "never run"}


def _read_with(**payloads):
    def read(section: str):
        return payloads.get(section, _empty_read(section))
    return read


# ── Labelling ──────────────────────────────────────────────────────────────


def test_every_section_appears_present_or_not():
    report = R.build_study_report(_network(), _empty_read)
    assert [s["id"] for s in report["sections"]] == list(R.SECTION_ORDER)


def test_a_section_carries_the_provenance_its_engine_states():
    """Read, never asserted: the engine owns its own label."""
    read = _read_with(copt={"engine": "copt", "fidelity": "analytic_convolution",
                            "metrics": {"lole_hours": 4.0}})
    section = next(s for s in R.build_study_report(_network(), read)["sections"]
                   if s["id"] == "copt")
    assert section["engine"] == "copt"
    assert section["fidelity"] == "analytic_convolution"
    assert section["stated_by_engine"] is True


def test_provenance_is_read_out_of_a_nested_result_block():
    """The MC's own labels live under `result`, where the study record puts them."""
    read = _read_with(mc={"status": "done",
                          "result": {"engine": "mc", "fidelity": "sequential_mc",
                                     "metrics": {}}})
    section = next(s for s in R.build_study_report(_network(), read)["sections"]
                   if s["id"] == "mc")
    assert (section["engine"], section["fidelity"]) == ("mc", "sequential_mc")


def test_a_section_without_a_stated_engine_is_labelled_and_marked_assumed():
    """
    The frontier payload states no engine. Leaving it blank would put an
    unlabelled number beside labelled ones, which is the failure this exists
    to prevent — but it must be visible that the label is ours.
    """
    read = _read_with(frontier={"status": "done", "points": [{"eps": 1.0}]})
    section = next(s for s in R.build_study_report(_network(), read)["sections"]
                   if s["id"] == "frontier")
    assert section["engine"] and section["fidelity"]
    assert section["stated_by_engine"] is False


def test_every_section_carries_a_fidelity_caveat():
    for section in R.build_study_report(_network(), _empty_read)["sections"]:
        assert section["caveat"], f"{section['id']} has no caveat"


# ── Required disclosures ───────────────────────────────────────────────────


def test_the_engine_naming_rule_is_always_required():
    report = R.build_study_report(_network(), _empty_read)
    assert any("Name the engine" in d for d in report["required_disclosures"])


def test_a_reserve_margin_forces_the_not_a_reliability_target_sentence():
    read = _read_with(reserve_margin={"margin": 0.15, "by_period": []})
    report = R.build_study_report(_network(), read)
    assert any("NOT a met reliability target" in d
               for d in report["required_disclosures"])


def test_a_copt_result_forces_the_screening_sentence():
    read = _read_with(copt={"engine": "copt", "fidelity": "analytic_convolution"})
    report = R.build_study_report(_network(), read)
    assert any("screening estimate" in d
               for d in report["required_disclosures"])


def test_an_unconverged_mc_must_be_reported_as_an_interval():
    """
    An unconverged mean quoted as a point value is the most quotable wrong
    number a reliability study can produce.
    """
    read = _read_with(mc={"result": {
        "engine": "mc", "fidelity": "sequential_mc",
        "metrics": {"converged": False, "n_samples": 200,
                    "lole_ci": [1.0, 9.0]}}})
    report = R.build_study_report(_network(), read)
    assert any("did not converge" in d.lower()
               for d in report["required_disclosures"])
    assert any("never as a point value" in d
               for d in report["required_disclosures"])


def test_a_converged_mc_still_carries_its_interval():
    read = _read_with(mc={"result": {
        "engine": "mc", "fidelity": "sequential_mc",
        "metrics": {"converged": True, "n_samples": 2000,
                    "lole_ci": [3.1, 3.4]}}})
    report = R.build_study_report(_network(), read)
    assert any("interval" in d for d in report["required_disclosures"])


def test_a_loop_forces_the_horizon_basis_sentence():
    read = _read_with(margin_loop={"status": "done", "iterations": []})
    report = R.build_study_report(_network(), read)
    assert any("HORIZON-basis hours" in d
               for d in report["required_disclosures"])


def test_disclosures_are_not_claimed_for_absent_sections():
    """A caveat about a number nobody produced is noise."""
    report = R.build_study_report(_network(), _empty_read)
    assert not any("reserve margin" in d.lower()
                   for d in report["required_disclosures"])


# ── The negative space ─────────────────────────────────────────────────────


def test_what_was_never_run_is_stated():
    """
    A report that omits "no Monte Carlo was run" reads as if LOLE was
    measured.
    """
    report = R.build_study_report(_network(), _empty_read)
    assert len(report["not_established"]) == len(R.SECTION_ORDER)
    assert any("probabilistic LOLE" in line
               for line in report["not_established"])


def test_an_established_section_leaves_the_negative_space():
    read = _read_with(copt={"engine": "copt", "fidelity": "analytic_convolution"})
    report = R.build_study_report(_network(), read)
    assert not any(line.startswith("a zero-solve screening")
                   for line in report["not_established"])
    assert report["counts"]["sections_established"] == 1
    assert report["counts"]["sections_missing"] == len(R.SECTION_ORDER) - 1


# ── Evidence gaps ──────────────────────────────────────────────────────────


def test_a_frozen_demand_profile_is_an_evidence_gap():
    """
    A reliability study resting on one pasted value is a different document,
    and the reader is told before the numbers.
    """
    n = _network()
    n.loads_t.p_set["L1"] = pd.Series(np.full(48, 100.0), index=n.snapshots)
    gaps = R.build_study_report(n, _empty_read)["evidence_gaps"]
    assert any(g["code"] == "timeseries_frozen" for g in gaps)


def test_an_unservable_island_is_an_evidence_gap():
    n = _network()
    n.add("Bus", "orphan")
    n.add("Load", "stranded", bus="orphan", p_set=50.0)
    gaps = R.build_study_report(n, _empty_read)["evidence_gaps"]
    assert any(g["code"] == "island_no_supply" for g in gaps)


def test_an_unsourced_failure_rate_is_an_evidence_gap():
    health = {"counts": {"unsourced": 3, "drifted": 0}}
    gaps = R.build_study_report(_network(), _empty_read, health=health)["evidence_gaps"]
    gap = next(g for g in gaps if g["code"] == "outage_rate_unsourced")
    assert "3 asset(s)" in gap["subject"]
    assert "every reliability number here rests on them" in gap["detail"]


def test_drifted_provenance_is_an_evidence_gap():
    health = {"counts": {"unsourced": 0, "drifted": 2}}
    gaps = R.build_study_report(_network(), _empty_read, health=health)["evidence_gaps"]
    assert any(g["code"] == "outage_rate_drifted" for g in gaps)


def test_a_clean_network_with_no_ledger_has_no_gaps():
    assert R.build_study_report(_network(), _empty_read)["evidence_gaps"] == []


# ── The tool ───────────────────────────────────────────────────────────────


def test_tool_is_registered_and_read_tier():
    from services import chat_service
    from services import chat_tools_schema as S

    assert callable(T.DISPATCHERS["build_study_report"])
    assert any(t["name"] == "build_study_report" for t in S.TOOLS)
    assert chat_service._safety_tier_for("build_study_report") == "read"


def test_the_tool_reports_against_the_live_network(install_network):
    """
    On an UNSOLVED network exactly one section is established: the COPT
    screening is computed on demand from the fleet and needs no solve. Every
    other section is honestly absent — which is the report doing its job, not
    a gap in it.
    """
    install_network(_network())
    report = T.build_study_report()
    established = [s["id"] for s in report["sections"] if s["status"] == "ok"]
    assert established == ["copt"]
    assert report["counts"]["sections_missing"] == len(R.SECTION_ORDER) - 1
    assert any("probabilistic LOLE" in line
               for line in report["not_established"])
    json.dumps(report, default=str)


def test_the_campaign_objective_becomes_the_report_objective(install_network):
    install_network(_network())
    T.start_campaign("hit LOLE <= 3 h/yr at least cost", 30)
    report = T.build_study_report()
    assert report["objective"] == "hit LOLE <= 3 h/yr at least cost"
    assert report["campaign"]["budget_solves"] == 30


def test_no_campaign_means_no_objective(install_network):
    install_network(_network())
    report = T.build_study_report()
    assert report["objective"] is None
    assert report["campaign"] is None


def test_the_copt_section_brings_its_own_screening_disclosure(install_network):
    """The one section available without a solve must not arrive unlabelled."""
    install_network(_network())
    report = T.build_study_report()
    assert any("screening estimate" in d
               for d in report["required_disclosures"])


def test_the_writing_note_forbids_inventing_beyond_the_payload(install_network):
    install_network(_network())
    note = T.build_study_report()["writing_note"]
    assert "evidence, not prose" in note
    assert "not_established" in note


def test_the_system_prompt_routes_write_ups_here():
    from services import chat_service

    prompt = chat_service._build_system_prompt(chat_service.ChatSession())
    assert "build_study_report" in prompt
    assert "evidence_gaps BEFORE the numbers" in prompt
    assert "not_established" in prompt
