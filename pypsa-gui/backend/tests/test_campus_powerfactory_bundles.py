"""The PowerFactory (DGS) hand-off of a campus run (grid-compliance 09, item 6; GC-05 output).

The run writes one DGS bundle per island critical hour beside the PSS/E bundles,
lists them with what each could not carry, and serves each as a zip picked from
the listing.
"""

import io
import json
import zipfile

import pytest
from fastapi import HTTPException

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
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_the_run_writes_dgs_bundles_equal_to_a_direct_call_and_lists_them(
    island_run, tmp_path
):
    import shutil

    from gridspine.drivers import campus_study as cs

    project, run = island_run
    listing = ce.powerfactory_bundles(project)
    assert (
        listing["has_powerfactory"]
        and listing["error"] is None
        and not listing["stale"]
    )
    assert listing["bundles"], "the island campus has island hours"
    for b in listing["bundles"]:
        names = {f["name"] for f in b["files"]}
        assert any(n.endswith(".dgs") for n in names) and "manifest.json" in names
        assert isinstance(b["not_written"], int) and isinstance(
            b["pending_verification"], int
        )
    direct = tmp_path / "direct"
    shutil.copytree(run, direct, ignore=shutil.ignore_patterns(cs.POWERFACTORY_DIR))
    cs.powerfactory_campus(direct, cs.island_hours(direct))
    assert _tree(run / cs.POWERFACTORY_DIR) == _tree(direct / cs.POWERFACTORY_DIR)


def test_the_zip_holds_every_file_of_the_bundle(island_run):
    project, run = island_run
    name = ce.powerfactory_bundles(project)["bundles"][0]["name"]
    data, stale = ce.powerfactory_bundle_zip(project, name)
    assert stale is False
    bundle = run / ce.cs.POWERFACTORY_DIR / name
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert {n: zf.read(n) for n in zf.namelist()} == {
            f"{name}/{k}": v for k, v in _tree(bundle).items()
        }


def test_the_psse_zip_is_unchanged_by_the_shared_helpers(island_run):
    project, run = island_run
    name = ce.psse_bundles(project)["bundles"][0]["name"]
    data, _ = ce.psse_bundle_zip(project, name)
    bundle = run / ce.cs.PSSE_DIR / name
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert {n: zf.read(n) for n in zf.namelist()} == {
            f"{name}/{k}": v for k, v in _tree(bundle).items()
        }


def test_a_bundle_with_values_not_carried_says_how_many(island_run):
    project, run = island_run
    name = ce.powerfactory_bundles(project)["bundles"][0]["name"]
    manifest = run / ce.cs.POWERFACTORY_DIR / name / "manifest.json"
    m = json.loads(manifest.read_text())
    manifest.write_text(
        json.dumps(
            {
                **m,
                "not_written": [{"element": "a"}, {"element": "b"}, {"element": "c"}],
                "pending_verification": [{"element": "d"}],
            }
        )
    )
    b = next(
        b for b in ce.powerfactory_bundles(project)["bundles"] if b["name"] == name
    )
    assert (b["not_written"], b["pending_verification"]) == (3, 1)
    assert [i["element"] for i in b["not_written_items"]] == ["a", "b", "c"]
    assert len(b["pending_items"]) == 1
    manifest.write_text(
        json.dumps(
            {
                **m,
                "not_written": [
                    {
                        "pp_table": "sgen",
                        "pp_name": "PV 1",
                        "dgs_name": "pv_1",
                        "attribute": "q_mvar",
                        "value": None,
                        "reason": "no verified attribute",
                    }
                ],
                "pending_verification": [],
            }
        )
    )
    b = next(
        b for b in ce.powerfactory_bundles(project)["bundles"] if b["name"] == name
    )
    assert b["not_written_items"] == [
        {"element": "PV 1", "attribute": "q_mvar", "reason": "no verified attribute"}
    ]
    assert (b["not_written"], b["pending_verification"], b["pending_items"]) == (
        1,
        0,
        [],
    )
    manifest.write_text(
        json.dumps(
            {
                **m,
                "not_written": [
                    {"pp_name": None, "dgs_name": "shunt_3", "attribute": "p_mw"},
                    {"attribute": "q_mvar"},
                ],
            }
        )
    )
    b = next(
        b for b in ce.powerfactory_bundles(project)["bundles"] if b["name"] == name
    )
    assert [i["element"] for i in b["not_written_items"]] == ["shunt_3", ""]
    for text in (
        "{not json",
        json.dumps([m]),
    ):  # unreadable, or JSON that is not an object
        manifest.write_text(text)
        b = next(
            b for b in ce.powerfactory_bundles(project)["bundles"] if b["name"] == name
        )
        assert (b["not_written"], b["pending_verification"]) == (None, None), text
    assert (b["not_written_items"], b["pending_items"]) == ([], [])


