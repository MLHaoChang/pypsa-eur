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

from fastapi import HTTPException

from harness import edit_card
from harness import runner as harness_runner
from services import (
    chat_tools,
    component_identity,
    dirty_state,
    run_envelope,
    run_handover,
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
    ask,
    envelope_of,
    granted,
    on_run,
    profile,
    refusals,
    stopped,
    until,
)
from tests.test_harness_envelope_atomic import gap, writer  # noqa: F401
from tests.test_harness_run_skeleton import foreground, site, two_buses  # noqa: F401
from tests.test_harness_runner import detached, task_path  # noqa: F401
from tests.test_run_intake_grant import host  # noqa: F401

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
    def add(data=CSV, filename="demand.csv"):
        return upload_service.add_upload(
            PyPSAService.get_loaded_project(),
            data,
            filename,
            "text/csv",
            project_dir=site[1],
        ).file_id

    return add


def args(file_id, load="L1"):
    return {
        "file_id": file_id,
        "time_col": "time",
        "value_col": "demand_mw",
        "load_name": load,
    }


def write_step(file_id, n=5, load="L1"):
    return {"id": f"inputs.{n}", "tool": TOOL, "args": args(file_id, load)}


@pytest.fixture
def planned(granted, upload, site, monkeypatch):
    """A granted run whose grant lists one confirmed intake write row, pinned
    on the network as stage 1 does after creating the project."""
    monkeypatch.setitem(
        run_profiles.PROFILES,
        "eh_site@1",
        {
            **run_profiles.EH_SITE,
            "envelope": {**run_profiles.EH_SITE["envelope"], "edits": []},
        },
    )

    def go(file_id=None, load="L1", steps=None, pin=True, detach=True):
        file_id = file_id or upload()
        sha = hashlib.sha256(
            upload_service.get_upload_bytes(
                PyPSAService.get_loaded_project(), file_id, project_dir=site[1]
            )
        ).hexdigest()
        row = {
            "write": TOOL,
            "args": args(file_id, load),
            "source": {"kind": "table", "upload_id": file_id, "sha256": sha},
            "status": "confirmed",
        }
        task, path, _ = granted(
            steps=steps if steps is not None else [write_step(file_id, load=load)],
            detach=detach,
        )
        run_envelope.plan_writes(path, [row])  # what the grant records
        if pin:
            run_envelope.pin_planned(path, ctx())  # what stage 1 records
        return task, path, file_id

    return go


def accept(path):
    """The network's current inputs, accepted onto the run's chain and brief
    (as a carded user edit would be), so a test reaches admission (C1) and
    not the drift stop."""
    fingerprint = run_envelope.identity(ctx())["fingerprint"]
    record = json.loads(path.read_text())
    record["fingerprint"] = fingerprint
    path.write_text(json.dumps(record))
    envelope = envelope_of(path)
    envelope["chain"].append({"after_change": "user-card", "fingerprint": fingerprint})
    run_envelope.path_for(path).write_text(json.dumps(envelope))


def carded(record):
    """The run waits for the user's card on the step: nothing admitted it."""
    run = record["run"]
    return (
        run["status"] == "needs_user"
        and (run.get("waiting") or {}).get("kind") == "card"
    )


def written(path):
    return [
        a
        for a in envelope_of(path)["audit"]
        if a["event"] == "used" and a["tool"] == TOOL
    ]


def missed(path, kind):
    """G9: a call that misses its pin is refused under the destructive tier,
    with the typed kind it missed on in the audit row's `planned_miss`."""
    row = [a for a in envelope_of(path)["audit"] if a["event"] == "refused"][-1]
    return row["reason"] == "destructive" and row.get("planned_miss") == kind


# ── The card (P1) ─────────────────────────────────────────────────────────


def test_the_card_binds_the_load_uid_the_upload_and_the_aligned_series(site, upload):
    file_id = upload()
    uid = component_identity.uid_for(
        ctx().network, "Load", "L1"
    )  # minted by the pin, never a card
    card = edit_card.preview(TOOL, args(file_id))
    binding = card["binding"]
    assert card["complete"] and card["kind"] == "series_edit"
    assert binding["load_uid"] == uid
    assert binding["file_id"] == file_id and binding["value_col"] == "demand_mw"
    assert binding["upload_sha256"] == hashlib.sha256(CSV).hexdigest()
    assert card["digest"] == edit_card._digest(
        {k: v for k, v in card.items() if k != "digest"}
    )
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
    accept(path)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and not written(path) and missed(path, "uid_changed")
    assert "L1" not in ctx().network.loads_t.p_set.columns


