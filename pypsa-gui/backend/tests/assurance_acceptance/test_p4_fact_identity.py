"""AA-05 S5a (A4.6, AA05-FREEZE-1; GK B (34) Q2 (a), Q3 (a), K1–K3).

For every profile in `harness.calculations.PROFILES`, and so every metric in
`DEFINITIONS`: the evidence record, the receipt, the answer's blocks and the
evidence route agree on each fact's identity (metric, formula_id, unit,
entity_id, window, evidence_id, project_id) and each slot's disposition.

- The record stores `window` (start, end, snapshot count, weight basis per
  metric) and `solve_stamp` {input, exact}. `public` strips both, so the
  evidence route and the checked export are byte-equal to before (stored-format
  ruling), and a record saved without them reads as it did.
- K2: an unavailable slot never reads as 0 or blank, and keeps its reason.
- K3: machine surfaces carry the exact float: repr equality, no rounding.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import pytest

from harness import calculations, fact_identity
from services import assurance_records as records
from services.assurance_artifacts import solve_stamp
from services.run_evidence import RUN_MAC_DOMAIN
from tests.assurance_acceptance.fact_profiles import *  # noqa: F401,F403
from tests.assurance_acceptance.fact_profiles import DRIVERS, build, owner
from tests.assurance_acceptance.support import approval
from tests.test_assurance_metrics import NumericalProvider

PROFILES = sorted(DRIVERS)


def _unit_matches(unit, declared):
    """A declared unit, or the one a placeholder resolves to for this entity:
    capacity's by component class, an investment report's by its currency."""
    if declared == "MW, MVA or MWh by class":
        return unit in {"MW", "MVA", "MWh"}
    currency, _, rest = unit.partition("/")
    resolved = len(currency) == 3 and currency.isalpha() and currency.isupper()
    if declared == "report currency":
        return resolved and not rest
    if declared == "currency/MWh":
        return resolved and rest == "MWh"
    return unit == declared


def _capture(request, checked, profile):
    ctx, scope = build(request, profile)
    return ctx, scope, records.capture_scoped(owner(checked), scope)


def _answer(checked, scope, record):
    """The checked turn, answered with the record's own values."""
    values = {f["metric"]: f["value"] for f in record["facts"]}
    frames = checked.run(NumericalProvider(values), message="Check results.", checked_scope=scope)
    published = approval(frames)
    answer = checked.client.get("/api/chat/answers/" + published["answer_id"])
    assert answer.status_code == 200, answer.text
    return published["receipt"], answer.json()


def test_every_metric_has_one_weight_basis():
    assert list(fact_identity.WEIGHT_BASIS) == list(calculations.DEFINITIONS)
    tokens = {"generators", "objective", "stores", "generators*years", "none", "derived",
              "mixed:generators+stores"}
    assert set(fact_identity.WEIGHT_BASIS.values()) <= tokens


def test_every_metric_is_reached_by_a_profile_driver():
    reached = {m for p in PROFILES for m in calculations.PROFILES[p]}
    # capacity/1 asks for its DEFAULT_METRICS; configured_capacity is opt-in by name.
    assert set(calculations.DEFINITIONS) - reached <= {"configured_capacity", "cost_delta",
                                                      "cost_change_percent"}


@pytest.mark.parametrize("profile", PROFILES)
def test_the_record_stores_its_window_and_solve_stamp(request, checked, profile):
    ctx, scope, record = _capture(request, checked, profile)
    window = record["window"]
    assert (window["start"], window["end"]) == (record["scope"]["start"], record["scope"]["end"])
    requested = calculations.requested_metrics(scope)
    if profile in fact_identity.NO_SNAPSHOT_INDEX:
        # No snapshot index: no count, and nothing is snapshot-weighted.
        assert window["snapshots"] is None
        assert set(window["weight_basis"].values()) == {"none"}
    else:
        assert isinstance(window["snapshots"], int) and window["snapshots"] >= 1
    assert window["weight_basis"] == {m: fact_identity.WEIGHT_BASIS[m] for m in requested}
    assert record["solve_stamp"] == solve_stamp(ctx.network)
    assert record["project_id"] == ctx.project_uuid == scope["project_id"]


