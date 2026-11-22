"""GC-08's tables in the report pack: the SSTI unit-interaction-factor screen
and the sub-synchronous series-resonance scan of the campus run.

The tables come through the scorecard's own validated read
(``grid_readiness.ssti_screens_for``, GK A2 S1), never a second parser of the
run files. Each caption is the scorecard row's note, so a row the scorecard
refused (a damaged table, a contradicting verdict, a profile moved since the
run) states its reason there, and its Verdict column judges nothing. The
off-grid rule is the scorecard's ``grid_case`` (S2): islanded, the UIF rows
read "not evaluated (islanded)"; off-grid, the scan from the PCC leaves the
pack as its rows leave the scorecard.

No run with the screens yet gives None (pending). A failed stage or an
unreadable record raises ``SstiSourceUnavailable`` (GK A2 (a)), so the pack
shows the package unavailable rather than a failed run as delivered.
"""

from __future__ import annotations

import math

from models.report import Table

UIF_COLUMNS = ["Bus", "Case", "Machine", "UIF", "Threshold", "Verdict", "Note"]
RESONANCE_COLUMNS = [
    "Case",
    "Machine",
    "Resonance (Hz)",
    "Complement (Hz)",
    "Mode (Hz)",
    "Margin (Hz)",
    "Verdict",
    "Note",
]
#: A row the scorecard did not judge: the table shows what the run wrote, never a verdict.
NOT_JUDGED = "not judged"
ISLANDED = "not evaluated (islanded)"
NON_FINITE = "—"
UNREADABLE_NOTE = "an unreadable note"


class SstiSourceUnavailable(RuntimeError):
    """The SSTI screens failed or their record is unreadable; the message is the scorecard's plain reason."""


def _missing(v) -> bool:
    return (
        v is None
        or (isinstance(v, float) and math.isnan(v))
        or (isinstance(v, str) and not v.strip())
    )


def _number(v) -> str:
    """A number the run wrote, read as the scorecard reads one (``_ssti_hz``), in four significant figures;
    blank when empty, "—" when not a finite number."""
    from services.adequacy.grid_readiness import _ssti_hz

    if _missing(v):
        return ""
    x = _ssti_hz(v)
    return NON_FINITE if x is None else f"{x:.4g}"


def _text(v) -> str:
    """A name the run wrote, as plain text (S4): blank when empty, a number as a number."""
    return v.strip() if isinstance(v, str) else _number(v)


def _note(v) -> str:
    """A note cell as the scorecard reads one: a string, else an unreadable note (S4)."""
    if _missing(v):
        return ""
    return v.strip() if isinstance(v, str) else UNREADABLE_NOTE


def _cell(r, column: str, kind):
    return kind(r[column]) if column in r else ""


def _uif_table(row: dict, frame, case: str, path: str) -> Table:
    rows = []
    judged = (
        frame[frame["role"].astype(str) == "converter bus"]
        if frame is not None and "role" in frame
        else None
    )
    for _, r in judged.iterrows() if judged is not None else ():
        if case == "islanded":
            verdict = ISLANDED
        elif row["verdict"] in ("pass", "warn"):
            verdict = "warn" if float(r["uif"]) > row["limit"] else "pass"
        else:
            verdict = NOT_JUDGED
        rows.append(
            [
                _cell(r, "bus", _text),
                _cell(r, "case", _text),
                _cell(r, "machine", _text),
                _cell(r, "uif", _number),
                _cell(r, "threshold", _number),
                verdict,
                _cell(r, "note", _note),
            ]
        )
    return Table(
        table_id="ssti_uif",
        columns=UIF_COLUMNS,
        rows=rows,
        caption=row["note"],
        source_path=path,
    )


def _resonance_table(row: dict, frame, path: str) -> Table:
    from gridspine.static.subsync_scan import SERIES

    rows = []
    series = (
        frame[frame["kind"].astype(str) == SERIES]
        if frame is not None and "kind" in frame
        else None
    )
    for _, r in series.iterrows() if series is not None else ():
        verdict = (
            ("pass" if float(r["margin_hz"]) >= row["limit"] else "warn")
            if row["verdict"] in ("pass", "warn")
            else NOT_JUDGED
        )
        rows.append(
            [
                _cell(r, "case", _text),
                _cell(r, "machine", _text),
                _cell(r, "f_er_hz", _number),
                _cell(r, "f_comp_hz", _number),
                _cell(r, "mode_hz", _number),
                _cell(r, "margin_hz", _number),
                verdict,
                _cell(r, "note", _note),
            ]
        )
    return Table(
        table_id="subsync_series_resonance",
        columns=RESONANCE_COLUMNS,
        rows=rows,
        caption=row["note"],
        source_path=path,
    )


def gc08_tables(project) -> list[Table] | None:
    """The ``SOURCES["GC-08"]`` provider (``grid_compliance.sources``)."""
    from services.adequacy import grid_readiness as G

    screens = G.ssti_screens_for(project)
    read = screens["read"]
    if read["state"] == "pending":
        return None
    if read["state"] != "read":
        raise SstiSourceUnavailable(read["reason"])
    rows = {r["id"]: r for r in screens["rows"]}
    tables = [
        _uif_table(rows[G.SSTI_UIF], read["uif"], screens["case"], read["files"]["uif"])
    ]
    if screens["case"] == "grid":
        tables.append(
            _resonance_table(
                rows[G.SUBSYNC_SR], read["resonance"], read["files"]["resonance"]
            )
        )
    return tables
