"""Study harness 35 (GK B (71) plan GO, Q1-Q3 (a), C1-C10): eh_site@6 imports a
URDB tariff the intake cites, in the inputs stage, from an upload (no network,
L7). Each test name carries the GK B condition it covers (`c1_` .. `c10_`).

The fixture `tests/fixtures/urdb_rate_handbuilt.json` is hand-built in the
OpenEI `items[]` shape (C10): URDB data is a user-scoped import only and never
ships (`.scratch/gridspine-library-research/terms/TERMS-INDEX.md`).
"""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
from types import SimpleNamespace

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from services import (
    chat_tools,
    run_envelope,
    run_intake,
    run_profiles,
    run_urdb,
    upload_service,
)

REF = "eh_site@6"
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "urdb_rate_handbuilt.json"
TARGET = {"component": "Settings", "name": "Commercial", "attr": "import_tariff_ref"}
# C9: computed on master 06859826 in a fresh worktree, never on this branch.
PINNED = {
    "eh_site@1": "8d8f09dec396579ec12bd9344959ac6ecd5cef1eaa0a47de737bd83bca51cf46",
    "eh_site@2": "2eefcb14bc7d5f05a465be591bf54238d9e4f1c7bf7948cff935c461b381f9f8",
    "eh_site@3": "446df3bb5862d095fe54bd3a52ca3fe010dc2dca16776da366ee952efa92e9a6",
    "eh_site@4": "85e2b14fd93680c4a1bacc6b28309a1ff06c5ed676ae445f4c943b511f7f0b45",
    "eh_site@5": "f9103a56825f260d0161df7085ca985e9d3065f8b26e4b21a07f7df8fc4e867b",
}


# ── C9: the profile (L8) ─────────────────────────────────────────────────────


@pytest.mark.parametrize("ref", sorted(PINNED))
def test_c9_earlier_versions_are_byte_equal(ref):
    dumped = json.dumps(run_profiles.PROFILES[ref], sort_keys=True).encode()
    assert hashlib.sha256(dumped).hexdigest() == PINNED[ref]


def test_c9_v6_differs_from_v5_only_by_the_urdb_gap():
    v5, v6 = (
        json.loads(json.dumps(run_profiles.PROFILES[r])) for r in ("eh_site@5", REF)
    )
    assert v6["version"] == 6
    assert v6["envelope"]["gaps"] == [*v5["envelope"]["gaps"], run_profiles.URDB_GAP]
    v5["version"], v5["envelope"]["gaps"] = 6, v6["envelope"]["gaps"]
    assert v5 == v6
    assert run_envelope.check_block(v6["envelope"])["gaps"][-1]["source"] == {
        "kind": "urdb_upload"
    }


def test_c9_start_task_offers_v6():
    from harness import catalogue

    tool = next(t for t in catalogue.TOOLS if t["name"] == "start_task")
    assert (
        tool["input_schema"]["properties"]["run"]["properties"]["profile"]["enum"][-1]
        == REF
    )


# ── fixtures ─────────────────────────────────────────────────────────────────


def _network(snapshots=None, periods=None):
    n = pypsa.Network()
    n.set_snapshots(
        snapshots
        if snapshots is not None
        else pd.date_range("2026-01-01", periods=24, freq="h")
    )
    if periods is not None:
        n.set_investment_periods(periods)
    return n


def _ctx(currency="USD", **net):
    return SimpleNamespace(
        loaded_project="P",
        storage_dir=None,
        network=_network(**net),
        solver_state={"solver_config": {"finance": {"currency": currency}}},
    )


@pytest.fixture
def project(client, install_network, tmp_projects_dir):
    from tests.test_chat_tools_library import _project

    _project(install_network, tmp_projects_dir)
    return tmp_projects_dir / "P"


def _upload(blob=None):
    return upload_service.add_upload(
        "P",
        blob if blob is not None else FIXTURE.read_bytes(),
        "rate.json",
        "application/json",
    )


def _row(project, **citation):
    host = run_intake.Host(name="P", dir=project, owner_id="u")
    row, gap = run_intake._urdb_row({"write": "import_urdb_tariff", **citation}, host)
    assert gap is None, gap
    return dict(row, status="confirmed")


