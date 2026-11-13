from __future__ import annotations

import csv
import io
import logging
import pathlib
import tempfile
import zipfile

from db.models import Session as SessionRow
from db.session import get_db
from deps import current_session
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from models.schemas import ImportSummary
from services import active_project, change_log_service
from sqlalchemy.orm import Session as DBSession
from services.pypsa_service import PyPSAService
from services.upload_guard import read_capped, safe_extract
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()

BASE_DIR = pathlib.Path(__file__).parent.parent


def _build_summary(n) -> ImportSummary:
    return ImportSummary(
        buses=len(n.buses),
        generators=len(n.generators),
        lines=len(n.lines),
        links=len(n.links),
        storage_units=len(n.storage_units),
        stores=len(n.stores),
        loads=len(n.loads),
        transformers=len(n.transformers),
        snapshots=len(n.snapshots),
    )


# ── Exports ───────────────────────────────────────────────────────────────────

# Each export is split into a `_*_bytes()/_*_text()` generator (materialises the
# file content + audit-logs) and a thin route that wraps it in a StreamingResponse.
# The chat-tool layer calls the generator directly so it can persist the bytes as
# a downloadable `agent_export` artifact — a StreamingResponse can't be returned
# as a chat-tool result (json.dumps stringifies it to "<StreamingResponse ...>").


def _export_netcdf_bytes() -> bytes:
    n = PyPSAService.get_network()
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
        tmp = pathlib.Path(f.name)
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.export_network_to_netcdf(n, tmp)
    data = tmp.read_bytes()
    tmp.unlink(missing_ok=True)
    change_log_service.log("export", "Network", "network.nc", "Exported network as NetCDF (.nc)")
    return data


@router.get("/export/netcdf")
def export_netcdf():
    return StreamingResponse(
        io.BytesIO(_export_netcdf_bytes()),
        media_type="application/x-netcdf",
        headers={"Content-Disposition": "attachment; filename=network.nc"},
    )


# EXPORT-INJECT-1: a spreadsheet runs a cell starting with one of these as a
# formula (OWASP CSV injection). A string cell gets a leading single quote; a
# number, negative ones included, stays a number.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _escape_formula(value):
    if isinstance(value, str) and value.startswith(_FORMULA_TRIGGERS) and not _is_number(value):
        return "'" + value
    return value


def _unescape_formula(value):
    """The GUI's own imports undo `_escape_formula`, so an export round-trips."""
    if isinstance(value, str) and value.startswith("'") and _escape_formula(value[1:]) != value[1:]:
        return value[1:]
    return value


def _map_csv_folder(folder, fn) -> None:
    for f in pathlib.Path(folder).rglob("*.csv"):
        with f.open(newline="", encoding="utf-8") as fh:
            rows = [[fn(cell) for cell in row] for row in csv.reader(fh)]
        with f.open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh, lineterminator="\n").writerows(rows)


def _map_xlsx_strings(path, fn, data_only: bool = False) -> None:
    """Apply `fn` to every text cell; a formula cell counts as text, so a
    formula the export would have written becomes an escaped string. An
    import passes `data_only`: a workbook saved by Excel keeps each formula's
    cached value, as PyPSA's own reader would see it."""
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=data_only)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type in ("s", "f") and isinstance(cell.value, str):
                    new = fn(cell.value)
                    if new != cell.value or cell.data_type == "f":
                        cell.value = new
                        cell.data_type = "s"
    wb.save(str(path))


def _export_csv_zip_bytes() -> bytes:
    n = PyPSAService.get_network()
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmpdir:
        n.export_to_csv_folder(tmpdir)
        _map_csv_folder(tmpdir, _escape_formula)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in pathlib.Path(tmpdir).rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(tmpdir))
    change_log_service.log("export", "Network", "network_csv.zip", "Exported network as CSV (zip)")
    return buf.getvalue()


@router.get("/export/csv")
def export_csv():
    return StreamingResponse(
        io.BytesIO(_export_csv_zip_bytes()),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=network_csv.zip"},
    )