def test_c1_a_load_deleted_and_made_again_under_the_granted_name_gets_a_card(planned):
    task, path, _ = planned()
    network = ctx().network
    network.remove("Load", "L1")
    component_identity.drop(network, "Load", "L1")
    network.add("Load", "L1", bus="B1", p_set=5)  # same name, same values, new uid
    dirty_state.bump_revision(ctx())
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and not written(path) and missed(path, "uid_changed")
    assert series() is None


def test_c1_a_card_that_differs_from_the_pin_gets_a_card(planned):
    """Same args, same load, same upload: the card shows another series it
    replaces (the load's demand moved since the pin), so not the pinned digest."""
    task, path, _ = planned()
    ctx().network.loads.at["L1", "p_set"] = 6.0
    dirty_state.bump_revision(ctx())
    accept(path)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and not written(path) and missed(path, "plan_changed")
    assert series() is None


def test_c1_a_changed_upload_gets_a_card(planned, upload):
    task, path, _ = planned()
    # The step now names other bytes than the grant listed.
    other = upload(OTHER_CSV, "other.csv")
    record = json.loads(path.read_text())
    record["steps"][-1]["args"] = args(other)
    path.write_text(json.dumps(record))
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and not written(path)
    assert series() is None


def test_c1_a_row_the_plan_leaves_out_gets_a_card(planned, upload):
    """A write the grant did not list (another load) is never admitted."""
    ctx().network.add("Load", "L2", bus="B1", p_set=1.0)
    file_id = upload()
    task, path, _ = planned(file_id, steps=[write_step(file_id, load="L2")])
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and not written(path)
    assert series("L2") is None


def test_c1_a_planned_write_is_admitted_once(planned):
    task, path, file_id = planned()
    record = json.loads(path.read_text())
    record["steps"].append(dict(write_step(file_id, n=6), status="pending"))
    path.write_text(json.dumps(record))
    until(harness_runner.Runner("a"), path, task, stopped)
    assert len(written(path)) == 1


def test_c1_a_write_never_pinned_at_creation_gets_a_card(planned):
    task, path, _ = planned(pin=False)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and not written(path) and series() is None


# ── C2: one network revision from plan check to write ─────────────────────


def test_c2_a_revision_bump_between_the_plan_check_and_the_write_gets_a_card(
    planned, gap
):
    task, path, _ = planned()
    fired = gap(
        lambda: writer(lambda n: n.generators.__setitem__("p_nom", 21.0)).join(),
        tool=TOOL,
    )
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert fired
    assert (
        record["run"]["status"] == "paused"
        and record["run"]["reason"] == "envelope_drift"
    )
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
    assert not {
        t for t in run_profiles.BOUND_CONTEXT_TOOLS if "sweep" in t or "study" in t
    }
    assert run_envelope.PLANNED_TOOLS == frozenset({TOOL})


def test_c3_an_agent_turn_never_gets_the_planned_write():
    assert TOOL not in run_profiles.L1_TOOLS


# ── LOAD-SHAPE-1 ──────────────────────────────────────────────────────────


def test_load_shape_1_no_series_is_written_without_a_confirmed_intake_row(
    granted, upload
):
    file_id = upload()
    task, path, _ = granted(steps=[write_step(file_id)])  # no planned row on the grant
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and series() is None


def test_load_shape_1_a_struck_or_proposed_row_is_never_planned(granted, upload):
    file_id = upload()
    task, path, _ = granted(steps=[write_step(file_id)])
    sha = hashlib.sha256(
        CSV
    ).hexdigest()  # a complete row: only its status keeps it out
    row = {
        "write": TOOL,
        "args": args(file_id),
        "source": {"kind": "table", "upload_id": file_id, "sha256": sha},
    }
    run_envelope.plan_writes(
        path, [dict(row, status="struck"), dict(row, status="proposed")]
    )
    assert envelope_of(path).get("planned", []) == []
    run_envelope.plan_writes(path, [dict(row, status="confirmed")])
    assert [p["status"] for p in envelope_of(path)["planned"]] == ["granted"]


