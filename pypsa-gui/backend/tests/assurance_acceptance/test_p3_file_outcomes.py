"""A3.1/A3.4/A3.6: network import and export read-backs.

Issue 04, slice S3 (GK B (26) X4–X8). Before an import runs,
`harness.outcomes.note_import` reads the call's own file into a scratch
Network with the import route's reader steps; after it, `read_import` reads
the live network's rows and values against that scratch copy. An export
watches the uploads this call adds (`upload_service.watch_uploads`, a dedup
hit included); exactly one must be an agent_export in the project, and
`read_export` reads it back and compares it with the live network. Excel and MATPOWER read adapter_pending (X6). The
handler's returned JSON is never read.
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
    rows = _n_rows(_live())
    assert result["_outcome"] == {
        "version": 1,
        "outcome": "completed",
        "reason": None,
        "counts": {"requested": rows, "done": rows, "extra": 0, "allowed_extras": []},
    }
    assert len(_live().snapshots) == 168


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


@pytest.fixture
def template_in_project(baseline):
    """The datacenter template in the bound baseline project, read as a load would."""
    ctx = PyPSAService.get_active_context()
    ctx.network = outcomes._load_netcdf(_template_bytes())
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


# ── Excel and MATPOWER: no independent reader yet (X6) ───────────────────


@pytest.mark.parametrize("tool", ["import_excel_pending", "import_matpower_pending"])
def test_import_formats_without_a_reader_are_pending(tool):
    tool = tool.removesuffix("_pending")
    out = outcomes.classify_import(outcomes.note_import(tool, {"bytes_b64": ""}), None)
    assert (out["outcome"], out["reason"]) == ("uncertain", "adapter_pending")


@pytest.mark.parametrize("fmt", ["excel", "matpower"])
def test_uploaded_import_formats_without_a_reader_are_pending(fmt):
    note = outcomes.note_import(
        "import_uploaded_network", {"file_id": "f", "format": fmt}
    )
    out = outcomes.classify_import(note, None)
    assert (out["outcome"], out["reason"]) == ("uncertain", "adapter_pending")


@pytest.mark.parametrize("tool", ["export_excel_pending", "export_matpower_pending"])
def test_export_formats_without_a_reader_are_pending(tool):
    tool = tool.removesuffix("_pending")
    out = outcomes.classify_export(outcomes.note_export(tool), None)
    assert (out["outcome"], out["reason"]) == ("uncertain", "adapter_pending")


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


def _inote():
    return {"tool": "import_network_nc", "format": "netcdf", "expected": object()}


def _iread(**kw):
    return {
        "revision_start": 3,
        "revision_end": 3,
        "requested": 4,
        "missing": 0,
        "extras": [],
        "match": True,
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
        (
            {"tool": "import_excel", "pending": True},
            None,
            "uncertain",
            "adapter_pending",
        ),
        (_inote(), {"error": "readback_failed"}, "uncertain", "readback_failed"),
        (_inote(), None, "uncertain", "readback_failed"),
        (_inote(), _iread(revision_end=4), "uncertain", "revision_moved"),
        (_inote(), _iread(missing=1), "partial", "rows_missing"),
        (_inote(), _iread(missing=1, extras=[("Bus", "z")]), "partial", "rows_missing"),
        (_inote(), _iread(extras=[("Bus", "z")]), "partial", "extra_rows"),
        (_inote(), _iread(missing=4), "uncertain", "not_as_file"),
        (_inote(), _iread(match=False), "uncertain", "not_as_file"),
        (_inote(), _iread(match=None), "uncertain", "not_as_file"),
        (_inote(), _iread(user_ts={"Load": {}}), "uncertain", "not_as_file"),
        (_inote(), _iread(match=outcomes.UNCOMPARABLE), "uncertain", "not_comparable"),
    ],
)
def test_classify_import_table(note, read, outcome, reason):
    out = outcomes.classify_import(note, read)
    assert (out["outcome"], out["reason"]) == (outcome, reason)


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
    return {"tool": "export_network_nc", "format": "netcdf", "revision": 3}


def _eread(**kw):
    return {
        "new_files": 1,
        "same_ctx": True,
        "revision_start": 3,
        "revision_end": 3,
        "match": True,
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
        (
            {"tool": "export_excel", "pending": True},
            None,
            "uncertain",
            "adapter_pending",
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
