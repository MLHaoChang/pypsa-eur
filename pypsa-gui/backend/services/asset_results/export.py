"""
Workbook builder. One `About` sheet of provenance, one `Summary` sheet of
scalars, then one sheet per exported category.

An exported file outlives the screen it came from, so every assumption that
shaped the numbers — source, horizon, period, view mode — is written down.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from .registry import CATEGORY_IDS, CATEGORY_LABELS
from .service import build_response

logger = logging.getLogger("pypsa_gui.asset_results")

SCOPES = ("view", "full")


def _about_rows(resp: dict, *, scope: str, project: str | None,
                from_iso, to_iso, period, omitted: list[tuple[str, str]]) -> list[list]:
    import pypsa

    rows: list[list[Any]] = [
        ["Asset", resp["asset"]["name"]],
        ["Component class", resp["asset"]["class"]],
        ["Carrier", resp["asset"].get("carrier", "")],
        ["Bus", resp["asset"].get("bus", "")],
        ["Project", project or "(unsaved)"],
        ["Export scope", scope],
        # For a full export `resp` is whichever category ran first, so naming
        # it here would always read "Summary" regardless of what the workbook
        # actually contains.
        ["Category", "(all applicable)" if scope == "full"
            else CATEGORY_LABELS.get(resp["category"], resp["category"])],
        ["View mode", resp["mode"]],
        ["Result source", resp["solve"]["source"]],
        ["Solver condition", resp["solve"].get("condition") or "—"],
        ["Objective", resp["solve"].get("objective")],
        ["Solve time (s)", resp["solve"].get("solve_time")],
        ["Horizon from", from_iso or "(full horizon)"],
        ["Horizon to", to_iso or "(full horizon)"],
        ["Period", str(period) if period is not None else "(all periods)"],
        ["PyPSA version", getattr(pypsa, "__version__", "unknown")],
        ["Generated at", datetime.now(timezone.utc).isoformat(timespec="seconds")],
    ]
    if resp["mode"] == "duration":
        rows.append(["Note", "Duration mode sorts EACH series independently — "
                             "a row is a rank, not a snapshot"])
    for cat_label, reason in omitted:
        rows.append([f"Omitted: {cat_label}", reason])
    return rows


def _data_rows(resp: dict) -> tuple[list, list[list]]:
    cols = resp["columns"]
    if resp["mode"] == "duration":
        header = ["rank", "pct_of_hours"] + [
            f"{c['label']} ({c['unit']})" if c["unit"] else c["label"] for c in cols]
        rows = []
        for i, rank in enumerate(resp["index"]):
            rows.append([rank, resp["pct_of_hours"][i]]
                        + [resp["series"][c["id"]][i] for c in cols])
        return header, rows

    first = "month" if resp["mode"] == "monthly" else "snapshot"
    header = [first]
    if resp.get("periods"):
        header.append("period")
    header += [f"{c['label']} ({c['unit']})" if c["unit"] else c["label"]
               for c in cols]
    rows = []
    for i, stamp in enumerate(resp["index"]):
        row = [stamp]
        if resp.get("periods"):
            row.append(resp["periods"][i])
        row += [resp["series"][c["id"]][i] for c in cols]
        rows.append(row)
    return header, rows


def build_workbook(
    n, component_class: str, name: str, *, scope: str, category: str,
    metric_ids: list[str], source: str, from_iso, to_iso, period, mode: str,
    project: str | None,
) -> bytes:
    import pandas as pd

    from .registry import metrics_for

    if scope == "view":
        categories = [category]
    else:
        categories = list(CATEGORY_IDS)

    sheets: dict[str, tuple[list, list[list]]] = {}
    scalar_rows: list[list] = [["Category", "Metric", "Value", "Unit", "Formula"]]
    headline_rows: list[list] = [
        ["Metric", "Value", "Unit", "Source tab", "Status", "Formula"]]
    omitted: list[tuple[str, str]] = []
    first_resp: dict | None = None
    headline: list[dict] = []

    for cat in categories:
        ids = metric_ids if scope == "view" else [
            m.id for m in metrics_for(component_class, cat)]
        # A full-scope export runs this eight times. One category raising must
        # cost that category, not the whole workbook — the sibling read
        # endpoint wraps the identical call for the same reason. Failures
        # become omissions with a reason, so the user still gets the other
        # seven sheets and can see what is missing and why.
        try:
            resp = build_response(
                n, component_class, name, category=cat, metric_ids=ids,
                source=source, from_iso=from_iso, to_iso=to_iso, period=period,
                mode=mode,
            )
        except Exception as exc:  # noqa: BLE001 — one bad category must not
            logger.exception("asset export: category %s failed", cat)
            omitted.append((CATEGORY_LABELS[cat], f"failed to compute: {exc}"))
            continue
        if first_resp is None:
            first_resp = resp
        if resp.get("headline"):
            headline = resp["headline"]
        st = next(c for c in resp["categories"] if c["id"] == cat)
        if st["status"] != "ok":
            omitted.append((CATEGORY_LABELS[cat], st.get("reason", "")))
            continue

        by_id = {m["id"]: m for m in resp["metrics"]}
        cat_scalar_rows: list[list] = []
        for mid, val in resp["scalars"].items():
            m = by_id.get(mid, {})
            label = m.get("label", mid)
            if isinstance(val, dict):
                for k, v in val.items():
                    row = [f"{label} — {k}", v, m.get("unit", ""), m.get("formula", "")]
                    scalar_rows.append([CATEGORY_LABELS[cat], *row])
                    cat_scalar_rows.append(row)
            else:
                row = [label, val, m.get("unit", ""), m.get("formula", "")]
                scalar_rows.append([CATEGORY_LABELS[cat], *row])
                cat_scalar_rows.append(row)

        if resp["columns"]:
            # Series data drives the category sheet; the category's own
            # scalars still land in the aggregate `Summary` sheet above.
            sheets[CATEGORY_LABELS[cat]] = _data_rows(resp)
        elif cat_scalar_rows and cat != "summary":
            # No series metrics at all (e.g. Capacity is scalar-only for
            # Generator) — an `ok` category must still get its own sheet, or
            # it silently vanishes from the workbook despite resolving `ok`.
            # `summary` is excluded: its label IS "Summary", which would
            # collide with the aggregate scalar sheet of the same name; its
            # content (identity/params) is already fully represented there.
            sheets[CATEGORY_LABELS[cat]] = (
                ["Metric", "Value", "Unit", "Formula"], cat_scalar_rows)

    if first_resp is None:
        # Every attempted category raised (for `scope=view` that is the ONE
        # requested category). Unlike the sibling read endpoint
        # (routers/asset_results.py::get_asset_results), this route has no
        # try/except of its own, so a bare `assert` here was the only guard —
        # it raises an unhandled AssertionError (an opaque 500 with no
        # detail) and vanishes entirely under `python -O`, where asserts are
        # compiled out and the function would instead crash further down on
        # `first_resp["asset"]` against `None`. Fail explicitly instead.
        raise HTTPException(
            500,
            f"Failed to export asset results for {component_class}/{name}: "
            "every requested category raised an exception (see server log).",
        )
    # The headline KPIs ride along on the `summary` response, which a full
    # export always runs. A `view` export of some other category has to ask
    # for them — the "Key results" sheet is the page a reader opens first,
    # and it should not depend on which tab happened to be on screen.
    if not headline:
        try:
            headline = build_response(
                n, component_class, name, category="summary", metric_ids=[],
                source=source, from_iso=from_iso, to_iso=to_iso, period=period,
                mode=mode,
            ).get("headline", [])
        except Exception:  # noqa: BLE001 — a missing summary must not cost
            logger.exception("asset export: headline KPIs failed")           # the workbook
            headline = []

    for h in headline:
        headline_rows.append([
            h["label"],
            h.get("value") if h.get("status") == "ok" else None,
            h.get("unit", ""),
            h.get("category_label", ""),
            "ok" if h.get("status") == "ok" else
            f"{h.get('status')}: {h.get('reason', '')}".strip(": "),
            h.get("formula", ""),
        ])

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        about = _about_rows(first_resp, scope=scope, project=project,
                            from_iso=from_iso, to_iso=to_iso, period=period,
                            omitted=omitted)
        pd.DataFrame(about, columns=["Field", "Value"]).to_excel(
            xl, sheet_name="About", index=False, header=False)
        pd.DataFrame(headline_rows[1:], columns=headline_rows[0]).to_excel(
            xl, sheet_name="Key results", index=False)
        pd.DataFrame(scalar_rows[1:], columns=scalar_rows[0]).to_excel(
            xl, sheet_name="Summary", index=False)
        for sheet, (header, rows) in sheets.items():
            pd.DataFrame(rows, columns=header).to_excel(
                xl, sheet_name=sheet[:31], index=False)
        _style(xl.book)
    return buf.getvalue()


# Two decimals everywhere, matching the on-screen table. The UNDERLYING value
# stays full precision — this is a display format, so a user who widens the
# column or re-reads the file in pandas still gets every digit.
_NUM_FORMAT = "#,##0.00"


def _style(book) -> None:
    """Number format + readable column widths on every sheet."""
    for ws in book.worksheets:
        widths: dict[int, int] = {}
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, bool):
                    # bools are ints in Python; a numeric format would render
                    # True as "1.00", which is not what the model said.
                    text = str(cell.value)
                elif isinstance(cell.value, (int, float)):
                    cell.number_format = _NUM_FORMAT
                    text = f"{cell.value:,.2f}"
                elif cell.value is None:
                    text = ""
                else:
                    text = str(cell.value)
                widths[cell.column] = min(
                    48, max(widths.get(cell.column, 9), len(text) + 2))
        for col, width in widths.items():
            ws.column_dimensions[
                ws.cell(row=1, column=col).column_letter].width = width
        # About is written header=False, so its first row is data, not a
        # header to pin.
        if ws.title != "About":
            ws.freeze_panes = "A2"
