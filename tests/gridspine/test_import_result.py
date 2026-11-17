"""The shared import contract: report types and the importer registry."""

import ast
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


def _spec(fid="psse", exts=(".raw", ".dyr"), package="fake_ingest", name=None):
    return ImporterSpec(
        format_id=fid,
        display_name=name or f"{fid} file",
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
    with pytest.raises(ImportFormatError, match="available: psse"):
        ir.get("plexos", declared)
    with pytest.raises(ImportFormatError, match="available: none"):
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
    with pytest.raises(ImportFormatError, match="read_psse"):
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
    assert _listed_importers(spec_text) == {s.module for s in ir.DECLARED}


def _listed_importers(spec_text):
    """
    The `gridspine.ingest.*_import` strings in the spec's hiddenimports.

    Parsed as Python, so a commented-out line does not count.
    """
    listed = set()
    for node in ast.walk(ast.parse(spec_text)):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(t, ast.Name) and t.id == "hiddenimports" for t in targets
            ):
                for const in ast.walk(node.value):
                    if isinstance(const, ast.Constant) and isinstance(const.value, str):
                        if re.fullmatch(r"gridspine\.ingest\.\w+_import", const.value):
                            listed.add(const.value)
    return listed


def test_listed_importers_ignores_comments_and_other_lists():
    text = (
        'datas = ["gridspine.ingest.x_import"]\n'
        "hiddenimports = [\n"
        '    "gridspine.ingest.a_import",\n'
        '    # "gridspine.ingest.b_import",\n'
        '    "gridspine.ingest.import_result",\n'
        "]\n"
        'hiddenimports += ["gridspine.ingest.c_import"]\n'
    )
    assert _listed_importers(text) == {
        "gridspine.ingest.a_import",
        "gridspine.ingest.c_import",
    }


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


def test_a_missing_reader_dependency_is_an_import_format_error(fake_package):
    _write_importer(fake_package, "psse", "import not_a_real_dependency_xyz\n")
    with pytest.raises(
        ImportFormatError, match="PSS/E case importer cannot load"
    ) as err:
        ir.read("psse", ["case.raw"], declared=(_spec("psse", name="PSS/E case"),))
    assert isinstance(err.value.__cause__, ModuleNotFoundError)


def test_a_bug_while_loading_a_reader_is_not_hidden(fake_package):
    _write_importer(fake_package, "psse", "raise RuntimeError('bug in the importer')\n")
    with pytest.raises(RuntimeError, match="bug in the importer"):
        ir.read("psse", ["case.raw"], declared=(_spec("psse"),))


@pytest.mark.parametrize(
    "name",
    [
        "a/b.json",
        "a\\b.json",
        "..",
        ".",
        "/abs.json",
        "C:x.json",
        " x.json",
        "network.nc",
        "results_state.pkl",
        "",
        7,
    ],
)
def test_sidecar_names_are_plain_unreserved_file_names(name):
    with pytest.raises(ValueError, match="sidecar"):
        ImportResult(network=None, report=_report(), sidecars={name: {}})


@pytest.mark.parametrize("content", [{"x": float("nan")}, [float("inf")], object()])
def test_sidecar_content_is_json_ready_or_bytes(content):
    with pytest.raises(ValueError, match="sidecar"):
        ImportResult(network=None, report=_report(), sidecars={"user_ts.json": content})


def test_sidecar_bytes_and_json_are_accepted():
    result = ImportResult(
        network=None,
        report=_report(),
        sidecars={"user_ts.json": {"a": [1, 2]}, "layout.json": b"{}"},
    )
    assert set(result.sidecars) == {"user_ts.json", "layout.json"}


def test_result_needs_a_report():
    with pytest.raises(TypeError, match="ImportReport"):
        ImportResult(network=None, report={"source_format": "psse"})


def test_to_dict_revalidates_after_a_late_change():
    value = [1.0]
    report = _report(assumed=[Assumed("Line 1", "Rating", value, "missing")])
    value.append(float("nan"))
    with pytest.raises(ValueError, match="Assumed.value"):
        report.to_dict()
    report = _report()
    report.counts["Buses"] = -1
    with pytest.raises(ValueError, match="Buses"):
        report.to_dict()
    report = _report()
    report.unmapped.append(("Row 3", "why"))
    with pytest.raises(TypeError, match="Unmapped"):
        report.to_dict()


@pytest.mark.parametrize(
    "late_change, error, match",
    [
        ("result.report.counts['Buses'] = True", ValueError, "Buses"),
        ("result.sidecars['network.nc'] = b''", ValueError, "reserved"),
        ("result.report.suggestions.append(None)", TypeError, "Suggestion"),
    ],
)
def test_read_revalidates_what_the_reader_changed_late(
    fake_package, late_change, error, match
):
    _write_importer(
        fake_package,
        "psse",
        "from gridspine.ingest.import_result import ImportReport, ImportResult\n"
        "def read_psse(paths, options):\n"
        "    result = ImportResult(network=None, report=ImportReport("
        "source_format='psse', source_files=[], counts={'Buses': 1}))\n"
        f"    {late_change}\n"
        "    return result\n",
    )
    with pytest.raises(error, match=match):
        ir.read("psse", ["case.raw"], declared=(_spec("psse"),))
