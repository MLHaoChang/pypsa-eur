"""A project's template marker (``eh_template.json``) and the sensitivity case
that has none (ui-modern issue 07, owner decision UM07-KIND-1).

A case made by ``run_sensitivity_sweep`` takes its base's project type,
read-side: ``services.template_marker`` follows the case's
``sensitivity_case.json`` to its base, and every reader of the marker goes
through it. The sweep does not copy the marker.
"""
import ast
import json
import pathlib
import uuid
from types import SimpleNamespace

import pytest

from services import chat_tools, flow_service, project_registry, template_marker as tm
from services import workflow_tools as wt
from services.pypsa_service import PyPSAService
from services.results import headline_kpis as hk
from tests.test_headline_kpis import cards, checked_profiles_on  # noqa: F401
from tests.test_result_profiles_assurance import solve, windy  # noqa: F401
from tests.test_workflow_tools import baseline, case  # noqa: F401

#: The shared fixtures this module takes by name.
FIXTURES = (baseline, windy, checked_profiles_on)

BACKEND = pathlib.Path(__file__).resolve().parents[1]

#: An energy-hub marker as a template writes it: an id, a ledger row, a
#: growth input left unset and the data-centre opt-in's block.
MARKER = {
    "id": "eh_datacenter",
    "ledger": [{"item": "scr_min", "value": 3.0, "source": "template"}],
    "growth_inputs": [{"kind": "dc_load", "year": 2030, "set": False}],
    "dc_load": {
        "spec": {"ambient_annual_mean_c": 10.0, "ambient_seasonal_amplitude_k": 8.0,
                 "ambient_diurnal_amplitude_k": 4.0, "backup": {}},
        "envelope": {"peak_mw": 12.0},
    },
}


def write_marker(directory: pathlib.Path, meta=MARKER) -> pathlib.Path:
    path = directory / tm.MARKER_FILE
    path.write_text(json.dumps(meta))
    return path


def case_dir(tmp_path, baseline_id, name="case") -> pathlib.Path:
    """A case folder as the sweep leaves it: its record, no marker."""
    d = tmp_path / name
    d.mkdir()
    (d / wt.SENSITIVITY_CASE_FILE).write_text(json.dumps({"baseline_id": str(baseline_id)}))
    return d


@pytest.fixture
def eh_base(baseline):
    """The baseline project carrying an energy-hub marker."""
    ctx, project_id, path = baseline
    write_marker(path)
    return ctx, project_id, path


# ── The helper ───────────────────────────────────────────────────────────────

def test_a_swept_case_reads_its_base_template_and_the_sweep_copies_nothing(eh_base):
    _, project_id, _ = eh_base
    out = chat_tools.run_sensitivity_sweep(project_id, [case()])
    assert out["status"] == "submitted", out
    child = out["cases"][0]
    with chat_tools._acting() as (db, user):
        row = project_registry.resolve_project(db, user, child["project_id"])
        d = project_registry.project_dir(row)
    try:
        assert not (d / tm.MARKER_FILE).exists()
        assert tm.read_marker(d) == MARKER
        assert flow_service.read_template(d) == MARKER
        assert hk.project_kind(flow_service.read_template(d)) == "energy_hub"
    finally:
        chat_tools.wait_for_job(child["job"]["id"], 25)


def test_a_plain_project_and_a_case_of_a_plain_base_have_no_template(baseline, tmp_path):
    _, project_id, path = baseline
    assert tm.read_marker(path) is None and tm.source_path(path) is None
    d = case_dir(tmp_path, project_id)
    assert tm.read_marker(d) is None and tm.source_path(d) is None
    assert hk.project_kind(flow_service.read_template(d)) == "network"


def test_a_case_that_has_its_own_marker_reads_its_own(eh_base, tmp_path):
    _, project_id, path = eh_base
    d = case_dir(tmp_path, project_id)
    own = write_marker(d, {"id": "eh_microgrid"})
    assert tm.source_path(d) == own and tm.read_marker(d) == {"id": "eh_microgrid"}
    assert tm.source_path(path) == path / tm.MARKER_FILE


def test_a_case_of_a_case_follows_the_chain(eh_base, tmp_path):
    _, project_id, path = eh_base
    first = case_dir(tmp_path, project_id, "first")
    second = case_dir(tmp_path, project_id, "second")
    # `second`'s record names a project row whose folder is `first`.
    with chat_tools._acting() as (db, user):
        row = project_registry.resolve_project(db, user, project_id)
        base_dir = project_registry.project_dir(row)
    calls = []

    def dir_of(row):
        calls.append(row.id)
        return first if len(calls) == 1 else base_dir

    import services.template_marker as module
    original = module.project_registry.project_dir
    module.project_registry.project_dir = dir_of
    try:
        assert tm.read_marker(second) == MARKER
    finally:
        module.project_registry.project_dir = original
    assert len(calls) == 2