def _granted(project, ctx, **citation):
    """A granted envelope with eh_site@6's gaps; the urdb gap pinned from the row."""
    upload = _upload()
    row = _row(project, upload_id=upload.file_id, **citation)
    gaps = [
        dict(
            g,
            at_grant=run_envelope._gap_instances(ctx, g, [row])
            if g["source"]["kind"] == "urdb_upload"
            else {"Commercial": {"tariffs": {}}},
        )
        for g in run_profiles.PROFILES[REF]["envelope"]["gaps"]
        if g["tool"] == "attach_tariff"
    ]
    return {"gaps": gaps, "edits": [], "runs": [], "state": "granted"}, row


def _record(envelope):
    return next(g for g in envelope["gaps"] if g["source"]["kind"] == "urdb_upload")[
        "at_grant"
    ]["Commercial"]


def _import(args):
    return chat_tools.DISPATCHERS["import_urdb_tariff"](
        **{k: v for k, v in args.items() if v is not None}
    )


def _versions(name):
    """The latest version of Library tariff `name` (a version is never removed)."""
    from routers.library import ItemKind, get_item

    return chat_tools._library_call(
        get_item, ItemKind.tariff, name, version=None
    ).ref.version


def _count():
    from routers.library import ItemKind, list_items

    page = chat_tools._library_call(list_items, ItemKind.tariff)
    return len(page.get("items", page) if isinstance(page, dict) else page)


CARD = {
    "complete": True,
    "rows": [
        {
            "role": "typed",
            "component_class": "Settings",
            "name": "Commercial",
            "field": "import_tariff_ref",
            "old": None,
        }
    ],
}


# ── the grant: what it pins (C2, C3) and the import route's own hash ────────


def test_c2_the_intake_row_pins_every_import_arg_and_accept_partial_defaults_false(
    project,
):
    upload = _upload()
    row = _row(project, upload_id=upload.file_id)
    sha = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert row["args"] == {
        "file_id": upload.file_id,
        "name": f"urdb-{sha[:12]}",
        "cyclic_year": False,
        "accept_partial": False,
        "valid_from": None,
        "item_index": None,
        "tariff_id": None,
        "jurisdiction": None,
    }
    assert set(row["args"]) == set(run_urdb.ARGS)
    assert row["source"]["sha256"] == sha and row["value"]["currency"] == "USD"
    assert run_intake.params([row]) == {}  # never a creation param


def test_c3_the_granted_hash_is_the_one_the_import_route_stores(project):
    envelope, row = _granted(project, _ctx())
    record = _record(envelope)
    assert (
        record["args"] == row["args"]
        and record["upload_sha256"] == row["source"]["sha256"]
    )
    assert _import(record["args"])["ref"]["hash"] == record["hash"]


def test_a_second_urdb_row_grants_none(project):
    upload = _upload()
    rows = [
        _row(project, upload_id=upload.file_id),
        _row(project, upload_id=upload.file_id, item_index=0),
    ]
    gap = dict(run_profiles.URDB_GAP)
    assert run_envelope._gap_instances(_ctx(), gap, rows) == {}
    assert (
        run_envelope._gap_instances(_ctx(), gap) == {}
    )  # an existing project's grant: no intake rows


def test_an_older_profile_takes_no_urdb_citation(project, monkeypatch):
    host = run_intake.Host(name="P", dir=project, owner_id="u")
    monkeypatch.setattr(run_intake, "host_for", lambda *a: host)
    upload = _upload()
    request = {
        "project_name": "x",
        "citations": [{"write": "import_urdb_tariff", "upload_id": upload.file_id}],
    }
    old = run_intake.take(request, run_profiles.PROFILES["eh_site@5"], None, "u", "p")
    assert not old["rows"] and [g["kind"] for g in old["gaps"]] == [
        "source_not_accepted"
    ]
    new = run_intake.take(request, run_profiles.PROFILES[REF], None, "u", "p")
    assert [r["write"] for r in new["rows"]] == ["import_urdb_tariff"]


def test_an_unusable_upload_is_an_intake_gap(project):
    host = run_intake.Host(name="P", dir=project, owner_id="u")
    bad = _upload(b"not json")
    row, gap = run_intake._urdb_row(
        {"write": "import_urdb_tariff", "upload_id": bad.file_id}, host
    )
    assert row is None and gap["kind"] == "urdb_unusable"
    row, gap = run_intake._urdb_row(
        {"write": "import_urdb_tariff", "upload_id": bad.file_id, "name": "x"}, host
    )
    assert (
        row is None and gap["kind"] == "source_not_accepted"
    )  # the model names no arg (C2)


