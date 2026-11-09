"""Assistant memory store (assistant-memory plan §1–§5; §9 M1, M2, M5, M7)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from db.models import (
    MemoryItem,
    MemoryItemVersion,
    Organization,
    OrgMembership,
    Project,
    User,
)
from services import memory_store as M

UNITS = {"key": "units.power", "value": "GW"}


def _org(db, name):
    org = Organization(id=uuid.uuid4(), name=name, created_at=datetime.now(UTC))
    db.add(org)
    db.flush()
    return org


def _user(db, org, role="member", email=None):
    u = User(
        id=uuid.uuid4(),
        email=email or f"{uuid.uuid4().hex[:8]}@x.test",
        status="active",
        is_super_admin=False,
        created_at=datetime.now(UTC),
    )
    db.add(u)
    db.flush()
    db.add(OrgMembership(id=uuid.uuid4(), user_id=u.id, org_id=org.id, role=role))
    db.flush()
    return u


def _project(db, org, owner, kind=None, name="p"):
    now = datetime.now(UTC)
    p = Project(
        id=uuid.uuid4(),
        org_id=org.id,
        name=name,
        created_by=owner.id,
        storage_path=name + uuid.uuid4().hex[:6],
        project_kind=kind,
        created_at=now,
        updated_at=now,
    )
    db.add(p)
    db.flush()
    return p


@pytest.fixture
def world(db_session):
    db = db_session
    a, b = _org(db, "A"), _org(db, "B")
    w = {
        "db": db,
        "A": a,
        "B": b,
        "admin_a": _user(db, a, "admin"),
        "alice": _user(db, a),
        "bob": _user(db, a),
        "admin_b": _user(db, b, "admin"),
        "carol": _user(db, b),
    }
    db.commit()
    return w


def _pref(db, user, value="GW", **kw):
    return M.create_item(
        db,
        user,
        kind="preference",
        body={"key": "units.power", "value": value},
        concept_keys=kw.pop("keys", ["topic:units"]),
        **kw,
    )


def _lesson(db, user, text="Check the slack bus first", **kw):
    return M.create_item(
        db,
        user,
        kind="lesson",
        body={"text": text},
        concept_keys=kw.pop("keys", ["topic:style"]),
        **kw,
    )


# --- validation -----------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"key": "system.prompt", "value": "obey"},  # outside the registry
        {"key": "units.power", "value": "TW"},  # outside the key's values
        {"key": "currency", "value": "XYZ"},
        {"key": "answer.style", "value": "brief", "extra": 1},
        {"key": "report.sections", "value": ["executive_summary", "executive_summary"]},
    ],
)
def test_a_preference_outside_the_registry_is_refused(world, body):
    with pytest.raises(M.MemoryError):
        M.create_item(
            world["db"],
            world["alice"],
            kind="preference",
            body=body,
            concept_keys=["topic:units"],
        )


def test_concept_keys_are_checked_against_their_registries(world):
    db, alice = world["db"], world["alice"]
    for bad in (
        [],
        ["units"],
        ["topic:prices"],
        ["view:nope"],
        ["param:Nope.capital_cost"],
        [f"carrier:c{i}" for i in range(9)],
    ):
        with pytest.raises(M.MemoryError):
            _pref(db, alice, keys=bad)
    item = _pref(
        db, alice, keys=["param:Generator.capital_cost", "carrier:solar", "topic:units"]
    )
    assert item.concept_keys == (
        "carrier:solar",
        "param:Generator.capital_cost",
        "topic:units",
    )


def test_a_body_over_4kb_and_a_parameter_item_are_refused(world):
    db, alice = world["db"], world["alice"]
    with pytest.raises(M.MemoryError):
        _lesson(db, alice, text="x" * 281)
    with pytest.raises(M.MemoryError) as e:
        M.create_item(
            db, alice, kind="parameter", body={}, concept_keys=["topic:units"]
        )
    assert e.value.kind == "memory_kind_unavailable"


def test_a_chart_spec_is_validated_by_the_chart_spec_module(world):
    db, alice = world["db"], world["alice"]
    spec = {
        "schema": "chart_spec@1",
        "series": [
            {"metric": "p_nom_opt", "unit": "MW", "component_class": "Generator"}
        ],
        "chart": {"type": "bar"},
        "style": {"stacked": True},
    }
    item = M.create_item(
        db, alice, kind="chart_spec", body={"spec": spec}, concept_keys=["topic:style"]
    )
    assert item.summary == "chart: bar of Generator/p_nom_opt [MW] (stacked)"
    for bad in (
        {**spec, "schema": "chart_spec@2"},
        {**spec, "code": "x"},
        {**spec, "chart": {"type": "pie"}},
        {**spec, "series": [{"metric": "Bad Metric", "unit": "MW"}]},
    ):
        with pytest.raises(M.MemoryError):
            M.create_item(
                db,
                alice,
                kind="chart_spec",
                body={"spec": bad},
                concept_keys=["topic:style"],
            )


# --- scope and access -------------------------------------------------------------


def test_another_users_item_is_not_found_by_id(world):
    db = world["db"]
    item = _pref(db, world["alice"])
    for other in (world["bob"], world["carol"], world["admin_a"]):
        for call in (
            lambda u: M.get_item(db, u, item.public_id),
            lambda u: M.versions(db, u, item.id),
            lambda u: M.edit_item(db, u, item.id, summary="mine now"),
            lambda u: M.transition(db, u, item.id, "retire"),
            lambda u: M.nominate(db, u, item.id),
        ):
            with pytest.raises(M.MemoryError) as e:
                call(other)
            assert e.value.status == 404
    with pytest.raises(M.MemoryError) as e:
        M.get_item(db, world["alice"], "mem_not-a-uuid")
    assert e.value.status == 404


def test_list_shows_own_and_org_items_only(world):
    db = world["db"]
    mine = _pref(db, world["alice"])
    _pref(db, world["bob"], value="MW")
    org = _promote(world, _lesson(db, world["bob"]))
    _pref(db, world["carol"])
    ids = {v.id for v in M.list_items(db, world["alice"])}
    assert ids == {mine.id, org.id}
    assert {v.id for v in M.list_items(db, world["alice"], scope="org")} == {org.id}


# --- status machine and versions ----------------------------------------------------


def test_status_machine(world):
    db, alice = world["db"], world["alice"]
    item = _pref(db, alice, status="proposed", origin="chat")
    with pytest.raises(M.MemoryError) as e:
        M.transition(db, alice, item.id, "retire")
    assert e.value.status == 409
    assert M.transition(db, alice, item.id, "confirm").status == "confirmed"
    assert M.transition(db, alice, item.id, "retire").status == "retired"
    assert M.transition(db, alice, item.id, "restore").status == "confirmed"
    gone = _lesson(db, alice, status="proposed", origin="chat")
    assert M.transition(db, alice, gone.id, "dismiss").status == "dismissed"
    for action in ("confirm", "restore", "retire"):
        with pytest.raises(M.MemoryError):
            M.transition(db, alice, gone.id, action)
    with pytest.raises(M.MemoryError):
        M.edit_item(db, alice, gone.id, summary="again")


def test_an_edit_writes_a_new_version_and_old_versions_stay(world):
    db, alice = world["db"], world["alice"]
    item = _pref(db, alice)
    v1 = db.query(MemoryItemVersion).filter_by(item_id=item.id, version=1).one()
    v1_bytes = (v1.body_json, v1.summary, v1.evidence_json)
    edited = M.edit_item(db, alice, item.id, body={"key": "units.power", "value": "MW"})
    assert edited.version == 2 and edited.body["value"] == "MW"
    assert edited.summary == "units.power: MW"
    hist = M.versions(db, alice, item.id)
    assert [h["version"] for h in hist] == [1, 2]
    assert hist[0]["body"] == UNITS
    db.expire_all()
    v1 = db.query(MemoryItemVersion).filter_by(item_id=item.id, version=1).one()
    assert (v1.body_json, v1.summary, v1.evidence_json) == v1_bytes


def test_a_second_button_save_is_a_new_version_of_the_same_item(world):
    db, alice = world["db"], world["alice"]
    first = _pref(db, alice, project_kind="capacity_expansion")
    second = _pref(db, alice, value="kW", project_kind="capacity_expansion")
    assert second.id == first.id and second.version == 2
    other_kind = _pref(db, alice, value="kW", project_kind="connection")
    assert other_kind.id != first.id


# --- recall: ranking and the org rule (M1) -----------------------------------------------


def test_recall_ranks_user_over_org_and_drops_the_shadowed_org_item(world):
    db, alice, a = world["db"], world["alice"], world["A"]
    p = _project(db, a, alice)
    org_item = _promote(world, _pref(db, world["bob"], value="MW"))
    org_other = _promote(world, _lesson(db, world["bob"], keys=["carrier:solar"]))
    mine = _pref(db, alice, value="kW")
    got = M.recall(db, alice, project_org_id=p.org_id, project_kind=p.project_kind)
    assert [v.id for v in got] == [mine.id, org_other.id]
    assert org_item.id not in {v.id for v in got}


def test_recall_ranks_specific_kind_then_applies(world):
    db, alice, a = world["db"], world["alice"], world["A"]
    _project(db, a, alice, kind="connection")
    anyk = _lesson(db, alice, "any kind", keys=["carrier:wind"])
    spec = _lesson(
        db, alice, "connection only", keys=["carrier:solar"], project_kind="connection"
    )
    other = _lesson(
        db,
        alice,
        "planning only",
        keys=["carrier:gas"],
        project_kind="planning_dynamics",
    )
    used = _lesson(db, alice, "used", keys=["carrier:coal"])
    M.record_apply(db, alice, used.id)
    got = [
        v.id
        for v in M.recall(db, alice, project_org_id=a.id, project_kind="connection")
    ]
    assert got == [spec.id, used.id, anyk.id]
    assert other.id not in got


def test_a_user_in_another_orgs_project_gets_only_preferences_and_chart_specs(world):
    """M1: Alice (org A) working in an org-B project sees no org-A lesson or org
    item, only her own preferences."""
    db, alice = world["db"], world["alice"]
    pb = _project(db, world["B"], world["carol"], name="b-project")
    pref = _pref(db, alice)
    lesson = _lesson(db, alice)
    org_pref = _promote(world, _pref(db, world["bob"], keys=["topic:currency"]))
    in_b = {
        v.id for v in M.recall(db, alice, project_org_id=pb.org_id, project_kind=None)
    }
    assert in_b == {pref.id}
    pa = _project(db, world["A"], alice)
    in_a = {
        v.id for v in M.recall(db, alice, project_org_id=pa.org_id, project_kind=None)
    }
    assert in_a == {pref.id, lesson.id, org_pref.id}


def test_an_unbound_turn_recalls_only_own_any_kind_preferences(world):
    db, alice = world["db"], world["alice"]
    pref = _pref(db, alice)
    _pref(db, alice, keys=["topic:currency"], project_kind="connection")
    _lesson(db, alice)
    _promote(world, _pref(db, world["bob"], keys=["topic:style"]))
    assert [
        v.id for v in M.recall(db, alice, project_org_id=None, project_kind=None)
    ] == [pref.id]


def test_recall_filters_by_concept_kind_and_query(world):
    db, alice, a = world["db"], world["alice"], world["A"]
    pref = _pref(db, alice)
    lesson = _lesson(db, alice, "Check the SLACK bus", keys=["carrier:solar"])
    kw = {"project_org_id": a.id, "project_kind": None}
    assert [v.id for v in M.recall(db, alice, kinds=["lesson"], **kw)] == [lesson.id]
    assert [v.id for v in M.recall(db, alice, concept_keys=["topic:units"], **kw)] == [
        pref.id
    ]
    assert [v.id for v in M.recall(db, alice, query="slack", **kw)] == [lesson.id]
    got = M.recall(db, alice, screen={"carrier:solar"}, **kw)
    assert got[0].id == lesson.id
    assert [
        v.id
        for v in M.recall(
            db, alice, screen={"carrier:solar"}, require_concept=True, **kw
        )
    ] == [lesson.id]


def test_retired_and_proposed_items_are_not_recalled(world):
    db, alice, a = world["db"], world["alice"], world["A"]
    M.transition(db, alice, _pref(db, alice).id, "retire")
    _lesson(db, alice, status="proposed", origin="chat")
    assert M.recall(db, alice, project_org_id=a.id, project_kind=None) == []


def test_a_ranking_snapshot_holds_across_an_apply(world):
    db, alice, a = world["db"], world["alice"], world["A"]
    first = _lesson(db, alice, "first", keys=["carrier:solar"])
    second = _lesson(db, alice, "second", keys=["carrier:wind"])
    snap: dict = {}
    kw = {"project_org_id": a.id, "project_kind": None}
    before = [v.id for v in M.recall(db, alice, snapshot=snap, **kw)]
    M.record_apply(db, alice, before[-1])
    M.record_apply(db, alice, before[-1])
    assert [v.id for v in M.recall(db, alice, snapshot=snap, **kw)] == before
    assert [v.id for v in M.recall(db, alice, **kw)] == before[::-1]
    assert {first.id, second.id} == set(before)


# --- revisions (M5) -----------------------------------------------------------------------


def test_writes_bump_the_owners_revision_and_counters_do_not(world):
    db, alice, a = world["db"], world["alice"], world["A"]
    assert M.revisions(db, alice, a.id) == (0, 0)
    item = _pref(db, alice)
    assert M.revisions(db, alice, a.id) == (1, 0)
    M.record_apply(db, alice, item.id)
    M.record_recall(db, [item.id])
    assert M.revisions(db, alice, a.id) == (1, 0)
    M.edit_item(db, alice, item.id, summary="power in GW")
    assert M.revisions(db, alice, a.id) == (2, 0)
    org = _promote(world, _lesson(db, world["bob"]))
    assert M.revisions(db, alice, a.id)[1] == 1
    M.transition(db, world["admin_a"], org.id, "retire")
    assert M.revisions(db, alice, a.id)[1] == 2


# --- nominate and promote (§5, M2) ------------------------------------------------------------


def _promote(world, item):
    db = world["db"]
    owner = db.get(User, item.user_id)
    M.nominate(db, owner, item.id)
    admin = world["admin_a"] if item.org_id == world["A"].id else world["admin_b"]
    return M.promote(db, admin, item.id)


def test_promote_copies_the_item_and_keeps_the_members_own(world):
    db, alice, admin = world["db"], world["alice"], world["admin_a"]
    mine = _pref(db, alice)
    M.nominate(db, alice, mine.id)
    assert [v.id for v in M.list_items(db, admin, nominated=True)] == [mine.id]
    assert M.get_item(db, admin, mine.id).id == mine.id  # the admin reads a nomination
    org = M.promote(db, admin, mine.id, summary="Org default: GW")
    assert (org.owner_scope, org.origin, org.promoted_from) == (
        "org",
        "promoted",
        mine.id,
    )
    assert org.summary == "Org default: GW" and org.org_id == world["A"].id
    assert M.get_item(db, alice, mine.id).owner_scope == "user"
    assert M.list_items(db, admin, nominated=True) == []
    with pytest.raises(M.MemoryError):  # the nomination is spent
        M.get_item(db, admin, mine.id)


def test_org_admin_checks_run_against_the_items_own_org(world):
    """M2: another org's admin and a non-admin member get 404, never "any admin"."""
    db, alice = world["db"], world["alice"]
    mine = _pref(db, alice)
    M.nominate(db, alice, mine.id)
    for wrong in (world["admin_b"], world["bob"]):
        assert M.list_items(db, wrong, nominated=True) == []
        for call in (
            lambda u: M.promote(db, u, mine.id),
            lambda u: M.decline(db, u, mine.id),
            lambda u: M.get_item(db, u, mine.id),
        ):
            with pytest.raises(M.MemoryError) as e:
                call(wrong)
            assert e.value.status == 404
    org = M.promote(db, world["admin_a"], mine.id)
    for wrong in (world["admin_b"], world["bob"], alice):
        with pytest.raises(M.MemoryError) as e:
            M.edit_item(db, wrong, org.id, summary="x")
        assert e.value.status == 404
    with pytest.raises(M.MemoryError):
        M.get_item(db, world["admin_b"], org.id)
    assert M.get_item(db, world["bob"], org.id).id == org.id  # members read org items


