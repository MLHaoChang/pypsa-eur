"""Checked copilot charts (checked-results S10): drawn from a checked answer's
evidence only, and bound to that answer.

A chart may draw only per-snapshot series the request's evidence `raw` holds,
over the evidence window, every value as stored (no resampling). Loading % is
the branch profile's own `utilisation_series`. A requested series the evidence
does not hold is listed as not drawn, with a plain reason; nothing is fetched
from the live network. The evidence is re-checked at render time (the solve
must still match its inputs and no AC power flow may have overwritten the LP);
an outdated chart is refused, not drawn. The PNG carries the same footer as the
answer card, and its manifest names the evidence and its source hash, which
`assurance_artifacts.verify_chart` re-checks on download.
"""

from __future__ import annotations

import hashlib
import io
import math
import threading

MAX_SERIES = 8
MAX_POINTS = 1200  # one per pixel column at the rendered width; never decimated
_LOCK = threading.Lock()
POWER = "MW per snapshot (not energy-weighted)"
# Plain titles for the "Not drawn" list; codes stay in the receipt.
REASONS = {
    "not_in_evidence": "Not part of this answer's checked evidence",
    "missing_values": "Some values in the window are missing",
    "outdated_evidence": "The solve no longer matches its inputs",
    "too_many_points": "Too many snapshots to draw every value",
    "draft_not_checked": "The answer could not be checked",
    "render_failed": "The chart could not be saved",
}


def _window(index, scope):
    if "start" not in scope:
        return 0, len(index) - 1
    import pandas as pd

    return (index.index(pd.Timestamp(scope["start"]).isoformat()),
            index.index(pd.Timestamp(scope["end"]).isoformat()))


def _branch(raw, entity):
    matches = [b for b in raw.get("branches", {}).values() if b.get("name") == entity]
    return matches[0] if len(matches) == 1 else None


def series_table(record):
    """{series: (unit, values over the full index) or None}, and the index, for the
    evidence's profile. Values are the evidence's own, untouched."""
    from harness import branch_calculations

    scope, raw = record["request_scope"], record["raw"]
    profile, entity = scope["profile"], scope["entity_id"]
    if profile == "generator-dispatch/1":
        gen = (raw.get("generators") or {}).get(entity) or raw
        return gen["index"], {"p": ("MW", POWER, gen["inputs"]["p"])}
    if profile == "storage-unit-dispatch/1":
        unit = raw["storage_unit"]
        inputs = unit["inputs"]
        return unit["index"], {
            "p_store": ("MW", POWER, inputs["p_store"]),
            "p_dispatch": ("MW", POWER, inputs["p_dispatch"]),
            "state_of_charge": ("MWh", "MWh stored at each snapshot", inputs["state_of_charge"]),
        }
    if profile == "bus-price/1":
        return raw["index"], {"marginal_price": ("EUR/MWh", "EUR/MWh per snapshot", raw["price"])}
    if profile == "branch-loading/1":
        branch = _branch(raw, entity)
        if branch is None:
            return raw["index"], {}
        series = branch_calculations.utilisation_series(branch, 0, len(raw["index"]) - 1)
        loading = None if series is None else [100 * u for u in series[1]]
        return raw["index"], {
            "p0": ("MW", POWER, branch["p0"]),
            "p1": ("MW", POWER, branch["p1"]),
            "loading": ("%", "% of rating x s_max_pu per snapshot", loading),
        }
    return raw.get("index") or [], {}


def dataset(record, requested):
    """The drawn dataset and a slot per requested series."""
    index, table = series_table(record)
    a, b = _window(index, record["request_scope"])
    window = index[a: b + 1]
    drawn, slots = {}, []
    for name in requested:
        entry = table.get(name)
        values = None if entry is None or entry[2] is None else entry[2][a: b + 1]
        if entry is None:
            reason = "not_in_evidence"
        elif values is None or any(v is None or not math.isfinite(v) for v in values):
            reason = "missing_values"
        elif len(values) > MAX_POINTS:
            reason = "too_many_points"
        else:
            drawn[name] = (entry[0], entry[1], list(values))
            slots.append({"series": name, "disposition": "drawn", "unit": entry[0]})
            continue
        slots.append({"series": name, "disposition": "not_drawn", "reason": reason, "title": REASONS[reason]})
    data = {"index": list(window), "columns": list(drawn), "values": [drawn[c][2] for c in drawn],
            "units": {c: drawn[c][0] for c in drawn}, "axis_labels": {c: drawn[c][1] for c in drawn}}
    return data, slots, {"start": window[0] if window else None, "end": window[-1] if window else None,
                         "snapshots": len(window)}


