"""A project's template marker (``eh_template.json``) and the sensitivity case
that has none (ui-modern issue 07, owner decision UM07-KIND-1, GK B plan
conditions U1-U8).

A case made by ``run_sensitivity_sweep`` takes its base's project type,
read-side: ``services.template_marker`` follows the case's
``sensitivity_case.json`` to its base, and every reader of the marker goes
through it. The sweep does not copy the marker.
"""

import ast
import json
import pathlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from services import chat_tools, flow_service, project_registry
from services import template_marker as tm
from services.pypsa_service import PyPSAService
from services.results import headline_kpis as hk
from tests.test_headline_kpis import cards, checked_profiles_on  # noqa: F401
from tests.test_result_profiles_assurance import solve, windy  # noqa: F401
from tests.test_workflow_tools import baseline, case  # noqa: F401

#: The shared fixtures this module takes by name.
FIXTURES = (baseline, windy, checked_profiles_on)

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def dc_marker() -> dict:
    """A data-centre marker as the template writes it: its id, the dc_load
    spec and envelope, a ledger row and a growth input left unset."""
    from project_templates import eh_templates as t

    return {
        "id": "eh_datacenter",
        "ledger": [{"item": "scr_min", "value": 3.0, "source": "template"}],
        "growth_inputs": [{"kind": "dc_load", "year": 2030, "set": False}],
        "dc_load": {
            "spec": t.dc_load_preset("eh_datacenter"),
            "envelope": {"peak_mw": 12.0},
        },
    }


def write_marker(directory: pathlib.Path, meta) -> pathlib.Path:
    path = directory / tm.MARKER_FILE
    path.write_text(json.dumps(meta) if isinstance(meta, dict) else meta)
    return path


@pytest.fixture
def eh_base(baseline):
    """The baseline project carrying a data-centre marker."""
    ctx, project_id, path = baseline
    meta = dc_marker()
    write_marker(path, meta)
    return SimpleNamespace(ctx=ctx, id=project_id, dir=path, meta=meta)


@pytest.fixture
def sweep():
    """``sweep(project_id, name)``: a real ``run_sensitivity_sweep`` case,
    ``(id, dir)``; every queued job is waited for at teardown."""
    jobs = []

    def run(project_id, name="Tool Sensitivity"):
        out = chat_tools.run_sensitivity_sweep(project_id, [case(name)])
        assert out["status"] == "submitted", out
        child = out["cases"][0]
        jobs.append(child["job"]["id"])
        with chat_tools._acting() as (db, user):
            row = project_registry.resolve_project(db, user, child["project_id"])
            return child["project_id"], project_registry.project_dir(row)

    def wait():
        while jobs:
            chat_tools.wait_for_job(jobs.pop(), 25)

    run.wait = wait
    yield run
    run.wait()


@pytest.fixture
def eh_case(eh_base, sweep):
    return sweep(eh_base.id)[1]


def edit_row(project_id, **values):
    from db import session as _db
    from db.models import Project

    with _db.SessionLocal() as db:
        row = db.get(Project, uuid.UUID(project_id))
        for key, value in values.items():
            setattr(row, key, value)
        db.commit()


# ── The helper ───────────────────────────────────────────────────────────────


def test_a_swept_case_reads_its_base_template_and_the_sweep_copies_nothing(
    eh_base, eh_case
):
    assert not (eh_case / tm.MARKER_FILE).exists()
    assert tm.resolve(eh_case) == tm.Marker(tm.BASE, eh_base.dir / tm.MARKER_FILE)
    assert tm.read_marker(eh_case) == eh_base.meta
    assert flow_service.read_template(eh_case) == eh_base.meta
    assert hk.project_kind(flow_service.read_template(eh_case)) == "energy_hub"
    assert tm.resolve(eh_base.dir).state == tm.OWN


def test_a_plain_project_and_a_case_of_a_plain_base_have_no_template(baseline, sweep):
    _, project_id, path = baseline
    assert tm.resolve(path) == tm.Marker(tm.NONE) and tm.read_marker(path) is None
    _, d = sweep(project_id)
    assert tm.resolve(d) == tm.Marker(tm.NONE) and not tm.present(d)
    assert hk.project_kind(flow_service.read_template(d)) == "network"


