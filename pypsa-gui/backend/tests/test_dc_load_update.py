"""te 04 slice 2: PUE, critical share, backup mode and architecture are editable after creation.

PUT /api/campus-electrical/{name}/dc-load: the merged spec goes through the
create body's own checks, the facility profile is rewritten on the live
network by the one writer creation uses, and the marker follows, all or
nothing.
"""

from __future__ import annotations


import numpy as np
import pytest

from tests.test_dc_backup import _stage_dc


def _make(client, tmp_path, monkeypatch, tid, name, body=None):
    _stage_dc(tmp_path, monkeypatch, tid)
    r = client.post(
        f"/api/projects/from_template/{tid}",
        params={"name": name},
        json={"dc_load": body or {}},
    )
    assert r.status_code == 200, r.text
    return r.json().get("name", name)


def _loads(ctx):
    p = ctx.network.loads_t.p_set
    return p[["it_load", "cooling"]].copy()


def _marker(client, name):
    return client.get(f"/api/projects/{name}/eh_template").json()["dc_load"]


@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
def test_updating_with_the_same_values_changes_nothing(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx, tid
):
    name = _make(client, tmp_path, monkeypatch, tid, "dc_same")
    ctx = session_ctx(client)
    before = _loads(ctx)
    spec = _marker(client, name)["spec"]
    r = client.put(
        f"/api/campus-electrical/{name}/dc-load",
        json={
            "pue_at_design": spec["pue_at_design"],
            "critical_share": spec["critical_share"],
        },
    )
    assert r.status_code == 200, r.text
    assert _loads(ctx).equals(before)


@pytest.mark.parametrize("tid", ["eh_datacenter", "eh_datacenter_staged"])
def test_a_pue_change_moves_the_facility_load_and_the_marker(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx, tid
):
    name = _make(client, tmp_path, monkeypatch, tid, "dc_pue")
    ctx = session_ctx(client)
    before = _loads(ctx)
    old_rev = __import__("services.dirty_state", fromlist=["x"]).revision(ctx)
    r = client.put(
        f"/api/campus-electrical/{name}/dc-load", json={"pue_at_design": 1.5}
    )
    assert r.status_code == 200, r.text
    after = _loads(ctx)
    total = lambda df: df.sum(axis=1)  # noqa: E731
    ratio = total(after).max() / total(before).max()
    assert ratio > 1.05
    assert _marker(client, name)["spec"]["pue_at_design"] == 1.5
    assert __import__("services.dirty_state", fromlist=["x"]).revision(ctx) > old_rev


