"""The shared import contract: report types and the importer registry."""

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from gridspine.ingest import import_result as ir
from gridspine.ingest.import_result import (
    Assumed,
    ImporterSpec,
    ImportFormatError,
    ImportReport,
    ImportResult,
    Suggestion,
    Unmapped,
)

ROOT = Path(__file__).resolve().parents[2]


def _report(**over):
    base = dict(
        source_format="psse",
        source_files=["case.raw", "case.dyr"],
        counts={"Buses": 3, "Lines": 2},
        unmapped=[
            Unmapped("Switched shunt 7", "no matching asset type yet"),
            Unmapped("FACTS device 2", "no matching asset type yet"),
        ],
        assumed=[
            Assumed("Line 1-2", "Thermal rating", 100.0, "RATEA was 0"),
            Assumed("Line 2-3", "Thermal rating", 80.0, "RATEA was 0"),
        ],
        warnings=["Area names missing"],
    )
    base.update(over)
    return ImportReport(**base)


def test_report_round_trips_through_a_plain_dict():
    report = _report()
    data = report.to_dict()
    assert data["unmapped"][0] == {
        "source_item": "Switched shunt 7",
        "reason": "no matching asset type yet",
    }
    assert data["assumed"][0]["parameter"] == "Thermal rating"
    assert ImportReport.from_dict(data) == report


def test_report_lists_are_copied_not_shared():
    files = ["a.raw"]
    report = _report(source_files=files)
    files.append("b.raw")
    assert report.source_files == ["a.raw"]


@pytest.mark.parametrize("bad", [-1, 1.5, True, "3"])
def test_counts_must_be_non_negative_ints(bad):
    with pytest.raises(ValueError, match="Buses"):
        _report(counts={"Buses": bad})


@pytest.mark.parametrize(
    "make",
    [
        lambda: _report(source_format=""),
        lambda: _report(counts={"": 1}),
        lambda: _report(source_files=[""]),
        lambda: _report(warnings=[""]),
        lambda: Unmapped("", "why"),
        lambda: Unmapped("Row 3", ""),
        lambda: Assumed("", "Rating", 1, "why"),
        lambda: Assumed("Line", "", 1, "why"),
        lambda: Assumed("Line", "Rating", 1, ""),
    ],
)
def test_empty_names_and_reasons_are_refused(make):
    with pytest.raises(ValueError):
        make()


def test_unmapped_and_assumed_entries_must_be_their_types():
    with pytest.raises(TypeError):
        _report(unmapped=[("Row 3", "why")])
    with pytest.raises(TypeError):
        _report(assumed=[{"asset": "x"}])


def test_import_result_carries_its_parts():
    report = _report()
    result = ImportResult(network=object(), sidecars={"costs.csv": b""}, report=report)
    assert result.report is report
    assert ImportResult(network=object(), report=report).sidecars == {}


def _suggestion(**over):
    base = dict(
        target="Line 1-2",
        parameter="Voltage",
        current=110000,
        proposed=110,
        why="Value looks like volts; other lines are in kV",
        source="other lines in the same file",
        confidence="high",
    )
    base.update(over)
    return Suggestion(**base)


def test_suggestions_round_trip_and_default_to_the_importer():
    s = _suggestion()
    assert s.by == "importer"
    report = _report(suggestions=[s, _suggestion(by="assistant", current=None)])
    assert ImportReport.from_dict(report.to_dict()) == report


@pytest.mark.parametrize(
    "over",
    [
        {"confidence": "certain"},
        {"by": "user"},
        {"source": ""},
        {"why": ""},
        {"target": ""},
        {"parameter": ""},
        {"proposed": float("nan")},
        {"current": float("inf")},
        {"proposed": object()},
    ],
)
def test_suggestion_refuses_bad_fields(over):
    with pytest.raises(ValueError):
        _suggestion(**over)


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), [1.0, float("-inf")], object()]
)
def test_assumed_values_must_be_json_ready_and_finite(bad):
    with pytest.raises(ValueError, match="Assumed.value"):
        Assumed("Line 1", "Rating", bad, "missing")