def test_a_declined_nomination_stays_invisible_to_other_members(world):
    db, alice = world["db"], world["alice"]
    mine = _pref(db, alice)
    M.nominate(db, alice, mine.id)
    assert M.decline(db, world["admin_a"], mine.id).nomination == "declined"
    assert M.list_items(db, world["admin_a"], nominated=True) == []
    for other in (world["bob"], world["admin_a"]):
        assert mine.id not in {v.id for v in M.list_items(db, other)}
        with pytest.raises(M.MemoryError):
            M.get_item(db, other, mine.id)


# --- dismissed proposals and the sweeper (M7) -------------------------------------------------


def test_a_dismissed_proposal_is_remembered_for_30_days(world):
    db, alice = world["db"], world["alice"]
    item = _lesson(db, alice, status="proposed", origin="chat")
    M.transition(db, alice, item.id, "dismiss")
    body = {"text": "Check the slack bus first"}
    assert M.recently_dismissed(db, alice, "lesson", ["topic:style"], body)
    assert not M.recently_dismissed(db, alice, "lesson", ["topic:units"], body)
    assert not M.recently_dismissed(db, world["bob"], "lesson", ["topic:style"], body)
    later = datetime.now(UTC) + timedelta(days=31)
    assert not M.recently_dismissed(
        db, alice, "lesson", ["topic:style"], body, now=later
    )