def test_a_failed_step_lists_nothing_and_leaves_no_bundle(hub, monkeypatch):
    _sidecar(hub)
    _island_campus(hub)

    def half_then_fail(run_dir, hours=None):
        (run_dir / ce.cs.POWERFACTORY_DIR / "x_p0_h0").mkdir(parents=True)
        raise ContractError("no converter spectrum table")

    monkeypatch.setattr(ce.cs, "powerfactory_campus", half_then_fail)
    run = _run(hub)
    listing = ce.powerfactory_bundles(hub)
    assert (
        listing["error"] == "no converter spectrum table" and listing["bundles"] == []
    )
    assert not (run / ce.cs.POWERFACTORY_DIR).exists()
    assert ce.psse_bundles(hub)["bundles"], "the PSS/E step and the study go on"
    with pytest.raises(HTTPException) as exc:
        ce.powerfactory_bundle_zip(hub, "x_p0_h0")
    assert exc.value.status_code == 404


def test_any_other_error_fails_the_study(hub, monkeypatch):
    _sidecar(hub)
    _island_campus(hub)
    monkeypatch.setattr(
        ce.cs,
        "powerfactory_campus",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")),
    )
    with pytest.raises(RuntimeError):
        _run(hub)


def test_a_run_from_before_the_step_has_no_powerfactory_and_no_error(island_run):
    project, run = island_run
    (run / ce.POWERFACTORY_STATUS_FILE).unlink()
    listing = ce.powerfactory_bundles(project)
    assert (
        listing["has_run"]
        and listing["has_powerfactory"] is False
        and listing["bundles"] == []
    )
    assert listing["error"] is None


def test_the_psse_listing_still_raises_on_an_unreadable_step_record(island_run):
    """Only the PowerFactory listing turns an unreadable record into an error
    line; the PSS/E listing keeps its behaviour (D1)."""
    project, run = island_run
    (run / ce.PSSE_STATUS_FILE).write_text("{not json")
    with pytest.raises(ValueError):
        ce.psse_bundles(project)


@pytest.mark.parametrize("text", ["{not json", "[1, 2]"])
def test_an_unreadable_step_record_lists_an_error_and_no_bundle(island_run, text):
    project, run = island_run
    (run / ce.POWERFACTORY_STATUS_FILE).write_text(text)
    listing = ce.powerfactory_bundles(project)
    assert listing["has_powerfactory"] and listing["bundles"] == []
    assert listing["error"] == "the record of the PowerFactory step cannot be read"
    with pytest.raises(HTTPException) as exc:
        ce.powerfactory_bundle_zip(
            project, next(p.name for p in (run / ce.cs.POWERFACTORY_DIR).iterdir())
        )
    assert exc.value.status_code == 404


def test_no_run_lists_nothing(hub):
    listing = ce.powerfactory_bundles(hub)
    assert (
        listing["has_run"] is False
        and listing["has_powerfactory"] is False
        and listing["bundles"] == []
    )


def test_a_run_without_island_hours_says_so(hub):
    ce.draft(hub)
    ce.run(hub, {"k": 1, "invest": False})
    listing = ce.powerfactory_bundles(hub)
    assert listing["skipped"] == ce.PSSE_NO_HOURS and listing["bundles"] == []


def test_a_lookup_outside_the_listing_is_404(island_run, tmp_path):
    project, run = island_run
    root = run / ce.cs.POWERFACTORY_DIR
    (root / "stray.txt").write_text("x")
    (root / "linked_p0_h0").symlink_to(tmp_path)
    for name in ("../psse", "stray.txt", "linked_p0_h0", "nope", ""):
        with pytest.raises(HTTPException) as exc:
            ce.powerfactory_bundle_zip(project, name)
        assert exc.value.status_code == 404, name
    assert "linked_p0_h0" not in {
        b["name"] for b in ce.powerfactory_bundles(project)["bundles"]
    }
