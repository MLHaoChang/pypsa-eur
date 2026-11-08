"""AA-05 S5b (A4.6; owner card 2026-11-08 "Pin them as fact-free"; GK B (35) B1–B7).

The checked chart's dataset names each evidence slot's identity (`metrics`:
metric, disposition, formula_id, unit, and the reason when not answered) and
its `warnings`, the same lines as the answer receipt's limitations (B3).

- For every profile, the identity agrees with the sealed record, the answer's
  blocks and the receipt.
- B1: the identity is read from the record only, so a definition changed after
  the chart was drawn leaves it "current".
- B2: a chart drawn before S5b carries neither key and still verifies; a
  dataset missing only one of them does not.
- K1/B6: evidence from another project draws nothing and writes nothing.
"""

# ruff: noqa: F811 -- imported shared fixtures.
import json

import pytest

from harness import calculations
from services import assurance_records as records
from services import checked_charts
from tests.assurance_acceptance.fact_profiles import *  # noqa: F401,F403
from tests.assurance_acceptance.fact_profiles import DRIVERS, build, owner
from tests.assurance_acceptance.support import approval
from tests.test_assurance_metrics import NumericalProvider
from tests.test_branch_loading_assurance import LINE
from tests.test_branch_loading_assurance import solve as solve_grid
from tests.test_checked_charts import GENERATOR, ask, sidecar

PROFILES = sorted(DRIVERS)


def _answer(checked, scope, record):
    values = {f["metric"]: f["value"] for f in record["facts"]}
    frames = checked.run(
        NumericalProvider(values), message="Check results.", checked_scope=scope
    )
    published = approval(frames)
    answer = checked.client.get("/api/chat/answers/" + published["answer_id"])
    assert answer.status_code == 200, answer.text
    receipt = published["receipt"]
    [evidence_id] = receipt["evidence_ids"]
    return receipt, answer.json(), records.evidence(owner(checked), evidence_id)


@pytest.mark.parametrize("profile", PROFILES)
def test_the_chart_identity_is_the_records_and_the_answers(request, checked, profile):
    _ctx, scope = build(request, profile)
    receipt, answer, stored = _answer(
        checked, scope, records.capture_scoped(owner(checked), scope)
    )
    metrics = checked_charts.identity(stored)
    facts = {f["metric"]: f for f in stored["facts"]}
    blocks = {b["metric"]: b for b in answer["blocks"]}
    # One entry per requested slot, in the slots' order, with the slot's disposition.
    assert [m["metric"] for m in metrics] == list(calculations.requested_metrics(scope))
    assert [(m["metric"], m["disposition"]) for m in metrics] == [
        (s["metric"], s["disposition"]) for s in receipt["result_slots"]
    ]
    for entry in metrics:
        if entry["disposition"] == "answered":
            fact = facts[entry["metric"]]
            assert entry == {
                "metric": fact["metric"],
                "disposition": "answered",
                "formula_id": fact["formula_id"],
                "unit": fact["unit"],
            }
            block = blocks[entry["metric"]]
            assert (block["formula_id"], block["unit"]) == (
                entry["formula_id"],
                entry["unit"],
            )
        else:
            slot = next(s for s in stored["slots"] if s["metric"] == entry["metric"])
            assert entry == {
                "metric": slot["metric"],
                "disposition": slot["disposition"],
                "formula_id": None,
                "unit": None,
                "reason": slot["reason"],
            }
            assert entry["metric"] not in blocks and entry["reason"].strip()
    # B3: the chart's warnings are the receipt's limitations, line for line.
    assert calculations.slot_warnings(stored["slots"]) == receipt["limitations"]


def test_a_drawn_chart_carries_the_identity_of_its_evidence(solved, checked):
    receipt = ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p"])
    manifest = sidecar(receipt["chart"])
    record = records.evidence(owner(checked), receipt["evidence_ids"][0])
    assert manifest["dataset"]["metrics"] == checked_charts.identity(record)
    assert manifest["dataset"]["metrics"] == [
        {
            "metric": f["metric"],
            "disposition": "answered",
            "formula_id": f["formula_id"],
            "unit": f["unit"],
        }
        for f in record["facts"]
    ]
    assert {m["metric"] for m in manifest["dataset"]["metrics"]} == set(GENERATOR)
    # Every slot answered: the key is present and empty, as the receipt has none.
    assert manifest["dataset"]["warnings"] == [] == receipt["limitations"]
    assert checked_charts.verify(manifest) == "current"


def _branch_chart(grid, checked):
    """A drawn branch chart whose evidence has unavailable slots (a NaN flow inside the window)."""
    ctx = solve_grid(grid)
    first, second = (v.isoformat() for v in ctx.network.snapshots[:2])
    ctx.network.lines_t.p0.iloc[0, 0] = float("nan")
    receipt = ask(
        checked,
        ctx,
        "branch-loading/1",
        "L12",
        {"branch_rating": LINE["branch_rating"]},
        ["p1", "loading"],
        component_class="Line",
        start=first,
        end=second,
    )
    assert receipt["chart"]["status"] == "drawn", receipt["chart"]
    return receipt


def _generator_chart(solved, checked):
    return ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p"])


CHARTS = {"generator": ("solved", _generator_chart), "branch": ("grid", _branch_chart)}


