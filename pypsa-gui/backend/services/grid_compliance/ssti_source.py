"""GC-08's tables in the report pack: the SSTI unit-interaction-factor screen
and the sub-synchronous series-resonance scan of the campus run.

The tables come through the scorecard's own validated read
(``grid_readiness.ssti_screens_for``, GK A2 S1), never a second parser of the
run files. Each caption is the scorecard row's note, so a row the scorecard
refused (a damaged table, a contradicting verdict, a profile moved since the
run) states its reason there, and its Verdict column judges nothing. The
off-grid rule is the scorecard's ``grid_case`` (S2): islanded, the UIF rows
read "not evaluated (islanded)" and their UIF "—"; off-grid, the scan from the
PCC leaves the pack as its rows leave the scorecard.

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


def _apart(v, ref) -> tuple[str, str]:
    """``v`` and the limit ``ref`` it is judged against, as ``_number`` shows them, with more figures where
    four would print two different values alike (GK A2 B3: 0.10004 against 0.1 never reads "0.1")."""
    shown, limit = _number(v), _number(ref)
    from services.adequacy.grid_readiness import _ssti_hz

    # a blank or unreadable side never prints like a number, so only two different numbers tie
    x, y = (None if _missing(u) else _ssti_hz(u) for u in (v, ref))
    figures = 4
    while shown == limit and x != y and figures < 17:
        figures += 1
        shown, limit = f"{x:.{figures}g}", f"{y:.{figures}g}"
    return shown, limit


def _text(v) -> str:
    """A name the run wrote, as plain text (S4): blank when empty; an id the CSV read as a number prints
    whole (GK A2 B2: bus 110012, never "1.1e+05"), as the scorecard's note prints it."""
    if _missing(v):
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, float):
        if not math.isfinite(v):
            return NON_FINITE
        if v.is_integer():
            return str(int(v))
    return str(v)


def _note(v) -> str:
    """A note cell as the scorecard reads one: a string, else an unreadable note (S4)."""
    if _missing(v):
        return ""
    return v.strip() if isinstance(v, str) else UNREADABLE_NOTE


def _judged(value, row: dict) -> str:
    """One run row's verdict under the scorecard row's own limit and rule (a judged row's values are all
    finite: the scorecard refused the table otherwise)."""
    from services.adequacy.grid_readiness import _judge

    return _judge(float(value), row["limit"], row["rule"])


def _cell(r, column: str, kind):
    return kind(r[column]) if column in r else ""


def _uif_table(row: dict, frame, case: str, path: str) -> Table:
    try:
        from gridspine.static.ssti_uif import ROLE_ISLAND
    except (
        ImportError
    ):  # pragma: no cover - build-shape branch: no gridspine, no island rows to print
        ROLE_ISLAND = None

    rows = []
    role = frame["role"].astype(str) if frame is not None and "role" in frame else None
    judged = frame[role == "converter bus"] if role is not None else None
    for _, r in judged.iterrows() if judged is not None else ():
        uif, threshold = _apart(r.get("uif"), r.get("threshold"))
        if case == "islanded":
            # the UIF counts the grid's infeed, which an island lacks: no number to show
            verdict, uif = ISLANDED, NON_FINITE
        elif row["verdict"] in ("pass", "warn"):
            verdict = _judged(r["uif"], row)
        else:
            verdict = NOT_JUDGED
        rows.append(
            [
                _cell(r, "bus", _text),
                _cell(r, "case", _text),
                _cell(r, "machine", _text),
                uif,
                threshold,
                verdict,
                _cell(r, "note", _note),
            ]
        )
    # GC-08b: the island cases' rows are printed, not judged, until the scorecard judges them (GC-08e)
    island = frame[role == ROLE_ISLAND] if role is not None and ROLE_ISLAND else None
    for _, r in island.iterrows() if island is not None else ():
        uif, threshold = _apart(r.get("uif"), r.get("threshold"))
        rows.append(
            [
                _cell(r, "bus", _text),
                _cell(r, "case", _text),
                _cell(r, "machine", _text),
                uif,
                threshold,
                NOT_JUDGED,
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
    try:
        from gridspine.static.subsync_scan import SERIES
    except ImportError as exc:  # pragma: no cover - build-shape branch
        raise SstiSourceUnavailable(f"The SSTI screens need gridspine ({exc})") from exc

    from services.adequacy.grid_readiness import subsync_judged

    rows = []
    series = (
        frame[frame["kind"].astype(str) == SERIES]
        if frame is not None and "kind" in frame
        else None
    )
    # a row the card skips (GK A2 H1: undecided, or no resonance in the band) is never judged here either
    judged = subsync_judged(series) if series is not None else None
    for i, r in series.iterrows() if series is not None else ():
        verdict = (
            _judged(r["margin_hz"], row)
            if row["verdict"] in ("pass", "warn") and judged[i]
            else NOT_JUDGED
        )
        rows.append(
            [
                _cell(r, "case", _text),
                _cell(r, "machine", _text),
                _cell(r, "f_er_hz", _number),
                _cell(r, "f_comp_hz", _number),
                _cell(r, "mode_hz", _number),
                _apart(r.get("margin_hz"), row["limit"])[0],
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