# ── C1: one fill tool, two kinds ─────────────────────────────────────────────


def test_c1_a_library_tariff_gap_never_admits_an_import(project):
    envelope, row = _granted(project, _ctx())
    envelope["gaps"] = [
        g for g in envelope["gaps"] if g["source"]["kind"] == "library_tariff"
    ]
    note = {}
    assert (
        run_envelope._match_prepare(
            envelope, "import_urdb_tariff", row["args"], _ctx(), note
        )
        == "unlisted_tool"
    )
    assert note == {}


def test_c1_covers_offers_the_import_only_beside_a_urdb_gap(project, monkeypatch):
    envelope, _ = _granted(project, _ctx())
    library_only = dict(
        envelope,
        gaps=[g for g in envelope["gaps"] if g["source"]["kind"] == "library_tariff"],
    )
    monkeypatch.setattr(run_envelope, "live", lambda path: True)
    for env, want in ((envelope, True), (library_only, False)):
        monkeypatch.setattr(run_envelope, "_load", lambda path, env=env: env)
        assert run_envelope.covers("p", "import_urdb_tariff") is want
        assert run_envelope.covers("p", "attach_tariff") is True


def test_c1_a_urdb_gap_never_admits_another_held_tariff(project):
    envelope, row = _granted(project, _ctx())
    _import(row["args"])
    other = dict(row["args"], name="someone-else")
    _import(
        other
    )  # the same rate under another name: a held tariff the grant did not name
    ctx = _ctx()
    assert (
        run_envelope._match_fill(
            envelope, "attach_tariff", {"name": "someone-else"}, CARD, ctx, {}
        )
        == "not_granted_source"
    )
    note = {}
    assert (
        run_envelope._match_fill(
            envelope, "attach_tariff", {"name": row["args"]["name"]}, CARD, ctx, note
        )
        is None
    )
    assert note["source"] == _record(envelope)["hash"]
    assert (
        run_envelope._match_fill(
            envelope,
            "attach_tariff",
            {"name": row["args"]["name"], "replace_inline": True},
            CARD,
            ctx,
            {},
        )
        == "outside_entry"
    )


def test_c1_the_library_gap_stays_as_strict_beside_a_urdb_gap(project):
    envelope, row = _granted(project, _ctx())
    _import(row["args"])
    for g in envelope["gaps"]:
        if g["source"]["kind"] == "library_tariff":
            g["at_grant"]["Commercial"]["tariffs"] = {
                "h1": ["a", 1],
                "h2": ["b", 1],
            }  # two held: the user's choice
    urdb = next(g for g in envelope["gaps"] if g["source"]["kind"] == "urdb_upload")
    urdb["at_grant"] = {}
    assert (
        run_envelope._match_fill(
            envelope, "attach_tariff", {"name": row["args"]["name"]}, CARD, _ctx(), {}
        )
        == "not_granted_source"
    )


def test_c1_the_load_profile_binding_never_reads_an_import():
    assert run_envelope.PREPARE_KIND == {
        "generate_load_profile": "load_generator",
        "import_urdb_tariff": "urdb_upload",
    }
    assert set(run_envelope.PREPARE_KIND) == set(run_envelope.GAP_PREPARE)


# ── C2: the model supplies no import arg ─────────────────────────────────────


@pytest.mark.parametrize("change", [{"file_id": "f" * 16}, {"item_index": 0}])
def test_c2_a_different_file_or_item_is_refused(project, change):
    envelope, row = _granted(project, _ctx())
    args = {**row["args"], **change}
    assert (
        run_envelope._match_prepare(envelope, "import_urdb_tariff", args, _ctx(), {})
        == "not_granted_source"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"name": "mine"},
        {"cyclic_year": True},
        {"accept_partial": True},
        {"valid_from": "2020-01-01"},
        {"tariff_id": "x"},
        {"jurisdiction": "NL"},
        {"extra": 1},
    ],
)
def test_c2_a_different_name_or_flag_is_refused(project, change):
    envelope, row = _granted(project, _ctx())
    args = {**row["args"], **change}
    assert (
        run_envelope._match_prepare(envelope, "import_urdb_tariff", args, _ctx(), {})
        == "not_granted_source"
    )


