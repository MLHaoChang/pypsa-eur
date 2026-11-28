"""Grid-compliance 29: the campus run's harmonic emission screen (``campus_study.harmonics_campus``) and the
field measurement of the background harmonics at the PCC it takes (GK A2 D4 to D7, G1 to G4)."""

import json
import math
import os
import re
import threading

import pypsa
import pytest
import yaml
from fastapi import HTTPException

from services import campus_electrical_service as ce
from services import project_registry
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401 - fixtures

CSV = b"order,u_h_pct_p95\n3,0.9\n5,2.0\n7,1.1\n11,0.6\n"
SETTINGS = {"k": 1, "invest": False, "profile": "generic_assumed"}


def _with_units(project):
    """Two drives on the hub's MV bus, each a harmonic source."""
    nc = project_registry.project_dir(project) / ce.NETWORK_FILE
    n = pypsa.Network(str(nc))
    n.add("Bus", "nh3", carrier="NH3")
    for name in ("compressor", "pump"):
        n.add("Link", name, bus0="mv", bus1="nh3", p_nom=5.0)
    n.links["eh_process"] = [False, True, True]
    n.links["rated_mva"] = [math.nan, 15.0, 5.0]
    n.links["harmonic_source"] = ["", "6-pulse", "12-pulse"]
    n.export_to_netcdf(str(nc))


def _run(project):
    return ce.run(project, SETTINGS)["results"]["harmonic_emission"]


def _put(
    project,
    data=CSV,
    source="Utility measurement",
    measured_on="2026-10-03",
    filename="bg_pcc.csv",
):
    return ce.put_background(project, data, source, measured_on, filename)


def _pcc(project) -> str:
    return str(
        yaml.safe_load((ce.campus_dir(project) / ce.CAMPUS_FILE).read_text())["campus"][
            "pcc"
        ]["bus"]
    )


def _poc_rows(emission):
    return [r for r in emission["rows"] if r["level"] == "poc"]


def _with_bg(emission):
    return [
        r for r in _poc_rows(emission) if str(r["label"]).startswith("with background")
    ]


@pytest.fixture
def drafted(hub):  # noqa: F811
    _with_units(hub)
    ce.draft(hub)
    return hub


# ── the screen in the run ────────────────────────────────────────────────────


def test_a_run_without_a_background_screens_and_says_it_had_none(drafted):
    emission = _run(drafted)
    assert emission["background"] == {"status": "none"}
    assert {r["unit"] for r in emission["rows"] if r["level"] == "unit"} == {
        "compressor",
        "pump",
    }
    assert len(_poc_rows(emission)) == 1 and _with_bg(emission) == []
    assert emission["label"]


def test_a_stored_background_gives_the_with_background_row(drafted):
    _put(drafted)
    emission = _run(drafted)
    assert emission["background"]["status"] == "used"
    assert "Utility measurement" in emission["background"]["citation"]
    (row,) = _with_bg(emission)
    assert row["verdict"] in ("pass", "fail", "unknown") and row["u_h_pct"] is not None
    assert ce.get_background(drafted)["in_latest_run"] is True
    assert ce.get_state(drafted)["stale"] is False


def _rename_pcc(project, new="PCC_NEW"):
    """The campus PCC bus renamed in the saved campus file, everywhere it is named."""
    old = _pcc(project)
    path = ce.campus_dir(project) / ce.CAMPUS_FILE
    ce.save_campus(
        project, re.sub(rf"(?<!\w){re.escape(old)}(?!\w)", new, path.read_text())
    )
    assert _pcc(project) == new
    return old, new


def test_a_pcc_renamed_after_upload_reads_unknown_with_the_reason_d5(drafted):
    _put(drafted)
    old, new = _rename_pcc(drafted)
    emission = _run(drafted)
    assert emission["background"]["status"] == "unusable"
    assert (
        old in emission["background"]["reason"]
        and new in emission["background"]["reason"]
    )
    (row,) = _with_bg(emission)
    assert (
        row["verdict"] == "unknown"
        and row["u_h_pct"] is None
        and row["thd_pct"] is None
    )
    assert old in row["note"] and "Background not used" in row["note"]
    assert not (ce.campus_dir(drafted) / "run" / "background_harmonics.json").exists()
    assert ce.get_background(drafted)["in_latest_run"] is False


def test_bytes_that_no_longer_parse_read_unknown_g1(drafted, monkeypatch):
    _put(drafted)

    def refuse(*a, **kw):
        raise ce.ContractError("Background harmonics: test refusal")

    monkeypatch.setattr(ce.cs, "import_background_campus", refuse)
    emission = _run(drafted)
    assert (
        emission["background"]["status"] == "unusable"
        and "test refusal" in emission["background"]["reason"]
    )
    (row,) = _with_bg(emission)
    assert row["verdict"] == "unknown"


