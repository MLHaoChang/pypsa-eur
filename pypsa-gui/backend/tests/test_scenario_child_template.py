# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""A scenario child (Expert Scenarios '+') reads its parent's template marker
(template-e2e issue 23 slice a), so its Guided study runs as the parent's does.
A study fork of the parent does not."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from services import chat_tools, flow_scope, flow_service, project_registry
from services import template_marker as tm
from tests.test_template_marker import (  # noqa: F401
    baseline,
    dc_marker,
    edit_row,
    eh_base,
    write_marker,
)


@pytest.fixture
def child(client, eh_base):
    """``(id, dir)`` of a scenario made from the template base by the route
    the Scenarios '+' button calls."""
    response = client.post(
        "/api/projects/Tool Baseline/scenarios", json={"name": "Tool What If"}
    )
    assert response.status_code == 201, response.text
    with chat_tools._acting() as (db, user):
        row = project_registry.resolve_project(db, user, "Tool What If")
        return str(row.id), project_registry.project_dir(row)


def _meta(directory):
    return json.loads((directory / "metadata.json").read_text())


def _write_meta(directory, **values):
    meta = _meta(directory)
    meta.update(values)
    (directory / "metadata.json").write_text(json.dumps(meta))


def test_a_scenario_child_reads_its_parents_template(eh_base, child):
    _, d = child
    assert not (d / tm.MARKER_FILE).exists()  # the fork still copies none
    assert tm.resolve(d) == tm.Marker(tm.BASE, eh_base.dir / tm.MARKER_FILE)
    assert flow_service.read_template(d) == eh_base.meta
    assert tm.present(d)


def test_the_childs_study_is_offered_not_refused(eh_base, child):
    _, d = child
    template = flow_service.read_template(d)
    scope = flow_scope.read_scope(d, template)
    assert flow_scope.option_status(
        "reliability", "goal_study", has_template=template is not None
    ) == (flow_scope.AVAILABLE, None)
    assert scope == flow_scope.read_scope(eh_base.dir, eh_base.meta)


def test_a_child_follows_its_parents_edits_until_it_has_its_own_copy(eh_base, child):
    _, d = child
    write_marker(eh_base.dir, {**eh_base.meta, "ledger": []})
    assert tm.read_marker(d)["ledger"] == []
    own = {**dc_marker(), "own": True}
    write_marker(d, own)
    assert tm.resolve(d) == tm.Marker(tm.OWN, d / tm.MARKER_FILE)
    assert tm.read_marker(d) == own
    assert json.loads((eh_base.dir / tm.MARKER_FILE).read_text())["ledger"] == []


def test_a_writes_target_is_the_childs_own_folder(child):
    _, d = child
    assert tm.own_path(d) == d / tm.MARKER_FILE


def test_a_study_fork_of_the_parent_stays_a_plain_network(eh_base, child):
    child_id, d = child
    _write_meta(d, owner_study_id=str(uuid.uuid4()), owner_base_project=eh_base.id)
    assert tm.resolve(d) == tm.Marker(tm.NONE)


def test_study_keys_naming_another_base_do_not_make_a_fork(eh_base, child):
    # A scenario made from a fork copies the fork's keys; they name the
    # fork's base, not this row's parent (`study.forks.is_study_owned`).
    _, d = child
    _write_meta(d, owner_study_id=str(uuid.uuid4()), owner_base_project=str(uuid.uuid4()))
    assert tm.resolve(d).state == tm.BASE


def test_a_child_moved_out_from_under_its_parent_reads_none(child):
    child_id, d = child
    edit_row(child_id, parent_project_id=None)
    assert tm.resolve(d) == tm.Marker(tm.NONE)


def test_a_child_in_another_organisation_than_its_parent_reads_none(child):
    from db import session as _db
    from db.models import Organization

    child_id, d = child
    with _db.SessionLocal() as db:
        other = Organization(name=f"other-{uuid.uuid4()}", created_at=datetime.now(UTC))
        db.add(other)
        db.commit()
        other_id = other.id
    edit_row(child_id, org_id=other_id)
    assert tm.resolve(d) == tm.Marker(tm.NONE)


def test_a_child_whose_parent_folder_lost_its_marker_reads_none(eh_base, child):
    _, d = child
    (eh_base.dir / tm.MARKER_FILE).unlink()
    assert tm.resolve(d) == tm.Marker(tm.NONE)


def test_a_folder_naming_a_parent_but_holding_no_row_reads_none(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps({"parent_project": "Tool Baseline"}))
    assert tm.resolve(tmp_path) == tm.Marker(tm.NONE)


def test_a_failed_lookup_reads_none_not_an_error(child, monkeypatch, caplog):
    from db import session as _db

    _, d = child

    def broken():
        raise RuntimeError("database gone")

    with monkeypatch.context() as m:
        m.setattr(_db, "SessionLocal", broken)
        assert tm.resolve(d) == tm.Marker(tm.NONE)
    assert "parent of" in caplog.text


def test_a_root_project_costs_no_lookup(eh_base, monkeypatch):
    from db import session as _db

    (eh_base.dir / tm.MARKER_FILE).unlink()

    def forbidden():
        raise AssertionError("a root project needs no query")

    monkeypatch.setattr(_db, "SessionLocal", forbidden)
    assert tm.resolve(eh_base.dir) == tm.Marker(tm.NONE)