def test_c2_the_granted_args_are_admitted_and_a_model_turn_never_is(project):
    envelope, row = _granted(project, _ctx())
    note = {}
    assert (
        run_envelope._match_prepare(
            envelope, "import_urdb_tariff", row["args"], _ctx(), note
        )
        is None
    )
    assert note == {"source": _record(envelope)["hash"]}
    session = SimpleNamespace(run_step_call="step-1")
    for call_id in ("model-call", None):
        assert (
            run_envelope._refusal(
                session,
                envelope,
                {},
                "import_urdb_tariff",
                row["args"],
                "write",
                False,
                None,
                _ctx(),
                {"call_id": call_id, "scope": None},
            )
            == "own_step_only"
        )


def test_c2_the_inputs_stage_runs_the_granted_args(project, tmp_path, monkeypatch):
    envelope, row = _granted(project, _ctx())
    monkeypatch.setattr(run_envelope, "_load", lambda path: envelope)
    steps = run_envelope.urdb_steps(tmp_path / "t.json", "inputs", start=3)
    assert steps == [
        {
            "id": "inputs.3",
            "tool": "import_urdb_tariff",
            "args": row["args"],
            "status": "pending",
        },
        {
            "id": "inputs.4",
            "tool": "attach_tariff",
            "args": {"name": row["args"]["name"]},
            "status": "pending",
        },
    ]
    _record(envelope)["refused"] = {"kind": "currency_mismatch", "message": "m"}
    _record(envelope).pop("hash")
    assert run_envelope.urdb_steps(tmp_path / "t.json", "inputs") == []


# ── C3: the hash before any write ────────────────────────────────────────────


def test_c3_an_edited_upload_is_refused_and_nothing_is_written(project):
    envelope, row = _granted(project, _ctx())
    before = _count()
    rate = json.loads(FIXTURE.read_text())
    rate["items"][0]["energyratestructure"][0][0]["rate"] = 0.5
    upload_service.get_upload_path("P", row["args"]["file_id"]).write_bytes(
        json.dumps(rate).encode()
    )
    assert (
        run_envelope._match_prepare(
            envelope, "import_urdb_tariff", row["args"], _ctx(), {}
        )
        == run_urdb.CHANGED
    )
    assert _count() == before


def test_c3_an_upload_gone_since_the_grant_is_refused(project):
    envelope, row = _granted(project, _ctx())
    upload_service.delete_upload("P", row["args"]["file_id"])
    assert (
        run_envelope._match_prepare(
            envelope, "import_urdb_tariff", row["args"], _ctx(), {}
        )
        == run_urdb.CHANGED
    )


def test_c3_bytes_changed_between_the_card_and_the_grant_are_refused(project):
    upload = _upload()
    row = _row(project, upload_id=upload.file_id)
    row["source"]["sha256"] = "0" * 64
    record = run_envelope._gap_instances(_ctx(), run_profiles.URDB_GAP, [row])[
        "Commercial"
    ]
    assert record["refused"]["kind"] == run_urdb.CHANGED and "hash" not in record


@pytest.mark.parametrize("commercial", [{"import_tariff_ref": {"kind": "library", "id": "t", "version": 1}},
                                        {"import_tariff": {"id": "inline"}}])
def test_c3_a_project_that_prices_imports_already_writes_nothing(project, commercial):
    envelope, row = _granted(project, _ctx())
    ctx = _ctx()
    ctx.solver_state["solver_config"]["commercial"] = commercial
    before = _count()
    assert run_envelope._match_prepare(envelope, "import_urdb_tariff", row["args"], ctx, {}) == "not_a_gap"
    assert _count() == before


# ── C4: one Library write ────────────────────────────────────────────────────


def test_c4_a_second_import_reuses_the_item_and_adds_no_version(project):
    envelope, row = _granted(project, _ctx())
    first = _import(row["args"])["ref"]
    assert (
        run_envelope._match_prepare(
            envelope, "import_urdb_tariff", row["args"], _ctx(), {}
        )
        is None
    )
    again = _import(row["args"])["ref"]
    assert again == first and _versions(row["args"]["name"]) == 1


