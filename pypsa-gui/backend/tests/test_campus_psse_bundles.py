"""
Grid-compliance 23: the campus Run writes the PSS/E hand-off bundles and the app serves them.

The ``psse`` step runs ``cs.psse_campus`` inside the cuts stage and records its outcome in
``psse_status.json``; ``GET /psse`` lists the latest run's bundles, ``GET /psse/<bundle>.zip``
serves one. The study's other outputs are the same with and without the step.
"""
import io
import json
import zipfile

import pytest
from fastapi import HTTPException

from gridspine.ingest.psse_raw_parse import read_raw_text
from gridspine.schema.contracts import ContractError
from services import campus_electrical_service as ce
from tests import test_campus_electrical_service as _ces
from tests.test_campus_island_hours_run import _island_campus, _run
from tests.test_campus_island_tables import _sidecar

hub, user_and_db = _ces.hub, _ces.user_and_db


@pytest.fixture
def island_run(hub):
    _sidecar(hub)
    _island_campus(hub)
    return hub, _run(hub)


def _tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_run_writes_bundles_and_lists_them(island_run):
    project, run = island_run
    listing = ce.psse_bundles(project)
    assert listing["has_run"] and not listing["stale"] and listing["error"] is None
    assert listing["bundles"], "the island campus has island hours"
    for b in listing["bundles"]:
        assert b["name"].endswith(f"p{b['period']}_h{b['hour']}")
        names = {f["name"] for f in b["files"]}
        assert any(n.endswith(".raw") for n in names) and any(n.endswith(".dyr") for n in names)
        assert b["size"] == sum(f["size"] for f in b["files"]) > 0
    assert json.loads((run / ce.PSSE_STATUS_FILE).read_text())["bundles"] == [b["name"] for b in listing["bundles"]]


def test_the_zip_holds_every_file_on_disk_and_the_raw_reads_back(island_run):
    project, run = island_run
    name = ce.psse_bundles(project)["bundles"][0]["name"]
    data, stale = ce.psse_bundle_zip(project, name)
    assert stale is False
    zf = zipfile.ZipFile(io.BytesIO(data))
    on_disk = _tree(run / "psse" / name)
    assert sorted(zf.namelist()) == sorted(f"{name}/{n}" for n in on_disk)
    for n, content in on_disk.items():
        assert zf.read(f"{name}/{n}") == content
    raw = next(n for n in zf.namelist() if n.endswith(".raw"))
    case = read_raw_text(zf.read(raw).decode())
    assert case.tables["bus"] is not None and len(case.tables["bus"]) > 0


def test_the_bundles_equal_a_direct_psse_campus_call(island_run, tmp_path):
    from gridspine.drivers import campus_study as cs

    project, run = island_run
    mine = _tree(run / "psse")
    cs.psse_campus(run, cs.island_hours(run))           # the engine writes it again on the same run
    assert _tree(run / "psse") == mine


def test_the_step_changes_nothing_the_other_stages_read(hub, monkeypatch):
    _sidecar(hub)
    _island_campus(hub)
    with_step = _tree(_run(hub))
    monkeypatch.setattr(ce, "_psse_bundles", lambda run_dir: None)
    without = _tree(_run(hub))
    own = lambda n: n.startswith("psse/") or n == ce.PSSE_STATUS_FILE        # noqa: E731
    assert {k: v for k, v in with_step.items() if not own(k)} == without
    assert any(own(k) for k in with_step) and not any(own(k) for k in without)


def test_a_run_without_island_hours_says_so_and_writes_no_bundle(hub):
    ce.draft(hub)
    _run(hub)
    listing = ce.psse_bundles(hub)
    assert listing["bundles"] == [] and listing["skipped"] == ce.PSSE_NO_HOURS and listing["error"] is None


def test_no_run_means_no_bundles_and_no_stale_flag(hub):
    ce.draft(hub)
    assert ce.psse_bundles(hub) == {"has_run": False, "stale": False, "run_at": None, "bundles": [],
                                    "skipped": None, "error": None}


@pytest.mark.parametrize("exc", [ContractError("the campus has no GENSAL data"), OSError("disk full")])
def test_a_contract_or_file_error_is_recorded_and_the_study_goes_on(hub, monkeypatch, exc):
    _sidecar(hub)
    _island_campus(hub)

    def refuse(*a, **k):
        raise exc

    monkeypatch.setattr(ce.cs, "psse_campus", refuse)
    run = _run(hub)
    listing = ce.psse_bundles(hub)
    assert listing["bundles"] == [] and listing["error"] == str(exc)
    assert (run / "campus_compliance.csv").is_file()                 # sizing and the later stages still ran


def test_any_other_error_fails_the_study(hub, monkeypatch):
    _sidecar(hub)
    _island_campus(hub)

    def boom(*a, **k):
        raise RuntimeError("not a contract problem")

    monkeypatch.setattr(ce.cs, "psse_campus", boom)
    with pytest.raises(RuntimeError, match="not a contract problem"):
        _run(hub)


def test_a_bundle_of_an_older_run_is_listed_and_served_as_stale(island_run):
    project, _ = island_run
    # change the campus the pin reads: the run is now older than the file
    import yaml
    path = ce.campus_dir(project) / ce.CAMPUS_FILE
    spec = yaml.safe_load(path.read_text())
    spec["edited_after_run"] = True
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    listing = ce.psse_bundles(project)
    assert listing["stale"] is True
    assert ce.psse_bundle_zip(project, listing["bundles"][0]["name"])[1] is True


def test_a_lookup_outside_the_listing_is_refused(island_run, tmp_path):
    project, run = island_run
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x")
    (run / "psse" / "linked").symlink_to(outside, target_is_directory=True)     # a symlink out of psse/
    first = ce.psse_bundles(project)["bundles"][0]["name"]
    (run / "psse" / first / "link.raw").symlink_to(outside / "secret.txt")        # a file symlinked out
    for bad in ("..", "../run", str(outside), "/etc/passwd", "p9_h999", "linked", ""):
        with pytest.raises(HTTPException) as e:
            ce.psse_bundle_zip(project, bad)
        assert e.value.status_code == 404, bad
    names = zipfile.ZipFile(io.BytesIO(ce.psse_bundle_zip(project, first)[0])).namelist()
    assert not any("secret" in n or n.endswith("link.raw") for n in names)