def test_the_unknown_row_has_the_columns_of_a_real_with_background_row_g2(drafted):
    _put(drafted)
    (real,) = _with_bg(_run(drafted))
    _rename_pcc(drafted)
    (unknown,) = _with_bg(_run(drafted))
    assert set(unknown) == set(real)
    assert unknown["label"] == real["label"]
    assert unknown["thd_limit_pct"] == real["thd_limit_pct"]


def test_a_screen_failure_never_fails_the_run_and_leaves_no_rows_g3(
    drafted, monkeypatch
):
    real = ce.cs.harmonics_campus

    def broken(run_dir, *a, **kw):
        real(run_dir, *a, **kw)  # it wrote its CSV, then fails: the CSV may not stay
        raise ce.ContractError("harmonics: test failure")

    monkeypatch.setattr(ce.cs, "harmonics_campus", broken)
    _put(
        drafted
    )  # taken by the run, but the screen fails: not "used by the latest run"
    results = ce.run(drafted, SETTINGS)["results"]
    assert results["harmonic_emission"] == {"error": "harmonics: test failure"}
    run = ce.campus_dir(drafted) / "run"
    for name in (ce.cs.HARMONICS_CSV, ce.HARMONIC_EMISSION_META):
        assert not (run / name).exists()
    assert results["compliance"]
    assert ce.get_background(drafted)["in_latest_run"] is False
    monkeypatch.setattr(ce.cs, "harmonics_campus", real)
    again = _run(drafted)  # the next run clears the earlier error
    assert "error" not in again and not (run / ce.HARMONIC_EMISSION_ERROR).exists()


def test_a_run_from_before_the_stage_has_no_emission_screen(drafted):
    _run(drafted)
    run = ce.campus_dir(drafted) / "run"
    for name in (
        ce.HARMONIC_EMISSION_META,
        ce.cs.HARMONICS_CSV,
        ce.BACKGROUND_USED_FILE,
    ):
        (run / name).unlink()
    state = ce.get_state(drafted)
    assert state["results"]["harmonic_emission"] is None and state["stale"] is False


# ── the stale pin (D6) ───────────────────────────────────────────────────────


def test_background_used_is_written_when_there_is_none_g4(drafted):
    _run(drafted)
    used = json.loads(
        (ce.campus_dir(drafted) / "run" / ce.BACKGROUND_USED_FILE).read_text()
    )
    assert used == {"status": "none", "record_sha256": "none"}
    assert ce.get_state(drafted)["stale"] is False  # none taken, none stored
    _put(drafted)
    assert ce.get_state(drafted)["stale"] is True  # a measurement added after the run


@pytest.mark.parametrize(
    "change",
    [
        {"source": "Another survey"},
        {"measured_on": "2026-10-04"},
        {"filename": "other.csv"},
        {"data": CSV + b"13,0.2\n"},
    ],
)
def test_any_change_to_the_record_marks_the_run_stale_d6(drafted, change):
    _put(drafted)
    _run(drafted)
    assert ce.get_state(drafted)["stale"] is False
    assert ce.get_background(drafted)["in_latest_run"] is True
    _put(drafted, **change)
    assert ce.get_background(drafted)["in_latest_run"] is False
    assert ce.get_state(drafted)["stale"] is True


def test_removal_marks_the_run_stale_and_the_same_record_again_does_not_d6(drafted):
    _put(drafted)
    _run(drafted)
    ce.delete_background(drafted)
    assert ce.get_state(drafted)["stale"] is True
    _put(drafted)  # the identical record: only uploaded_at differs, which is not pinned
    assert ce.get_state(drafted)["stale"] is False


def test_an_unreadable_store_breaks_the_pin(drafted):
    _put(drafted)
    _run(drafted)
    (ce.campus_dir(drafted) / ce.BACKGROUND_DIR / ce.BACKGROUND_DATA).write_bytes(
        b"order,u_h_pct_p95\n3,9\n"
    )
    assert ce.get_state(drafted)["stale"] is True
    with pytest.raises(HTTPException) as e:
        ce.get_background(drafted)
    assert (
        e.value.status_code == 409
        and e.value.detail["error_kind"] == "campus_background_unreadable"
    )
    assert (
        e.value.detail["message"]
        == "The stored measurement cannot be read: remove it and upload it again."
    )


# ── the upload (D4) ──────────────────────────────────────────────────────────


