"""The project contents table and the safe bundle import (project-io issue 01).

GK B (63) plan conditions: I1 project kind, I2 safe staging, I3 cleanup, I4 the
guard both ways, I5 old snapshots and bundles, I10 the cost guard (#1628) refuses before the row.
"""

from __future__ import annotations

import ast
import io
import json
import pathlib
import stat
import zipfile

import pytest

from routers import projects
from services import project_contents as PC

BACKEND = pathlib.Path(__file__).resolve().parents[1]


# ── I4: the guard, both ways ─────────────────────────────────────────────────


def _declared_names() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for root in ("services", "routers", "harness"):
        for path in sorted((BACKEND / root).rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in tree.body:
                if not (
                    isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                ):
                    continue
                var = node.targets[0].id
                if not (var == "SIDECAR_NAME" or var.endswith("_FILE")):
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ):
                    out.setdefault(node.value.value, []).append(
                        f"{path.relative_to(BACKEND)}:{var}"
                    )
    return out


def test_every_declared_project_file_is_in_the_table_or_excluded():
    table = {e.path.rsplit("/", 1)[-1] for e in PC.TABLE}
    missing = {
        name: where
        for name, where in _declared_names().items()
        if name not in table and name not in PC.NOT_CARRIED and " " not in name
    }
    assert not missing, (
        f"declared project file(s) neither in project_contents.TABLE nor NOT_CARRIED: {missing}"
    )


def test_every_table_entry_has_a_writer():
    sources = "\n".join(
        p.read_text()
        for root in ("services", "routers", "harness")
        for p in (BACKEND / root).rglob("*.py")
        if p.name != "project_contents.py"
    )
    # Templates write some names; the gridspine drivers write the campus run files.
    for extra in (BACKEND / "project_templates", BACKEND.parents[1] / "gridspine"):
        sources += "\n".join(p.read_text() for p in extra.rglob("*.py"))
    orphans = [
        e.path
        for e in PC.TABLE
        if not e.pending_writer
        and f'"{e.path.rsplit("/", 1)[-1]}"' not in sources
        and f'"{e.path}"' not in sources
    ]
    assert not orphans, f"table entries no module writes: {orphans}"


def test_experiments_stays_out_and_the_derived_lists_follow_the_table():
    assert PC.entry("experiments") is None
    assert "experiments" not in PC.names(PC.BUNDLE)
    for name in ("eh_template.json", "cases.json", "import_report.json"):
        assert name in projects._BUNDLE_FILES
    assert {
        "eh_template.json",
        "cases.json",
        "sensitivity_case.json",
    } == projects._FORK_SKIPPED_FILES
    assert "gridspine" in PC.names(PC.BUNDLE) and "gridspine" in PC.names(PC.SAVE_AS)
    assert (
        "gridspine" in projects._SNAPSHOT_DIRS
        and "gridspine" not in projects._BUNDLE_DIRS
    )
    assert PC.skipped("gridspine", PC.SNAPSHOT) == ("run",)
    assert PC.NOT_CARRIED.keys().isdisjoint(e.path for e in PC.TABLE)


# ── I2: safe staging ─────────────────────────────────────────────────────────


def _zip(members: dict[str, bytes], *, link: str | None = None) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        if link:
            info = zipfile.ZipInfo(link)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
    return zipfile.ZipFile(io.BytesIO(buf.getvalue()))


@pytest.mark.parametrize(
    "bad, kind",
    [
        ("/etc/x", "bundle_unsafe_path"),
        ("../x.json", "bundle_unsafe_path"),
        ("uploads/../../x", "bundle_unsafe_path"),
        ("C:/x.json", "bundle_unsafe_path"),
        ("uploads\\x", "bundle_unsafe_path"),
    ],
)
def test_unsafe_member_paths_are_refused(bad, kind):
    with pytest.raises(PC.BundleRefused) as exc:
        PC.screen_members(_zip({"network.nc": b"x", bad: b"y"}))
    assert exc.value.kind == kind


def test_a_symlink_member_is_refused():
    with pytest.raises(PC.BundleRefused) as exc:
        PC.screen_members(_zip({"network.nc": b"x"}, link="uploads/a"))
    assert exc.value.kind == "bundle_unsafe_path"


@pytest.mark.parametrize(
    "a, b",
    [
        ("layout.json", "LAYOUT.json"),
        ("uploads/a", "uploads/a."),
        ("uploads/a", "uploads/a "),
    ],
)
def test_members_that_fold_to_one_file_are_refused(a, b):
    with pytest.raises(PC.BundleRefused) as exc:
        PC.screen_members(_zip({a: b"1", b: b"2"}))
    assert exc.value.kind == "bundle_name_clash"