def test_a_case_whose_base_row_is_gone_reads_as_a_plain_network(tmp_path):
    d = case_dir(tmp_path, uuid.uuid4())
    assert tm.read_marker(d) is None and tm.source_path(d) is None


def test_a_case_whose_base_has_lost_its_marker_reads_as_a_plain_network(eh_base, tmp_path):
    _, project_id, path = eh_base
    d = case_dir(tmp_path, project_id)
    (path / tm.MARKER_FILE).unlink()
    assert tm.read_marker(d) is None


@pytest.mark.parametrize("record", ["not json", "[]", '{"baseline_id": 7}',
                                    '{"baseline_id": "not-a-uuid"}', "{}"])
def test_an_unreadable_case_record_reads_as_a_plain_network(tmp_path, record):
    d = tmp_path / "case"
    d.mkdir()
    (d / wt.SENSITIVITY_CASE_FILE).write_text(record)
    assert tm.read_marker(d) is None


def test_a_case_record_that_names_its_own_row_ends(baseline, monkeypatch, tmp_path):
    _, project_id, _ = baseline
    d = case_dir(tmp_path, project_id)
    import services.template_marker as module
    monkeypatch.setattr(module.project_registry, "project_dir", lambda row: d)
    assert tm.read_marker(d) is None


def test_a_marker_that_is_not_an_object_is_none(tmp_path):
    (tmp_path / tm.MARKER_FILE).write_text("[1, 2]")
    assert tm.read_marker(tmp_path) is None
    (tmp_path / tm.MARKER_FILE).write_text("{broken")
    assert tm.read_marker(tmp_path) is None


def test_writes_go_to_the_projects_own_folder(tmp_path):
    assert tm.own_path(tmp_path) == tmp_path / tm.MARKER_FILE


# ── The headline strip ──────────────────────────────────────────────────────

def test_the_strip_of_an_energy_hub_case_shows_its_base_cards(eh_base, windy, client, tmp_path):
    _, project_id, _ = eh_base
    windy.storage_dir = str(case_dir(tmp_path, project_id))
    solve(windy)
    kind, by_id, order = cards(client)
    assert (kind, tuple(order)) == ("energy_hub", hk.ORDER["energy_hub"])
    assert {"lcoe", "unserved_energy"} <= set(by_id) and "peak_line_loading" not in by_id


# ── Each reader, given a case of an energy-hub base ─────────────────────────

@pytest.fixture
def eh_case(eh_base, tmp_path):
    return case_dir(tmp_path, eh_base[1])


def test_grid_limits_reads_the_base_ledger(eh_case):
    from services.adequacy import grid_limits
    assert grid_limits._template_default(eh_case, "scr_min") == {"value": 3.0, "source": "template"}


def test_grid_readiness_reads_the_base_ledger(eh_case):
    from services.adequacy.grid_readiness import _Inputs
    inp = _Inputs(eh_case, None, None, None, None, False)
    assert inp.meta == MARKER and "scr_min" in inp.ledger


def test_dc_backup_reads_the_base_meta(eh_case):
    from services.adequacy import dc_backup
    assert dc_backup._meta(eh_case) == MARKER


def test_dc_load_profile_reads_the_base_spec(eh_case):
    from services import dc_load_profile
    assert dc_load_profile.read_ambient(eh_case) == {
        "ambient_annual_mean_c": 10.0, "ambient_seasonal_amplitude_k": 8.0,
        "ambient_diurnal_amplitude_k": 4.0}
    assert dc_load_profile.read_envelope(eh_case) == {"peak_mw": 12.0}


def test_the_eh_template_route_serves_the_base_template(eh_case):
    from routers.adequacy_worksheet import get_eh_template
    assert get_eh_template(project=SimpleNamespace(directory=eh_case)) == MARKER


def test_the_growth_flags_of_a_case_are_its_base(eh_base, monkeypatch):
    _, project_id, _ = eh_base
    out = chat_tools.run_sensitivity_sweep(project_id, [case()])
    child = out["cases"][0]
    try:
        from routers import results
        monkeypatch.setattr(PyPSAService, "get_active_context",
                            staticmethod(lambda: SimpleNamespace(project_uuid=child["project_id"])))
        assert results._ic_growth_flags() == ("growth_placeholder:dc_load_2030",)
    finally:
        chat_tools.wait_for_job(child["job"]["id"], 25)


def test_a_report_of_a_case_counts_it_a_hub(eh_case, monkeypatch):
    from routers import reports
    assert reports._is_hub_project(eh_case) is False  # no network.nc beside it
    (eh_case / "network.nc").write_bytes(b"")
    assert reports._is_hub_project(eh_case) is True