def test_the_bytes_are_stored_under_a_fixed_name_and_the_filename_is_metadata(drafted):
    got = _put(drafted, filename="../../evil name.csv")
    d = ce.campus_dir(drafted) / ce.BACKGROUND_DIR
    assert sorted(p.name for p in d.iterdir()) == sorted(
        [ce.BACKGROUND_META, ce.BACKGROUND_DATA]
    )
    assert (d / ce.BACKGROUND_DATA).read_bytes() == CSV
    assert got["filename"] == "evil name.csv" and got["poc"] == _pcc(drafted)
    assert got == {**ce.get_background(drafted)}
    assert (
        got["has_background"] and got["orders"] == 4 and got["in_latest_run"] is False
    )


def _refused(project, status, kind, field, **kw):
    with pytest.raises(HTTPException) as e:
        _put(project, **kw)
    assert e.value.status_code == status, e.value.detail
    assert e.value.detail["error_kind"] == kind and e.value.detail.get("field") == field
    assert not (
        ce.campus_dir(project) / ce.BACKGROUND_DIR / ce.BACKGROUND_META
    ).exists()
    return e.value.detail["message"]


def test_the_size_cap_is_named_and_refused_with_413(drafted):
    pad = b"x" * (1024 * 1024 - len(CSV) + 1)  # the literal cap: 1 MiB
    msg = _refused(drafted, 413, "campus_background_too_large", "file", data=CSV + pad)
    assert msg == "File: larger than 1 MiB"


@pytest.mark.parametrize(
    "kw,field",
    [
        ({"source": "   "}, "source"),
        ({"source": "s" * 201}, "source"),
        ({"measured_on": "03.10.2026"}, "measured_on"),
        ({"measured_on": ""}, "measured_on"),
        ({"filename": "f" * 201}, "filename"),
        ({"data": b"order,u_h_pct_p95\n5,-0.1\n"}, "file"),
        ({"data": b"not,a,measurement\n"}, "file"),
    ],
)
def test_bad_input_is_a_422_that_names_the_field(drafted, kw, field):
    msg = _refused(drafted, 422, "campus_background_invalid", field, **kw)
    assert msg.split(":")[0] in ("Source", "Measured on", "File")


def test_no_campus_yet_is_a_422(hub):  # noqa: F811
    with pytest.raises(HTTPException) as e:
        _put(hub)
    assert (
        e.value.status_code == 422
        and e.value.detail["error_kind"] == "campus_background_no_campus"
    )
    assert "draft the campus" in e.value.detail["message"]


def test_a_failed_record_write_leaves_the_earlier_pair_g4(drafted, monkeypatch):
    _put(drafted)
    real = os.replace

    def fail_meta(src, dst):
        if str(dst).endswith(ce.BACKGROUND_META):
            raise OSError("disk full")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", fail_meta)
    with pytest.raises(OSError):
        _put(drafted, data=CSV + b"13,0.2\n", source="Another survey")
    monkeypatch.setattr(os, "replace", real)
    d = ce.campus_dir(drafted) / ce.BACKGROUND_DIR
    assert (d / ce.BACKGROUND_DATA).read_bytes() == CSV
    assert ce.get_background(drafted)["source"] == "Utility measurement"
    assert not [p for p in d.iterdir() if p.name.endswith(".tmp")]


def test_a_failed_first_record_write_leaves_nothing_g4(drafted, monkeypatch):
    real = os.replace

    def fail_meta(src, dst):
        if str(dst).endswith(ce.BACKGROUND_META):
            raise OSError("disk full")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", fail_meta)
    with pytest.raises(OSError):
        _put(drafted)
    monkeypatch.setattr(os, "replace", real)
    d = ce.campus_dir(drafted) / ce.BACKGROUND_DIR
    assert not (d / ce.BACKGROUND_DATA).exists() and ce.get_background(drafted) == {
        "has_background": False
    }


def test_put_and_delete_take_the_same_lock(drafted, monkeypatch):
    """While a PUT holds the store's lock, a DELETE waits for it: the store ends as the whole pair or none."""
    d = ce.campus_dir(drafted) / ce.BACKGROUND_DIR
    entered, release = threading.Event(), threading.Event()
    real_poc = ce._campus_poc

    def held(project):
        entered.set()
        release.wait(5)
        return real_poc(project)

    monkeypatch.setattr(ce, "_campus_poc", held)
    put = threading.Thread(target=_put, args=(drafted,))
    put.start()
    assert entered.wait(5)
    deleted = threading.Thread(target=ce.delete_background, args=(drafted,))
    deleted.start()
    deleted.join(0.2)
    assert deleted.is_alive()  # blocked on the lock the PUT holds
    release.set()
    put.join(5)
    deleted.join(5)
    assert (
        not (d / ce.BACKGROUND_META).exists() and not (d / ce.BACKGROUND_DATA).exists()
    )


