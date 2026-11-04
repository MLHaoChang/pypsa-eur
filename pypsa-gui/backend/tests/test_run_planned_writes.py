# ruff: noqa: F811  (pytest fixtures imported from sibling test modules)
"""Study harness issue 21c: a stage-2 planned write (`apply_demand_from_excel`
from a confirmed intake row) is admitted under the envelope only by its own
card digest, pinned on the created network with the target load's uid (C1);
the plan check and the write share one network revision (C2); the tool
resolves the project through the bound context, and no sweep or study tool
is ever admitted (C3). LOAD-SHAPE-1: no shaped load profile without a card
or a user-confirmed intake row.

Written ahead of the implementation (MAX-BUILDERS-1: no code until #1100
merges); the names it calls are the plan sent to GK B (21).
"""

from __future__ import annotations

import hashlib
import json

import pytest

from harness import edit_card
from harness import runner as harness_runner
from services import (
    chat_tools,
    component_identity,
    dirty_state,
    run_envelope,
    run_profiles,
    upload_service,
)
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline,
    dispatch,
    edit,
    helpers_state,
    session,
    wait_until,
)
from tests.test_harness_envelope import (  # noqa: F401
    envelope_of,
    granted,
    profile,
    refusals,
    stopped,
    until,
)
from tests.test_harness_envelope_atomic import gap, writer  # noqa: F401
from tests.test_harness_run_skeleton import foreground, site, two_buses  # noqa: F401
from tests.test_harness_runner import detached, task_path  # noqa: F401

TOOL = "apply_demand_from_excel"
# The baseline network: two hourly snapshots from 2026-01-01, Load L1 at 5 MW.
CSV = b"time,demand_mw\n2026-01-01 00:00,7.5\n2026-01-01 01:00,9.0\n"
OTHER_CSV = b"time,demand_mw\n2026-01-01 00:00,7.5\n2026-01-01 01:00,9.5\n"


def ctx():
    return PyPSAService.get_active_context()


def series(name="L1"):
    p_set = ctx().network.loads_t.p_set
    return list(p_set[name]) if name in p_set.columns else None


@pytest.fixture
def upload(site):
    ctx_ = ctx()

    def add(data=CSV, filename="demand.csv"):
        return upload_service.add_upload(
            ctx_.name, data, filename, "text/csv", project_dir=site[1]
        ).file_id

    return add


def args(file_id, load="L1"):
    return {
        "file_id": file_id,
        "time_col": "time",
        "value_col": "demand_mw",
        "load_name": load,
    }


def write_step(file_id, n=1, load="L1"):
    return {"id": f"inputs.{n}", "tool": TOOL, "args": args(file_id, load)}


@pytest.fixture
def planned(granted, upload, site, monkeypatch):
    """A granted run whose grant lists one confirmed intake write row, pinned
    on the network as stage 1 does after creating the project."""
    monkeypatch.setitem(
        run_profiles.PROFILES,
        "eh_site@1",
        {**run_profiles.EH_SITE, "envelope": {**run_profiles.EH_SITE["envelope"], "edits": []}},
    )

    def go(file_id=None, load="L1", steps=None, pin=True):
        file_id = file_id or upload()
        sha = hashlib.sha256(
            upload_service.get_upload_bytes(ctx().name, file_id, project_dir=site[1])
        ).hexdigest()
        row = {
            "write": TOOL,
            "args": args(file_id, load),
            "source": {"kind": "table", "upload_id": file_id, "sha256": sha},
            "status": "confirmed",
        }
        task, path, _ = granted(
            steps=steps if steps is not None else [write_step(file_id, load=load)]
        )
        run_envelope.plan_writes(path, [row])  # what the grant records
        if pin:
            run_envelope.pin_planned(path, ctx())  # what stage 1 records
        return task, path, file_id

    return go


def written(path):
    return [a for a in envelope_of(path)["audit"] if a["event"] == "used" and a["tool"] == TOOL]


# ── The card (P1) ─────────────────────────────────────────────────────────


def test_the_card_binds_the_load_uid_the_upload_and_the_aligned_series(site, upload):
    file_id = upload()
    card = edit_card.preview(TOOL, args(file_id))
    binding = card["binding"]
    assert card["complete"] and card["kind"] == "series_edit"
    assert binding["load_uid"] == component_identity.uid_for(ctx().network, "Load", "L1")
    assert binding["upload_id"] == file_id and binding["value_col"] == "demand_mw"
    assert binding["upload_sha256"] == hashlib.sha256(CSV).hexdigest()
    assert card["digest"] == edit_card._digest({k: v for k, v in card.items() if k != "digest"})
    assert series() is None  # a card writes nothing


def test_a_different_upload_is_a_different_card(site, upload):
    one = edit_card.preview(TOOL, args(upload()))
    two = edit_card.preview(TOOL, args(upload(OTHER_CSV, "other.csv")))
    assert one["binding"]["series_sha256"] != two["binding"]["series_sha256"]
    assert one["digest"] != two["digest"]


# ── C1: admitted only by the pinned card digest and uid ───────────────────


def test_c1_a_planned_write_whose_card_equals_the_grant_is_admitted(planned):
    task, path, _ = planned()
    until(harness_runner.Runner("a"), path, task, stopped)
    assert series() == [7.5, 9.0]
    assert len(written(path)) == 1 and not refusals(path)