def test_c4_another_tariff_under_the_name_is_refused_typed(project):
    envelope, row = _granted(project, _ctx())
    rate = json.loads(FIXTURE.read_text())
    rate["items"][0]["fixedchargefirstmeter"] = 99.0
    other = _upload(json.dumps(rate).encode())
    _import(
        dict(row["args"], file_id=other.file_id)
    )  # the user's own tariff under that name
    before = _versions(row["args"]["name"])
    assert (
        run_envelope._match_prepare(
            envelope, "import_urdb_tariff", row["args"], _ctx(), {}
        )
        == run_urdb.NAME_TAKEN
    )
    assert _versions(row["args"]["name"]) == before


# ── C5, C6: Library write rights and upload access at the grant ─────────────


def test_c5_a_user_outside_every_organisation_may_not_write_the_library(project):
    from db.models import OrgMembership

    assert run_urdb.may_write_library() is True
    with chat_tools._acting() as (db, user):
        kept = [
            (m.org_id, m.role)
            for m in db.query(OrgMembership).filter(OrgMembership.user_id == user.id)
        ]
        db.query(OrgMembership).filter(OrgMembership.user_id == user.id).delete()
        db.commit()
    try:
        assert run_urdb.may_write_library() is False
    finally:
        with chat_tools._acting() as (db, user):
            db.add_all(
                [
                    OrgMembership(user_id=user.id, org_id=org, role=role)
                    for org, role in kept
                ]
            )
            db.commit()
    assert run_urdb.may_write_library() is True