def test_sweep_dismissed_drops_old_bodies_and_keeps_the_hash(world):
    db, alice = world["db"], world["alice"]
    old = _lesson(db, alice, "old one", status="proposed", origin="chat")
    new = _lesson(db, alice, "new one", status="proposed", origin="chat")
    kept = _lesson(db, alice, "confirmed one")
    M.transition(db, alice, old.id, "dismiss")
    M.transition(db, alice, new.id, "dismiss")
    row = db.get(MemoryItem, old.id)
    row.updated_at = datetime.now(UTC) - timedelta(days=30, minutes=1)
    db.commit()
    h = row.dismissed_hash
    assert M.sweep_dismissed(db) == 1
    assert M.sweep_dismissed(db) == 0
    db.expire_all()
    assert db.get(MemoryItem, old.id).dismissed_hash == h
    gone = db.query(MemoryItemVersion).filter_by(item_id=old.id).one()
    assert gone.body_json is None and json.loads(gone.evidence_json) == []
    assert (
        db.query(MemoryItemVersion).filter_by(item_id=new.id).one().body_json
        is not None
    )
    assert (
        db.query(MemoryItemVersion).filter_by(item_id=kept.id).one().body_json
        is not None
    )


# --- evidence labels (M1) ----------------------------------------------------------------------