def test_a_case_with_its_own_copy_reads_it_and_the_base_no_longer_reaches_it(
    eh_base, eh_case
):
    own = write_marker(eh_case, {**eh_base.meta, "id": "eh_microgrid"})
    assert tm.resolve(eh_case) == tm.Marker(tm.OWN, own)
    write_marker(eh_base.dir, {**eh_base.meta, "id": "eh_datacenter_staged"})
    assert tm.read_marker(eh_case)["id"] == "eh_microgrid"


def test_a_case_of_a_case_follows_the_chain(eh_base, sweep):
    first_id, first = sweep(eh_base.id)
    sweep.wait()  # the first case is the next sweep's baseline: no queued job
    _, second = sweep(first_id, "Tool Sensitivity Second")
    assert tm.resolve(second) == tm.Marker(tm.BASE, eh_base.dir / tm.MARKER_FILE)
    assert tm.read_marker(second) == eh_base.meta


def test_a_case_whose_base_has_lost_its_marker_reads_as_a_plain_network(
    eh_base, eh_case
):
    (eh_base.dir / tm.MARKER_FILE).unlink()
    assert tm.resolve(eh_case) == tm.Marker(tm.NONE)


def test_a_case_whose_base_row_is_gone_reads_as_a_plain_network(eh_case):
    record = json.dumps({"baseline_id": str(uuid.uuid4())})
    (eh_case / tm.CASE_FILE).write_text(record)
    assert tm.resolve(eh_case) == tm.Marker(tm.NONE)


def test_a_case_moved_out_from_under_its_base_reads_as_a_plain_network(eh_base, sweep):
    case_id, d = sweep(eh_base.id)
    edit_row(case_id, parent_project_id=None)
    assert tm.resolve(d) == tm.Marker(tm.NONE)


def test_a_case_in_another_organisation_than_its_base_reads_none(eh_base, sweep):
    """U1: a case never surfaces a base its reader could not open."""
    from db import session as _db
    from db.models import Organization

    case_id, d = sweep(eh_base.id)
    sweep.wait()  # the job is the user's to wait for only while the case is
    with _db.SessionLocal() as db:
        other = Organization(name=f"other-{uuid.uuid4()}", created_at=datetime.now(UTC))
        db.add(other)
        db.commit()
        other_id = other.id
    edit_row(case_id, org_id=other_id)
    assert tm.resolve(d) == tm.Marker(tm.NONE)


def test_a_folder_that_is_not_the_bases_child_does_not_reach_it(
    eh_base, eh_case, tmp_path
):
    # The base has a real child (`eh_case`); a record elsewhere naming it is not one.
    assert tm.resolve(eh_case).state == tm.BASE
    (tmp_path / tm.CASE_FILE).write_text(json.dumps({"baseline_id": eh_base.id}))
    assert tm.resolve(tmp_path) == tm.Marker(tm.NONE)


@pytest.mark.parametrize(
    "record",
    ["not json", "[]", '{"baseline_id": 7}', '{"baseline_id": "not-a-uuid"}', "{}"],
)
def test_an_unreadable_case_record_is_unreadable_not_plain(eh_case, record, caplog):
    """U2: as an own marker that cannot be parsed: no template, but there."""
    (eh_case / tm.CASE_FILE).write_text(record)
    assert tm.resolve(eh_case) == tm.Marker(tm.UNREADABLE)
    assert tm.read_marker(eh_case) is None and tm.present(eh_case)
    assert "cannot be read" in caplog.text


def test_a_torn_base_marker_parses_as_an_own_torn_marker_does(eh_base, eh_case):
    write_marker(eh_base.dir, "{broken")
    assert tm.resolve(eh_case).state == tm.BASE
    assert tm.read_marker(eh_case) is None and tm.present(eh_case)
    write_marker(eh_case, "[1, 2]")
    assert tm.resolve(eh_case).state == tm.OWN and tm.read_marker(eh_case) is None