@pytest.mark.parametrize("profile", PROFILES)
def test_each_surface_names_the_same_fact(request, checked, profile):
    _ctx, scope, record = _capture(request, checked, profile)
    requested = calculations.requested_metrics(scope)
    assert [s["metric"] for s in record["slots"]] == list(requested)
    for fact in record["facts"]:
        definition = calculations.definition(fact["metric"], profile)
        assert fact["formula_id"] == definition["formula_id"]
        assert _unit_matches(fact["unit"], definition["unit"]), (fact["metric"], fact["unit"])
        assert fact["entity_id"] == scope["entity_id"]
        assert fact["path"] == "/" + fact["metric"]
    receipt, answer = _answer(checked, scope, record)
    [evidence_id] = receipt["evidence_ids"]
    stored = records.evidence(owner(checked), evidence_id)
    route = checked.client.get("/api/chat/evidence/" + evidence_id)
    assert route.status_code == 200, route.text
    shown = route.json()
    # The same fact on every machine surface: record, evidence route, answer blocks.
    facts = {f["metric"]: f for f in stored["facts"]}
    assert facts.keys() == {f["metric"] for f in record["facts"]}
    assert {f["metric"]: f for f in shown["facts"]} == facts
    blocks = {b["metric"]: b for b in answer["blocks"]}
    assert blocks.keys() == facts.keys()
    for metric, fact in facts.items():
        block = blocks[metric]
        for key in ("entity_id", "metric", "unit", "formula_id", "path"):
            assert block[key] == fact[key], (metric, key)
        assert repr(block["value"]) == repr(fact["value"]), metric  # K3: no rounding
        assert block["source"] == {"evidence_id": evidence_id, "path": fact["path"]}
    # The receipt's slots are the evidence's; its window is the record's.
    assert receipt["result_slots"] == stored["slots"]
    assert stored["window"]["weight_basis"] == record["window"]["weight_basis"]
    assert (stored["scope"]["start"], stored["scope"]["end"]) == (
        stored["window"]["start"], stored["window"]["end"])
    assert shown["project_id"] == stored["project_id"] == scope["project_id"]


@pytest.mark.parametrize("profile", PROFILES)
def test_unavailable_never_reads_as_zero_or_blank(request, checked, profile):
    """K2 on the record, receipt and answer: a slot without a fact keeps its reason."""
    _ctx, scope, record = _capture(request, checked, profile)
    answered = {f["metric"] for f in record["facts"]}
    for slot in record["slots"]:
        assert slot["disposition"] == ("answered" if slot["metric"] in answered else "unavailable")
        if slot["disposition"] == "unavailable":
            assert isinstance(slot.get("reason"), str) and slot["reason"].strip()
            assert slot["metric"] not in answered
    if answered == {s["metric"] for s in record["slots"]}:
        return
    receipt, answer = _answer(checked, scope, record)
    missing = {s["metric"]: s["reason"] for s in receipt["result_slots"]
               if s["disposition"] == "unavailable"}
    assert missing and not missing.keys() & {b["metric"] for b in answer["blocks"]}
    for metric, reason in missing.items():
        assert f"{metric}: {reason}" in receipt["limitations"]


def test_a_slot_that_excludes_snapshots_names_its_rows(request, checked):
    """Rows used and excluded come from the slot's valid/requested snapshots."""
    ctx, scope = build(request, "generator-dispatch/1")
    ctx.network.generators_t.p.iloc[0, ctx.network.generators_t.p.columns.get_loc("G1")] = float("nan")
    record = records.capture_scoped(owner(checked), scope)
    slot = next(s for s in record["slots"] if s["metric"] == "dispatch_energy")
    assert slot["disposition"] == "unavailable" and slot["reason"]
    assert slot["requested_snapshots"] == record["window"]["snapshots"]
    if "valid_snapshots" in slot:
        assert slot["valid_snapshots"] < slot["requested_snapshots"]


def test_the_evidence_route_and_export_hide_the_new_keys(request, checked):
    """Stored-format ruling: existing readers see the output they saw before."""
    _ctx, scope, record = _capture(request, checked, "generator-dispatch/1")
    stored = records.evidence(owner(checked), record["evidence_id"])
    assert {"window", "solve_stamp"} <= stored.keys()
    shown = records.public(stored)
    assert not shown.keys() & records.IDENTITY_KEYS
    legacy = {k: v for k, v in stored.items() if k not in records.IDENTITY_KEYS}
    assert records.public(legacy) == shown
    # A non-evidence record keeps a key of the same name.
    assert records.public({"record_kind": "answer", "window": 1})["window"] == 1


def test_a_record_saved_without_the_new_keys_still_reads(request, checked):
    _ctx, _scope, record = _capture(request, checked, "generator-dispatch/1")
    legacy = {k: v for k, v in record.items() if k not in records.IDENTITY_KEYS}
    legacy["evidence_id"] = "legacy-" + record["evidence_id"]
    records.save(legacy, legacy["evidence_id"])
    loaded = records.evidence(owner(checked), legacy["evidence_id"])
    assert loaded == legacy
    assert records.public(loaded) == records.public({**legacy, "window": {}, "solve_stamp": None})


def test_the_mac_domains_are_unchanged():
    assert records.RECORD_MAC_DOMAIN == b"pypsa-gui assistant-assurance record/1"
    assert RUN_MAC_DOMAIN == b"pypsa-gui run-manifest/1"
    assert records.server_key(records.RECORD_MAC_DOMAIN) != records.server_key(RUN_MAC_DOMAIN)