def test_c5_the_grant_refuses_before_anything_when_the_library_is_not_writable(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(run_urdb, "may_write_library", lambda: calls.append(1) or False)
    rows = [{"write": "import_urdb_tariff", "status": "confirmed"}]
    chosen = {"gaps": [dict(run_profiles.URDB_GAP)]}
    with pytest.raises(HTTPException) as exc:
        run_envelope._urdb_grant_check(chosen, rows)
    assert (
        exc.value.status_code == 403
        and exc.value.detail["error_kind"] == "not_authorized"
        and calls
    )
    run_envelope._urdb_grant_check(
        None, rows
    )  # step by step: every write keeps its card
    run_envelope._urdb_grant_check({"gaps": []}, rows)
    assert len(calls) == 1


def test_c6_another_projects_upload_is_refused_at_the_intake_and_the_grant(
    project, tmp_path
):
    upload = _upload()
    elsewhere = run_intake.Host(name="Q", dir=tmp_path / "Q", owner_id="u")
    (tmp_path / "Q").mkdir()
    row, gap = run_intake._urdb_row(
        {"write": "import_urdb_tariff", "upload_id": upload.file_id}, elsewhere
    )
    assert row is None and gap["kind"] == "not_found"
    own = _row(project, upload_id=upload.file_id)
    ctx = SimpleNamespace(
        **{**vars(_ctx()), "storage_dir": tmp_path / "Q"}
    )  # the run's project is Q
    record = run_envelope._gap_instances(ctx, run_profiles.URDB_GAP, [own])[
        "Commercial"
    ]
    assert record["refused"]["kind"] == run_urdb.CHANGED and "hash" not in record


# ── C7: the stamp rules, at the grant and again at use ───────────────────────


def _stamp(**ctx):
    tariff = run_urdb.plan(FIXTURE.read_bytes(), run_urdb.pinned_args("f", "a" * 64))[
        "tariff"
    ]
    return run_urdb.stamp(_ctx(**ctx), tariff)


def test_c7_a_currency_mismatch_is_refused_at_the_grant(project):
    envelope, _ = _granted(project, _ctx("EUR"))
    record = _record(envelope)
    assert record["refused"]["kind"] == "currency_mismatch" and "hash" not in record
    assert (
        "priced in USD" in record["refused"]["message"]
        and "EUR" in record["refused"]["message"]
    )
    assert (
        _stamp(currency=None) is None
    )  # an unstated project currency: the rule as it is (#499)


@pytest.mark.parametrize(
    "snapshots,refused",
    [
        (
            pd.date_range("2024-12-31", periods=48, freq="h"),
            True,
        ),  # starts before valid_from
        (
            pd.date_range("2027-12-31", periods=48, freq="h"),
            True,
        ),  # ends after valid_to
        (pd.date_range("2026-03-01", periods=48, freq="h"), False),
    ],
)
def test_c7_modelled_dates_outside_the_validity_are_refused(snapshots, refused):
    got = _stamp(snapshots=snapshots)
    assert (got is not None and got[0] == run_urdb.YEAR) is refused


@pytest.mark.parametrize(
    "periods,refused",
    [([2024, 2026], True), ([2026, 2028], True), ([2025, 2027], False)],
)
def test_c7_investment_periods_outside_the_validity_are_refused(periods, refused):
    snapshots = pd.date_range("2025-01-01", periods=24, freq="h")
    got = _stamp(snapshots=snapshots, periods=periods)
    assert (got is not None and got[0] == run_urdb.YEAR) is refused
    if refused:
        assert "Investment period(s)" in got[1]


def test_c7_an_open_valid_to_bounds_nothing_after_valid_from():
    rate = json.loads(FIXTURE.read_text())
    del rate["items"][0]["enddate"]
    tariff = run_urdb.plan(
        json.dumps(rate).encode(), run_urdb.pinned_args("f", "a" * 64)
    )["tariff"]
    assert tariff.valid_to is None
    late = _ctx(snapshots=pd.date_range("2040-01-01", periods=24, freq="h"))
    assert run_urdb.stamp(late, tariff) is None
    assert (
        run_urdb.stamp(
            _ctx(
                snapshots=pd.date_range("2025-01-01", periods=24, freq="h"),
                periods=[2040],
            ),
            tariff,
        )
        is None
    )
    early = _ctx(snapshots=pd.date_range("2024-06-01", periods=24, freq="h"))
    assert run_urdb.stamp(early, tariff)[0] == run_urdb.YEAR


def test_c7_a_currency_changed_after_the_grant_refuses_the_import_and_the_attach(
    project,
):
    envelope, row = _granted(project, _ctx("USD"))
    _import(row["args"])
    moved = _ctx("EUR")
    assert (
        run_envelope._match_prepare(
            envelope, "import_urdb_tariff", row["args"], moved, {}
        )
        == "currency_mismatch"
    )
    assert (
        run_envelope._match_fill(
            envelope, "attach_tariff", {"name": row["args"]["name"]}, CARD, moved, {}
        )
        == "currency_mismatch"
    )


def test_c7_the_plan_row_names_the_refusal(project):
    envelope, _ = _granted(project, _ctx("EUR"))
    urdb = next(g for g in envelope["gaps"] if g["source"]["kind"] == "urdb_upload")
    assert run_envelope._gap_row(urdb).startswith("- No URDB tariff is filled: Tariff")
    assert "imported to your Library when you approve" in run_envelope._gap_row(
        dict(urdb, at_grant={})
    )


# ── C8: no new error kind outside a fragment ─────────────────────────────────


def test_c8_the_refusals_are_envelope_reasons_and_intake_gap_kinds():
    assert run_urdb.CURRENCY == "currency_mismatch"
    assert "urdb_unusable" in run_intake._KIND_TEXT


# ── the pilot: the inputs stage imports and attaches, offline (L7) ───────────


def test_pilot_v6_imports_and_attaches_the_cited_urdb_tariff_offline(project):
    """The grant's two steps, each admitted from the envelope, then run through
    the real tools on a USD project: one Library item, its ref attached."""
    envelope, row = _granted(project, _ctx("USD"))
    steps = []
    import unittest.mock as mock

    with mock.patch.object(run_envelope, "_load", lambda path: envelope):
        steps = run_envelope.urdb_steps("t", "inputs")
    importer, attacher = steps
    assert (
        run_envelope._match_prepare(
            envelope, importer["tool"], importer["args"], _ctx(), {}
        )
        is None
    )
    ref = _import(importer["args"])["ref"]
    assert (
        run_envelope._match_fill(
            envelope, attacher["tool"], attacher["args"], CARD, _ctx(), {}
        )
        is None
    )
    assert ref["hash"] == _record(envelope)["hash"] and _versions(ref["id"]) == 1
    plan = copy.deepcopy(importer["args"])
    assert run_urdb.plan(FIXTURE.read_bytes(), plan)["hash"] == ref["hash"]
