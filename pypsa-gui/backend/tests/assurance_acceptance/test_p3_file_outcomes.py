"""A3.1/A3.4/A3.6: network import and export read-backs.

Issue 04, slice S3 (GK B (26) X4–X8). Before an import runs,
`harness.outcomes.note_import` reads the call's own file into a scratch
Network with the import route's reader steps; after it, `read_import` reads
the live network's rows and values against that scratch copy. An export
watches the uploads this call adds (`upload_service.watch_uploads`, a dedup
hit included); exactly one must be an agent_export in the project, and
`read_export` reads it back and compares it with the live network. Excel and
MATPOWER use their routes' readers (X6). The handler's returned JSON is never read.
"""

import base64
import pathlib
import tempfile

import pytest

from harness import outcomes
from services import chat_tools
from services.pypsa_service import PyPSAService
from tests.assurance_acceptance.test_p3_snapshot_outcomes import _frames
from tests.assurance_acceptance.test_p3_write_outcomes import _stream


def _template_bytes() -> bytes:
    from project_templates.eh_templates import build_eh_datacenter

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "network.nc"
        build_eh_datacenter().export_to_netcdf(path)
        return path.read_bytes()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _live():
    return PyPSAService.get_active_context().network


def _n_rows(n) -> int:
    return sum(len(names) for names in outcomes._rows(n).values())


# ── Imports (X4) ─────────────────────────────────────────────────────────


def test_a_netcdf_import_of_a_template_is_completed(baseline, harness):
    data = _template_bytes()
    result = _stream(
        harness, "import_network_nc", {"bytes_b64": _b64(data), "filename": "t.nc"}
    )
    out = result["_outcome"]
    assert (out["outcome"], out["reason"]) == ("completed", None)
    # Only the rows the import had to change count (E2): the standard line and
    # transformer types are already in every network, equal to the file's.
    assert 0 < out["counts"]["requested"] == out["counts"]["done"] < _n_rows(_live())
    assert out["counts"]["extra"] == 0 and out["counts"]["allowed_extras"] == []
    assert len(_live().snapshots) == 168


def test_an_import_that_does_nothing_reads_failed(baseline, harness, monkeypatch):
    # GK B (26) E2: an unchanged live state never reads partial.
    import routers.io as io_router

    async def nothing(file, db=None, session=None):
        return {"buses": 0}

    monkeypatch.setattr(io_router, "import_netcdf", nothing)
    out = _stream(harness, "import_network_nc", {"bytes_b64": _b64(_template_bytes())})[
        "_outcome"
    ]
    assert (out["outcome"], out["reason"]) == ("failed", None)


def test_reimporting_the_live_network_reads_completed(baseline, harness):
    # D5: an unchanged state that already is the file reads completed.
    data = _template_bytes()
    chat_tools.import_network_nc(_b64(data), "t.nc")
    out = _stream(harness, "import_network_nc", {"bytes_b64": _b64(data)})["_outcome"]
    assert out["outcome"] == "completed", out
    assert out["counts"]["requested"] == 0


def test_an_edit_between_import_and_read_back_reads_uncertain(
    baseline, harness, monkeypatch
):
    # GK B (26) E3: the read-back's revision must equal the note's.
    from services import dirty_state

    real = outcomes.read_import

    def bumped(note):
        dirty_state.bump_revision(PyPSAService.get_active_context())
        return real(note)

    monkeypatch.setattr(outcomes, "read_import", bumped)
    out = _stream(harness, "import_network_nc", {"bytes_b64": _b64(_template_bytes())})[
        "_outcome"
    ]
    assert (out["outcome"], out["reason"]) == ("uncertain", "revision_moved")


def test_a_project_switch_with_an_equal_revision_reads_other_context(
    baseline, harness, monkeypatch
):
    # GK B (27) E3: imports keep the revision counter, so two projects can both
    # read the same revision; the note binds the project, as note_undo does.
    real = outcomes.read_import

    def switched(note):
        monkeypatch.setattr(
            PyPSAService, "get_loaded_project", classmethod(lambda cls: "another")
        )
        return real(note)

    monkeypatch.setattr(outcomes, "read_import", switched)
    out = _stream(harness, "import_network_nc", {"bytes_b64": _b64(_template_bytes())})[
        "_outcome"
    ]
    assert (out["outcome"], out["reason"]) == ("uncertain", "other_context")


def test_a_csv_bundle_import_of_a_template_is_completed(baseline, harness):
    from routers.io import _export_csv_zip_bytes

    chat_tools.import_network_nc(_b64(_template_bytes()), "t.nc")
    bundle = _export_csv_zip_bytes()
    result = _stream(
        harness, "import_csv_bundle", {"bytes_b64": _b64(bundle), "filename": "t.zip"}
    )
    assert result["_outcome"]["outcome"] == "completed", result["_outcome"]


def test_an_uploaded_netcdf_import_is_completed(baseline, harness):
    from services import upload_service
    from services.assistant_tools import _project

    project, directory, _owner = _project()
    meta = upload_service.add_upload(
        project.name,
        _template_bytes(),
        "t.nc",
        "application/x-netcdf",
        kind="user_upload",
        project_dir=directory,
    )
    result = _stream(
        harness,
        "import_uploaded_network",
        {"file_id": meta.file_id, "format": "netcdf"},
    )
    assert result["_outcome"]["outcome"] == "completed", result["_outcome"]


def _route_also(monkeypatch, fn):
    """Make the import route do one more thing after it reads the file."""
    import routers.io as io_router

    real = io_router._refuse_invalid_names

    def wrapped(n):
        real(n)
        fn(n)

    monkeypatch.setattr(io_router, "_refuse_invalid_names", wrapped)