# ── GK B (21) conditions ──────────────────────────────────────────────────


def test_q2_the_shared_helper_writes_byte_identical_p_set(site, upload):
    """The refactor into one `demand_series` changes no written byte."""
    file_id = upload()
    expected = chat_tools.demand_series(ctx(), args(file_id))["series"]
    chat_tools.apply_demand_from_excel(**args(file_id))
    written_now = ctx().network.loads_t.p_set["L1"]
    assert written_now.to_numpy().tobytes() == expected.to_numpy().tobytes()
    assert list(written_now) == [7.5, 9.0]


@pytest.mark.parametrize("loop_name", ["l1", "l2"])
def test_g1_an_agent_call_with_the_planned_args_is_refused_and_cards(
    planned, loop_name
):
    task, path, file_id = planned(steps=[], detach=False)
    with on_run(task, path) as run:
        run.loop_scope = {
            "loop": loop_name,
            "round": 1,
            "changes": 2,
            "tunable": frozenset(),
        }
        got = ask(run, TOOL, args(file_id))
        assert got is None  # the card path
    assert missed(path, "outside_entry")
    assert not written(path) and series() is None


def test_g2_upload_bytes_swapped_after_admission_write_nothing_and_card(
    planned, site, monkeypatch
):
    task, path, file_id = planned()
    real_series = chat_tools.demand_series
    swaps = []

    def swapped(context, call_args):
        if run_envelope.PLANNED_WRITE.get() is not None and not swaps:
            # In the admitted handler, under the project's locks: after
            # admission, before the write.
            swaps.append(True)
            upload_service.get_upload_path(
                PyPSAService.get_loaded_project(), file_id, project_dir=site[1]
            ).write_bytes(OTHER_CSV)
        return real_series(context, call_args)

    monkeypatch.setattr(chat_tools, "demand_series", swapped)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert swaps and carded(record)
    assert refusals(path)[-1] == "plan_changed"
    assert series() is None


def test_g2_at_the_write_itself_with_the_card_recheck_off(planned, site, monkeypatch):
    """The tool's own check under the locks (`verify_planned_write`), alone:
    the loop's card re-check is switched off, so only it can refuse."""
    task, path, file_id = planned()
    monkeypatch.setattr(edit_card, "require_current", lambda *a, **k: None)
    real_bytes = upload_service.get_upload_bytes

    def swapped(*a, **k):  # the bytes the write re-reads under the lock
        return (
            OTHER_CSV
            if run_envelope.PLANNED_WRITE.get() is not None
            else real_bytes(*a, **k)
        )

    monkeypatch.setattr(upload_service, "get_upload_bytes", swapped)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and refusals(path)[-1] == "plan_changed"
    assert series() is None


def test_g2_a_series_other_than_the_pinned_one_is_not_written(planned, monkeypatch):
    task, path, _ = planned()
    monkeypatch.setattr(edit_card, "require_current", lambda *a, **k: None)
    real_series = chat_tools.demand_series

    def other(context, call_args):
        out = real_series(context, call_args)
        if run_envelope.PLANNED_WRITE.get() is not None:
            out = dict(out, series=out["series"] * 2)
        return out

    monkeypatch.setattr(chat_tools, "demand_series", other)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and refusals(path)[-1] == "plan_changed"
    assert series() is None


def test_g2_a_load_whose_uid_changed_after_admission_is_not_written(
    planned, monkeypatch
):
    task, path, _ = planned()
    monkeypatch.setattr(edit_card, "require_current", lambda *a, **k: None)
    real_get = component_identity.get

    def moved(network, cls, name):
        return (
            "another-uid"
            if run_envelope.PLANNED_WRITE.get() is not None
            else real_get(network, cls, name)
        )

    monkeypatch.setattr(component_identity, "get", moved)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and refusals(path)[-1] == "uid_changed"
    assert series() is None