def test_a_looping_chain_ends(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    calls = []

    def base_of(here):
        calls.append(here)
        return b if here == a else a

    monkeypatch.setattr(tm, "_base_dir", base_of)
    assert tm.resolve(a) == tm.Marker(tm.NONE)
    assert calls == [a, b]  # stopped at the first folder seen again


def test_a_base_that_cannot_be_looked_up_is_unreadable(eh_case, monkeypatch):
    from db import session as _db

    def broken():
        raise RuntimeError("database gone")

    with monkeypatch.context() as m:  # the sweep's teardown needs the database
        m.setattr(_db, "SessionLocal", broken)
        assert tm.resolve(eh_case) == tm.Marker(tm.UNREADABLE)


def test_a_folder_that_cannot_be_resolved_is_not_the_same(tmp_path, monkeypatch):
    def broken(self, *args, **kwargs):
        raise OSError("loop")

    monkeypatch.setattr(pathlib.Path, "resolve", broken)
    assert tm._same_dir(tmp_path, tmp_path) is False


@pytest.mark.parametrize(
    "hops, state", [(tm.MAX_DEPTH, tm.BASE), (tm.MAX_DEPTH + 1, tm.NONE)]
)
def test_a_chain_is_followed_up_to_the_depth_cap(tmp_path, monkeypatch, hops, state):
    dirs = [tmp_path / str(i) for i in range(hops + 1)]
    for d in dirs:
        d.mkdir()
    write_marker(dirs[-1], {"id": "eh_microgrid"})
    monkeypatch.setattr(tm, "_base_dir", lambda here: dirs[dirs.index(here) + 1])
    assert tm.resolve(dirs[0]).state == state


def test_the_case_record_is_the_one_the_sweep_writes():
    from services import workflow_tools as wt

    assert tm.CASE_FILE == wt.SENSITIVITY_CASE_FILE


def test_writes_go_to_the_projects_own_folder(eh_case):
    assert tm.own_path(eh_case) == eh_case / tm.MARKER_FILE


# ── The headline strip ──────────────────────────────────────────────────────


def strip_of(ctx, client, project_dir):
    ctx.storage_dir = str(project_dir)
    return cards(client)


def test_the_strip_of_an_energy_hub_case_shows_its_base_cards(
    eh_base, eh_case, windy, client
):
    solve(windy)
    kind, by_id, order = strip_of(windy, client, eh_case)
    assert (kind, tuple(order)) == ("energy_hub", hk.ORDER["energy_hub"])
    assert {"lcoe", "unserved_energy"} <= set(by_id)
    assert "peak_line_loading" not in by_id


@pytest.mark.parametrize("torn", ["base_marker", "case_record"])
def test_an_unreadable_case_shows_what_an_unreadable_own_marker_shows(
    eh_base, eh_case, windy, client, torn
):
    """U2, both ways: the strip of the case equals the strip of a project
    whose own marker cannot be parsed (and is no 500)."""
    solve(windy)
    write_marker(eh_base.dir, "{broken")
    own_torn = strip_of(windy, client, eh_base.dir)
    if torn == "case_record":
        write_marker(eh_base.dir, eh_base.meta)
        (eh_case / tm.CASE_FILE).write_text("{broken")
    assert strip_of(windy, client, eh_case) == own_torn
    assert own_torn[0] == "network"


# ── Each reader, given a real swept case of an energy-hub base (U5) ─────────


def test_grid_limits_reads_the_base_ledger(eh_case):
    from services.adequacy import grid_limits

    assert grid_limits._template_default(eh_case, "scr_min") == {
        "value": 3.0,
        "source": "template",
    }


def test_grid_readiness_reads_the_base_ledger(eh_base, eh_case):
    from services.adequacy.grid_readiness import _Inputs

    inp = _Inputs(eh_case, None, None, None, None, False)
    assert inp.meta == eh_base.meta and "scr_min" in inp.ledger


def test_dc_backup_reads_the_base_spec(eh_base, eh_case):
    from project_templates import eh_templates as t
    from services.adequacy import dc_backup

    assert dc_backup._meta(eh_case) == eh_base.meta
    assert dc_backup.project_spec(eh_case, t)[0] == "eh_datacenter"


def curtailing(meta: dict) -> dict:
    """``meta`` with curtailment on in its spec (a price and a share > 0)."""
    spec = {
        **meta["dc_load"]["spec"],
        "critical_share": 0.6,
        "curtailable_share": 0.2,
        "curtailment_price_eur_per_mwh": 150.0,
        "curtail_max_duration_h": 4.0,
    }
    return {**meta, "dc_load": {**meta["dc_load"], "spec": spec}}


def fingerprint_text(ctx, storage, monkeypatch) -> str:
    """The text `_network_input_fingerprint` hashes, with `storage` as the
    project folder."""
    import hashlib

    from services import workflow_tools as wt

    seen = []

    class Capture:
        def __init__(self, data):
            seen.append(data.decode())

        def hexdigest(self):
            return hashlib.sha256(seen[-1].encode()).hexdigest()

    monkeypatch.setattr(wt, "hashlib", SimpleNamespace(sha256=Capture))
    saved, ctx.storage_dir = ctx.storage_dir, str(storage)
    try:
        wt._network_input_fingerprint(ctx)
    finally:
        ctx.storage_dir = saved
        monkeypatch.undo()
    return seen[-1]


def test_the_solver_terms_of_a_case_read_the_base_spec(
    eh_base, eh_case, monkeypatch, tmp_path
):
    """U4 c, F2: the base spec's curtailment terms reach the case's LP terms
    and its input fingerprint; the solver config does not switch them."""
    from services.solver import dc_curtailment

    write_marker(eh_base.dir, curtailing(eh_base.meta))
    terms = dc_curtailment.terms_for(str(eh_case))
    assert terms == dc_curtailment.Terms(150.0, 0.2, 4.0)
    part = "dc-curtailment/1\n" + json.dumps([150.0, 0.2, 4.0])
    assert part in fingerprint_text(eh_base.ctx, eh_case, monkeypatch)
    assert part not in fingerprint_text(eh_base.ctx, tmp_path, monkeypatch)


def test_dc_load_profile_reads_the_base_spec(eh_base, eh_case):
    from services import dc_load_profile

    spec = eh_base.meta["dc_load"]["spec"]
    fields = dc_load_profile.AMBIENT_FIELDS
    expected = (
        {k: float(spec[k]) for k in fields} if all(k in spec for k in fields) else None
    )
    assert dc_load_profile.read_ambient(eh_case) == expected
    assert dc_load_profile.read_envelope(eh_case) == {"peak_mw": 12.0}


def test_the_eh_template_route_serves_the_base_template(eh_base, eh_case):
    from routers.adequacy_worksheet import get_eh_template

    project = SimpleNamespace(directory=eh_case)
    assert get_eh_template(project=project) == eh_base.meta


def test_the_growth_flags_of_a_case_are_its_base(eh_base, sweep, monkeypatch):
    from routers import results

    case_id, _ = sweep(eh_base.id)
    active = SimpleNamespace(project_uuid=case_id)
    monkeypatch.setattr(
        PyPSAService, "get_active_context", staticmethod(lambda: active)
    )
    assert results._ic_growth_flags() == ("growth_placeholder:dc_load_2030",)


def test_a_report_of_a_saved_case_counts_it_a_hub(eh_case):
    from routers import reports

    assert (eh_case / "network.nc").is_file()
    assert reports._is_hub_project(eh_case) is True


def test_growth_rows_of_a_case_are_its_base(eh_base, eh_case):
    from services import growth_placeholders as gp

    assert gp._meta_path(eh_case) == eh_base.dir / tm.MARKER_FILE
    assert gp._own_meta_path(eh_case) == eh_case / tm.MARKER_FILE


def test_a_growth_edit_on_a_case_sets_the_row_in_the_case_copy(eh_base, eh_case):
    """F1: the real `mark_set` on a swept case writes the case's own copy
    from the base's content; the base's bytes and mtime never change."""
    import pandas as pd

    from services import growth_placeholders as gp

    row = {"kind": "load_scale", "year": 2030, "set": False, "value": {"L1": 1.0}}
    write_marker(eh_base.dir, {**eh_base.meta, "growth_inputs": [row]})
    before = stamp(eh_base.dir / tm.MARKER_FILE)
    n = eh_base.ctx.network
    prior = gp.before(n, "loads", "p_set", ["L1"], project_dir=eh_case)
    assert prior is not None
    series = pd.Series(10.0, index=n.snapshots)
    now = gp.mark_set(n, "loads", "p_set", {"L1": series}, prior, project_dir=eh_case)
    assert now == ["load_scale_2030"]
    own = json.loads((eh_case / tm.MARKER_FILE).read_text())
    assert own["growth_inputs"][0]["set"] is True
    assert own["id"] == eh_base.meta["id"] and own["ledger"] == eh_base.meta["ledger"]
    assert stamp(eh_base.dir / tm.MARKER_FILE) == before


# ── Copy-on-write (U7) and undo (U3) ────────────────────────────────────────


def stamp(path: pathlib.Path):
    return path.read_bytes(), path.stat().st_mtime_ns


def test_a_dc_backup_set_on_a_case_writes_the_case_not_the_base(eh_base, eh_case):
    from project_templates import eh_templates as t
    from services.adequacy import dc_backup

    before = stamp(eh_base.dir / tm.MARKER_FILE)
    stored = dc_backup.save_backup(eh_case, t, {"mode": "hybrid", "autonomy_h": 8.0})
    own = json.loads((eh_case / tm.MARKER_FILE).read_text())
    assert own["dc_load"]["spec"]["backup"] == stored
    assert own["id"] == eh_base.meta["id"]
    assert own["ledger"] == eh_base.meta["ledger"]
    assert stamp(eh_base.dir / tm.MARKER_FILE) == before


def test_the_dc_backup_route_on_a_case_writes_the_case(eh_base, sweep, client):
    """The write runs through the case's own route and project lock."""
    case_id, d = sweep(eh_base.id)
    before = stamp(eh_base.dir / tm.MARKER_FILE)
    with chat_tools._acting() as (db, user):
        name = project_registry.resolve_project(db, user, case_id).name
    r = client.put(f"/api/campus-electrical/{name}/dc-backup", json={"mode": "hybrid"})
    assert r.status_code == 200, r.text
    assert (d / tm.MARKER_FILE).is_file()
    assert stamp(eh_base.dir / tm.MARKER_FILE) == before


@pytest.fixture
def active_case(eh_case, monkeypatch):
    from services import network_lengths

    monkeypatch.setattr(network_lengths, "active_project_dir", lambda: eh_case)
    return eh_case


def test_undoing_the_edit_that_made_a_case_copy_removes_it(eh_base, active_case):
    from services import growth_placeholders as gp

    before = stamp(eh_base.dir / tm.MARKER_FILE)
    snapshot = gp.snapshot_meta()
    assert snapshot is None  # the case has no marker of its own
    gp._write(gp._own_meta_path(), {**eh_base.meta, "growth_inputs": []})
    gp.restore_meta(snapshot)
    assert not (active_case / tm.MARKER_FILE).exists()
    assert tm.read_marker(active_case) == eh_base.meta
    assert stamp(eh_base.dir / tm.MARKER_FILE) == before


def test_undo_keeps_a_copy_the_case_had_before_the_edit(eh_base, active_case):
    from services import growth_placeholders as gp

    own = write_marker(active_case, {**eh_base.meta, "id": "eh_microgrid"})
    snapshot = gp.snapshot_meta()
    assert snapshot == own.read_bytes()
    gp._write(gp._own_meta_path(), {**eh_base.meta, "growth_inputs": []})
    gp.restore_meta(snapshot)
    assert own.read_bytes() == snapshot


def test_a_case_copy_that_cannot_be_read_skips_the_snapshot(
    eh_base, active_case, monkeypatch
):
    """A snapshot of None would make the undo remove the copy."""
    from services import growth_placeholders as gp

    own = write_marker(active_case, eh_base.meta)

    def unreadable(self):
        raise OSError("io")

    monkeypatch.setattr(pathlib.Path, "read_bytes", unreadable)
    with pytest.raises(OSError):
        gp.snapshot_meta()
    monkeypatch.undo()
    assert own.is_file()


def test_undo_never_removes_the_marker_of_a_project_that_is_not_a_case(
    eh_base, monkeypatch
):
    from services import growth_placeholders as gp
    from services import network_lengths

    monkeypatch.setattr(network_lengths, "active_project_dir", lambda: eh_base.dir)
    before = stamp(eh_base.dir / tm.MARKER_FILE)
    gp.restore_meta(None)
    assert stamp(eh_base.dir / tm.MARKER_FILE) == before


# ── A queued solve of a case (U4) ───────────────────────────────────────────


def test_a_case_capture_holds_the_base_marker_and_its_hash(eh_base, eh_case):
    from services import solve_capture

    base_marker = eh_base.dir / tm.MARKER_FILE
    hashes = solve_capture.sidecar_hashes(eh_case)
    assert hashes[tm.MARKER_FILE] == solve_capture._sha256(base_marker)
    write_marker(eh_base.dir, {**eh_base.meta, "id": "eh_microgrid"})
    after = solve_capture.sidecar_hashes(eh_case)
    assert after[tm.MARKER_FILE] != hashes[tm.MARKER_FILE]


def test_a_queued_job_reads_only_its_captured_copy(eh_base, eh_case):
    """U4 a: the capture holds the base's bytes as its own marker and no case
    record, so a job never resolves the live base again."""
    from services import solve_capture

    record = solve_capture.capture(eh_base.ctx, eh_case, None)
    try:
        captured = pathlib.Path(record["dir"])
        base_bytes = (eh_base.dir / tm.MARKER_FILE).read_bytes()
        assert (captured / tm.MARKER_FILE).read_bytes() == base_bytes
        assert not (captured / tm.CASE_FILE).exists()
        write_marker(eh_base.dir, {**eh_base.meta, "id": "eh_microgrid"})
        assert tm.resolve(captured).state == tm.OWN
        assert tm.read_marker(captured) == eh_base.meta
    finally:
        solve_capture.remove(record)


@pytest.mark.parametrize("who", ["template_project", "case_with_own_copy"])
def test_a_capture_of_an_own_marker_is_byte_for_byte_as_before(eh_base, eh_case, who):
    """U4 b: a project that has its own marker captures and hashes it."""
    from services import solve_capture

    if who == "template_project":
        d, own = eh_base.dir, eh_base.dir / tm.MARKER_FILE
    else:
        d = eh_case
        own = write_marker(d, {**eh_base.meta, "id": "eh_microgrid"})
    assert solve_capture.sidecar_hashes(d)[tm.MARKER_FILE] == solve_capture._sha256(own)
    record = solve_capture.capture(eh_base.ctx, d, None)
    try:
        captured = pathlib.Path(record["dir"]) / tm.MARKER_FILE
        assert captured.read_bytes() == own.read_bytes()
    finally:
        solve_capture.remove(record)


# ── No new direct reader (U6) ───────────────────────────────────────────────

#: Files that may name the marker file directly: the helper, the template
#: builders that write it into a template folder, and project creation, which
#: writes it into a project folder it has just made (no case exists there).
ALLOWED = {
    "services/template_marker.py",
    "routers/projects.py",
}
#: Per-function exemptions, so a new direct read elsewhere in the file is
#: still seen. `sidecars()` names the marker in its tuple; `_source` reads
#: that entry through the helper (pinned by the capture tests above).
EXEMPT_FUNCTIONS = {"services/solve_capture.py": {"sidecars"}}
ALLOWED_DIRS = ("project_templates/", "tests/", "smoke/")
#: The names the marker file goes by.
NAMES = {"MARKER_FILE", "TEMPLATE_FILE", "META_FILE"}


def _constant_text(node) -> str | None:
    """The text of a string built from constants only: a literal, a
    concatenation of literals, or an f-string's literal parts."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _constant_text(node.left), _constant_text(node.right)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            inner = value.value if isinstance(value, ast.FormattedValue) else value
            text = _constant_text(inner)
            if text is None:
                return None
            parts.append(text)
        return "".join(parts)
    return None


def _name_of(node) -> str | None:
    return node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)


def _direct_uses(source: str, exempt: frozenset | set = frozenset()) -> list[int]:
    """Lines that name the marker file outside the helper: the file name
    built from constants anywhere, or one of its names (``NAMES`` and any
    name an import binds them to) used as a path part: the right of ``/``,
    an argument of any call (``joinpath``, ``os.path.join``, ``Path(d,
    NAME)``, ``open``), the right of ``+`` (``str(d) + "/" + NAME``), the
    value of an assignment (an alias) or a member of a tuple, list or set
    literal. Lines inside a function named in ``exempt`` are skipped.

    What it cannot see: a name rebound through a variable before use
    (``x = NAME`` is caught, ``y = x; d / y`` is not), a name read through
    ``getattr`` or a dict, and a file name assembled from non-constant parts.
    ``str(d) + "/" + NAME`` and the alias assignment ``x = NAME`` are
    covered."""
    tree = ast.parse(source)
    skip = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name in exempt
        ):
            skip.update(range(node.lineno, node.end_lineno + 1))
    names = set(NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            for alias in node.names:
                if alias.name.rsplit(".", 1)[-1] in NAMES and alias.asname:
                    names.add(alias.asname)
    lines = set()
    for node in ast.walk(tree):
        if _constant_text(node) == tm.MARKER_FILE:
            lines.add(node.lineno)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div | ast.Add):
            if _name_of(node.right) in names:
                lines.add(node.lineno)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            if node.value is not None and _name_of(node.value) in names:
                lines.add(node.lineno)
        elif isinstance(node, ast.Call):
            args = [*node.args, *(k.value for k in node.keywords)]
            if any(_name_of(n) in names for a in args for n in ast.walk(a)):
                lines.add(node.lineno)
        elif isinstance(node, ast.Tuple | ast.List | ast.Set):
            if any(_name_of(e) in names for e in node.elts):
                lines.add(node.lineno)
    return sorted(lines - skip)


def test_no_module_reads_the_marker_file_but_through_the_helper():
    found = {}
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in ALLOWED or rel.startswith(ALLOWED_DIRS) or "/." in "/" + rel:
            continue
        lines = _direct_uses(path.read_text("utf-8"), EXEMPT_FUNCTIONS.get(rel, set()))
        if lines:
            found[rel] = lines
    assert found == {}


@pytest.mark.parametrize(
    "source",
    [
        'p = d / "eh_template.json"',
        'p = d / ("eh_" + "template.json")',
        "p = d / f\"eh_{'template'}.json\"",
        "p = d / META_FILE",
        "p = d / flow_service.TEMPLATE_FILE",
        "p = d / template_marker.MARKER_FILE",
        "p = d.joinpath(META_FILE)",
        "p = os.path.join(d, flow_service.TEMPLATE_FILE)",
        "p = Path(d, META_FILE)",
        "SIDECARS = (OTHER, TEMPLATE_FILE)",
        "files = [flow_service.TEMPLATE_FILE]",
        "p = open(d + MARKER_FILE)",
        'p = str(d) + "/" + TEMPLATE_FILE',
        "alias = flow_service.TEMPLATE_FILE",
    ],
)
def test_the_guard_sees_a_direct_reader(source):
    assert _direct_uses(source) == [1]


def test_the_guard_follows_a_renamed_import():
    source = "from services.template_marker import MARKER_FILE as X\np = d / X\n"
    assert _direct_uses(source) == [2]


def test_the_exemption_covers_only_the_named_function():
    source = (
        "def sidecars():\n    return (A, TEMPLATE_FILE)\n"
        "def other(d):\n    return d / TEMPLATE_FILE\n"
    )
    assert _direct_uses(source) == [2, 4]
    assert _direct_uses(source, {"sidecars"}) == [4]


def test_solve_capture_has_no_direct_read_outside_sidecars():
    source = (BACKEND / "services/solve_capture.py").read_text("utf-8")
    assert _direct_uses(source, EXEMPT_FUNCTIONS["services/solve_capture.py"]) == []
    assert _direct_uses(source) != []


def test_the_guard_passes_the_helper():
    assert _direct_uses("p = template_marker.source_path(d)") == []