def test_an_invalid_value_is_refused_and_changes_nothing(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    name = _make(client, tmp_path, monkeypatch, "eh_datacenter", "dc_bad")
    ctx = session_ctx(client)
    before, marker = _loads(ctx), _marker(client, name)
    r = client.put(
        f"/api/campus-electrical/{name}/dc-load", json={"critical_share": 1.4}
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "dc_load_invalid"
    assert r.json()["detail"]["field"]
    assert _loads(ctx).equals(before) and _marker(client, name) == marker


def test_an_unknown_key_is_refused(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    name = _make(client, tmp_path, monkeypatch, "eh_datacenter", "dc_key")
    r = client.put(f"/api/campus-electrical/{name}/dc-load", json={"it_mw": 3})
    assert (
        r.status_code == 422 and r.json()["detail"]["error_kind"] == "dc_load_invalid"
    )


def test_an_edited_series_is_not_overwritten_without_the_flag(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    name = _make(client, tmp_path, monkeypatch, "eh_datacenter", "dc_edit")
    ctx = session_ctx(client)
    ctx.network.loads_t.p_set["it_load"] = ctx.network.loads_t.p_set["it_load"] * 0.5
    edited = _loads(ctx)
    r = client.put(
        f"/api/campus-electrical/{name}/dc-load", json={"pue_at_design": 1.5}
    )
    assert (
        r.status_code == 409 and r.json()["detail"]["error_kind"] == "dc_series_edited"
    )
    assert _loads(ctx).equals(edited)
    assert _marker(client, name)["spec"]["pue_at_design"] != 1.5
    r = client.put(
        f"/api/campus-electrical/{name}/dc-load",
        json={"pue_at_design": 1.5, "overwrite_series": True},
    )
    assert r.status_code == 200, r.text
    assert not _loads(ctx).equals(edited)


def test_a_running_study_refuses_before_any_write(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    from services.pypsa_service import PyPSAService

    name = _make(client, tmp_path, monkeypatch, "eh_datacenter", "dc_busy")
    ctx = session_ctx(client)
    before, marker = _loads(ctx), _marker(client, name)
    from fastapi import HTTPException

    def busy(*a, **k):
        raise HTTPException(409, {"error_kind": "study_running"})

    monkeypatch.setattr(
        PyPSAService,
        "refuse_if_study_running",
        classmethod(lambda cls, *a, **k: busy()),
    )
    r = client.put(
        f"/api/campus-electrical/{name}/dc-load", json={"pue_at_design": 1.5}
    )
    assert r.status_code == 409
    assert _loads(ctx).equals(before) and _marker(client, name) == marker


def test_undo_restores_the_profile_and_the_marker(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    name = _make(client, tmp_path, monkeypatch, "eh_datacenter", "dc_undo")
    ctx = session_ctx(client)
    before, marker = _loads(ctx), _marker(client, name)
    assert (
        client.put(
            f"/api/campus-electrical/{name}/dc-load", json={"pue_at_design": 1.5}
        ).status_code
        == 200
    )
    r = client.post("/api/network/undo")
    assert r.status_code == 200, r.text
    ctx = session_ctx(client)  # an undo re-keys the project's context
    assert np.allclose(_loads(ctx).to_numpy(), before.to_numpy())
    assert _marker(client, name) == marker


def test_backup_mode_and_architecture_are_stored_without_touching_the_network(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    name = _make(client, tmp_path, monkeypatch, "eh_datacenter", "dc_mode")
    ctx = session_ctx(client)
    before = _loads(ctx)
    r = client.put(
        f"/api/campus-electrical/{name}/dc-load",
        json={"backup": {"mode": "hybrid"}, "architecture": "dc_800v"},
    )
    assert r.status_code == 200, r.text
    spec = _marker(client, name)["spec"]
    assert spec["backup"]["mode"] == "hybrid" and spec["architecture"] == "dc_800v"
    assert _loads(ctx).equals(before)


def test_an_empty_update_is_refused(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    name = _make(client, tmp_path, monkeypatch, "eh_datacenter", "dc_empty")
    r = client.put(f"/api/campus-electrical/{name}/dc-load", json={})
    assert (
        r.status_code == 422 and r.json()["detail"]["error_kind"] == "dc_load_invalid"
    )


def test_a_series_change_needs_the_open_network(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    from routers.campus_electrical import _eh_templates
    from services import project_registry
    from services.dc_load_update import DcLoadUpdateError, update_dc_load

    name = _make(client, tmp_path, monkeypatch, "eh_datacenter", "dc_closed")
    marker = _marker(client, name)
    project_dir = project_registry.project_dir(project_row(name))
    with pytest.raises(DcLoadUpdateError) as exc:
        update_dc_load(project_dir, _eh_templates(), {"pue_at_design": 1.5}, None)
    assert exc.value.kind == "project_not_open"
    assert _marker(client, name) == marker


def test_a_put_never_rewrites_an_unsaved_foreground_network(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    name = _make(client, tmp_path, monkeypatch, "eh_datacenter", "dc_draft")
    ctx = session_ctx(client)
    before, marker = _loads(ctx), _marker(client, name)
    monkeypatch.setattr(ctx, "loaded_project", None)
    r = client.put(
        f"/api/campus-electrical/{name}/dc-load", json={"pue_at_design": 1.5}
    )
    assert (
        r.status_code == 409 and r.json()["detail"]["error_kind"] == "project_not_open"
    )
    assert _loads(ctx).equals(before) and _marker(client, name) == marker


def test_a_study_on_the_projects_own_context_refuses(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    from fastapi import HTTPException

    from services.pypsa_service import PyPSAService

    name = _make(client, tmp_path, monkeypatch, "eh_datacenter", "dc_own")
    ctx = session_ctx(client)
    before, marker = _loads(ctx), _marker(client, name)

    def refuse(cls, action="", *, ctx=None):
        if ctx is not None:  # only the project's own context is running a study
            raise HTTPException(409, {"error_kind": "study_running"})

    monkeypatch.setattr(PyPSAService, "refuse_if_study_running", classmethod(refuse))
    r = client.put(
        f"/api/campus-electrical/{name}/dc-load", json={"backup": {"mode": "hybrid"}}
    )
    assert r.status_code == 409
    assert _loads(ctx).equals(before) and _marker(client, name) == marker


# ── the service on its own: refusals and the all-or-nothing paths ──────────


class _Tpl:
    """The real templates module, with the profile writer replaceable."""

    def __init__(self, real, apply=None):
        self._real, self._apply = real, apply

    def __getattr__(self, name):
        return getattr(self._real, name)

    def apply_dc_profile_to_network(self, n, tid, spec, planning=None):
        if self._apply is None:
            return None  # the series stay as they are: they match the spec
        return self._apply(n, tid, spec, planning)


def _stub_network(columns=("it_load", "cooling")):
    import pandas as pd
    from types import SimpleNamespace

    return SimpleNamespace(
        loads_t=SimpleNamespace(p_set=pd.DataFrame({c: [1.0, 2.0] for c in columns}))
    )


def _service(client, tmp_path, monkeypatch, project_row, name):
    from routers.campus_electrical import _eh_templates
    from services import project_registry

    _make(client, tmp_path, monkeypatch, "eh_datacenter", name)
    return project_registry.project_dir(project_row(name)), _eh_templates()


def test_the_service_refuses_what_it_cannot_apply(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    from services.dc_load_update import DcLoadUpdateError, update_dc_load

    d, tpl = _service(client, tmp_path, monkeypatch, project_row, "dc_svc")
    with pytest.raises(DcLoadUpdateError) as exc:
        update_dc_load(d, tpl, {"backup": "x"})
    assert exc.value.field == "dc_load.backup"
    with pytest.raises(DcLoadUpdateError):
        update_dc_load(d, tpl, {"backup": {"mode": "diesel"}})
    for columns in (("cooling",), ("it_load",)):
        with pytest.raises(DcLoadUpdateError) as exc:
            update_dc_load(d, tpl, {"pue_at_design": 1.5}, _stub_network(columns))
        assert exc.value.kind == "project_not_open"
    # a project that is not a data centre has no spec to change
    other = tmp_path / "plain"
    other.mkdir()
    with pytest.raises(DcLoadUpdateError):
        update_dc_load(other, tpl, {"pue_at_design": 1.5})


def test_a_failed_series_write_puts_the_series_back(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    from services.dc_load_update import update_dc_load

    d, real = _service(client, tmp_path, monkeypatch, project_row, "dc_rb1")
    n = _stub_network()
    before = n.loads_t.p_set.copy()

    def half_write(net, tid, spec, planning):
        net.loads_t.p_set[["it_load", "cooling"]] = 99.0
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        update_dc_load(d, _Tpl(real, half_write), {"pue_at_design": 1.5}, n)
    assert n.loads_t.p_set.equals(before)


def test_a_failed_marker_write_puts_the_series_back(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    from services.adequacy import dc_backup
    from services.dc_load_update import update_dc_load

    d, real = _service(client, tmp_path, monkeypatch, project_row, "dc_rb2")
    n = _stub_network()
    before = n.loads_t.p_set.copy()
    calls = []

    def write(net, tid, spec, planning):
        calls.append(1)
        if len(calls) > 1:  # the first call is the "was it edited" check
            net.loads_t.p_set[["it_load", "cooling"]] = 99.0

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(dc_backup, "_write_meta", broken)
    with pytest.raises(OSError):
        update_dc_load(d, _Tpl(real, write), {"pue_at_design": 1.5}, n)
    assert len(calls) == 2 and n.loads_t.p_set.equals(before)
    # a marker-only failure leaves no series to restore and still raises
    with pytest.raises(OSError):
        update_dc_load(d, _Tpl(real, write), {"architecture": "dc_800v"}, n)
    assert n.loads_t.p_set.equals(before)
