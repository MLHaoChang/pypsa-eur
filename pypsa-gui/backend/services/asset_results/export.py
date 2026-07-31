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
    omitted: list[tuple[str, str]] = []
    first_resp: dict | None = None

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

    assert first_resp is not None
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        about = _about_rows(first_resp, scope=scope, project=project,
                            from_iso=from_iso, to_iso=to_iso, period=period,
                            omitted=omitted)
        pd.DataFrame(about, columns=["Field", "Value"]).to_excel(
            xl, sheet_name="About", index=False, header=False)
        pd.DataFrame(scalar_rows[1:], columns=scalar_rows[0]).to_excel(
            xl, sheet_name="Summary", index=False)
        for sheet, (header, rows) in sheets.items():
            pd.DataFrame(rows, columns=header).to_excel(
                xl, sheet_name=sheet[:31], index=False)
    return buf.getvalue()