def test_a_branch_chart_names_an_unavailable_slot_and_warns(grid, checked):
    receipt = _branch_chart(grid, checked)
    manifest = sidecar(receipt["chart"])
    record = records.evidence(owner(checked), receipt["evidence_ids"][0])
    unavailable = [s for s in record["slots"] if s["disposition"] != "answered"]
    assert unavailable, record["slots"]
    assert manifest["dataset"]["warnings"] == [
        f"{s['metric']}: {s['reason']}" for s in unavailable
    ]
    assert manifest["dataset"]["warnings"] == receipt["limitations"]
    named = {m["metric"]: m for m in manifest["dataset"]["metrics"]}
    for slot in unavailable:
        assert named[slot["metric"]] == {
            "metric": slot["metric"],
            "disposition": slot["disposition"],
            "formula_id": None,
            "unit": None,
            "reason": slot["reason"],
        }
    assert checked_charts.verify(manifest) == "current"


@pytest.mark.parametrize("kind", sorted(CHARTS))
def test_a_definition_changed_after_drawing_still_verifies(
    request, checked, monkeypatch, kind
):
    """B1: the identity comes from the sealed record, never the live definitions,
    for answered slots and for unavailable ones (the branch chart has both)."""
    fixture, draw = CHARTS[kind]
    receipt = draw(request.getfixturevalue(fixture), checked)
    manifest = sidecar(receipt["chart"])
    for metric in [m["metric"] for m in manifest["dataset"]["metrics"]]:
        changed = {
            **calculations.DEFINITIONS[metric],
            "formula_id": metric + "/changed",
            "unit": "changed",
        }
        monkeypatch.setitem(calculations.DEFINITIONS, metric, changed)
    assert checked_charts.verify(manifest) == "current"


# The series each profile with a per-snapshot series can draw (checked_charts.series_table).
SERIES = {
    "generator-dispatch/1": ["p"],
    "storage-unit-dispatch/1": ["p_store", "p_dispatch", "state_of_charge"],
    "bus-price/1": ["marginal_price"],
    "branch-loading/1": ["p0", "p1", "loading"],
}


@pytest.mark.parametrize("profile", sorted(SERIES))
def test_each_drawable_profile_draws_its_records_identity(request, checked, profile):
    """End to end for every profile that draws: the manifest's metrics and
    warnings are the record's and the receipt's."""
    assert set(SERIES) <= set(DRIVERS)
    _ctx, scope = build(request, profile)
    record = records.capture_scoped(owner(checked), scope)
    receipt, _answer_json, stored = _answer(
        checked, {**scope, "chart": SERIES[profile]}, record
    )
    chart = receipt["chart"]
    assert chart["status"] == "drawn", chart
    manifest = sidecar(chart)
    assert manifest["dataset"]["metrics"] == checked_charts.identity(stored)
    assert [m["metric"] for m in manifest["dataset"]["metrics"]] == [
        s["metric"] for s in stored["slots"]
    ]
    assert manifest["dataset"]["warnings"] == receipt["limitations"]
    assert checked_charts.verify(manifest) == "current"


def _without(manifest, *keys):
    dataset = {k: v for k, v in manifest["dataset"].items() if k not in keys}
    return {**manifest, "dataset": dataset}


def test_a_chart_drawn_before_the_identity_still_verifies(solved, checked):
    """B2: neither key is a legacy chart; one alone is a mismatch."""
    receipt = ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p"])
    manifest = sidecar(receipt["chart"])
    legacy = _without(manifest, *checked_charts.IDENTITY_KEYS)
    assert checked_charts.verify(legacy) == "current"
    assert checked_charts.verify(_without(manifest, "metrics")) is False
    assert checked_charts.verify(_without(manifest, "warnings")) is False
    # A legacy dataset is still compared value for value.
    tampered = json.loads(json.dumps(legacy))
    tampered["dataset"]["values"][0][0] += 1
    assert checked_charts.verify(tampered) is False
    # An empty warnings list is a key that is present, not a legacy chart
    # (the "metrics" removal above already leaves it alone).
    assert manifest["dataset"]["warnings"] == []


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["metrics"].pop(),
        lambda d: d["metrics"][0].update(formula_id="other"),
        lambda d: d["metrics"][0].update(unit="GW"),
        lambda d: d["metrics"][0].update(disposition="unavailable"),
        lambda d: d["warnings"].append("dispatch_energy: made up"),
    ],
)
def test_a_changed_identity_does_not_verify(solved, checked, change):
    receipt = ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p"])
    manifest = json.loads(json.dumps(sidecar(receipt["chart"])))
    change(manifest["dataset"])
    assert checked_charts.verify(manifest) is False


def test_evidence_from_another_project_draws_and_writes_nothing(
    solved, checked, monkeypatch
):
    """K1/B6: refused before drawing, saving the file or the sidecar."""
    from services import chat_tools
    from services.assistant_tools import _project

    receipt = ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p"])
    record = records.evidence(owner(checked), receipt["evidence_ids"][0])
    _, root, _ = _project()
    before = sorted(p.name for p in (root / "assistant_artifacts").iterdir())

    def must_not_run(*args, **kwargs):
        raise AssertionError("drew or saved for another project's evidence")

    monkeypatch.setattr(checked_charts, "_draw", must_not_run)
    monkeypatch.setattr(chat_tools, "_save_agent_export", must_not_run)
    other = {**record, "project_id": "00000000-0000-4000-8000-000000000000"}
    chart = checked_charts.render(solved, other, ["p"])
    assert chart["status"] == "not_drawn" and chart["reason"] == "other_project"
    assert chart["series"][0]["title"] == checked_charts.REASONS["other_project"]
    assert sorted(p.name for p in (root / "assistant_artifacts").iterdir()) == before


def test_a_manifest_naming_another_project_does_not_verify(solved, checked):
    receipt = ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p"])
    manifest = sidecar(receipt["chart"])
    assert checked_charts.verify(manifest) == "current"
    moved = {**manifest, "project_id": "00000000-0000-4000-8000-000000000000"}
    assert checked_charts.verify(moved) is False