def caption(slots, window):
    lines = [f"Chart drawn from checked evidence · {sum(s['disposition'] == 'drawn' for s in slots)} series · "
             f"{window['start']} to {window['end']}"]
    lines += [f"Not drawn: {s['series']} — {s['title']}" for s in slots if s["disposition"] != "drawn"]
    return lines


def refused(requested, reason):
    return {"status": "not_drawn", "reason": reason, "title": REASONS[reason],
            "series": [{"series": s, "disposition": "not_drawn", "reason": reason, "title": REASONS[reason]}
                       for s in requested]}


def render(ctx, record, requested):
    """The receipt's chart block: the bound download, or why nothing was drawn."""
    from harness import profile_common as common
    from services import assistant_tools, chat_tools
    from services.assurance_artifacts import canonical_sha256

    requested = list(dict.fromkeys(requested))[:MAX_SERIES]
    if not common.lp_outputs_live(ctx):  # the evidence is re-checked at render time
        return refused(requested, "outdated_evidence")
    data, slots, window = dataset(record, requested)
    if not data["columns"]:
        return {"status": "not_drawn", "reason": "nothing_drawable", "title": "No requested series could be drawn",
                "series": slots}
    lines = caption(slots, window) + [f"Evidence {record['evidence_id']}"]
    png = _draw(record, data, lines)
    project, root, _owner = assistant_tools._project()
    byte_hash = hashlib.sha256(png).hexdigest()
    manifest = {
        "schema_version": 1, "project_id": str(project.id), "source_kind": "checked_evidence",
        "evidence_id": record["evidence_id"], "source_sha256": record["source_sha256"],
        "profile": record["request_scope"]["profile"], "entity_id": record["request_scope"]["entity_id"],
        "requested": requested, "window": window, "series": slots, "caption": lines,
        "dataset": data, "dataset_sha256": canonical_sha256(data),
        "render_spec": {"kind": "line", "points": "every snapshot as stored", "caption": lines},
        "artifact_sha256": byte_hash,
    }
    manifest["render_spec_sha256"] = canonical_sha256(manifest["render_spec"])
    provenance = {"manifest": manifest}
    assistant_tools.tasks._save(root / "assistant_artifacts" / (byte_hash[:16] + ".json"), provenance)
    artifact = chat_tools._save_agent_export(
        png, f"checked_{record['request_scope']['entity_id']}.png"[:100], "image/png",
        assurance_sha256=canonical_sha256(provenance))
    return {"status": "drawn", "file_id": artifact["file_id"], "filename": artifact["filename"],
            "download_url": artifact["download_url"], "png_sha256": byte_hash,
            "evidence_id": record["evidence_id"], "window": window, "series": slots, "caption": lines}


def safe_render(ctx, record, requested):
    """`render`, with a failure to save the file reported as not drawn."""
    from fastapi import HTTPException

    try:
        return render(ctx, record, requested)
    except (HTTPException, OSError, ValueError, KeyError):
        return refused(list(dict.fromkeys(requested))[:MAX_SERIES], "render_failed")


def _draw(record, data, lines):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    units = list(dict.fromkeys(data["units"].values()))
    with _LOCK:
        figure = Figure(figsize=(10, 2.6 * len(units) + 1.2), dpi=120)
        FigureCanvasAgg(figure)
        axes = figure.subplots(len(units), 1, squeeze=False)[:, 0]
        x = list(range(len(data["index"])))
        for axis, unit in zip(axes, units):
            for name, values in zip(data["columns"], data["values"]):
                if data["units"][name] == unit:
                    axis.plot(x, values, label=name, drawstyle="steps-post")
                    label = data["axis_labels"][name]
            axis.set_ylabel(label, fontsize=8)
            axis.legend(fontsize=8)
        axes[-1].set_xlabel(f"Snapshot ({data['index'][0]} to {data['index'][-1]})", fontsize=8)
        scope = record["request_scope"]
        axes[0].set_title(f"{scope['entity_id']} · {scope['profile']}", fontsize=10)
        figure.text(0.01, 0.01, "\n".join(lines), fontsize=7, va="bottom", ha="left")
        figure.tight_layout(rect=(0, 0.04 + 0.025 * len(lines), 1, 1))
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png")
    return buffer.getvalue()


def verify(manifest):
    """True when a checked chart's dataset is still its evidence's: the evidence
    record's source hash and the series values, re-extracted, must match."""
    from services import assurance_records

    record = assurance_records.load(assurance_records.actor_id(), manifest["evidence_id"], "evidence")
    if record["source_sha256"] != manifest["source_sha256"]:
        return False
    data, slots, window = dataset(record, manifest["requested"])
    return data == manifest["dataset"] and slots == manifest["series"] and window == manifest["window"]