def test_report_dict_survives_strict_json():
    report = _report(suggestions=[_suggestion()])
    assert json.loads(json.dumps(report.to_dict(), allow_nan=False)) == report.to_dict()


def test_suggestions_must_be_suggestions():
    with pytest.raises(TypeError, match="Suggestion"):
        _report(suggestions=[{"target": "x"}])


def test_import_format_error_is_a_value_error():
    assert issubclass(ImportFormatError, ValueError)


def _spec(fid="psse", exts=(".raw", ".dyr"), package="fake_ingest"):
    return ImporterSpec(
        format_id=fid,
        display_name=f"{fid} file",
        extensions=exts,
        help="help",
        package=package,
    )


def test_spec_normalises_extensions_to_lowercase_with_a_dot():
    assert _spec(exts=("RAW", ".Dyr")).extensions == (".raw", ".dyr")


@pytest.mark.parametrize(
    "over",
    [
        {"format_id": "PSS/E"},
        {"format_id": ""},
        {"format_id": None},
        {"display_name": ""},
        {"extensions": ()},
        {"extensions": ("",)},
        {"help": ""},
    ],
)
def test_spec_refuses_bad_fields(over):
    fields = dict(
        format_id="psse", display_name="PSS/E case", extensions=(".raw",), help="h"
    )
    fields.update(over)
    with pytest.raises(ValueError):
        ImporterSpec(**fields)


@pytest.fixture
def fake_package(tmp_path, monkeypatch):
    """A throwaway package standing in for gridspine.ingest."""
    pkg = tmp_path / "fake_ingest"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield pkg
    for name in [m for m in sys.modules if m.startswith("fake_ingest")]:
        del sys.modules[name]


def _write_importer(pkg, fid, body=None):
    body = body or (
        f"""
        import sys
        from gridspine.ingest.import_result import ImportReport, ImportResult

        LOADED = True

        def read_{fid}(paths, options):
            report = ImportReport(source_format={fid!r}, source_files=[p.name for p in paths])
            return ImportResult(network=dict(options), report=report, sidecars={{"metadata.json": {{}}}})
        """
    )
    (pkg / f"{fid}_import.py").write_text(textwrap.dedent(body))


def test_registry_lists_only_present_importers_without_importing_them(fake_package):
    _write_importer(fake_package, "psse")
    _write_importer(fake_package, "excel")
    declared = (
        _spec("psse"),
        _spec("plexos", (".xml", ".xlsx")),
        _spec("excel", (".XLSX",)),
    )
    found = ir.importers(declared)
    assert [s.format_id for s in found] == ["excel", "psse"]
    assert "fake_ingest.psse_import" not in sys.modules
    assert [s.format_id for s in ir.for_extension("xlsx", declared)] == ["excel"]
    assert [s.format_id for s in ir.for_extension(".RAW", declared)] == ["psse"]
    assert ir.for_extension(".csv", declared) == []
    assert ir.get("psse", declared) is declared[0]


def test_for_extension_returns_every_match(fake_package):
    _write_importer(fake_package, "plexos")
    _write_importer(fake_package, "excel")
    declared = (_spec("plexos", (".xml", ".xlsx")), _spec("excel", (".xlsx",)))
    assert [s.format_id for s in ir.for_extension(".xlsx", declared)] == [
        "excel",
        "plexos",
    ]


def test_get_names_the_available_importers(fake_package):
    _write_importer(fake_package, "psse")
    declared = (_spec("psse"), _spec("plexos"))
    with pytest.raises(KeyError, match="available: psse"):
        ir.get("plexos", declared)
    with pytest.raises(KeyError, match="available: none"):
        ir.get("psse", (_spec("plexos"),))


def test_duplicate_format_ids_are_refused():
    with pytest.raises(ValueError, match="psse"):
        ir.importers((_spec("psse"), _spec("psse")))


