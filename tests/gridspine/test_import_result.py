"""The shared import contract: report types and the importer registry."""
import subprocess
import sys
import textwrap

import pytest

from gridspine.ingest import import_result as ir
from gridspine.ingest.import_result import (
    Assumed,
    ImporterSpec,
    ImportReport,
    ImportResult,
    Unmapped,
)


def _report(**over):
    base = dict(
        source_format="psse",
        source_files=["case.raw", "case.dyr"],
        counts={"Buses": 3, "Lines": 2},
        unmapped=[Unmapped("Switched shunt 7", "no matching asset type yet")],
        assumed=[Assumed("Line 1-2", "Thermal rating", 100.0, "RATEA was 0")],
        warnings=["Area names missing"],
    )
    base.update(over)
    return ImportReport(**base)


def test_report_round_trips_through_a_plain_dict():
    report = _report()
    data = report.to_dict()
    assert data["unmapped"] == [
        {"source_item": "Switched shunt 7", "reason": "no matching asset type yet"}
    ]
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


def _spec(fid="psse", exts=(".raw", ".dyr")):
    return ImporterSpec(
        format_id=fid,
        display_name="PSS/E case",
        extensions=exts,
        help="A .raw network file, optionally with a .dyr dynamic file.",
        reader=lambda paths, options: None,
    )


def test_spec_normalises_extensions_to_lowercase_with_a_dot():
    assert _spec(exts=("RAW", ".Dyr")).extensions == (".raw", ".dyr")


@pytest.mark.parametrize(
    "over",
    [
        {"format_id": "PSS/E"},
        {"format_id": ""},
        {"display_name": ""},
        {"extensions": ()},
        {"help": ""},
        {"reader": "not callable"},
    ],
)
def test_spec_refuses_bad_fields(over):
    fields = dict(
        format_id="psse",
        display_name="PSS/E case",
        extensions=(".raw",),
        help="help",
        reader=lambda paths, options: None,
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


def _write_importer(pkg, module, fid, exts):
    (pkg / f"{module}.py").write_text(
        textwrap.dedent(
            f"""
            from gridspine.ingest.import_result import ImporterSpec

            def read(paths, options):
                return None

            IMPORTER = ImporterSpec(
                format_id={fid!r},
                display_name={fid!r} + " file",
                extensions={exts!r},
                help="help",
                reader=read,
            )
            """
        )
    )


def test_registry_discovers_import_modules(fake_package):
    _write_importer(fake_package, "psse_import", "psse", (".raw",))
    _write_importer(fake_package, "plexos_import", "plexos", (".xml", ".xlsx"))
    _write_importer(fake_package, "excel_import", "excel", (".XLSX",))
    (fake_package / "helpers.py").write_text("IMPORTER = 'ignored: not *_import'\n")

    found = ir.importers(package="fake_ingest")
    assert [s.format_id for s in found] == ["excel", "plexos", "psse"]
    assert ir.get("psse", package="fake_ingest").extensions == (".raw",)
    assert [s.format_id for s in ir.for_extension("xlsx", package="fake_ingest")] == [
        "excel",
        "plexos",
    ]
    assert [s.format_id for s in ir.for_extension(".RAW", package="fake_ingest")] == ["psse"]
    assert ir.for_extension(".csv", package="fake_ingest") == []


def test_get_unknown_format_names_the_known_ones(fake_package):
    _write_importer(fake_package, "psse_import", "psse", (".raw",))
    with pytest.raises(KeyError, match="psse"):
        ir.get("powerfactory", package="fake_ingest")


def test_duplicate_format_ids_are_refused(fake_package):
    _write_importer(fake_package, "psse_import", "psse", (".raw",))
    _write_importer(fake_package, "psse2_import", "psse", (".raw",))
    with pytest.raises(ValueError, match="psse"):
        ir.importers(package="fake_ingest")


def test_import_module_without_importer_is_refused(fake_package):
    (fake_package / "half_import.py").write_text("X = 1\n")
    with pytest.raises(TypeError, match="half_import"):
        ir.importers(package="fake_ingest")


def test_real_package_registry_loads():
    """Every importer that has landed in gridspine/ingest registers cleanly."""
    for spec in ir.importers():
        assert ir.get(spec.format_id) is spec


def test_contract_module_imports_no_engine():
    code = (
        "import sys, gridspine.ingest.import_result\n"
        "bad = [m for m in ('pypsa', 'pandapower') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