def test_g10_a_replaced_series_that_moved_inside_the_handler_is_not_written(
    planned, monkeypatch
):
    """G10: the series the write replaces is re-read under the locks; one that
    moved after admission (here inside the handler) is not the grant's card."""
    task, path, _ = planned()
    monkeypatch.setattr(edit_card, "require_current", lambda *a, **k: None)
    real_series = chat_tools.demand_series

    def moved(context, call_args):
        if run_envelope.PLANNED_WRITE.get() is not None:
            context.network.loads.at["L1", "p_set"] = 9.0
        return real_series(context, call_args)

    monkeypatch.setattr(chat_tools, "demand_series", moved)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and refusals(path)[-1] == "plan_changed"
    assert series() is None


# ── G9: the destructive tier is lifted only by the pinned-card match ──────


@pytest.mark.parametrize(
    "tool, call, tier",
    [
        ("cluster_network", {"n_clusters": 1}, "destructive"),
        ("delete_component", {"component_class": "Load", "name": "L1"}, "delete"),
    ],
)
def test_g9_another_destructive_tool_is_refused_under_a_planned_envelope(
    planned, tool, call, tier
):
    task, path, _ = planned(steps=[], detach=False)
    with on_run(task, path) as run:
        assert ask(run, tool, call) is None
    row = [a for a in envelope_of(path)["audit"] if a["event"] == "refused"][-1]
    assert row["reason"] == tier and "planned_miss" not in row
    assert list(ctx().network.loads.index) == ["L1"]


def test_g9_a_planned_tool_also_listed_as_a_run_is_still_matched_by_its_pin(planned):
    """The tier pass-through for PLANNED_TOOLS reaches only `_match_planned`:
    an envelope that also lists the tool as a run does not admit an unpinned call."""
    task, path, file_id = planned(steps=[], detach=False, pin=False)
    envelope = envelope_of(path)
    envelope["runs"].append({"tool": TOOL})
    run_envelope.path_for(path).write_text(json.dumps(envelope))
    with on_run(task, path) as run:
        assert ask(run, TOOL, args(file_id)) is None
    assert missed(path, "plan_changed") and not written(path) and series() is None


@pytest.mark.parametrize("miss", ["other_args", "used_pin", "digest"])
def test_g9_a_call_that_misses_its_pin_is_refused_as_destructive(planned, miss):
    task, path, file_id = planned(steps=[], detach=False)
    call, kind = (
        args(file_id),
        {
            "other_args": "outside_entry",
            "used_pin": "planned_used",
            "digest": "plan_changed",
        }[miss],
    )
    if miss == "other_args":
        call = dict(call, value_col="other")
    elif miss == "used_pin":
        assert run_envelope.consume_planned(path, call_id="call-0", args=call)
    else:
        ctx().network.loads.at["L1", "p_set"] = 6.0
        dirty_state.bump_revision(ctx())
        accept(path)
    with on_run(task, path) as run:
        assert ask(run, TOOL, call) is None
    assert missed(path, kind) and not written(path) and series() is None


@pytest.mark.parametrize("broken", ["incomplete", "no_replaces_sha256"])
def test_g3_a_card_at_the_pin_that_is_incomplete_or_lacks_a_hash_is_carded(
    planned, monkeypatch, broken
):
    """The pin checks the card itself (`_binding_matches`): an incomplete card,
    or one whose binding lacks the replaced series' hash, never pins."""
    real = run_envelope._card

    def card(context, tool, call_args):
        out = real(context, tool, call_args)
        if broken == "incomplete":
            return dict(out, complete=False)
        return dict(
            out,
            binding={k: v for k, v in out["binding"].items() if k != "replaces_sha256"},
        )

    monkeypatch.setattr(run_envelope, "_card", card)
    task, path, _ = planned()
    assert [(p["status"], p.get("carded")) for p in envelope_of(path)["planned"]] == [
        ("carded", "plan_changed")
    ]
    assert all("pinned" not in p for p in envelope_of(path)["planned"])