def test_a_bundle_over_the_unpacked_cap_is_refused():
    zf = _zip({"network.nc": b"x" * 600, "layout.json": b"y" * 600})
    assert PC.screen_members(zf, max_unpacked=1200)
    with pytest.raises(PC.BundleRefused) as exc:
        PC.screen_members(zf, max_unpacked=1199)
    assert exc.value.kind == "bundle_too_large"


def test_members_the_table_does_not_name_are_reported_not_allowed():
    allowed, ignored = PC.screen_members(
        _zip(
            {
                "network.nc": b"x",
                "uploads/f/a.txt": b"a",
                "evil.py": b"x",
                "experiments/p.json": b"{}",
                "campus_electrical/campus_input.yaml": b"",
                "campus_electrical/draft_notes.json": b"{}",
            }
        )
    )
    assert sorted(allowed) == [
        "campus_electrical/campus_input.yaml",
        "network.nc",
        "uploads/f/a.txt",
    ]
    assert sorted(ignored) == [
        "campus_electrical/draft_notes.json",
        "evil.py",
        "experiments/p.json",
    ]


# ── the route: what is carried, kind, refusals, cleanup ─────────────────────


def _bundle_from(
    project_dir: pathlib.Path,
    extra: dict[str, bytes] | None = None,
    meta: dict | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.write(project_dir / "network.nc", "network.nc")
        zf.writestr("metadata.json", json.dumps({"name": "x", **(meta or {})}))
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def _post(client, data: bytes, name: str):
    return client.post(
        f"/api/projects/import_bundle?name={name}",
        files={"file": ("p.pypsaproj.zip", data, "application/zip")},
    )


def _left_behind(project_row, projects_root: pathlib.Path, name: str) -> list[str]:
    left = []
    if project_row(name) is not None:
        left.append("row")
    staging = projects_root / ".bundle-staging"
    if staging.exists() and any(staging.iterdir()):
        left.append("staging")
    left += [str(p) for p in projects_root.rglob(name) if p.is_dir()]
    return left


@pytest.fixture
def projects_root():
    from settings import get_settings

    return pathlib.Path(get_settings().projects_root)


def test_a_round_trip_carries_the_marker_cases_importer_sidecars_and_kind(
    client, api_project, project_storage_dir, project_row
):
    name = api_project("pc-src")
    d = project_storage_dir(name)
    for f in ("eh_template.json", "cases.json", "psse_case.json", "import_report.json"):
        (d / f).write_text(json.dumps({"f": f}))
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200, r.text
    members = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {
        "eh_template.json",
        "cases.json",
        "psse_case.json",
        "import_report.json",
    } <= members

    r = _post(client, r.content, "pc_dst")
    assert r.status_code in (200, 201), r.text
    dst = project_storage_dir("pc_dst")
    for f in ("eh_template.json", "cases.json", "psse_case.json", "import_report.json"):
        assert json.loads((dst / f).read_text()) == {"f": f}
    assert project_row("pc_dst").project_kind is None


def test_a_planning_bundle_keeps_its_kind_and_gridspine_but_not_its_source_paths(
    client, api_project, project_storage_dir, project_row, _auth_db
):
    from services import gridspine_service as gs

    if not gs.GRIDSPINE_AVAILABLE:
        pytest.skip("gridspine is not in this build")
    name = api_project("pc-plan")
    from db.models import Project

    with _auth_db[1]() as db:
        db.get(Project, project_row(name).id).project_kind = gs.PLANNING_DYNAMICS
        db.commit()
    d = project_storage_dir(name)
    (d / "gridspine" / "run").mkdir(parents=True)
    (d / "gridspine" / "config.json").write_text(
        json.dumps(
            {"hours": 3, "from_dispatch": "/elsewhere/run", "from_network": "/etc/x.nc"}
        )
    )
    (d / "gridspine" / "run" / "manifest.json").write_text("{}")
    (d / "gridspine" / "run" / "dispatch.csv").write_bytes(b"h,p\r\n0,\x001.5\n")
    r = client.get(f"/api/projects/{name}/bundle")
    meta = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("metadata.json"))
    assert meta["project_kind"] == gs.PLANNING_DYNAMICS

    r = _post(client, r.content, "pc_plan_dst")
    assert r.status_code in (200, 201), r.text
    assert set(r.json()["stripped"]) == {
        "gridspine.from_dispatch",
        "gridspine.from_network",
    }
    dst = project_storage_dir("pc_plan_dst")
    cfg = json.loads((dst / "gridspine" / "config.json").read_text())
    assert (
        cfg["hours"] == 3
        and cfg["from_dispatch"] is None
        and cfg["from_network"] is None
    )
    # A copy claims nothing new (GK B, replacing Q-A): run/ arrives byte-equal
    # and nothing in it is added or marked current by the import.
    src_run, dst_run = d / "gridspine" / "run", dst / "gridspine" / "run"
    assert sorted(p.name for p in dst_run.iterdir()) == sorted(
        p.name for p in src_run.iterdir()
    )
    for f in src_run.iterdir():
        assert (dst_run / f.name).read_bytes() == f.read_bytes(), f.name
    assert project_row("pc_plan_dst").project_kind == gs.PLANNING_DYNAMICS


