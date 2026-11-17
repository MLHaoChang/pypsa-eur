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
* link efficiency — a value ``eh_stages.link_attr_series`` falls back on
  (``LINK_EFFICIENCY_DEFAULT``, lossless), and a conversion Link (its two
  buses carry different carriers) at exactly 1.0: PyPSA fills a blank
  efficiency with 1.0 on import, and lossless conversion is never measured.
  Slack and study-internal Links are left out.
"""

from __future__ import annotations

import math
from typing import Any

GOAL_SECTION = "scenarios"
ASSET_SECTION = "assets"
CARD = "reliability"

_COMPONENTS = (
    ("generators", "Generator", "units"),
    ("storage_units", "StorageUnit", "storage units"),
    ("links", "Link", "links"),
)


def _row(
    key: str,
    label: str,
    value: Any,
    shown: str,
    source: str,
    reason: str,
    section: str,
    *,
    component: str = "",
    entity: str = "",
    field: str = "",
) -> dict:
    return {
        "key": key,
        "label": label,
        "value": value,
        "shown": shown,
        "source": source,
        "reason": reason,
        "section": section,
        "card": CARD,
        "component": component,
        "entity": entity,
        "field": field,
    }


def _plain(raw: str) -> str:
    text = str(raw).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _number(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _goal_rows(cfg: Any, template: dict | None) -> list[dict]:
    from services.reliability_goal import STORED, TEMPLATE, effective_goal

    goal = effective_goal(cfg if cfg is not None else {}, template)
    out: list[dict] = []
    for key, label in (
        ("mc_draws", "Simulation runs per reliability check"),
        ("site_archetype", "Site type"),
    ):
        g = goal[key]
        if g["source"] == STORED:
            continue
        value = g["value"]
        reason = (
            "the template's recommendation"
            if g["source"] == TEMPLATE
            else "the engine default"
        )
        shown = f"{value} runs" if key == "mc_draws" else _plain(value)
        out.append(
            _row(key, label, value, shown, g["source"], reason, GOAL_SECTION, field=key)
        )
    return out


def _carrier_label(n, carrier: str) -> str:
    nice = n.carriers["nice_name"].get(carrier)
    if isinstance(nice, str) and nice.strip():
        return nice.strip()
    return _plain(carrier) if carrier else "Unnamed carrier"


def _outage_rows(n) -> list[dict]:
    from services.adequacy.occurrence import (
        CARRIER_DEFAULTS,
        _is_set,
        resolve_outage_params,
    )

    out: list[dict] = []
    for attr, component, noun in _COMPONENTS:
        df = getattr(n, attr)
        params = resolve_outage_params(n, attr)
        carriers = df["carrier"].astype(str).str.strip().str.lower()
        groups: dict[tuple[str, str], list[str]] = {}
        no_basis: list[str] = []
        for name in df.index:
            src = params.at[name, "source"]
            # "Outages already in availability" zeroes the rate the run uses,
            # so a carrier default the run does not apply is not assumed.
            if src == "carrier_default" and not params.at[name, "outages_in_availability"]:
                groups.setdefault(("rate", carriers[name]), []).append(str(name))
            elif (
                src == "asset"
                and not _is_set(
                    df.at[name, "mttr_hours"] if "mttr_hours" in df.columns else None
                )
                and carriers[name] in CARRIER_DEFAULTS
            ):
                groups.setdefault(("mttr", carriers[name]), []).append(str(name))
            if src == "asset" and not _is_set(
                df.at[name, "outage_rate_basis"] if "outage_rate_basis" in df.columns else None
            ):
                no_basis.append(str(name))
        for (kind, carrier), names in groups.items():
            d = CARRIER_DEFAULTS[carrier]
            what = _carrier_label(n, carrier)
            reason = f"the {what.lower()} class default: {d.source}"
            entity = ", ".join(names)
            if kind == "rate":
                out.append(
                    _row(
                        f"outage_rate:{component}:{carrier}",
                        f"Outage rate of {what.lower()} {noun} ({', '.join(names)})",
                        d.rate,
                        f"{_number(d.rate * 100)} % {d.basis}, repair {_number(d.mttr_hours)} h",
                        "carrier_default",
                        reason,
                        ASSET_SECTION,
                        component=component,
                        entity=entity,
                        field="outage_rate_value",
                    )
                )
            else:
                out.append(
                    _row(
                        f"mttr:{component}:{carrier}",
                        f"Repair time of {what.lower()} {noun} ({', '.join(names)})",
                        d.mttr_hours,
                        f"{_number(d.mttr_hours)} h",
                        "carrier_default",
                        reason,
                        ASSET_SECTION,
                        component=component,
                        entity=entity,
                        field="mttr_hours",
                    )
                )
        if no_basis:
            # resolve_outage_params reads an asset rate without a basis as FOR.
            out.append(
                _row(
                    f"outage_basis:{component}",
                    f"Outage rate basis of {noun} ({', '.join(no_basis)})",
                    "FOR",
                    "FOR (forced outage rate)",
                    "default",
                    "an outage rate without a basis is read as a forced outage rate",
                    ASSET_SECTION,
                    component=component,
                    entity=", ".join(no_basis),
                    field="outage_rate_basis",
                )
            )
    return out


def _efficiency_rows(n) -> list[dict]:
    from models.energy_hub import EH_INTERNAL_ROLES
    from services.adequacy.eh_stages import LINK_EFFICIENCY_DEFAULT, link_attr_series
    from services.adequacy.slack import is_slack_name

    links = n.links
    bus_carrier = n.buses["carrier"].astype(str)
    out: list[dict] = []
    for name in links.index:
        role = str(links.at[name, "eh_role"]) if "eh_role" in links.columns else ""
        if is_slack_name(str(name)) or role in EH_INTERNAL_ROLES:
            continue
        _, fell_back = link_attr_series(n, name, "efficiency", LINK_EFFICIENCY_DEFAULT)
        if fell_back:
            reason = "no efficiency is set, so the study reads it as lossless"
        elif (
            name not in n.links_t.efficiency.columns
            and bus_carrier.get(links.at[name, "bus0"])
            != bus_carrier.get(links.at[name, "bus1"])
            and float(links.at[name, "efficiency"]) == LINK_EFFICIENCY_DEFAULT
        ):
            reason = (
                "a conversion at exactly 100 % is the value a blank efficiency gets"
            )
        else:
            continue
        out.append(
            _row(
                f"efficiency:{name}",
                f"Conversion efficiency of {name}",
                LINK_EFFICIENCY_DEFAULT,
                "100 % (lossless)",
                "default",
                reason,
                ASSET_SECTION,
                component="Link",
                entity=str(name),
                field="efficiency",
            )
        )
    return out


def voll_is_set(cfg: Any) -> bool:
    """A usable VOLL: a finite number above 0. Missing, None, NaN, inf and
    0 are "not set" — never priced as 0.0 (template-e2e 06)."""
    get = cfg.get if isinstance(cfg, dict) else (lambda k: getattr(cfg, k, None))
    try:
        v = float(get("voll"))
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0


def voll_missing_row() -> dict:
    """The row a surface that serves unpriced figures carries (GK B Q1)."""
    return _row(
        "voll",
        "Value of lost load",
        None,
        "not set",
        "missing",
        "unserved energy is not priced, so every cost of it is left blank",
        GOAL_SECTION,
        field="voll",
    )


def run_assumptions(n, cfg: Any, template: dict | None) -> list[dict]:
    """The rows, goal fields first. ``n`` None (no network loaded) gives the
    goal rows alone; ``cfg`` is a SolverConfig or its dict."""
    rows = _goal_rows(cfg, template)
    if n is not None:
        rows += _outage_rows(n) + _efficiency_rows(n)
    return rows


def study_assumptions(
    rows: list[dict], goal_sources: dict[str, str], requested: dict[str, Any]
) -> list[dict]:
    """The rows a study run records. A goal field the request set to a value
    of its own (``override``, ``eh_request_with_goal``) was chosen for that
    run, so its row goes; one the request merely repeated is still the
    assumed value."""
    out = []
    for r in rows:
        key = r["field"] if r["section"] == GOAL_SECTION else None
        if (
            key
            and goal_sources.get(key) == "override"
            and requested.get(key) != r["value"]
        ):
            continue
        out.append(r)
    return out