def test_q1_a_card_whose_binding_is_not_the_granted_row_is_never_pinned(
    granted, upload, site, monkeypatch
):
    monkeypatch.setitem(
        run_profiles.PROFILES,
        "eh_site@1",
        {
            **run_profiles.EH_SITE,
            "envelope": {**run_profiles.EH_SITE["envelope"], "edits": []},
        },
    )
    file_id = upload()
    task, path, _ = granted(steps=[write_step(file_id)])
    row = {
        "write": TOOL,
        "args": args(file_id),
        "status": "confirmed",
        "source": {"kind": "table", "upload_id": file_id, "sha256": "0" * 64},
    }  # not these bytes
    run_envelope.plan_writes(path, [row])
    run_envelope.pin_planned(path, ctx())
    entry = envelope_of(path)["planned"][0]
    assert entry["status"] == "carded" and "pinned" not in entry
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and series() is None


def test_g3_a_parse_failure_at_the_pin_cards_and_never_pins(planned, upload):
    bad = upload(b"time,demand_mw\nnot-a-time,x\n", "bad.csv")
    task, path, _ = planned(bad)
    assert all(
        p.get("pinned") is None and p.get("carded")
        for p in envelope_of(path)["planned"]
    )
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and not written(path) and series() is None


def test_g3_a_parse_failure_at_admission_cards(planned, monkeypatch):
    task, path, _ = planned()

    def broken(context, call_args):
        raise HTTPException(
            400, {"error_kind": "value_column_parse_error", "message": "x"}
        )

    monkeypatch.setattr(chat_tools, "demand_series", broken)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and not written(path) and series() is None


def test_g3_a_uid_lookup_failure_at_admission_cards(planned, monkeypatch):
    task, path, _ = planned()
    monkeypatch.setattr(component_identity, "get", lambda *a: None)
    record = until(harness_runner.Runner("a"), path, task, stopped)
    assert carded(record) and not written(path) and series() is None


def test_g3_a_check_that_raises_at_admission_is_refused_not_admitted(
    planned, monkeypatch
):
    """Admission alone (the write-time check is not reached): a check that
    throws is a refusal, never an admission."""
    task, path, file_id = planned(steps=[], detach=False)

    def boom(*a, **k):
        raise RuntimeError("identity store unreadable")

    card = edit_card.preview(
        TOOL, args(file_id)
    )  # the pinned card, computed before the break
    with on_run(task, path) as run:
        monkeypatch.setattr(component_identity, "get", boom)
        assert (
            run_envelope.admit(
                run,
                call_id="call-1",
                tool=TOOL,
                args=args(file_id),
                tier="destructive",
                external=None,
                edit_card=card,
            )
            is None
        )
    assert missed(path, "plan_changed") and not written(path)


def test_g3_an_incomplete_card_with_the_pinned_digest_is_refused(planned, monkeypatch):
    task, path, file_id = planned(steps=[], detach=False)
    real = edit_card.preview
    monkeypatch.setattr(
        edit_card, "preview", lambda tool, a: dict(real(tool, a), complete=False)
    )
    with on_run(task, path) as run:
        assert ask(run, TOOL, args(file_id)) is None
    assert missed(path, "plan_changed") and not written(path)


def test_g4_a_stale_process_neither_pins_nor_consumes(planned, monkeypatch):
    """Pin and consume are envelope writes under the file lock with a live
    lease: a process that lost its lease writes neither."""
    task, path, file_id = planned(pin=False)
    monkeypatch.setattr(run_envelope, "_lease_live", lambda *a, **k: False)
    run_envelope.pin_planned(path, ctx())
    assert all("pinned" not in p for p in envelope_of(path)["planned"])
    assert (
        run_envelope.consume_planned(path, call_id="call-stale", args=args(file_id))
        is False
    )
    assert not any(p.get("used_by") for p in envelope_of(path)["planned"])


def test_g4_a_planned_write_is_consumed_once_under_the_lock(planned):
    task, path, file_id = planned(steps=[])
    assert run_envelope.consume_planned(path, call_id="call-1", args=args(file_id))
    assert not run_envelope.consume_planned(path, call_id="call-2", args=args(file_id))
    assert envelope_of(path)["planned"][0]["used_by"] == "call-1"