def test_exactly_at_the_caps_is_not_refused_for_size(drafted):
    pad = b"x" * (
        1024 * 1024 - len(CSV)
    )  # 1 MiB exactly: past the size check, refused by the parse
    _refused(drafted, 422, "campus_background_invalid", "file", data=CSV + pad)
    assert (
        _put(drafted, source="s" * 200, filename="f" * 196 + ".csv")["source"]
        == "s" * 200
    )


def test_the_unknown_row_keeps_the_screen_note(drafted):
    _put(drafted)
    _rename_pcc(drafted)
    emission = _run(drafted)
    plant = next(
        r
        for r in _poc_rows(emission)
        if not str(r["label"]).startswith("with background")
    )
    (unknown,) = _with_bg(emission)
    assert unknown["note"].endswith(plant["note"])


def test_a_record_that_is_not_an_object_reads_unreadable(drafted):
    _put(drafted)
    (ce.campus_dir(drafted) / ce.BACKGROUND_DIR / ce.BACKGROUND_META).write_text("[]")
    with pytest.raises(HTTPException) as e:
        ce.get_background(drafted)
    assert (
        e.value.status_code == 409
        and e.value.detail["error_kind"] == "campus_background_unreadable"
    )
    assert (
        e.value.detail["message"]
        == "The stored measurement cannot be read: remove it and upload it again."
    )


def test_a_campus_that_names_no_pcc_bus_is_a_422(drafted):
    path = ce.campus_dir(drafted) / ce.CAMPUS_FILE
    spec = yaml.safe_load(path.read_text())
    del spec["campus"]["pcc"]["bus"]
    path.write_text(yaml.safe_dump(spec))
    with pytest.raises(HTTPException) as e:
        _put(drafted)
    assert (
        e.value.status_code == 422
        and e.value.detail["error_kind"] == "campus_background_no_campus"
    )
    assert "PCC bus" in e.value.detail["message"]


def test_an_upload_without_a_filename_is_stored(drafted):
    got = _put(drafted, filename=None)
    assert got["has_background"] and got["filename"] is None


def test_an_error_without_a_message_is_named_by_its_type(drafted, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError()

    monkeypatch.setattr(ce.cs, "harmonics_campus", broken)
    assert ce.run(drafted, SETTINGS)["results"]["harmonic_emission"] == {
        "error": "RuntimeError"
    }


def test_a_store_unreadable_at_run_time_reads_unknown_and_stale(drafted):
    _put(drafted)
    (ce.campus_dir(drafted) / ce.BACKGROUND_DIR / ce.BACKGROUND_DATA).write_bytes(
        b"order,u_h_pct_p95\n3,9\n"
    )
    emission = _run(drafted)
    assert emission["background"]["status"] == "unusable"
    assert "cannot be read" in emission["background"]["reason"]
    assert (
        str(ce.campus_dir(drafted)) not in emission["background"]["reason"]
    )  # no path reaches the user
    (row,) = _with_bg(emission)
    assert row["verdict"] == "unknown" and "cannot be read" in row["note"]
    used = json.loads(
        (ce.campus_dir(drafted) / "run" / ce.BACKGROUND_USED_FILE).read_text()
    )
    assert used["record_sha256"] == "unreadable"
    assert ce.get_state(drafted)["stale"] is True


def test_the_view_says_when_it_was_uploaded(drafted):
    got = _put(drafted)
    assert got["uploaded_at"].startswith("20") and got["measured_on"] == "2026-10-03"
    assert ce.get_background(drafted)["uploaded_at"] == got["uploaded_at"]


def test_the_bundled_files_carry_the_background_to_a_copy_d5(drafted, user_and_db):  # noqa: F811
    """GK A2 on #2132: the files a bundle carries (``_BUNDLE_CAMPUS_FILES``) take the measurement with them,
    so a copy's next run screens with it instead of silently without."""
    import shutil

    from routers.projects import _BUNDLE_CAMPUS_FILES

    _put(drafted)
    db, user = user_and_db
    copy = project_registry.create_root(db, user, "Campus Hub Copy")
    src, dst = (
        project_registry.project_dir(drafted),
        project_registry.ensure_project_dir(copy),
    )
    shutil.copy(src / ce.NETWORK_FILE, dst / ce.NETWORK_FILE)
    for rel in _BUNDLE_CAMPUS_FILES:
        if (src / rel).is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src / rel, dst / rel)
    emission = _run(copy)
    assert emission["background"]["status"] == "used"
    (row,) = _with_bg(emission)
    assert row["u_h_pct"] is not None
    assert ce.get_background(copy)["source"] == "Utility measurement"