def test_a_row_the_route_adds_reads_partial_unless_allow_listed(
    baseline, harness, monkeypatch
):
    _route_also(monkeypatch, lambda n: n.add("Carrier", "router_extra"))
    data = _b64(_template_bytes())
    out = _stream(harness, "import_network_nc", {"bytes_b64": data})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("partial", "extra_rows")
    assert out["counts"]["extra"] == 1 and out["paths_extra"] == [
        "Carrier/router_extra"
    ]
    assert out["counts"]["done"] == out["counts"]["requested"]

    # X4: the allow-list is closed and in code; an allowed extra is named.
    monkeypatch.setattr(
        outcomes, "IMPORT_EXTRAS", frozenset({("Carrier", "router_extra")})
    )
    out = _stream(harness, "import_network_nc", {"bytes_b64": data})["_outcome"]
    assert out["outcome"] == "completed"
    assert out["counts"]["allowed_extras"] == ["Carrier/router_extra"]


def test_the_import_allow_list_is_empty_and_closed():
    # Measured: the import routes add columns (normalise_eh_columns), no rows.
    assert outcomes.IMPORT_EXTRAS == frozenset()
    assert isinstance(outcomes.IMPORT_EXTRAS, frozenset)


def test_a_row_missing_after_import_reads_partial(baseline, harness, monkeypatch):
    _route_also(monkeypatch, lambda n: n.remove("Load", n.loads.index[0]))
    out = _stream(harness, "import_network_nc", {"bytes_b64": _b64(_template_bytes())})[
        "_outcome"
    ]
    assert (out["outcome"], out["reason"]) == ("partial", "rows_missing")
    assert out["counts"]["done"] == out["counts"]["requested"] - 1


def test_a_needed_row_left_at_its_old_values_is_not_done(
    baseline, harness, monkeypatch
):
    # GK B (27) E2: a needed row that is present but unlike the file is not done.
    def drop_one_and_spoil_one(n):
        n.remove("Load", n.loads.index[0])
        n.generators.loc[n.generators.index[0], "p_nom"] += 12345.0

    _route_also(monkeypatch, drop_one_and_spoil_one)
    out = _stream(harness, "import_network_nc", {"bytes_b64": _b64(_template_bytes())})[
        "_outcome"
    ]
    assert (out["outcome"], out["reason"]) == ("partial", "rows_missing")
    assert out["counts"]["done"] == out["counts"]["requested"] - 2


def test_a_value_unlike_the_file_reads_uncertain(baseline, harness, monkeypatch):
    def bump(n):
        n.generators.loc[n.generators.index[0], "marginal_cost"] += 1

    _route_also(monkeypatch, bump)
    out = _stream(harness, "import_network_nc", {"bytes_b64": _b64(_template_bytes())})[
        "_outcome"
    ]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_file")


def test_a_series_unlike_the_file_reads_uncertain(baseline, harness, monkeypatch):
    def bump(n):
        df = n.loads_t.p_set
        df.iloc[3, 0] += 1

    _route_also(monkeypatch, bump)
    out = _stream(harness, "import_network_nc", {"bytes_b64": _b64(_template_bytes())})[
        "_outcome"
    ]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_file")