def test_a_growth_edit_on_a_case_copies_the_marker_before_writing(eh_base, eh_case):
    from services import growth_placeholders as gp
    base_bytes = (eh_base[2] / tm.MARKER_FILE).read_bytes()
    assert gp._read(gp._meta_path(eh_case)) == MARKER
    gp._write(gp._own_meta_path(eh_case), {**MARKER, "growth_inputs": []})
    assert json.loads((eh_case / tm.MARKER_FILE).read_text())["growth_inputs"] == []
    assert (eh_base[2] / tm.MARKER_FILE).read_bytes() == base_bytes


def test_undoing_the_edit_that_made_a_case_marker_removes_it(eh_case, monkeypatch):
    from services import growth_placeholders as gp
    monkeypatch.setattr(gp, "_meta_path", lambda project_dir=None: tm.source_path(eh_case))
    monkeypatch.setattr(gp, "_own_meta_path", lambda project_dir=None: tm.own_path(eh_case))
    before = gp.snapshot_meta()
    assert before is None  # the case has no marker of its own
    write_marker(eh_case, {**MARKER, "growth_inputs": []})
    gp.restore_meta(before)
    assert not (eh_case / tm.MARKER_FILE).exists()
    assert tm.read_marker(eh_case) == MARKER


def test_undo_leaves_a_project_without_a_marker_alone(tmp_path, monkeypatch):
    from services import growth_placeholders as gp
    monkeypatch.setattr(gp, "_own_meta_path", lambda project_dir=None: tm.own_path(tmp_path))
    gp.restore_meta(None)
    assert not (tmp_path / tm.MARKER_FILE).exists()


def test_a_dc_backup_set_on_a_case_writes_the_case_not_the_base(eh_base, eh_case):
    from services.adequacy import dc_backup
    base_bytes = (eh_base[2] / tm.MARKER_FILE).read_bytes()
    meta = dc_backup._meta(eh_case)
    meta["dc_load"]["spec"]["backup"] = {"hours": 4}
    dc_backup._write_meta(eh_case, meta)
    assert json.loads((eh_case / tm.MARKER_FILE).read_text())["dc_load"]["spec"]["backup"] == {"hours": 4}
    assert (eh_base[2] / tm.MARKER_FILE).read_bytes() == base_bytes


# ── A queued solve of a case ────────────────────────────────────────────────

def test_a_case_capture_holds_the_base_marker_and_its_hash(eh_base, eh_case):
    from services import solve_capture
    base_marker = eh_base[2] / tm.MARKER_FILE
    hashes = solve_capture.sidecar_hashes(eh_case)
    assert hashes[tm.MARKER_FILE] == solve_capture._sha256(base_marker)
    write_marker(eh_base[2], {**MARKER, "id": "eh_microgrid"})
    assert solve_capture.sidecar_hashes(eh_case)[tm.MARKER_FILE] != hashes[tm.MARKER_FILE]


def test_a_case_capture_copies_the_base_marker(eh_base, eh_case):
    from services import solve_capture
    ctx = eh_base[0]
    record = solve_capture.capture(ctx, eh_case, None)
    try:
        captured = pathlib.Path(record["dir"]) / tm.MARKER_FILE
        assert captured.read_bytes() == (eh_base[2] / tm.MARKER_FILE).read_bytes()
        assert tm.read_marker(pathlib.Path(record["dir"])) == MARKER
        assert solve_capture.verify(record).get("ok", True)
    finally:
        solve_capture.remove(record)


# ── No new direct reader ────────────────────────────────────────────────────

#: Files that may name the marker file directly: the helper, the template
#: builders that write it into a template folder, and project creation, which
#: writes it into a project folder it has just made (no case exists there).
ALLOWED = {"services/template_marker.py", "routers/projects.py"}
ALLOWED_DIRS = ("project_templates/", "tests/", "smoke/")
#: The names the marker file goes by.
NAMES = {"MARKER_FILE", "TEMPLATE_FILE", "META_FILE"}


def _direct_uses(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and node.value == tm.MARKER_FILE:
            lines.append(node.lineno)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = node.right
            name = right.id if isinstance(right, ast.Name) else getattr(right, "attr", None)
            if name in NAMES:
                lines.append(node.lineno)
    return lines


def test_no_module_reads_the_marker_file_but_through_the_helper():
    found = {}
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in ALLOWED or rel.startswith(ALLOWED_DIRS) or "/." in "/" + rel:
            continue
        lines = _direct_uses(path.read_text("utf-8"))
        if lines:
            found[rel] = lines
    assert found == {}


@pytest.mark.parametrize("source", [
    'p = d / "eh_template.json"',
    'p = d / META_FILE',
    'p = d / flow_service.TEMPLATE_FILE',
])
def test_the_guard_sees_a_direct_reader(source):
    assert _direct_uses(source) == [1]


def test_the_guard_passes_the_helper():
    assert _direct_uses("p = template_marker.source_path(d)") == []