def test_g4_after_a_takeover_the_old_runner_does_not_consume(planned):
    import time

    task, path, file_id = planned()
    record = json.loads(path.read_text())
    record["run"]["runner"] = {
        "lease_owner": "a",
        "lease_until": time.time() - 1,
        "restarts": 0,
        "restart_times": [],
    }  # runner a's lease lapsed
    path.write_text(json.dumps(record))
    assert not run_envelope.consume_planned(
        path, call_id="call-a", args=args(file_id), holder="a"
    )
    record = until(harness_runner.Runner("b"), path, task, stopped)  # b takes over
    assert record["run"]["runner"]["lease_owner"] == "b"
    assert len(written(path)) == 1 and series() == [7.5, 9.0]
    assert envelope_of(path)["planned"][0]["used_by"].startswith("run-")
    assert not run_envelope.consume_planned(
        path, call_id="call-a2", args=args(file_id), holder="a"
    )
    assert not run_envelope.consume_planned(
        path, call_id="call-b2", args=args(file_id), holder="b"
    )  # once


@pytest.mark.parametrize("kind", ["plan_changed", "uid_changed"])
def test_g5_a_typed_mismatch_cards_at_the_catching_site(planned, kind, monkeypatch):
    """The typed refusal is caught as itself: a base-class catch that let it
    through (or turned it into an admission) fails here."""
    task, path, file_id = planned(steps=[], detach=False)
    error = run_envelope.PlannedMismatch.kinds[kind]
    monkeypatch.setattr(
        run_envelope,
        "_check_planned",
        lambda *a, **k: (_ for _ in ()).throw(error(kind)),
    )
    with on_run(task, path) as run:
        assert ask(run, TOOL, args(file_id)) is None
    assert missed(path, kind)
    assert error is not run_envelope.PlannedMismatch and issubclass(
        error, run_envelope.PlannedMismatch
    )
    assert series() is None


def test_g6_the_pin_reads_the_runs_project_not_the_foreground(planned):
    task, path, _ = planned(pin=False)
    run_ctx = ctx()
    with foreground(two_buses()) as front:
        assert PyPSAService.get_network() is front.network
        run_envelope.pin_planned(path, run_ctx)
    pinned = envelope_of(path)["planned"][0]["pinned"]
    assert pinned["uid"] == component_identity.uid_for(run_ctx.network, "Load", "L1")


def test_g6_with_no_run_context_the_created_project_is_never_pinned_on_the_foreground(
    planned, monkeypatch
):
    from services import assistant_tasks

    task, path, _ = planned(pin=False)
    pinned = []
    monkeypatch.setattr(
        run_envelope, "pin_planned", lambda p, c, holder=None: pinned.append(c)
    )
    assert assistant_tasks.RUN_CONTEXT.get() is None
    assistant_tasks._pin_planned(path)
    assert pinned == []
    token = assistant_tasks.RUN_CONTEXT.set(ctx())
    try:
        assistant_tasks._pin_planned(path)
    finally:
        assistant_tasks.RUN_CONTEXT.reset(token)
    assert pinned == [ctx()]


def test_g7_the_write_goes_to_the_bound_contexts_user_ts(site, upload):
    """`loads_t.p_set` is persisted through the store save reads; the
    planned write uses the context's own store, as profile_gen/apply does."""
    file_id = upload()
    chat_tools.apply_demand_from_excel(**args(file_id))
    assert list(ctx().user_ts[("loads", "p_set", "L1")]) == [7.5, 9.0]
    # The ordinary chat path still writes where it did: the module-global
    # store is the active project's, which save_project reads.
    from services.user_timeseries import _user_ts

    assert list(_user_ts[("loads", "p_set", "L1")]) == [7.5, 9.0]


# ── End to end: intake row → plan-review card → grant → pinned on the created project ──


def _site_csv():
    from project_templates import eh_site

    lines = ["time,demand_mw"] + [
        f"{t},{1.0 + (i % 24) / 10}" for i, t in enumerate(eh_site.SNAPSHOTS)
    ]
    return ("\n".join(lines) + "\n").encode()