def test_an_unreadable_file_note_reads_uncertain(baseline, harness, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(outcomes, "_read_file", boom)
    out = _stream(harness, "import_network_nc", {"bytes_b64": _b64(_template_bytes())})[
        "_outcome"
    ]
    assert (out["outcome"], out["reason"]) == ("uncertain", "no_submission")


def _assert_tool_error_without_outcome(harness, tool, args):
    # GK B (26) R3: S3 puts no `_outcome` on a raise; S4 owns the error path.
    frames, collector = _frames(harness, tool, args)
    assert not [d for e, d in frames if e == "tool_result"]
    assert [c for c in collector if c.get("is_error")], collector
    assert "_outcome" not in repr(collector)


def test_a_refused_import_is_a_tool_error_with_no_outcome(baseline, harness):
    import pypsa

    n = pypsa.Network()
    n.add("Bus", "ic:frame")
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "bad.nc"
        n.export_to_netcdf(path)
        data = path.read_bytes()
    _assert_tool_error_without_outcome(
        harness, "import_network_nc", {"bytes_b64": _b64(data)}
    )


# ── Exports (X5) ─────────────────────────────────────────────────────────


@pytest.fixture(params=["loaded", "in_memory"])
def template_in_project(baseline, request):
    """The datacenter template in the bound baseline project: read as a load would,
    or built in memory as a template or edit leaves it (GK B (26) E1)."""
    from project_templates.eh_templates import build_eh_datacenter

    ctx = PyPSAService.get_active_context()
    ctx.network = (
        outcomes._load_netcdf(_template_bytes())
        if request.param == "loaded"
        else build_eh_datacenter()
    )
    assert PyPSAService.get_loaded_project()
    return ctx


@pytest.mark.parametrize("tool", ["export_network_nc", "export_csv_bundle"])
def test_an_export_of_a_template_is_completed(template_in_project, harness, tool):
    out = _stream(harness, tool, {})["_outcome"]
    assert out == {"version": 1, "outcome": "completed", "reason": None}


def test_a_repeated_export_of_an_unchanged_network_is_completed(
    template_in_project, harness, monkeypatch
):
    # Uploads are content-addressed: equal bytes land on the first file (a
    # dedup hit). The bytes are pinned, since a writer may vary them.
    import functools

    import routers.io as io_router

    for name in ("_export_netcdf_bytes", "_export_csv_zip_bytes"):
        monkeypatch.setattr(io_router, name, functools.cache(getattr(io_router, name)))
    for tool in ("export_network_nc", "export_csv_bundle"):
        first = _stream(harness, tool, {})
        second = _stream(harness, tool, {})
        assert first["file_id"] == second["file_id"]
        assert second["_outcome"]["outcome"] == "completed", (tool, second["_outcome"])


def test_two_export_files_by_one_call_read_uncertain(
    template_in_project, harness, monkeypatch
):
    real = chat_tools._save_agent_export

    def twice(data, filename, mime, **kw):
        real(data + b"\0", filename, mime, **kw)
        return real(data, filename, mime, **kw)

    monkeypatch.setattr(chat_tools, "_save_agent_export", twice)
    out = _stream(harness, "export_network_nc", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "export_file_count")


def test_an_export_by_another_session_is_not_this_calls_file(
    template_in_project, harness, monkeypatch
):
    # X5/X2: only the uploads THIS call adds count; another session's export
    # landing in the same store between note and read-back is not ours.
    import threading

    real = chat_tools._save_agent_export

    def elsewhere(data, filename, mime, **kw):
        import contextvars

        from services import upload_service

        def other():  # the same project, with no watch: another call's context
            upload_service._UPLOAD_WATCH.set(None)
            box["meta"] = real(data, filename, mime, **kw)

        box = {}
        thread = threading.Thread(target=contextvars.copy_context().run, args=(other,))
        thread.start()
        thread.join()
        return box["meta"]

    monkeypatch.setattr(chat_tools, "_save_agent_export", elsewhere)
    out = _stream(harness, "export_network_nc", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "export_file_count")


def test_no_new_export_file_reads_uncertain(template_in_project, harness, monkeypatch):
    monkeypatch.setattr(
        chat_tools, "_save_agent_export", lambda *a, **k: {"file_id": "x"}
    )
    out = _stream(harness, "export_network_nc", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "export_file_count")


def _export_of_a_changed_network(monkeypatch, tool, factor):
    """The export writes marginal_cost × factor for one generator; the network keeps it."""
    import routers.io as io_router

    name = {
        "export_network_nc": "_export_netcdf_bytes",
        "export_csv_bundle": "_export_csv_zip_bytes",
    }[tool]
    real = getattr(io_router, name)

    def changed():
        gens = PyPSAService.get_network().generators
        key = (gens.index[gens.marginal_cost.to_numpy() > 0][0], "marginal_cost")
        keep = gens.at[key]
        gens.at[key] = keep * factor
        try:
            return real()
        finally:
            gens.at[key] = keep

    monkeypatch.setattr(io_router, name, changed)


@pytest.mark.parametrize("tool", ["export_network_nc", "export_csv_bundle"])
def test_an_export_file_unlike_the_network_reads_uncertain(
    template_in_project, harness, monkeypatch, tool
):
    _export_of_a_changed_network(monkeypatch, tool, 1.001)
    out = _stream(harness, tool, {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_network")


def test_a_csv_export_sees_no_change_below_its_float_tolerance(
    template_in_project, harness, monkeypatch
):
    # X5 named limit: a csv bundle stores floats as text, so the check allows
    # CSV_RTOL; a change smaller than that reads completed (open `_pending` row).
    _export_of_a_changed_network(monkeypatch, "export_csv_bundle", 1 + 1e-14)
    assert (
        _stream(harness, "export_csv_bundle", {})["_outcome"]["outcome"] == "completed"
    )
    _export_of_a_changed_network(monkeypatch, "export_network_nc", 1 + 1e-14)
    out = _stream(harness, "export_network_nc", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_network")


def test_an_export_with_no_project_is_a_tool_error_with_no_outcome(baseline, harness):
    chat_tools.import_network_nc(_b64(_template_bytes()), "t.nc")  # unbinds the session
    assert not PyPSAService.get_loaded_project()
    _assert_tool_error_without_outcome(harness, "export_network_nc", {})


# ── Excel and MATPOWER (X6, rows AA04-P1..P5) ────────────────────────────
#
# An import reads the call's bytes with the route's own reader (the input
# workbook reader, the MATPOWER parser). An Excel export is read back with
# that reader and compared on what a workbook carries: no `n.meta`, floats
# to 15 digits (CSV_RTOL). MATPOWER carries buses, loads, generators and
# lines only, at 4 decimals: the read-back compares those fields with the
# live network and names the classes the format cannot carry.

# A three-bus case written by hand, not by the app's exporter.
_CASE3 = """function mpc = case3
mpc.version = '2';
mpc.baseMVA = 100;
mpc.bus = [
	1  3  0     0    0  0  1  1.0  0  110  1  1.1  0.9;
	2  1  60.5  12   0  0  1  1.0  0  110  1  1.1  0.9;
	3  1  40    8.25 0  0  1  1.0  0  110  1  1.1  0.9;
];
mpc.gen = [
	1  50  0  999  -999  1.0  100  1  150  10;
	3  20  0  999  -999  1.0  100  1  80   0;
];
mpc.branch = [
	1  2  0.01  0.1   0.02  120  0  0  0  0  1;
	2  3  0.02  0.15  0.01  90   0  0  0  0  1;
	1  3  0.015 0.12  0     100  0  0  0  0  1;
];
mpc.gencost = [
	2  0  0  2  12.5  0;
	2  0  0  2  30    0;
];
"""


def _excel_bytes(n=None) -> bytes:
    from project_templates.eh_templates import build_eh_datacenter
    from routers.io import _write_excel

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "network.xlsx"
        _write_excel(build_eh_datacenter() if n is None else n, path)
        return path.read_bytes()


def test_an_excel_import_of_a_template_is_completed(baseline, harness):
    result = _stream(
        harness,
        "import_excel",
        {"bytes_b64": _b64(_excel_bytes()), "filename": "t.xlsx"},
    )
    out = result["_outcome"]
    assert (out["outcome"], out["reason"]) == ("completed", None), out
    assert out["counts"]["requested"] == out["counts"]["done"] > 0
    assert "genset_new" in _live().generators.index


def test_a_matpower_import_is_completed(baseline, harness):
    result = _stream(
        harness,
        "import_matpower",
        {"bytes_b64": _b64(_CASE3.encode()), "filename": "c.m"},
    )
    out = result["_outcome"]
    assert (out["outcome"], out["reason"]) == ("completed", None), out
    assert _live().loads.at["load_2", "p_set"] == 60.5


@pytest.mark.parametrize("fmt", ["excel", "matpower"])
def test_an_uploaded_excel_or_matpower_import_is_completed(baseline, harness, fmt):
    from services import upload_service
    from services.assistant_tools import _project

    data, name = (
        (_excel_bytes(), "t.xlsx") if fmt == "excel" else (_CASE3.encode(), "c.m")
    )
    project, directory, _owner = _project()
    meta = upload_service.add_upload(
        project.name,
        data,
        name,
        "application/octet-stream",
        kind="user_upload",
        project_dir=directory,
    )
    out = _stream(
        harness, "import_uploaded_network", {"file_id": meta.file_id, "format": fmt}
    )["_outcome"]
    assert (out["outcome"], out["reason"]) == ("completed", None), out


@pytest.mark.parametrize("tool", ["import_excel", "import_matpower"])
def test_an_excel_or_matpower_import_that_does_nothing_reads_failed(
    baseline, harness, monkeypatch, tool
):
    import routers.io as io_router

    async def nothing(file, db=None, session=None):
        return {"buses": 0}

    monkeypatch.setattr(io_router, tool, nothing)
    data = _excel_bytes() if tool == "import_excel" else _CASE3.encode()
    out = _stream(harness, tool, {"bytes_b64": _b64(data)})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("failed", None)


def test_a_matpower_import_that_lands_another_value_reads_uncertain(
    baseline, harness, monkeypatch
):
    import routers.io as io_router

    real = io_router._parse_matpower

    def off(n, text):
        real(n, text)
        n.lines.loc["line_1", "s_nom"] += 1.0

    # The note reads the file; then the route parses it into another value.
    note = outcomes.note_import("import_matpower", {"bytes_b64": _b64(_CASE3.encode())})
    monkeypatch.setattr(io_router, "_parse_matpower", off)
    chat_tools.import_matpower(_b64(_CASE3.encode()))
    out = outcomes.classify_import(note, outcomes.read_import(note))
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_file")


def test_an_excel_export_of_a_template_is_completed(template_in_project, harness):
    # The template's asset provenance (n.meta) is not in a workbook: not a failure.
    from services import excel_workbook

    assert template_in_project.network.meta.get("asset_params")
    result = _stream(harness, "export_excel", {})
    assert result["_outcome"] == {"version": 1, "outcome": "completed", "reason": None}
    from services import upload_service
    from services.assistant_tools import _project

    project, directory, _owner = _project()
    data = upload_service.get_upload_bytes(
        project.name, result["file_id"], project_dir=directory
    )
    assert not excel_workbook.prepare(
        data, "x.xlsx", new_project=False
    ).network.meta.get("asset_params")


def test_an_excel_export_off_by_more_than_its_tolerance_reads_uncertain(
    template_in_project, harness, monkeypatch
):
    # A value moved by 1e-9 relative (beyond CSV_RTOL, within any display rounding).
    import routers.io as io_router

    assert outcomes.CSV_RTOL == 1e-12
    changed = template_in_project.network.copy()
    changed.generators.loc["genset_1", "p_nom"] *= 1 + 1e-9
    monkeypatch.setattr(io_router, "_export_excel_bytes", lambda: _excel_bytes(changed))
    out = _stream(harness, "export_excel", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_network")


@pytest.mark.parametrize("tool", ["import_excel", "import_matpower"])
def test_the_import_note_calls_the_routes_own_reader(baseline, monkeypatch, tool):
    # GK B (73): the route's reader, not a copy of it.
    import routers.io as io_router
    from services import excel_workbook

    module, name = (
        (excel_workbook, "prepare")
        if tool == "import_excel"
        else (io_router, "_parse_matpower")
    )
    real, calls = getattr(module, name), []

    def spy(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(module, name, spy)
    data = _excel_bytes() if tool == "import_excel" else _CASE3.encode()
    note = outcomes.note_import(tool, {"bytes_b64": _b64(data)})
    assert not note.get("error") and len(calls) == 1


def test_an_excel_export_unlike_the_network_reads_uncertain(
    template_in_project, harness, monkeypatch
):
    import routers.io as io_router

    changed = template_in_project.network.copy()
    changed.generators.loc["genset_1", "p_nom"] += 1.0
    monkeypatch.setattr(io_router, "_export_excel_bytes", lambda: _excel_bytes(changed))
    out = _stream(harness, "export_excel", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_network")


@pytest.fixture
def ieee14_in_project(baseline):
    from project_templates._build import build_ieee14

    ctx = PyPSAService.get_active_context()
    ctx.network = build_ieee14()
    assert PyPSAService.get_loaded_project()
    return ctx


def test_a_matpower_export_is_completed_and_names_what_it_cannot_carry(
    ieee14_in_project, harness
):
    # GK B (73) Q1: completed, and the classes the file lacks are in the text
    # the model reads, so an answer cannot claim a full export.
    live = ieee14_in_project.network
    live.add(
        "Link", "hvdc", bus0=live.buses.index[0], bus1=live.buses.index[1], p_nom=10
    )
    live.add("Store", "tank", bus=live.buses.index[2], e_nom=5)
    out = _stream(harness, "export_matpower", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("completed", None), out
    assert out["counts"]["not_carried"] == ["Carrier", "Link", "Store", "series"]
    for missing in ("Link", "Store", "series"):
        assert missing in out["notice"]


def test_the_matpower_tolerance_is_half_a_4th_decimal():
    assert outcomes.MATPOWER_ABS == 5e-5 + 1e-12


def test_the_matpower_check_is_written_apart_from_the_exporter():
    # GK B (73): the projection never calls the exporter's code.
    import inspect

    src = inspect.getsource(outcomes._matpower_holds) + inspect.getsource(
        outcomes._matpower_not_carried
    )
    assert "routers" not in src and "_export_matpower" not in src


def test_a_matpower_export_of_a_hub_names_its_links_and_storage(
    template_in_project, harness
):
    out = _stream(harness, "export_matpower", {})["_outcome"]
    assert out["outcome"] == "completed", out
    assert {"Link", "StorageUnit"} <= set(out["counts"]["not_carried"])


@pytest.mark.parametrize(
    "edit",
    [
        ("Bus", "v_nom", 1.0),
        ("Load", "p_set", 0.001),
        ("Generator", "p_nom", 0.001),
        ("Generator", "marginal_cost", 0.5),
        ("Line", "x", 1e-4),
        ("Line", "s_nom", 1.0),
    ],
)
def test_a_matpower_export_unlike_the_network_reads_uncertain(
    ieee14_in_project, harness, monkeypatch, edit
):
    # The file is written from a copy with one carried value moved.
    import routers.io as io_router

    cls, attr, step = edit
    live = ieee14_in_project.network
    changed = live.copy()
    frame = changed.static(cls)
    frame.loc[frame.index[0], attr] += step
    real = io_router._export_matpower_text

    def other():
        ieee14_in_project.network = changed
        try:
            return real()
        finally:
            ieee14_in_project.network = live

    monkeypatch.setattr(io_router, "_export_matpower_text", other)
    out = _stream(harness, "export_matpower", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_network"), edit


@pytest.mark.parametrize(
    "section, row",
    [
        ("mpc.bus", "\t99  1  0  0  0  0  1  1.0  0  110  1  1.1  0.9;\n"),
        ("mpc.gen", "\t1  5  0  999  -999  1.0  100  1  10  0;\n"),
        ("mpc.branch", "\t1  2  0.01  0.1  0  50  0  0  0  0  1;\n"),
    ],
)
def test_a_matpower_export_with_an_extra_row_reads_uncertain(
    ieee14_in_project, harness, monkeypatch, section, row
):
    # Mutants: a row the live network does not have, after all of its own.
    import routers.io as io_router

    real = io_router._export_matpower_text

    def extra():
        lines = real().splitlines(keepends=True)
        first = next(
            i for i, line in enumerate(lines) if line.startswith(section + " ")
        )
        end = next(i for i in range(first, len(lines)) if lines[i].startswith("];"))
        return "".join(lines[:end] + [row] + lines[end:])

    monkeypatch.setattr(io_router, "_export_matpower_text", extra)
    out = _stream(harness, "export_matpower", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_network")


def test_a_matpower_export_with_a_generator_on_another_bus_reads_uncertain(
    ieee14_in_project, harness, monkeypatch
):
    import routers.io as io_router

    live = ieee14_in_project.network
    changed = live.copy()
    gen = changed.generators.index[0]
    other = next(
        b for b in changed.buses.index if b != changed.generators.at[gen, "bus"]
    )
    changed.generators.loc[gen, "bus"] = other
    real = io_router._export_matpower_text

    def moved():
        ieee14_in_project.network = changed
        try:
            return real()
        finally:
            ieee14_in_project.network = live

    monkeypatch.setattr(io_router, "_export_matpower_text", moved)
    out = _stream(harness, "export_matpower", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_network")


def test_a_matpower_export_of_loads_with_only_p_or_only_q_is_completed(
    ieee14_in_project, harness
):
    # A bus with demand in one of P or Q still carries its load row.
    loads = ieee14_in_project.network.loads
    loads.loc[loads.index[0], "p_set"] = 0.0
    loads.loc[loads.index[1], "q_set"] = 0.0
    out = _stream(harness, "export_matpower", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("completed", None), out


def test_an_import_of_an_unknown_format_has_no_note(baseline):
    from routers.io import _export_csv_zip_bytes
    from services import upload_service
    from services.assistant_tools import _project

    project, directory, _owner = _project()
    meta = upload_service.add_upload(
        project.name,
        _export_csv_zip_bytes(),
        "t.zip",
        "application/zip",
        kind="user_upload",
        project_dir=directory,
    )
    note = outcomes.note_import(
        "import_uploaded_network", {"file_id": meta.file_id, "format": "bogus"}
    )
    assert note == {"tool": "import_uploaded_network", "error": True}


def test_a_matpower_export_missing_a_line_reads_uncertain(
    ieee14_in_project, harness, monkeypatch
):
    import routers.io as io_router

    real = io_router._export_matpower_text

    def short():
        lines = real().splitlines(keepends=True)
        first = next(i for i, line in enumerate(lines) if line.startswith("mpc.branch"))
        return "".join(lines[: first + 1] + lines[first + 2 :])

    monkeypatch.setattr(io_router, "_export_matpower_text", short)
    out = _stream(harness, "export_matpower", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_network")


# ── Export edge cases (rows AA04-P6, AA04-P7) ─────────────────────────────


def test_an_export_equal_to_a_user_upload_is_completed(
    template_in_project, harness, monkeypatch
):
    # P6: the export's bytes dedup onto the person's own upload, which stays a
    # user_upload. The watch proves it is this call's file: read it back.
    import functools

    import routers.io as io_router
    from services import upload_service
    from services.assistant_tools import _project

    monkeypatch.setattr(
        io_router,
        "_export_netcdf_bytes",
        functools.cache(io_router._export_netcdf_bytes),
    )
    project, directory, _owner = _project()
    mine = upload_service.add_upload(
        project.name,
        io_router._export_netcdf_bytes(),
        "mine.nc",
        "application/x-netcdf",
        kind="user_upload",
        project_dir=directory,
    )
    result = _stream(harness, "export_network_nc", {})
    assert result["file_id"] == mine.file_id and result["kind"] == "user_upload"
    assert result["_outcome"]["outcome"] == "completed", result["_outcome"]


def test_an_export_whose_result_names_another_file_reads_uncertain(
    template_in_project, harness, monkeypatch
):
    # GK B (73) Q2: the watched file must be the one the call's result names.
    real = chat_tools._save_agent_export

    def renamed(data, filename, mime, **kw):
        return {**real(data, filename, mime, **kw), "file_id": "someone-else"}

    monkeypatch.setattr(chat_tools, "_save_agent_export", renamed)
    out = _stream(harness, "export_network_nc", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "export_file_count")


@pytest.mark.parametrize("bad", [None, {}, {"file_id": None}])
def test_an_export_read_without_its_result_names_no_file(template_in_project, bad):
    note = outcomes.note_export("export_network_nc")
    chat_tools.export_network_nc()
    out = outcomes.classify_export(note, outcomes.read_export(note, bad))
    assert (out["outcome"], out["reason"]) == ("uncertain", "export_file_count")


def test_a_csv_export_missing_an_eh_column_reads_uncertain(
    template_in_project, harness, monkeypatch
):
    # P7 mutant: the export leaves out the links' eh_role column.
    import io
    import zipfile

    import routers.io as io_router

    assert "eh_role" in template_in_project.network.links.columns
    real = io_router._export_csv_zip_bytes

    def dropped():
        import pandas as pd

        source = zipfile.ZipFile(io.BytesIO(real()))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as target:
            for item in source.infolist():
                data = source.read(item)
                if item.filename.endswith("links.csv"):
                    frame = pd.read_csv(io.BytesIO(data), index_col=0, dtype=str)
                    data = frame.drop(columns="eh_role").to_csv().encode()
                target.writestr(item, data)
        return out.getvalue()

    monkeypatch.setattr(io_router, "_export_csv_zip_bytes", dropped)
    out = _stream(harness, "export_csv_bundle", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_network")


def test_the_csv_import_read_back_still_normalises(baseline, monkeypatch):
    # P7: only the export read-back skips the route's EH step; an import keeps it.
    from routers.io import _export_csv_zip_bytes
    from services.adequacy import eh_columns

    chat_tools.import_network_nc(_b64(_template_bytes()), "t.nc")
    bundle = _export_csv_zip_bytes()
    real, calls = eh_columns.normalise_eh_columns, []
    monkeypatch.setattr(
        eh_columns, "normalise_eh_columns", lambda n: (calls.append(n), real(n))[1]
    )
    note = outcomes.note_import("import_csv_bundle", {"bytes_b64": _b64(bundle)})
    assert not note.get("error") and calls
    calls.clear()
    outcomes._read_file("csv_bundle", bundle, export=True)
    assert calls == []


def test_a_csv_export_cell_the_reader_would_coerce_reads_uncertain(
    template_in_project, harness, monkeypatch
):
    # P7: the import reader coerces an unreadable EH float cell to NaN. A read-back
    # with that step would match a live NaN; the export read-back reads raw text.
    import io
    import zipfile

    import routers.io as io_router

    live = template_in_project.network
    live.generators["rated_mva"] = float("nan")
    real = io_router._export_csv_zip_bytes

    def garbled():
        source = zipfile.ZipFile(io.BytesIO(real()))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as target:
            for item in source.infolist():
                data = source.read(item)
                if item.filename.endswith("generators.csv"):
                    head, *rows = data.decode().splitlines()
                    col = head.split(",").index("rated_mva")
                    cells = rows[0].split(",")
                    cells[col] = "garbled"
                    data = (
                        "\n".join([head, ",".join(cells), *rows[1:]]).encode() + b"\n"
                    )
                target.writestr(item, data)
        return out.getvalue()

    monkeypatch.setattr(io_router, "_export_csv_zip_bytes", garbled)
    out = _stream(harness, "export_csv_bundle", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_network")


def test_the_file_tools_are_write_tools():
    assert outcomes.IMPORT_TOOLS | outcomes.EXPORT_TOOLS <= outcomes.WRITE_TOOLS
    assert (
        not (outcomes.IMPORT_TOOLS | outcomes.EXPORT_TOOLS) & outcomes.ROW_WRITE_TOOLS
    )


# ── X8: a frame that cannot be compared ──────────────────────────────────


def test_an_uncomparable_frame_reads_not_comparable(baseline, harness, monkeypatch):
    monkeypatch.setattr(outcomes, "_match", lambda *a, **k: outcomes.UNCOMPARABLE)
    out = _stream(harness, "import_network_nc", {"bytes_b64": _b64(_template_bytes())})[
        "_outcome"
    ]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_comparable")


def test_match_reads_a_list_cell_it_cannot_order_as_uncomparable_or_false():
    import pypsa

    a, b = pypsa.Network(), pypsa.Network()
    for n in (a, b):
        n.add("Bus", ["x", "y"])
    a.buses["tags"] = [[1], [2]]
    b.buses["tags"] = [[1], [3]]
    assert outcomes._match(a, b) in (False, outcomes.UNCOMPARABLE)
    assert outcomes._match(a, a) in (True, outcomes.UNCOMPARABLE)


# ── Pure classifiers ─────────────────────────────────────────────────────


_NEED = [("Bus", "a"), ("Bus", "b"), ("Load", "l"), ("Generator", "g")]


def _inote(**kw):
    return {
        "tool": "import_network_nc",
        "format": "netcdf",
        "expected": object(),
        "revision": 3,
        "digest_before": "before",
        "need": _NEED,
        "file_rows": 6,
        **kw,
    }


def _iread(**kw):
    return {
        "revision_start": 3,
        "revision_end": 3,
        "missing": [],
        "extras": [],
        "equal": _NEED,
        "match": True,
        "digest": "after",
        "user_ts": {},
        **kw,
    }


@pytest.mark.parametrize(
    "note,read,outcome,reason",
    [
        (_inote(), _iread(), "completed", None),
        (None, _iread(), "uncertain", "no_submission"),
        (
            {"tool": "import_network_nc", "error": True},
            _iread(),
            "uncertain",
            "no_submission",
        ),
        (_inote(), {"error": "readback_failed"}, "uncertain", "readback_failed"),
        (_inote(), {"error": "other_context"}, "uncertain", "other_context"),
        (_inote(), None, "uncertain", "readback_failed"),
        (_inote(), _iread(revision_end=4), "uncertain", "revision_moved"),
        (
            _inote(),
            _iread(revision_start=4, revision_end=4),
            "uncertain",
            "revision_moved",
        ),
        (_inote(), _iread(missing=[("Bus", "a")]), "partial", "rows_missing"),
        (_inote(), _iread(missing=[("LineType", "x")]), "partial", "rows_missing"),
        (
            _inote(),
            _iread(missing=[("Bus", "a")], extras=[("Bus", "z")]),
            "partial",
            "rows_missing",
        ),
        (_inote(), _iread(extras=[("Bus", "z")]), "partial", "extra_rows"),
        (_inote(), _iread(missing=_NEED), "uncertain", "not_as_file"),
        (_inote(), _iread(match=False), "uncertain", "not_as_file"),
        (_inote(), _iread(match=None), "uncertain", "not_as_file"),
        (_inote(), _iread(user_ts={"Load": {}}), "uncertain", "not_as_file"),
        (_inote(), _iread(match=outcomes.UNCOMPARABLE), "uncertain", "not_comparable"),
        # D5: nothing changed.
        (_inote(), _iread(digest="before"), "completed", None),
        (_inote(), _iread(digest="before", match=False), "failed", None),
        (_inote(), _iread(digest="before", user_ts={"Load": {}}), "failed", None),
        (
            _inote(digest_before=outcomes.UNCOMPARABLE),
            _iread(digest=outcomes.UNCOMPARABLE),
            "completed",
            None,
        ),
    ],
)
def test_classify_import_table(note, read, outcome, reason):
    out = outcomes.classify_import(note, read)
    assert (out["outcome"], out["reason"]) == (outcome, reason)


def test_classify_import_counts_only_rows_it_had_to_change():
    out = outcomes.classify_import(
        _inote(), _iread(missing=[("Bus", "a"), ("LineType", "x")])
    )
    assert out["counts"]["requested"] == 4 and out["counts"]["done"] == 3


def test_classify_import_counts_a_present_row_with_old_values_as_not_done():
    # GK B (27) E2: need {a, b, l}; a written, b dropped, l left as it was.
    out = outcomes.classify_import(
        _inote(need=_NEED[:3]),
        _iread(missing=[("Bus", "b")], equal=[("Bus", "a"), ("Generator", "g")]),
    )
    assert (out["outcome"], out["reason"]) == ("partial", "rows_missing")
    assert out["counts"]["requested"] == 3 and out["counts"]["done"] == 1


def test_classify_import_without_an_equal_list_counts_nothing_done():
    out = outcomes.classify_import(_inote(), _iread(equal=None, missing=[("Bus", "a")]))
    assert out["counts"]["done"] == 0


def test_classify_import_names_allowed_extras():
    out = outcomes.classify_import(
        _inote(), _iread(extras=[("Carrier", "AC")]), allowed={("Carrier", "AC")}
    )
    assert out["outcome"] == "completed"
    assert out["counts"] == {
        "requested": 4,
        "done": 4,
        "extra": 0,
        "allowed_extras": ["Carrier/AC"],
    }


def _enote():
    # S4b: the live state map; an export changes nothing (test_p3_raise_and_extra).
    return {"tool": "export_network_nc", "format": "netcdf", "revision": 3, "state": {}}


def _eread(**kw):
    return {
        "new_files": 1,
        "same_ctx": True,
        "revision_start": 3,
        "revision_end": 3,
        "match": True,
        "state": {},
        **kw,
    }


@pytest.mark.parametrize(
    "note,read,outcome,reason",
    [
        (_enote(), _eread(), "completed", None),
        (None, _eread(), "uncertain", "no_submission"),
        (
            {"tool": "export_network_nc", "error": True},
            _eread(),
            "uncertain",
            "no_submission",
        ),
        (_enote(), {"error": "readback_failed"}, "uncertain", "readback_failed"),
        (_enote(), {"new_files": 0}, "uncertain", "export_file_count"),
        (_enote(), {"new_files": 2}, "uncertain", "export_file_count"),
        (_enote(), _eread(same_ctx=False), "uncertain", "readback_failed"),
        (
            _enote(),
            _eread(revision_start=4, revision_end=4),
            "uncertain",
            "revision_moved",
        ),
        (_enote(), _eread(revision_end=4), "uncertain", "revision_moved"),
        (_enote(), _eread(match=False), "uncertain", "not_as_network"),
        (_enote(), _eread(match=None), "uncertain", "not_as_network"),
        (_enote(), _eread(match=outcomes.UNCOMPARABLE), "uncertain", "not_comparable"),
    ],
)
def test_classify_export_table(note, read, outcome, reason):
    out = outcomes.classify_export(note, read)
    assert (out["outcome"], out["reason"]) == (outcome, reason)


def test_the_frontend_file_tool_lists_match_the_backend():
    # review-gk.md (#1060): frontend copies of server sets carry a parity test.
    import re

    ts = (
        pathlib.Path(__file__).parents[3] / "frontend/src/components/toolOutcome.ts"
    ).read_text()
    block = re.search(r"const FILE_TOOLS[^=]*= new Set\(\[(.*?)\]\)", ts, re.S).group(1)
    assert (
        set(re.findall(r"'(\w+)'", block))
        == outcomes.IMPORT_TOOLS | outcomes.EXPORT_TOOLS
    )
    verbs = re.search(r"const WRITE_VERBS[^=]*= \{(.*?)\n\}", ts, re.S).group(1)
    assert set(re.findall(r"(\w+): 'imported'", verbs)) == outcomes.IMPORT_TOOLS


@pytest.mark.parametrize("change", ["weightings", "meta"])
def test_network_level_state_unlike_the_file_reads_uncertain(
    baseline, harness, monkeypatch, change
):
    # Spec review: snapshot weightings and `meta` are network state an import restores.
    def bump(n):
        if change == "weightings":
            n.snapshot_weightings.iloc[0, 0] = 7.0
        else:
            n.meta = {**n.meta, "asset_params": {"x": 1}}

    _route_also(monkeypatch, bump)
    out = _stream(harness, "import_network_nc", {"bytes_b64": _b64(_template_bytes())})[
        "_outcome"
    ]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_file")


@pytest.mark.parametrize("tool", ["export_network_nc", "export_csv_bundle"])
def test_an_export_that_drops_weightings_reads_uncertain(
    template_in_project, harness, monkeypatch, tool
):
    import routers.io as io_router

    name = {
        "export_network_nc": "_export_netcdf_bytes",
        "export_csv_bundle": "_export_csv_zip_bytes",
    }[tool]
    real = getattr(io_router, name)

    def changed():
        w = PyPSAService.get_network().snapshot_weightings
        keep = w.copy()
        w.iloc[0, :] = 5.0
        try:
            return real()
        finally:
            w.iloc[:, :] = keep.to_numpy()

    monkeypatch.setattr(io_router, name, changed)
    out = _stream(harness, tool, {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "not_as_network")


# ── GK B (26) E1/E4/E5 ───────────────────────────────────────────────────


@pytest.mark.parametrize("tool", ["export_network_nc", "export_csv_bundle"])
def test_an_export_after_an_edit_is_completed(template_in_project, harness, tool):
    # E1: a created row holds None in object columns, which a file returns as NaN.
    bus = _live().buses.index[0]
    _stream(
        harness,
        "batch_create_components",
        {
            "component_class": "Generator",
            "components": [{"name": "G_new", "bus": bus, "p_nom": 3}],
        },
    )
    out = _stream(harness, tool, {})["_outcome"]
    assert out == {"version": 1, "outcome": "completed", "reason": None}


@pytest.mark.parametrize("tool", ["export_network_nc", "export_csv_bundle"])
def test_an_export_after_topology_is_completed(template_in_project, harness, tool):
    # E4: SubNetwork rows hold an object whose str carries a memory address.
    _live().determine_network_topology()
    assert not _live().sub_networks.empty
    out = _stream(harness, tool, {})["_outcome"]
    assert out == {"version": 1, "outcome": "completed", "reason": None}


def test_the_digest_is_stable_across_loads_after_topology():
    data = _template_bytes()
    a, b = outcomes._load_netcdf(data), outcomes._load_netcdf(data)
    a.determine_network_topology()
    b.determine_network_topology()
    assert outcomes.network_digest(a) == outcomes.network_digest(b)


def test_the_digest_sees_meta():
    # E5 / M14.
    n = outcomes._load_netcdf(_template_bytes())
    before = outcomes.network_digest(n)
    n.meta = {**n.meta, "asset_params": {"x": 1}}
    assert outcomes.network_digest(n) != before


def test_stored_bytes_unlike_their_hash_read_uncertain(
    template_in_project, harness, monkeypatch
):
    # E5 / M15: the read-back re-reads the stored blob and re-checks its sha256.
    from services import upload_service
    from services.assistant_tools import _project

    real = chat_tools._save_agent_export

    def tampered(data, filename, mime, **kw):
        meta = real(data, filename, mime, **kw)
        project, directory, _owner = _project()
        path = upload_service.get_upload_path(
            project.name, meta["file_id"], project_dir=directory
        )
        other = _template_bytes()  # a valid file of another network
        path.write_bytes(other)
        return meta

    monkeypatch.setattr(chat_tools, "_save_agent_export", tampered)
    out = _stream(harness, "export_network_nc", {})["_outcome"]
    assert (out["outcome"], out["reason"]) == ("uncertain", "readback_failed")


def test_a_failed_handler_releases_the_upload_watch(
    template_in_project, harness, monkeypatch
):
    # Rider: a raise stops the per-call watch.
    from services import upload_service

    def boom(*a, **k):
        raise RuntimeError("disk")

    monkeypatch.setattr(chat_tools, "_save_agent_export", boom)
    _frames(harness, "export_network_nc", {})
    assert upload_service._UPLOAD_WATCH.get() is None


def _stale_template_bytes() -> bytes:
    """The datacenter template with its priced battery's owned costs left stale by a raw writer (TCU 10)."""
    from project_templates.eh_templates import build_eh_datacenter
    from services.asset_schema.derive import has_parts

    n = build_eh_datacenter()
    priced = [
        b
        for b in n.storage_units.index
        if has_parts("StorageUnit", n.storage_units.loc[b])
    ]
    assert priced
    n.storage_units.loc[priced, ["capital_cost", "fom_cost"]] = [1.0, 2.0]
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "network.nc"
        n.export_to_netcdf(path)
        return path.read_bytes()


@pytest.mark.parametrize("tool", ["import_network_nc", "import_csv_bundle"])
def test_an_import_whose_parts_rederive_a_stale_cost_still_reads_completed(
    baseline, harness, tool
):
    data = _stale_template_bytes()
    if tool == "import_csv_bundle":
        from routers.io import _export_csv_zip_bytes

        chat_tools.import_network_nc(_b64(data), "t.nc")
        data = _export_csv_zip_bytes()
        chat_tools.import_network_nc(_b64(_template_bytes()), "t.nc")
    name = "t.nc" if tool == "import_network_nc" else "t.zip"
    result = _stream(harness, tool, {"bytes_b64": _b64(data), "filename": name})
    assert result["_outcome"]["outcome"] == "completed", result["_outcome"]
    assert (_live().storage_units["capital_cost"] != 1.0).all()