def test_an_evidence_label_for_a_project_the_user_cannot_open_shows_no_name(world):
    db, alice = world["db"], world["alice"]
    secret = _project(db, world["B"], world["carol"], name="secret-plant")
    own = _project(db, world["A"], alice, name="own")
    links = [
        {"kind": "project", "id": str(secret.id), "label": "secret-plant"},
        {"kind": "project", "id": str(own.id), "label": "own"},
        {"kind": "chat", "id": "s1:4", "label": "a chat"},
    ]
    out = M.evidence_for(db, alice, links)
    assert out[0] == {
        "kind": "project",
        "id": None,
        "label": "source project (no access)",
        "at": None,
        "access": False,
    }
    assert out[1]["label"] == "own" and out[1]["access"] is True
    assert out[2] == links[2]
    assert "secret-plant" not in json.dumps(out)


# --- concepts from the screen ------------------------------------------------------------------


def test_concepts_for_reads_only_allow_listed_fields():
    got = M.concepts_for(
        {
            "results_card": "commercial",
            "ui_mode": "expert",
            "guided_step": "economics",
            "selected_component": {
                "class": "Generator",
                "field": "capital_cost",
                "carrier": "solar",
            },
            "free_text": "topic:currency",
        }
    )
    assert got == {"card:commercial", "carrier:solar", "param:Generator.capital_cost"}
    assert M.concepts_for(None) == set()
    assert M.concepts_for({"results_card": "not-a-card"}) == set()