def test_c1_a_renamed_load_gets_a_card(planned):
    task, path, _ = planned()
    network = ctx().network
    network.add("Load", "L2", bus="B1", p_set=1.0)
    uid = component_identity.uid_for(network, "Load", "L1")
    component_identity.rename(network, "Load", "L1", "L_old")
    network.loads.rename(index={"L1": "L_old", "L2": "L1"}, inplace=True)
    assert component_identity.uid_for(network, "Load", "L1") != uid
    dirty_state.bump_revision(ctx())
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert record["run"]["status"] == "paused" and not written(path)
    assert "L1" not in ctx().network.loads_t.p_set.columns


def test_c1_a_load_deleted_and_made_again_under_the_granted_name_gets_a_card(planned):
    task, path, _ = planned()
    network = ctx().network
    network.remove("Load", "L1")
    component_identity.drop(network, "Load", "L1")
    network.add("Load", "L1", bus="B1", p_set=5)  # same name, same values, new uid
    dirty_state.bump_revision(ctx())
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert record["run"]["status"] == "paused" and not written(path)
    assert series() is None


def test_c1_a_changed_upload_gets_a_card(planned, upload):
    task, path, _ = planned()
    # The step now names other bytes than the grant listed.
    other = upload(OTHER_CSV, "other.csv")
    record = json.loads(path.read_text())
    record["steps"][-1]["args"] = args(other)
    path.write_text(json.dumps(record))
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert record["run"]["status"] == "paused" and not written(path)
    assert series() is None


def test_c1_a_row_the_plan_leaves_out_gets_a_card(planned, upload):
    """A write the grant did not list (another load) is never admitted."""
    ctx().network.add("Load", "L2", bus="B1", p_set=1.0)
    file_id = upload()
    task, path, _ = planned(file_id, steps=[write_step(file_id, load="L2")])
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert record["run"]["status"] == "paused" and not written(path)
    assert series("L2") is None


def test_c1_a_planned_write_is_admitted_once(planned):
    task, path, file_id = planned()
    record = json.loads(path.read_text())
    record["steps"].append(dict(write_step(file_id, n=2), status="pending"))
    path.write_text(json.dumps(record))
    until(harness_runner.Runner("a"), path, task, stopped)
    assert len(written(path)) == 1


def test_c1_a_write_never_pinned_at_creation_gets_a_card(planned):
    task, path, _ = planned(pin=False)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert record["run"]["status"] == "paused" and not written(path) and series() is None


# ── C2: one network revision from plan check to write ─────────────────────


def test_c2_a_revision_bump_between_the_plan_check_and_the_write_gets_a_card(planned, gap):
    task, path, _ = planned()
    fired = gap(
        lambda: writer(lambda n: n.generators.__setitem__("p_nom", 21.0)).join(), tool=TOOL
    )
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert fired
    assert record["run"]["status"] == "paused" and record["run"]["reason"] == "envelope_drift"
    assert series() is None  # nothing written under the lock


def test_c2_a_planned_write_is_an_atomic_tool():
    assert TOOL in run_envelope.ATOMIC_TOOLS


# ── C3: bound context; no sweep or study tool ─────────────────────────────


def test_c3_the_tool_writes_the_runs_project_not_the_foreground(site, upload):
    from services.study.runner import _on_ctx

    run_ctx = ctx()
    file_id = upload()
    with foreground(two_buses()) as front:
        _on_ctx(run_ctx, chat_tools.apply_demand_from_excel, **args(file_id))
        assert PyPSAService.get_network() is front.network
        assert "L1" not in front.network.loads_t.p_set.columns
    assert list(run_ctx.network.loads_t.p_set["L1"]) == [7.5, 9.0]


def test_c3_it_joins_the_bound_context_tools_and_no_sweep_or_study_tool_does():
    assert TOOL in run_profiles.BOUND_CONTEXT_TOOLS
    assert not run_profiles.BOUND_CONTEXT_TOOLS & run_envelope.NEVER_LISTED
    assert not {t for t in run_profiles.BOUND_CONTEXT_TOOLS if "sweep" in t or "study" in t}
    assert run_envelope.PLANNED_TOOLS == frozenset({TOOL})


def test_c3_an_agent_turn_never_gets_the_planned_write():
    assert TOOL not in run_profiles.L1_TOOLS


# ── LOAD-SHAPE-1 ──────────────────────────────────────────────────────────


def test_load_shape_1_no_series_is_written_without_a_confirmed_intake_row(granted, upload):
    file_id = upload()
    task, path, _ = granted(steps=[write_step(file_id)])  # no planned row on the grant
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert record["run"]["status"] == "paused" and series() is None


def test_load_shape_1_a_struck_or_proposed_row_is_never_planned(granted, upload):
    file_id = upload()
    task, path, _ = granted(steps=[write_step(file_id)])
    row = {"write": TOOL, "args": args(file_id), "source": {"kind": "table", "upload_id": file_id}}
    run_envelope.plan_writes(path, [dict(row, status="struck"), dict(row, status="proposed")])
    assert envelope_of(path).get("planned", []) == []