def test_an_intake_load_series_is_carded_granted_and_pinned_on_the_created_project(
    host, session, site
):
    import uuid

    from services import run_intake
    from tests.test_run_intake_grant import _new_task, _start

    owner_ctx = ctx()
    file_id = upload_service.add_upload(
        PyPSAService.get_loaded_project(),
        _site_csv(),
        "site-demand.csv",
        "text/csv",
        project_dir=host["dir"],
    ).file_id
    write = {
        "write": TOOL,
        "upload_id": file_id,
        "time_col": "time",
        "value_col": "demand_mw",
        "load": "general_load",
    }
    name = f"Intake {uuid.uuid4().hex[:6]}"
    view = _start(host, name, host["citations"] + [write])
    intake = view["run"]["intake"]
    shown = [r for r in intake["card"]["rows"] if r.get("write")]
    assert (
        len(shown) == 1
        and shown[0]["label"]
        == "Load general_load: demand series (replaces its current demand)"
    )
    assert shown[0]["from_document"] and "site-demand.csv" in shown[0]["source"]
    rows = json.loads(task_path(site[1], view).read_text())["run"]["intake"]["rows"]
    struck = run_intake.strike(
        rows, next(i for i, r in enumerate(rows) if r.get("write"))
    )
    assert (
        run_intake.digest(struck) != intake["digest"]
    )  # the digest binds the write row
    assert run_intake.params(rows) == run_intake.params(
        [r for r in rows if not r.get("write")]
    )
    run_envelope.decide(
        session, view["task_id"], "unattended", intake_digest=intake["digest"]
    )
    intent = run_handover.read_intent(task_path(site[1], view))
    new_path, _ = _new_task(intent)
    planned = envelope_of(new_path)["planned"]
    assert [(p["tool"], p["status"], p["args"]["load_name"]) for p in planned] == [
        (TOOL, "pinned", "general_load")
    ]
    assert planned[0]["upload_sha256"] == hashlib.sha256(_site_csv()).hexdigest()
    assert (
        run_envelope.planned_steps(new_path, "inputs")[0]["args"] == planned[0]["args"]
    )
    assert (
        ctx() is owner_ctx
    )  # the pin bound the created project, never the foreground (G6)


def test_q3_a_profile_requiring_a_load_series_asks_for_it_until_an_intake_row_gives_it():
    from services import run_intake

    profile = {**run_profiles.EH_SITE, "inputs": {"required": [run_intake.WRITE_PARAM]}}
    row = {
        "param": "load_series.L1",
        "write": TOOL,
        "args": args("u1"),
        "value": args("u1"),
        "unit": "",
        "source": {"kind": "table", "upload_id": "u1", "sha256": "x"},
        "status": "proposed",
    }
    asked = [i["param"] for i in run_intake.missing(profile, [], [])]
    assert run_intake.WRITE_PARAM in asked
    assert run_intake.WRITE_PARAM not in [
        i["param"] for i in run_intake.missing(profile, [row], [])
    ]
    assert run_intake.WRITE_PARAM in [
        i["param"]
        for i in run_intake.missing(profile, [dict(row, status="struck")], [])
    ]
    assert (
        run_profiles.EH_SITE["inputs"]["required"] == []
    )  # eh_site asks for none (Q3)


# ── FILL-11-7 (21c cleanup) ───────────────────────────────────────────────




@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_demand_stats_shared_by_preview_and_readback_match_master(site, upload, bad):
    """Preview `old` and readback agree with master's inline `card._stats`
    computation, non-finite `p_set` included."""
    from harness import edit_card
    from services.profile_gen import card

    file_id = upload()
    n = ctx().network
    n.loads_t.p_set["L1"] = [bad, 3.0]
    current = card._current(n, "loads", "p_set", "L1")
    try:
        master = card._stats(n, current, True, "generators")
    except Exception as exc:  # noqa: BLE001 — the shared helper must raise the same
        with pytest.raises(type(exc)):
            edit_card._demand_preview(args(file_id))
        return
    rows = edit_card._demand_preview(args(file_id))
    typed = [r for r in rows if r["role"] == "typed"]
    got = edit_card._demand_readback(args(file_id), typed)
    import math

    for row, value in zip(typed, got):
        k = row["field"].split(".", 1)[1]
        want = round(master[k], 6)
        assert (math.isnan(want) and math.isnan(row["old"]) and math.isnan(value)) or (
            row["old"] == value == want
        )