def test_a_capacity_expansion_bundle_never_writes_gridspine(
    client, api_project, project_storage_dir, project_row
):
    d = project_storage_dir(api_project("pc-ce"))
    r = _post(client, _bundle_from(d, {"gridspine/config.json": b"{}"}), "pc_ce_dst")
    assert r.status_code in (200, 201), r.text
    assert "gridspine/config.json" in r.json()["ignored"]
    assert not (project_storage_dir("pc_ce_dst") / "gridspine").exists()


@pytest.mark.parametrize("kind", ["nonsense", 7, ["planning_dynamics"]])
def test_an_unknown_kind_is_a_plain_refusal_that_leaves_nothing(
    client, api_project, project_storage_dir, project_row, projects_root, kind
):
    d = project_storage_dir(api_project("pc-kind"))
    r = _post(client, _bundle_from(d, meta={"project_kind": kind}), "pc_kind_dst")
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "bundle_unknown_kind"
    assert _left_behind(project_row, projects_root, "pc_kind_dst") == []


def test_an_unsafe_bundle_is_refused_before_the_row(
    client, api_project, project_storage_dir, project_row, projects_root
):
    d = project_storage_dir(api_project("pc-unsafe"))
    r = _post(
        client, _bundle_from(d, {"uploads/../../escape.txt": b"x"}), "pc_unsafe_dst"
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error_kind"] == "bundle_unsafe_path"
    assert _left_behind(project_row, projects_root, "pc_unsafe_dst") == []
    assert not any(projects_root.rglob("escape.txt"))


def test_an_ignored_member_is_never_written(client, api_project, project_storage_dir):
    d = project_storage_dir(api_project("pc-ign"))
    r = _post(client, _bundle_from(d, {"run.sh": b"rm -rf /"}), "pc_ign_dst")
    assert r.status_code in (200, 201), r.text
    assert r.json()["ignored"] == ["run.sh"]
    assert not (project_storage_dir("pc_ign_dst") / "run.sh").exists()


# ── I3: a failure at each step after the row leaves nothing; a retry works ──

_STEPS = [
    ("routers.projects._move_staged", None),
    ("routers.projects._reports_copy_guard", None),
    ("services.pypsa_service.PyPSAService.reset_network", "classattr"),
    ("services.project_registry.bind_context", None),
    ("services.active_project.set_active_project", None),
    ("routers.projects._restore_results_state", None),
    ("routers.projects._library_pin_issues", None),
]


@pytest.mark.parametrize(
    "target, how", _STEPS, ids=[s[0].rsplit(".", 1)[-1] for s in _STEPS]
)
def test_a_late_failure_removes_row_and_folder_and_a_retry_succeeds(
    client,
    api_project,
    project_storage_dir,
    project_row,
    projects_root,
    monkeypatch,
    target,
    how,
):
    d = project_storage_dir(api_project("pc-fail"))
    data = _bundle_from(d)
    dst = f"pc_fail_{target.rsplit('.', 1)[-1].lower()}"

    def boom(*a, **k):
        raise RuntimeError("injected")

    mod_path, attr = target.rsplit(".", 1)
    if how == "classattr":
        from services.pypsa_service import PyPSAService

        monkeypatch.setattr(PyPSAService, attr, boom)
    else:
        import importlib

        monkeypatch.setattr(importlib.import_module(mod_path), attr, boom)
    with pytest.raises(RuntimeError):
        _post(client, data, dst)
    assert _left_behind(project_row, projects_root, dst) == []
    monkeypatch.undo()
    r = _post(client, data, dst)
    assert r.status_code in (200, 201), r.text
    assert (project_storage_dir(dst) / "network.nc").exists()


def test_an_import_that_loses_the_name_race_gets_a_clean_409(
    client,
    api_project,
    project_storage_dir,
    project_row,
    projects_root,
    monkeypatch,
    _auth_db,
    seeded_identity,
):
    """Two imports of one name: the other wins between this one's checks and
    its row (the app serializes requests, so the race is staged here)."""
    from db.models import User
    from services import project_registry

    d = project_storage_dir(api_project("pc-race"))
    data = _bundle_from(d)
    real = projects._bundle_network_owning_its_costs

    def other_import_wins(*a, **k):
        out = real(*a, **k)
        with _auth_db[1]() as db:
            winner = project_registry.create_root(
                db, db.get(User, seeded_identity["user_id"]), "pc_race_dst"
            )
            project_registry.ensure_project_dir(winner)
            (project_registry.project_dir(winner) / "winner.txt").write_text("w")
        return out

    monkeypatch.setattr(projects, "_bundle_network_owning_its_costs", other_import_wins)
    r = _post(client, data, "pc_race_dst")
    assert r.status_code == 409, r.text
    winner = project_storage_dir("pc_race_dst")
    assert (winner / "winner.txt").exists(), "the loser removed the winner's folder"
    assert not (winner / "network.nc").exists(), (
        "the loser wrote into the winner's folder"
    )
    staging = projects_root / ".bundle-staging"
    assert not staging.exists() or not any(staging.iterdir())


# ── I10: the cost guard (#1628) refuses before the row ──────────────────────


def _priced_bundle(tmp_path, mutate) -> bytes:
    from services.pypsa_service import PyPSAService
    from tests.test_bulk_part_rederive import _network

    n = _network()
    mutate(n)
    nc = tmp_path / "network.nc"
    PyPSAService.export_network_to_netcdf(n, nc)
    return _bundle_from(tmp_path)


def test_a_non_finite_part_is_a_422_with_no_row_and_no_folder(
    client, project_row, projects_root, tmp_path
):
    def inf_part(n):
        n.storage_units.at["bess", "inv_energy_overnight"] = float("inf")

    r = _post(client, _priced_bundle(tmp_path, inf_part), "pc_inf_dst")
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "invalid_edit_value"
    assert _left_behind(project_row, projects_root, "pc_inf_dst") == []


# ── I5: old snapshots and Q-A: gridspine/run/ in snapshots ──────────────────


def test_an_old_snapshot_without_the_new_entries_leaves_the_live_copies(
    client, api_project, project_storage_dir
):
    name = api_project("pc-oldsnap")
    d = project_storage_dir(name)
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"})
    assert r.status_code == 200, r.text
    snap = d / "snapshots" / r.json()["id"]
    # Rewrite the capture as an older release made it: no marker, no gridspine/.
    m = json.loads((snap / "capture_manifest.json").read_text())
    m["tracked_files"] = [
        f for f in m["tracked_files"] if f not in ("eh_template.json", "cases.json")
    ]
    m["tracked_dirs"] = [x for x in m["tracked_dirs"] if x != "gridspine"]
    (snap / "capture_manifest.json").write_text(json.dumps(m))
    (d / "eh_template.json").write_text('{"later": true}')
    (d / "gridspine").mkdir()
    (d / "gridspine" / "config.json").write_text("{}")
    r = client.post(f"/api/projects/{name}/snapshots/{snap.name}/restore")
    assert r.status_code == 200, r.text
    assert (d / "eh_template.json").read_text() == '{"later": true}'
    assert (d / "gridspine" / "config.json").exists()


def test_a_snapshot_carries_gridspine_without_run_and_its_restore_keeps_the_live_run(
    client, api_project, project_storage_dir
):
    name = api_project("pc-gsnap")
    d = project_storage_dir(name)
    (d / "gridspine" / "run").mkdir(parents=True)
    (d / "gridspine" / "config.json").write_text('{"hours": 1}')
    (d / "gridspine" / "run" / "manifest.json").write_text('{"v": 1}')
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"})
    assert r.status_code == 200, r.text
    snap = d / "snapshots" / r.json()["id"]
    assert (snap / "gridspine" / "config.json").exists()
    assert not (snap / "gridspine" / "run").exists()

    (d / "gridspine" / "config.json").write_text('{"hours": 2}')
    (d / "gridspine" / "run" / "manifest.json").write_text('{"v": 2}')
    r = client.post(f"/api/projects/{name}/snapshots/{snap.name}/restore")
    assert r.status_code == 200, r.text
    assert (d / "gridspine" / "config.json").read_text() == '{"hours": 1}'
    assert (d / "gridspine" / "run" / "manifest.json").read_text() == '{"v": 2}'


def test_replace_dir_keeping_empties_all_but_the_kept(tmp_path):
    live = tmp_path / "gridspine"
    (live / "run").mkdir(parents=True)
    (live / "run" / "x").write_text("x")
    (live / "config.json").write_text("{}")
    (live / "sub").mkdir()
    projects._replace_dir_keeping(None, live, ("run",))
    assert sorted(p.name for p in live.iterdir()) == ["run"]