def test_read_runs_the_reader_and_checks_the_result(fake_package):
    _write_importer(fake_package, "psse")
    declared = (_spec("psse"),)
    result = ir.read("psse", ["case.raw"], {"base_mva": 100}, declared)
    assert result.report.source_files == ["case.raw"]
    assert result.network == {"base_mva": 100}
    assert ir.read("psse", ["case.raw"], declared=declared).network == {}


def test_read_refuses_a_module_without_its_reader(fake_package):
    _write_importer(fake_package, "psse", "def read_other(paths, options):\n    pass\n")
    with pytest.raises(TypeError, match="read_psse"):
        ir.read("psse", ["case.raw"], declared=(_spec("psse"),))


@pytest.mark.parametrize(
    "returns, error, match",
    [
        ("None", TypeError, "not ImportResult"),
        (
            "ImportResult(network=None, report=ImportReport(source_format='plexos', source_files=[]))",
            ValueError,
            "plexos",
        ),
        (
            "ImportResult(network=None, report=ImportReport(source_format='psse', source_files=[]), sidecars={'': 1})",
            ValueError,
            "sidecar",
        ),
    ],
)
def test_read_refuses_a_broken_result(fake_package, returns, error, match):
    _write_importer(
        fake_package,
        "psse",
        "from gridspine.ingest.import_result import ImportReport, ImportResult\n"
        f"def read_psse(paths, options):\n    return {returns}\n",
    )
    with pytest.raises(error, match=match):
        ir.read("psse", ["case.raw"], declared=(_spec("psse"),))


def test_declared_importers_are_the_four_formats():
    assert [s.format_id for s in ir.DECLARED] == ["excel", "psse", "dgs", "plexos"]
    assert {s.package for s in ir.DECLARED} == {"gridspine.ingest"}
    ir.importers()  # no duplicates; present modules only


def test_declared_importer_modules_are_frozen_into_the_app():
    """
    The registry finds importers with find_spec, so PyInstaller only bundles
    the ones the spec names; a missing one would vanish from the frozen app.
    """
    spec_text = (ROOT / "pypsa-gui" / "pypsa-gui.spec").read_text(encoding="utf-8")
    listed = set(re.findall(r'"(gridspine\.ingest\.\w+_import)"', spec_text))
    assert listed == {s.module for s in ir.DECLARED}


def test_contract_module_imports_no_engine():
    code = (
        "import sys, gridspine.ingest.import_result as ir\n"
        "ir.importers()\n"
        "bad = [m for m in ('pypsa', 'pandapower') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=ROOT)


def test_zero_counts_are_allowed():
    assert _report(counts={"Transformers": 0}).counts == {"Transformers": 0}


def test_non_string_names_are_refused_as_values():
    with pytest.raises(ValueError):
        Unmapped(7, "why")


def test_round_trip_keeps_every_unmapped_and_assumed_item():
    data = _report().to_dict()
    assert len(data["unmapped"]) == 2 and len(data["assumed"]) == 2
    back = ImportReport.from_dict(data)
    assert len(back.unmapped) == 2 and len(back.assumed) == 2


@pytest.mark.parametrize(
    "change, match",
    [
        (lambda d: d["counts"].update(Buses=True), "Buses"),
        (lambda d: d["counts"].update(Buses=-1), "Buses"),
        (lambda d: d["assumed"][0].update(value=float("nan")), "Assumed.value"),
    ],
)
def test_from_dict_validates_like_the_constructor(change, match):
    data = _report().to_dict()
    change(data)
    with pytest.raises(ValueError, match=match):
        ImportReport.from_dict(data)


def test_get_does_not_import_the_reader(fake_package):
    _write_importer(fake_package, "psse")
    ir.get("psse", (_spec("psse"),))
    ir.for_extension(".raw", (_spec("psse"),))
    assert "fake_ingest.psse_import" not in sys.modules
