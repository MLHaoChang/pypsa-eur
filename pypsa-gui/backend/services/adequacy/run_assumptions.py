"""The run inputs a study falls back on when the project does not set them
(template-e2e 08, DECISION-GAPS-1: nothing silent).

``run_assumptions`` is the one place that names them. Readiness raises each
row before the run (``flow_service``), the study's run record carries them,
and the report prints them — so the value the run used is the value the user
was shown. The function only reads; the engines keep their own fallbacks and
arithmetic, and these rows describe exactly those fallbacks:

* ``mc_draws`` / ``site_archetype`` — ``reliability_goal.effective_goal``,
  any source but ``stored``;
* per-carrier outage rate and repair time — ``occurrence.resolve_outage_params``
  (``carrier_default``, or an asset rate whose repair time is the carrier's);
* link efficiency — a non-finite value, which ``eh_stages._series`` reads
  as 1.0 (lossless).
"""
from __future__ import annotations

import math
from typing import Any

GOAL_SECTION = "scenarios"
ASSET_SECTION = "assets"
CARD = "reliability"

_COMPONENTS = (("generators", "Generator", "units"),
               ("storage_units", "StorageUnit", "storage units"),
               ("links", "Link", "links"))
_LISTED = 5


def _row(key: str, label: str, value: Any, shown: str, source: str, reason: str,
         section: str, *, component: str = "", entity: str = "",
         field: str = "") -> dict:
    return {"key": key, "label": label, "value": value, "shown": shown,
            "source": source, "reason": reason, "section": section, "card": CARD,
            "component": component, "entity": entity, "field": field}


def _plain(raw: str) -> str:
    text = str(raw).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _number(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _goal_rows(cfg: Any, template: dict | None) -> list[dict]:
    from services.reliability_goal import STORED, TEMPLATE, effective_goal

    goal = effective_goal(cfg if cfg is not None else {}, template)
    out: list[dict] = []
    for key, label in (("mc_draws", "Simulation runs per reliability check"),
                       ("site_archetype", "Site type")):
        g = goal[key]
        if g["source"] == STORED:
            continue
        value = g["value"]
        reason = ("the template's recommendation" if g["source"] == TEMPLATE
                  else "the engine default")
        shown = f"{value} runs" if key == "mc_draws" else _plain(value)
        out.append(_row(key, label, value, shown, g["source"], reason,
                        GOAL_SECTION, field=key))
    return out


def _carrier_label(n, carrier: str) -> str:
    carriers = getattr(n, "carriers", None)
    if carriers is not None and carrier in carriers.index and "nice_name" in carriers:
        nice = str(carriers.at[carrier, "nice_name"] or "").strip()
        if nice:
            return nice
    return _plain(carrier) if carrier else "Unnamed carrier"


def _names(names: list[str]) -> str:
    more = f" and {len(names) - _LISTED} more" if len(names) > _LISTED else ""
    return ", ".join(names[:_LISTED]) + more


def _outage_rows(n) -> list[dict]:
    from services.adequacy.occurrence import CARRIER_DEFAULTS, _is_set, resolve_outage_params

    out: list[dict] = []
    for attr, component, noun in _COMPONENTS:
        df = getattr(n, attr, None)
        if df is None or df.empty or "carrier" not in df.columns:
            continue
        params = resolve_outage_params(n, attr)
        carriers = df["carrier"].astype(str).str.strip().str.lower()
        groups: dict[tuple[str, str], list[str]] = {}
        for name in df.index:
            src = params.at[name, "source"]
            if src == "carrier_default":
                groups.setdefault(("rate", carriers[name]), []).append(str(name))
            elif src == "asset" and not _is_set(df.at[name, "mttr_hours"]
                                                if "mttr_hours" in df.columns else None) \
                    and carriers[name] in CARRIER_DEFAULTS:
                groups.setdefault(("mttr", carriers[name]), []).append(str(name))
        for (kind, carrier), names in groups.items():
            d = CARRIER_DEFAULTS[carrier]
            what = _carrier_label(n, carrier)
            reason = f"the {what.lower()} class default: {d.source}"
            entity = ", ".join(names)
            if kind == "rate":
                out.append(_row(
                    f"outage_rate:{component}:{carrier}",
                    f"Outage rate of {what.lower()} {noun} ({_names(names)})",
                    d.rate,
                    f"{_number(d.rate * 100)} % {d.basis}, repair {_number(d.mttr_hours)} h",
                    "carrier_default", reason, ASSET_SECTION,
                    component=component, entity=entity, field="outage_rate_value"))
            else:
                out.append(_row(
                    f"mttr:{component}:{carrier}",
                    f"Repair time of {what.lower()} {noun} ({_names(names)})",
                    d.mttr_hours, f"{_number(d.mttr_hours)} h",
                    "carrier_default", reason, ASSET_SECTION,
                    component=component, entity=entity, field="mttr_hours"))
    return out


def _efficiency_rows(n) -> list[dict]:
    links = getattr(n, "links", None)
    if links is None or links.empty or "efficiency" not in links.columns:
        return []
    series = getattr(getattr(n, "links_t", None), "efficiency", None)
    varying = set(getattr(series, "columns", []))
    out: list[dict] = []
    for name in links.index:
        if name in varying:
            continue
        try:
            v = float(links.at[name, "efficiency"])
        except (TypeError, ValueError):
            v = float("nan")
        if math.isfinite(v):
            continue
        out.append(_row(f"efficiency:{name}", f"Conversion efficiency of {name}",
                        1.0, "100 % (lossless)", "default",
                        "no efficiency is set, so the study reads it as lossless",
                        ASSET_SECTION, component="Link", entity=str(name),
                        field="efficiency"))
    return out


def run_assumptions(n, cfg: Any, template: dict | None) -> list[dict]:
    """The rows, goal fields first. ``n`` None (no network loaded) gives the
    goal rows alone; ``cfg`` is a SolverConfig or its dict."""
    rows = _goal_rows(cfg, template)
    if n is not None:
        rows += _outage_rows(n) + _efficiency_rows(n)
    return rows


def study_assumptions(rows: list[dict], goal_sources: dict[str, str],
                      requested: dict[str, Any]) -> list[dict]:
    """The rows a study run records. A goal field the request set to a value
    of its own (``override``, ``eh_request_with_goal``) was chosen for that
    run, so its row goes; one the request merely repeated is still the
    assumed value."""
    out = []
    for r in rows:
        key = r["field"] if r["section"] == GOAL_SECTION else None
        if key and goal_sources.get(key) == "override" and requested.get(key) != r["value"]:
            continue
        out.append(r)
    return out