def _export_excel_bytes() -> bytes:
    n = PyPSAService.get_network()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        tmp = pathlib.Path(f.name)
    try:
        # PyPSA appends when the path already exists. An empty mktemp file
        # is not a workbook; let its writer create the file instead.
        tmp.unlink()
        n.export_to_excel(str(tmp))
        _map_xlsx_strings(tmp, _escape_formula)
        data = tmp.read_bytes()
    except AttributeError:
        # Fallback: write each component DataFrame as a sheet
        import openpyxl
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for cname in ["buses", "carriers", "generators", "lines", "links",
                       "storage_units", "stores", "loads", "transformers"]:
            df = getattr(n, cname, None)
            if df is not None and not df.empty:
                ws = wb.create_sheet(cname[:31])
                rows = df.reset_index().values.tolist()
                ws.append(list(df.reset_index().columns))
                for row in rows:
                    ws.append(row)
        wb.save(str(tmp))
        _map_xlsx_strings(tmp, _escape_formula)
        data = tmp.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)
    change_log_service.log("export", "Network", "network.xlsx", "Exported network as Excel (.xlsx)")
    return data


@router.get("/export/excel")
def export_excel():
    return StreamingResponse(
        io.BytesIO(_export_excel_bytes()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=network.xlsx"},
    )


def _export_matpower_text() -> str:
    from jinja2 import Environment, FileSystemLoader
    n = PyPSAService.get_network()
    bus_idx = {name: i + 1 for i, name in enumerate(n.buses.index)}
    control_map = {"Slack": 3, "PV": 2, "PQ": 1}

    buses = []
    for name, row in n.buses.iterrows():
        Pd, Qd = 0.0, 0.0
        if not n.loads.empty:
            bus_loads = n.loads[n.loads.bus == name]
            Pd = float(bus_loads["p_set"].sum()) if not bus_loads.empty else 0.0
            Qd = float(bus_loads["q_set"].sum()) if not bus_loads.empty else 0.0
        buses.append({
            "bus_i": bus_idx[name],
            "type": control_map.get(getattr(row, "control", "PQ"), 1),
            "Pd": round(Pd, 4), "Qd": round(Qd, 4),
            "baseKV": float(row.get("v_nom", 1.0)),
        })

    gens = []
    for name, row in n.generators.iterrows():
        gens.append({
            "bus_i": bus_idx.get(row.bus, 1),
            "Pg": round(float(row.get("p_nom", 0)) * 0.5, 4),
            "Pmax": round(float(row.get("p_nom", 0)), 4),
            "Pmin": round(float(row.get("p_min_pu", 0)) * float(row.get("p_nom", 0)), 4),
            "marginal_cost": round(float(row.get("marginal_cost", 0)), 4),
        })

    branches = []
    for name, row in n.lines.iterrows():
        impedance_base = float(n.buses.at[row.bus0, "v_nom"]) ** 2 / 100
        branches.append({
            "fbus": bus_idx.get(row.bus0, 1),
            "tbus": bus_idx.get(row.bus1, 2),
            "r": float(row.get("r", 0)) / impedance_base,
            "x": float(row.get("x", 0.01)) / impedance_base,
            "b": float(row.get("b", 0)) * impedance_base,
            "rateA": round(float(row.get("s_nom", 0)), 4),
        })

    env = Environment(loader=FileSystemLoader(str(BASE_DIR / "templates")))
    tmpl = env.get_template("matpower.jinja2")
    content = tmpl.render(
        name=n.name,
        baseMVA=100,
        buses=buses,
        gens=gens,
        branches=branches,
    )
    change_log_service.log("export", "Network", "network.m", "Exported network as MATPOWER (.m)")
    return content


@router.get("/export/matpower")
def export_matpower():
    return StreamingResponse(
        io.StringIO(_export_matpower_text()),
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=network.m"},
    )


# ── Imports ───────────────────────────────────────────────────────────────────

def _replace_with(candidate) -> None:
    """Swap the parsed ``candidate`` in as the open network and clear any
    user-uploaded time series. The caller holds ``PyPSAService.get_lock()``."""
    from routers.network import _restore_user_ts
    # The same workspace gets a new network: a COPY of the undo stack keeps the
    # import undoable without touching the outgoing context's own history.
    PyPSAService.reset_network(carry="copy", network=candidate)
    _restore_user_ts({})


def _unreadable(what: str) -> HTTPException:
    return HTTPException(422, {
        "error_kind": "import_unreadable",
        "message": f"The file is not a readable {what} with at least one bus; "
                   "the open network is unchanged.",
    })


def _parse_candidate(what: str, parse, *, check_names: bool = True):
    """LC-43c: parse an uploaded file into a NEW network that is not yet open.

    Each import used to reset the open network first and parse into the blank
    one, so a file that did not parse left the session with no project and
    without its unsaved edits, and a MATPOWER file with no tables "imported" as
    an empty network. Parsing into a candidate means a refusal, for whatever
    reason, leaves the open project exactly as it was; `_replace_with` swaps
    the candidate in only once it has parsed and passed the name checks.
    The candidate is no one's network yet, so it is parsed WITHOUT
    `PyPSAService.get_lock()`: the lock is held for the swap only.

    A route's own refusal (an HTTPException, such as an unsafe zip member or a
    bad baseMVA) keeps its status and detail; any other parse failure, and a
    file that parses to no buses at all, is 422 ``import_unreadable``.
    """
    import pypsa

    candidate = pypsa.Network()
    candidate.name = "Untitled Project"
    try:
        parse(candidate)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - any parser failure is one refusal
        # The catch also covers our own mapping code (the CSV formula unescape,
        # the workbook rows, the MATPOWER tables): log the traceback so a bug
        # there is not hidden behind the user's 422 (GK B 53, I2).
        logger.warning("import of a %s refused as unreadable", what, exc_info=True)
        raise _unreadable(what) from exc
    if candidate.buses.empty:
        # A zip of no PyPSA tables, a workbook of no component sheets or a
        # MATPOWER file of no `mpc.bus` rows "parses" to an empty network.
        raise _unreadable(what)
    _refuse_reserved_buses(candidate)
    if check_names:
        _refuse_invalid_names(candidate)
    return candidate


def _unbind_session(db: DBSession, session: SessionRow | None) -> None:
    """Point the session at its scratch slot, where the import just landed.

    A raw import replaces the network with one that belongs to no saved
    project: `reset_network` publishes an UNBOUND context, which in server mode
    goes into the session's scratch slot. But each request re-resolves its
    context from the session's active-project POINTER, and nothing moved it —
    so the very next request resolved the previous project again and the
    import vanished behind a 200 that described a network nobody could see.
    Measured: import a 1-bus file over a 3-bus project, GET /network/buses,
    get the 3 buses back.

    `/network/reset` (New Project) had already met this and says so: "Leaving
    the pointer set would make the very next request re-resolve the old
    project ... the reset would appear to silently undo itself." An import is
    the same swap with content, so it needs the same un-pointing — which is
    also exactly what the UI does on its side, `setCurrentProject(null)`.

    Called on SUCCESS only. A refused import (`_parse_candidate`) raises
    before the swap, leaving the session on the project it was on, untouched.
    `session` is None in local mode, where there is no pointer to move.
    """
    if session is not None:
        active_project.set_active_project(db, session, None)


def _refuse_reserved_buses(n) -> None:
    """An imported network may not name a bus `ic:…` (the commercial reference
    frames' namespace, P2 WP2.2-0): the import is refused before it is opened."""
    from services.commercial.settlement_inputs import reserved_bus_name

    bad = [str(b) for b in n.buses.index if reserved_bus_name(b)]
    if bad:
        raise HTTPException(422, f"bus names starting 'ic:' are reserved: {bad[:5]}")


def _refuse_invalid_names(n) -> None:
    """OPEN-ITEMS item 8: an imported network may not bring in a name the
    create routes would refuse. The import is refused before it is opened, as above."""
    from services.component_names import invalid_names_in_network, refuse_invalid_names

    bad = invalid_names_in_network(n)
    if bad:
        refuse_invalid_names(bad)


@router.post("/import/netcdf")
async def import_netcdf(
    file: UploadFile = File(...),
    db: DBSession = Depends(get_db),
    session: SessionRow | None = Depends(current_session),
):
    data = await read_capped(file)
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
        f.write(data)
        tmp = pathlib.Path(f.name)
    try:
        with PyPSAService.get_netcdf_io_lock():
            n = _parse_candidate(
                "netCDF network",
                lambda c: PyPSAService.import_network_from_netcdf(c, tmp),
            )
        with PyPSAService.get_lock():
            _replace_with(n)
    finally:
        tmp.unlink(missing_ok=True)
    summary = _build_summary(n)
    change_log_service.log(
        "import", "Network", file.filename or "network.nc",
        f"Imported NetCDF '{file.filename}': {summary.buses} buses, {summary.generators} generators, {summary.snapshots} snapshots",
    )
    _unbind_session(db, session)
    return summary


@router.post("/import/csv")
async def import_csv(
    file: UploadFile = File(...),
    db: DBSession = Depends(get_db),
    session: SessionRow | None = Depends(current_session),
):
    import zipfile as _zip
    from services.adequacy.eh_columns import normalise_eh_columns
    data = await read_capped(file)
    with tempfile.TemporaryDirectory() as tmpdir:

        def parse(c):
            with _zip.ZipFile(io.BytesIO(data)) as zf:
                safe_extract(zf, tmpdir)
            _map_csv_folder(tmpdir, _unescape_formula)
            c.import_from_csv_folder(tmpdir)

        n = _parse_candidate("CSV bundle (a .zip of PyPSA CSV tables)", parse)
        normalise_eh_columns(n)
        with PyPSAService.get_lock():
            _replace_with(n)
    summary = _build_summary(n)
    change_log_service.log(
        "import", "Network", file.filename or "network_csv.zip",
        f"Imported CSV zip '{file.filename}': {summary.buses} buses, {summary.generators} generators, {summary.snapshots} snapshots",
    )
    _unbind_session(db, session)
    return summary


@router.post("/import/excel")
async def import_excel(
    file: UploadFile = File(...),
    db: DBSession = Depends(get_db),
    session: SessionRow | None = Depends(current_session),
):
    import openpyxl
    data = await read_capped(file)
    component_map = {
        "buses": "Bus", "generators": "Generator", "lines": "Line",
        "links": "Link", "loads": "Load", "storage_units": "StorageUnit",
        "stores": "Store", "transformers": "Transformer",
    }

    def parse(n):
        wb = openpyxl.load_workbook(io.BytesIO(data))
        if "network" in wb.sheetnames:
            # Native PyPSA exports include network/snapshot/dynamic sheets.
            # Keep their horizon and series; legacy custom component sheets
            # continue through the existing parser below.
            with tempfile.TemporaryDirectory() as directory:
                native_file = pathlib.Path(directory) / "network.xlsx"
                native_file.write_bytes(data)
                _map_xlsx_strings(native_file, _unescape_formula, data_only=True)
                n.import_from_excel(str(native_file), engine="openpyxl")
        else:
            for sheet_name in wb.sheetnames:
                comp_class = component_map.get(sheet_name)
                if comp_class is None:
                    continue
                ws = wb[sheet_name]
                headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
                for row in ws.iter_rows(min_row=2, values_only=True):
                    row_dict = dict(zip(headers, map(_unescape_formula, row)))
                    name = row_dict.pop("name", row_dict.pop(headers[0], None))
                    if name:
                        try:
                            n.add(comp_class, str(name), **{k: v for k, v in row_dict.items() if v is not None})
                        except Exception:
                            pass

    n = _parse_candidate("Excel workbook", parse)
    # P14: typed eh_* tags (a sheet's TRUE/1/blank → bool; netCDF-safe).
    from services.adequacy.eh_columns import normalise_eh_columns
    normalise_eh_columns(n)
    with PyPSAService.get_lock():
        _replace_with(n)
    summary = _build_summary(n)
    change_log_service.log(
        "import", "Network", file.filename or "network.xlsx",
        f"Imported Excel '{file.filename}': {summary.buses} buses, {summary.generators} generators",
    )
    _unbind_session(db, session)
    return summary


@router.post("/import/matpower")
async def import_matpower(
    file: UploadFile = File(...),
    db: DBSession = Depends(get_db),
    session: SessionRow | None = Depends(current_session),
):
    data = await read_capped(file)
    # No `_refuse_invalid_names`: every MATPOWER name is built from an int.
    n = _parse_candidate(
        "MATPOWER case", lambda c: _parse_matpower(c, data.decode("utf-8")),
        check_names=False,
    )
    with PyPSAService.get_lock():
        _replace_with(n)
    summary = _build_summary(n)
    change_log_service.log(
        "import", "Network", file.filename or "network.m",
        f"Imported MATPOWER '{file.filename}': {summary.buses} buses, {summary.generators} generators",
    )
    _unbind_session(db, session)
    return summary


def _parse_matpower(n, text: str) -> None:
    import re


    def _section(label):
        pattern = rf"mpc\.{label}\s*=\s*\[(.*?)\]"
        m = re.search(pattern, text, re.DOTALL)
        if not m:
            return []
        rows = []
        for line in m.group(1).strip().splitlines():
            line = re.sub(r"%.*", "", line).strip().rstrip(";").strip()
            if line:
                rows.append([float(x) for x in line.split()])
        return rows

    match = re.search(r"mpc\.baseMVA\s*=\s*([0-9.eE+-]+)", text)
    base_mva = float(match[1]) if match else 100.0
    import math
    if not math.isfinite(base_mva) or base_mva <= 0:
        raise HTTPException(422, "MATPOWER baseMVA must be positive and finite")
    buses_data = _section("bus")
    bus_map = {}
    for row in buses_data:
        bus_i = int(row[0])
        bus_name = f"bus_{bus_i}"
        bus_map[bus_i] = bus_name
        n.add("Bus", bus_name, v_nom=float(row[9]) if len(row) > 9 else 1.0)
        # MATPOWER embeds demand at the bus rather than in a separate table.
        pd, qd = (float(row[2]) if len(row) > 2 else 0), (float(row[3]) if len(row) > 3 else 0)
        if pd or qd:
            n.add("Load", f"load_{bus_i}", bus=bus_name, p_set=pd, q_set=qd)

    costs = _section("gencost")
    for i, row in enumerate(_section("gen")):
        bus_i = int(row[0])
        p_nom = float(row[8]) if len(row) > 8 else 0
        p_min = float(row[9]) if len(row) > 9 else 0
        cost = costs[i] if i < len(costs) else []
        marginal_cost = cost[4] if len(cost) >= 6 and int(cost[0]) == 2 and int(cost[3]) == 2 else 0
        n.add("Generator", f"gen_{i+1}",
              bus=bus_map.get(bus_i, f"bus_{bus_i}"),
              p_nom=p_nom, p_min_pu=p_min / p_nom if p_nom else 0,
              marginal_cost=marginal_cost)

    for i, row in enumerate(_section("branch")):
        fbus, tbus = int(row[0]), int(row[1])
        impedance_base = float(n.buses.at[bus_map[fbus], "v_nom"]) ** 2 / base_mva
        n.add("Line", f"line_{i+1}",
              bus0=bus_map.get(fbus, f"bus_{fbus}"),
              bus1=bus_map.get(tbus, f"bus_{tbus}"),
              r=float(row[2]) * impedance_base, x=float(row[3]) * impedance_base,
              b=float(row[4]) / impedance_base if len(row) > 4 else 0,
              s_nom=float(row[5]) if len(row) > 5 else 0.0)
