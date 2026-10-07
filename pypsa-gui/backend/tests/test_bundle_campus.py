"""The campus electrical study travels with the project bundle (plan C9).

C6 left "the bundle does not carry the campus files" as a gap. The bundle now
carries the campus description, the asset library (the project's copy and the
one the last run used) and what the run bought: the investment, its cost, the
compliance re-checked with the assets and who owns the PCC switchgear. Each
only when it exists, so an older bundle still imports.

NOT carried: the grid-code documents and drafts (a licensed text stays with
its licensee) and the engine's other run files (they are rebuilt by a run).
"""
import io
import zipfile

from routers.projects import _BUNDLE_CAMPUS_FILES
from services import campus_electrical_service as ce

CAMPUS_FILES = {
    "campus_electrical/campus_input.yaml": "campus:\n  pcc: {bus: GRID}\n",
    "campus_electrical/campus_assets.yaml": "discount_rate: {value: 0.05, source: assumed}\n",
    "campus_electrical/run/campus_assets_used.yaml": "discount_rate: {value: 0.05, source: assumed}\n",
    "campus_electrical/run/campus_investment.csv": "need,status\ntransformer T,chosen\n",
    "campus_electrical/run/campus_cost.csv": "period,capex_eur\n2030,1\n",
    "campus_electrical/run/campus_compliance_invested.csv": "check,status_with_measures\npcc_reactive,pass\n",
    "campus_electrical/run/campus_invest_scope.json": '{"pcc_switchgear": "campus"}',
}
NOT_CARRIED = {
    "campus_electrical/settings.json": "{}",
    "campus_electrical/run/campus_hourly.csv": "a,b\n1,2\n",
    "campus_electrical/grid_codes/documents/x.pdf": "%PDF-1.7",
}


def _write(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _export(client, name):
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200
    return r.content


def test_the_tuple_names_the_services_files():
    """The literals are the names the service and the engine write."""
    campus_dir = "campus_electrical"
    assert f"{campus_dir}/{ce.CAMPUS_FILE}" in _BUNDLE_CAMPUS_FILES
    assert f"{campus_dir}/{ce.LIBRARY_FILE}" in _BUNDLE_CAMPUS_FILES
    assert f"{campus_dir}/run/{ce.USED_LIBRARY_FILE}" in _BUNDLE_CAMPUS_FILES
    for const in (ce.cs.INVESTMENT_CSV, ce.cs.COST_CSV, ce.cs.COMPLIANCE_INVESTED_CSV, ce.cs.INVEST_SCOPE_JSON):
        assert f"{campus_dir}/run/{const}" in _BUNDLE_CAMPUS_FILES, const
    assert set(_BUNDLE_CAMPUS_FILES) == set(CAMPUS_FILES)


def test_the_export_carries_the_campus_files_that_exist_and_nothing_else_of_the_study(
        client, api_project, project_storage_dir):
    name = api_project("campus_bundle")
    root = project_storage_dir(name)
    _write(root, {**CAMPUS_FILES, **NOT_CARRIED})
    names = set(zipfile.ZipFile(io.BytesIO(_export(client, name))).namelist())
    assert set(CAMPUS_FILES) <= names, sorted(set(CAMPUS_FILES) - names)
    assert not set(NOT_CARRIED) & names


def test_a_project_without_a_campus_study_exports_none_of_it(client, api_project, project_storage_dir):
    name = api_project("no_campus")
    names = set(zipfile.ZipFile(io.BytesIO(_export(client, name))).namelist())
    assert not [n for n in names if n.startswith("campus_electrical")]


def test_a_partial_study_exports_only_what_exists(client, api_project, project_storage_dir):
    name = api_project("half_campus")
    _write(project_storage_dir(name), {k: v for k, v in CAMPUS_FILES.items() if k.endswith("campus_input.yaml")})
    names = {n for n in zipfile.ZipFile(io.BytesIO(_export(client, name))).namelist() if n.startswith("campus_")}
    assert names == {"campus_electrical/campus_input.yaml"}


def test_the_import_restores_them_byte_equal_and_the_library_is_the_projects_copy(
        client, api_project, project_storage_dir):
    name = api_project("campus_src")
    _write(project_storage_dir(name), CAMPUS_FILES)
    r = client.post("/api/projects/import_bundle?name=campus_dst",
                    files={"file": ("c.pypsaproj.zip", _export(client, name), "application/zip")})
    assert r.status_code in (200, 201), r.text
    dst = project_storage_dir("campus_dst")
    for rel, text in CAMPUS_FILES.items():
        assert (dst / rel).read_text() == text, rel
    assert not (dst / "campus_electrical/settings.json").exists()


def test_an_older_bundle_without_the_campus_files_still_imports(client, api_project, project_storage_dir):
    name = api_project("campus_old")
    src = zipfile.ZipFile(io.BytesIO(_export(client, name)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for m in src.namelist():
            zf.writestr(m, src.read(m))
    r = client.post("/api/projects/import_bundle?name=campus_old_dst",
                    files={"file": ("old.pypsaproj.zip", buf.getvalue(), "application/zip")})
    assert r.status_code in (200, 201), r.text
    assert not (project_storage_dir("campus_old_dst") / "campus_electrical").exists()
