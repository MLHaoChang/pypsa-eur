"""
The grid-readiness scorecard (DC grid readiness 02): a red-flag screening of
a project against its connection's limits and a grid-code profile, before the
power-system architecture is fixed.

**Generic** (owner decision D2): one function for every project. A template
is an instantiation, never a special case: it reaches the scorecard only
through what it writes into the project, its ledger rows (``eh_template.json``
``ledger``, items ``connection_ramp``, ``oscillation``, ``import_efficiency``,
``fault_level``, ``lole_goal``), its island sidecar and the opt-in AI-training
envelope (DC 01, ``dc_load_profile.read_envelope``). A project with no ledger
row for a limit gets the generic placeholder below, marked assumed.

**Advisory.** It reads files and a network copy, solves nothing, and feeds
neither ``validate_for_run`` nor the flow's ``run_ready`` (project workflow
R1-R4). A ``fail`` is a red flag to take to the network operator, never a
refusal.

Each row: ``{id, requirement, limit, unit, source, project_value, verdict,
assumed, note, section, target}``, ``verdict`` one of pass|warn|fail|unknown
and ``target`` the flow's ``{section, panel, component, entity, field}``.

Rules (gatekeeper review of 02):

* a missing input gives ``unknown``, never ``pass``;
* a requirement only a dynamic study can show (the profile's
  ``dynamic_requirements``) is always ``unknown``, "dynamic (EMT) study needed";
* ``assumed`` is true unless the limit's own tag is ``code`` (an extracted,
  unconfirmed limit counts as assumed), or an input it rests on is assumed
  (an assumed fault level makes the SCR and voltage-change rows assumed);
* the envelope is IT-side at the PoC before any UPS/BESS smoothing, so the
  ramp, oscillation and voltage-change verdicts read "before mitigation".
"""
from __future__ import annotations

import json
import math
import pathlib
from typing import Any

SCHEMA = 1
VERDICTS = ("pass", "warn", "fail", "unknown")

#: Placeholders for a project whose ledger carries no row for the limit
#: (a user-built network): the templates' own values, as fractions of the
#: connection where they scale with it.
GENERIC_RAMP_SHARE_PER_MIN = 0.10
GENERIC_OSCILLATION_PCT = 1.0
OSCILLATION_BAND_HZ = (0.1, 2.0)

#: The grid services (DC 04's ``SERVICES``) in words, for the panel and the report.
SERVICE_WORDS = {"grid_forming": "grid forming", "synthetic_inertia": "synthetic inertia",
                 "frequency_containment": "frequency containment",
                 "fast_frequency_response": "fast frequency response", "voltage_control": "voltage control"}


def _services_text(services) -> str:
    return ", ".join(SERVICE_WORDS.get(x, x) for x in services)


_FREQUENCY_SERVICES = ("grid_forming", "synthetic_inertia", "frequency_containment",
                       "fast_frequency_response")
_BEFORE_MITIGATION = ("the AI-training load is read at the point of connection before any UPS or "
                      "battery smoothing, so the verdict is before mitigation, not final")
#: The island study's scenarios in words.
SCENARIO_WORDS = {"ups_bridged": "UPS-bridged", "seamless": "seamless", "planned": "planned"}
#: Who a dynamic requirement applies to, in words.
_APPLIES_WORDS = {"ibr": "the site's inverter-based units", "facility": "the site", "load": "the load"}
_NO_ENVELOPE = ("No AI-training load profile on this project, so its ramp, swing and oscillation "
                "are not known (a data-centre project created with the AI-training profile has one)")


def _target(section, component=None, entity=None, field=None, panel=None) -> dict:
    return {"section": section, "panel": panel, "component": component, "entity": entity,
            "field": field}


def _row(id_, requirement, *, limit, unit, source, project_value, verdict, assumed, note,
         section, target, rule=None) -> dict:
    assert verdict in VERDICTS, verdict
    row = {"id": id_, "requirement": requirement, "limit": limit, "unit": unit,
           "source": source, "project_value": project_value, "verdict": verdict,
           "assumed": bool(assumed), "note": note, "section": section, "target": target}
    if rule is not None:
        row["rule"] = dict(rule)
    return row


#: A row's declared rule (checked results S9): the project value is compared with the
#: row's own `limit` within the rule's own `tolerance`; a value that meets it passes,
#: any other value is `otherwise`. The rule holds no threshold of its own, so it
#: cannot drift from the limit shown. SCR's tolerance is 0, as the SCR gate compares
#: exactly, so the scorecard and the gate cannot split.
RULE_SCR = {"comparator": ">=", "otherwise": "warn", "tolerance": 0.0}
RULE_RAMP = {"comparator": "<=", "otherwise": "fail", "tolerance": 1e-9}
RULE_RVC = {"comparator": "<=", "otherwise": "warn", "tolerance": 1e-9}
RULE_REACTIVE = {"comparator": ">=", "otherwise": "warn", "tolerance": 1e-9}
#: The oscillation row's after-mitigation value only (the before verdict also
#: reads the band of concern, which is not a scalar): in band, ≤ the limit.
RULE_OSC = {"comparator": "<=", "otherwise": "fail", "tolerance": 1e-9}


def _judge(value: float, limit: float, rule: dict) -> str:
    """The verdict of a value against a limit under a declared rule and its tolerance."""
    c, tol = rule["comparator"], float(rule["tolerance"])
    if c == "<=":
        met = value <= limit + tol
    elif c == "<":
        met = value < limit
    elif c == ">=":
        met = value >= limit - tol
    else:
        raise ValueError(f"unknown comparator {c!r}")
    return "pass" if met else rule["otherwise"]


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# ── inputs ─────────────────────────────────────────────────────────────────


class _Inputs:
    """What the scorecard reads, gathered once."""

    def __init__(self, project_dir, n, profile, profile_error, eh_study, study_edited):
        from services.adequacy.site_peak import import_links

        self.dir = pathlib.Path(project_dir)
        self.n = n
        self.profile = profile
        self.profile_error = profile_error
        meta = _read_json(self.dir / "eh_template.json")
        self.meta = meta if isinstance(meta, dict) else {}
        self.ledger = {r["item"]: r for r in self.meta.get("ledger") or []
                       if isinstance(r, dict) and isinstance(r.get("item"), str)}
        dc = self.meta.get("dc_load") if isinstance(self.meta.get("dc_load"), dict) else {}
        self.envelope = dc.get("envelope") if isinstance(dc.get("envelope"), dict) else None
        self.dc_spec = dc.get("spec") if isinstance(dc.get("spec"), dict) else None
        self.island_raw = _read_json(self.dir / "island_config.json")
        self.island, self.island_error = None, None
        if isinstance(self.island_raw, dict):
            from services.adequacy.island_config import ContractError, validate
            try:
                self.island = validate(self.island_raw)
            except ContractError as exc:
                self.island_error = str(exc)
        else:
            self.island_raw = None
        self.island_study = _read_json(self.dir / "campus_electrical" / "island_study.json")
        self.eh_study = eh_study if isinstance(eh_study, dict) else None
        self.study_edited = study_edited
        self.links = import_links(n) if n is not None else []
        self.connection_mw = (sum(float(n.links.at[l, "p_nom"]) for l in self.links)
                              if self.links else None)

    def marking(self, item):
        row = self.ledger.get(item)
        return row if row is not None and _num(row.get("value")) is not None else None

    @property
    def link(self):
        return self.links[0] if self.links else None


def _profile_title(inp: "_Inputs") -> str:
    """The grid code by its title (a project profile may have none: then its id)."""
    return str((inp.profile or {}).get("title") or (inp.profile or {}).get("name") or "the grid code")


def _marking_assumed(row: dict | None) -> bool:
    """A marking is assumed unless it is there and tagged ``code``."""
    return row is None or str(row.get("source")) != "code"


def _envelope_assumed(inp: _Inputs) -> bool:
    return str((inp.envelope or {}).get("source", "assumed")) != "code"


def _marking_source(row: dict) -> tuple[str, bool]:
    src = str(row.get("source") or "assumed")
    note = row.get("note") or ""
    if note.lower().startswith("assumed: "):
        note = note[len("assumed: "):]
    lead = "Template assumption" if src == "assumed" else f"Template value ({src})"
    return (f"{lead}: {note}" if note else lead, src != "code")


# ── rows ───────────────────────────────────────────────────────────────────


def _scr_rows(inp: _Inputs) -> list[dict]:
    from services.adequacy.scr_gate import PASS_SCR, evaluate_network_scr_gate

    fault = inp.marking("fault_level")
    fault_assumed = _marking_assumed(fault)
    target = _target("connection", "Bus", (fault or {}).get("entity"), "eh_sk_mva")
    base = dict(limit=PASS_SCR, unit=None, rule=RULE_SCR,
                source=("Screening rule: an SCR of 3.0, the edge between moderate and strong grids; "
                        "assumed, not a grid-code value"),
                section="connection", target=target)
    if inp.n is None:
        return [_row("scr", "Short-circuit ratio at the PoC", project_value=None,
                     verdict="unknown", assumed=True, note="The project has no saved network",
                     **base)]
    gates, status, payload, note = evaluate_network_scr_gate(inp.n)
    if status != "ok" or gates is None:
        return [_row("scr", "Short-circuit ratio at the PoC", project_value=None,
                     verdict="unknown", assumed=True,
                     note=("No short-circuit level and inverter capacity at every point of connection, "
                           "so the SCR is not known"), **base)]
    verdict = _judge(float(payload["min_scr"]), PASS_SCR, RULE_SCR)
    extra = "; below it an EMT study is recommended" if gates.emt_recommended else ""
    return [_row("scr", "Short-circuit ratio at the PoC", project_value=round(payload["min_scr"], 3),
                 verdict=verdict, assumed=True,
                 note=(f"Lowest SCR, at {payload['min_bus']}: fault level over inverter-based capacity "
                       f"(MVA){extra}; " + ("fault level assumed" if fault_assumed
                                            else "fault level as entered")),
                 **base)]


def _in_hour_uplift_mw(inp: _Inputs, period: str) -> float | None:
    """The in-hour step above the hourly mean for the phase in service in
    ``period``: IT MW × (full share − hourly-mean share) × PUE at design (DC 01's
    ``peak_note``). None without an AI-training spec."""
    if not inp.dc_spec:
        return None
    from models.flex_archetypes import DataCentreLoadSpec
    from services.dc_load_profile import training_utilisation, workload

    try:
        spec = DataCentreLoadSpec.model_validate(inp.dc_spec)
    except ValueError:
        return None
    w = workload(spec)
    year = int(period) if period.isdigit() else None
    live = [p for p in spec.phases if year is None or p.cod.year <= year] or list(spec.phases)
    it = live[-1].it_mw
    return it * (w.full_share - training_utilisation(w)) * spec.pue_at_design


def _peak_rows(inp: _Inputs) -> list[dict]:
    from services.adequacy.site_peak import describe, peak_check, unset_periods

    loss = inp.marking("import_efficiency")
    target = _target("connection", "Link", inp.link, "eh_energization_mw")
    req = ("Load within the energized import (after its efficiency) plus firm gensets and batteries, "
           "hour by hour")
    if inp.n is None or not inp.links:
        return [_row("energization_peak", req, limit=None, unit="MW", source="the network",
                     project_value=None, verdict="unknown", assumed=True,
                     note="No grid connection is tagged on this project" if inp.n is not None
                     else "The project has no saved network",
                     section="connection", target=target)]
    checked = peak_check(inp.n, inp.island_raw, unset=unset_periods(inp.meta))
    if all(r["energized_max_mw"] <= 0 for r in checked):
        return [_row("energization_peak", req, limit=None, unit="MW", source="the network",
                     project_value=None, verdict="unknown", assumed=True,
                     note=("The grid connection is normally open, so there is no import to check: the "
                           "island's adequacy is the reliability study's (the LOLE row)"),
                     section="connection", target=target)]
    rows = []
    for r in checked:
        if r["unset"]:
            # A placeholder growth value (E1): the example's same-stage figure,
            # so the period's verdict is not established.
            rows.append(_row(f"energization_peak:{r['period']}", f"{req} ({r['period']})",
                             limit=None, unit="MW", source="the template's growth inputs",
                             project_value=None, verdict="unknown", assumed=True,
                             note=(f"{r['period']}: not set ({', '.join(r['unset'])}): the "
                                   "period takes the example's same-stage value, a placeholder "
                                   "that is not a value to check against, so the period stays unknown "
                                   "(setting a placeholder is follow-up "
                                   "E1-set-growth-placeholders)"),
                             section="connection", target=target))
            continue
        uplift = _in_hour_uplift_mw(inp, r["period"])
        if r["headroom_mw"] < -1e-9:
            verdict = "fail"
        elif uplift is not None and r["headroom_mw"] < uplift:
            verdict = "warn"
        else:
            verdict = "pass"
        loss_src = (_marking_source(loss)[0] if loss is not None
                    else "the import Link's efficiency, as entered")
        if loss is not None and abs(float(loss["value"]) - r["efficiency"]) > 1e-9:
            loss_src += (f"; the network's {r['efficiency']:.4g} is used, not the ledger's "
                         f"{float(loss['value']):.4g} (the Link was edited)")
        notes = [f"{r['period']}: binding hour {r['binding_snapshot']}, hourly-mean load "
                 f"{r['load_mw']:.4g} MW against {describe(r)} (checked hour by hour; the period's "
                 f"peak is {r['peak_mw']:.4g} MW)",
                 "energization schedule" if r["scheduled"] else "no energization schedule: the connection's capacity",
                 f"import efficiency from {loss_src}",
                 "fixed loads only (a flexible conversion unit is a decision, not counted)"]
        if r.get("not_electric"):
            notes.append("not electric load: " + ", ".join(r["not_electric"]))
        if uplift is not None:
            notes.append(f"the in-hour AI-training step is about {uplift:.3g} MW above the hourly "
                         "mean, so headroom below that is a warning")
        rows.append(_row(f"energization_peak:{r['period']}", f"{req} ({r['period']})",
                         limit=round(r["cap_mw"], 4), unit="MW",
                         source=("The energization schedule (a template assumption) and the island "
                                 "requirements' genset N+k and ride-through" if r["scheduled"]
                                 else "The connection's capacity and the island requirements' genset N+k and ride-through"),
                         project_value=round(r["load_mw"], 4), verdict=verdict,
                         assumed=_marking_assumed(loss) or bool(inp.meta.get("energization")),
                         note="; ".join(notes), section="connection", target=target))
    return rows


def _limit_from(inp: _Inputs, item: str, generic: float | None, generic_note: str):
    """(value, source text, assumed) from the ledger row, else the generic placeholder."""
    row = inp.marking(item)
    if row is not None:
        src, assumed = _marking_source(row)
        return float(row["value"]), src, assumed
    if generic is None:
        return None, generic_note, True
    return generic, f"assumed (generic placeholder): {generic_note}", True


def _ramp_row(inp: _Inputs) -> dict:
    generic = (GENERIC_RAMP_SHARE_PER_MIN * inp.connection_mw
               if inp.connection_mw else None)
    limit, source, assumed = _limit_from(
        inp, "connection_ramp", generic,
        f"{GENERIC_RAMP_SHARE_PER_MIN:.0%} of the connection capacity per minute")
    target = _target("connection", "Link", inp.link, "connection_ramp")
    req = "Load ramp rate at the PoC within the connection ramp limit"
    if inp.envelope is None or limit is None:
        return _row("ramp_rate", req, rule=RULE_RAMP, limit=limit, unit="MW/min", source=source,
                    project_value=None, verdict="unknown", assumed=assumed,
                    note=_NO_ENVELOPE if inp.envelope is None else "No grid connection to scale the limit by",
                    section="connection", target=target)
    per_s = float(inp.envelope["max"]["max_dp_dt_mw_per_s"])
    per_min = mw_per_s_to_mw_per_min(per_s)
    return _row("ramp_rate", req, rule=RULE_RAMP, limit=limit, unit="MW/min", source=source,
                project_value=round(per_min, 4), verdict=_judge(per_min, limit, RULE_RAMP),
                assumed=assumed or _envelope_assumed(inp),
                note=(f"Steepest load change {per_s:.4g} MW/s = {per_min:.4g} MW/min (the steeper of the "
                      f"training step and the oscillation); {_BEFORE_MITIGATION}"),
                section="connection", target=target)


def mw_per_s_to_mw_per_min(v: float) -> float:
    """The one MW/s → MW/min conversion the ramp row uses."""
    return float(v) * 60.0


def _oscillation_row(inp: _Inputs) -> dict:
    limit, source, assumed = _limit_from(inp, "oscillation", GENERIC_OSCILLATION_PCT,
                                         f"{GENERIC_OSCILLATION_PCT:g} % of the connection")
    target = _target("connection", "Link", inp.link, "oscillation")
    lo, hi = OSCILLATION_BAND_HZ
    req = f"Power oscillation at the PoC ({lo:g}-{hi:g} Hz band) within the limit"
    if inp.envelope is None or not inp.connection_mw:
        return _row("oscillation", req, limit=limit, unit="% of connection, peak-to-peak",
                    source=source, project_value=None, verdict="unknown", assumed=assumed,
                    note=_NO_ENVELOPE if inp.envelope is None else "No grid connection to compare against",
                    section="connection", target=target)
    amp = float(inp.envelope["max"]["oscillation_amplitude_mw"])
    period = float(inp.envelope["max"]["oscillation_period_s"])
    hz = 1.0 / period if period > 0 else float("inf")
    pct = 2.0 * amp / inp.connection_mw * 100.0
    in_band = lo <= hz <= hi
    verdict = "pass" if (pct <= limit or not in_band) else "fail"
    band = (f"at {hz:.3g} Hz, inside the band" if in_band
            else f"at {hz:.3g} Hz, outside the assumed {lo:g}-{hi:g} Hz band of concern")
    return _row("oscillation", req, limit=limit, unit="% of connection, peak-to-peak",
                source=source, project_value=round(pct, 4), verdict=verdict,
                assumed=assumed or _envelope_assumed(inp),
                note=(f"{2 * amp:.4g} MW peak-to-peak over the {inp.connection_mw:g} MW connection, "
                      f"{band}; {_BEFORE_MITIGATION}"),
                section="connection", target=target)


def _rvc_row(inp: _Inputs) -> dict:
    target = _target("connection", "Bus", (inp.marking("fault_level") or {}).get("entity"),
                     "eh_sk_mva")
    req = "Rapid voltage change at the PoC on the AI-training load step"
    lim = (inp.profile or {}).get("rvc_limit_pct")
    if not isinstance(lim, dict):
        return _row("rapid_voltage_change", req, rule=RULE_RVC, limit=None, unit="% of nominal",
                    source=inp.profile_error or "The grid code sets no rapid-voltage-change limit",
                    project_value=None, verdict="unknown", assumed=True,
                    note="The grid code sets no rapid-voltage-change limit", section="connection", target=target)
    limit, source = float(lim["value"]), f"{_profile_title(inp)}: {lim['clause']}"
    lim_assumed = lim.get("source") != "code"
    sk = _poc_sk_mva(inp.n) if inp.n is not None else None
    fault_assumed = _marking_assumed(inp.marking("fault_level"))
    if inp.envelope is None or sk is None:
        return _row("rapid_voltage_change", req, rule=RULE_RVC, limit=limit, unit="% of nominal", source=source,
                    project_value=None, verdict="unknown", assumed=lim_assumed or fault_assumed,
                    note=_NO_ENVELOPE if inp.envelope is None
                    else "No short-circuit level at the point of connection",
                    section="connection", target=target)
    swing = float(inp.envelope["max"]["swing_mw"])
    d = swing / sk * 100.0
    return _row("rapid_voltage_change", req, rule=RULE_RVC, limit=limit, unit="% of nominal", source=source,
                project_value=round(d, 4), verdict=_judge(d, limit, RULE_RVC),
                assumed=lim_assumed or fault_assumed or _envelope_assumed(inp),
                note=(f"Voltage change ≈ load step / fault level × 100 = {swing:.4g} MW / {sk:g} MVA: a screening upper bound "
                      "(IEC 61000-3-7 style) that ignores the grid X/R; above the limit a power-flow "
                      f"or EMT study is needed; {_BEFORE_MITIGATION}"),
                section="connection", target=target)


def _poc_sk_mva(n) -> float | None:
    if "eh_poc" not in n.buses.columns or "eh_sk_mva" not in n.buses.columns:
        return None
    vals = [_num(n.buses.at[b, "eh_sk_mva"]) for b in n.buses.index
            if n.buses.at[b, "eh_poc"] is True or str(n.buses.at[b, "eh_poc"]).lower() in ("true", "1")]
    vals = [v for v in vals if v is not None and v > 0]
    return min(vals) if vals else None


def _services(inp: _Inputs):
    if inp.island is None:
        return None
    from services.adequacy.island_config import offered_services
    return offered_services(inp.island)


def _no_island_note(inp: _Inputs) -> str:
    return (f"The island requirements are not valid: {inp.island_error}" if inp.island_error
            else "The project has no island requirements yet")


def _unit_rating(n, name: str) -> tuple[float | None, bool]:
    """(rating MW, extendable-without-a-size) of a component by name."""
    for frame in ("storage_units", "generators", "links"):
        df = getattr(n, frame)
        if name in df.index:
            p_nom = _num(df.at[name, "p_nom"]) or 0.0
            opt = _num(df.at[name, "p_nom_opt"]) if "p_nom_opt" in df.columns else None
            ext = bool(df.at[name, "p_nom_extendable"]) if "p_nom_extendable" in df.columns else False
            if ext:
                return (opt, False) if opt and opt > 0 else (None, True)
            return p_nom, False
    return None, False


def _reactive_row(inp: _Inputs) -> dict:
    req = "Reactive power range at the PoC"
    lim = (inp.profile or {}).get("q_range_demand")
    target = _target("assets", None, None, "control")
    if not isinstance(lim, dict):
        return _row("reactive_range", req, rule=RULE_REACTIVE, limit=None, unit="Mvar",
                    source=inp.profile_error or "The grid code sets no reactive range",
                    project_value=None, verdict="unknown", assumed=True, note="The grid code sets no reactive range",
                    section="assets", target=target)
    source = f"{_profile_title(inp)}: {lim['clause']}"
    assumed = lim.get("source") != "code"
    services = _services(inp)
    if services is None or not inp.connection_mw:
        return _row("reactive_range", req, rule=RULE_REACTIVE, limit=None, unit="Mvar", source=source,
                    project_value=None, verdict="unknown", assumed=assumed,
                    note=_no_island_note(inp) if services is None else "No grid connection is tagged on this project",
                    section="assets", target=target)
    need = float(lim["value"]) * inp.connection_mw
    have, unsized, parts, unrated = 0.0, [], [], []
    for name, u in services["units"].items():
        if "voltage_control" not in u["services"]:
            continue
        pf = _num(inp.island["units"][name].get("values", {}).get("pf_rated"))
        rating, ext = _unit_rating(inp.n, name)
        if ext:
            unsized.append(name)
            continue
        if pf is None or rating is None or not 0 < pf <= 1:
            unrated.append(name)
            continue
        q = rating * math.tan(math.acos(pf))
        have += q
        parts.append(f"{name} {q:.3g}")
    if _judge(have, need, RULE_REACTIVE) == "pass":
        verdict = "pass"
    elif unsized:
        verdict = "unknown"
    else:
        verdict = "warn"
    derivation = (f"Needed {lim['value']:g} × {inp.connection_mw:g} MW import = {need:.4g} Mvar; "
                  f"offered {have:.4g} Mvar ({', '.join(parts) or 'none'}) as P × tan(acos rated "
                  "power factor), an assumed capability at rated power from the units offering "
                  "voltage control (a unit the island study does not credit counts zero)")
    if unrated:
        derivation += f"; {', '.join(unrated)} counted zero: no rated power factor or no rating"
    source = f"{source}. {derivation}"
    if unsized and verdict == "unknown":
        note = (f"Not sized yet: {', '.join(unsized)} — run the plan to see the reactive range "
                f"{'it adds' if len(unsized) == 1 else 'they add'}.")
    elif unsized:
        note = f"Enough without {', '.join(unsized)}, which are not sized yet"
    elif verdict == "warn":
        note = f"Short by {need - have:.4g} Mvar"
    else:
        note = "Covered by the units offering voltage control"
    return _row("reactive_range", req, rule=RULE_REACTIVE, limit=round(need, 4), unit="Mvar", source=source,
                project_value=round(have, 4), verdict=verdict, assumed=True, note=note,
                section="assets", target=target)


def _support_rows(inp: _Inputs) -> list[dict]:
    services = _services(inp)
    offered = services["services"] if services else None
    target = _target("assets", None, None, "preset")
    freq = [s for s in (offered or []) if s in _FREQUENCY_SERVICES]
    rows = [_row("frequency_support", "Frequency support offered by the site's converters",
                 limit="at least one of " + _services_text(_FREQUENCY_SERVICES), unit=None,
                 source="Assumed: the scorecard's own expectation for a large connection; the grid "
                        "code sets no limit", project_value=_services_text(offered) if offered else
                 ("none" if offered is not None else None),
                 verdict="unknown" if offered is None else ("pass" if freq else "warn"),
                 assumed=True,
                 note=_no_island_note(inp) if offered is None else
                 f"Control preset: {(services['preset'] or 'none').replace('_', ' ')}; what each unit "
                 "offers follows its control mode", section="assets", target=target)]
    for r in (inp.profile or {}).get("dynamic_requirements") or []:
        rows.append(_row(f"dynamic:{r['id']}", r["requirement"], limit=r["value"], unit=None,
                         source=f"{_profile_title(inp)}: {r['clause']}",
                         project_value=(_services_text(offered) if offered else None),
                         verdict="unknown", assumed=r.get("source") != "code",
                         note=("Needs a dynamic (EMT) study; it applies to "
                               f"{_APPLIES_WORDS.get(r.get('applies_to', 'facility'), 'the site')}; a "
                               "steady-state screening never passes it"),
                         section="assets", target=target))
    return rows


def _island_rows(inp: _Inputs) -> list[dict]:
    target = _target("assets", None, None, "island")
    scenarios = list(((inp.island_raw or {}).get("requirements") or {}).get("scenarios") or [])
    if inp.island is None:
        return [_row("grid_former", "A unit that forms the island's voltage", limit="a grid-forming battery or a genset",
                     unit=None, source="The island study's rule: a grid-forming battery or a genset forms the voltage",
                     project_value=None, verdict="unknown", assumed=False,
                     note=f"Not assessed: {_no_island_note(inp)}", section="assets", target=target)]
    from services.adequacy.island_config import has_grid_former

    # A valid sidecar names at least one islanding scenario (gridspine's
    # schema), so with a sidecar the site is asked to island; without one the
    # row above reads unknown, "not assessed".
    rows = []
    gf = has_grid_former(inp.island)
    rows.append(_row("grid_former", "A unit that forms the island's voltage",
                     limit="a grid-forming battery or a genset", unit=None,
                     source="The island study's rule: a grid-forming battery or a genset forms the voltage",
                     project_value="yes" if gf else "none", verdict="pass" if gf else "fail",
                     assumed=False,
                     note=f"Islanding scenarios: {', '.join(SCENARIO_WORDS.get(x, x) for x in scenarios)}",
                     section="assets", target=target))
    vals = (inp.island_raw.get("requirements") or {})
    bridge = _num((vals.get("bridge_s") or {}).get("value") if isinstance(vals.get("bridge_s"), dict)
                  else vals.get("bridge_s"))
    storage = vals.get("ride_through_storage") or []
    variants = (inp.island_study or {}).get("variants") or {}
    for sc in scenarios:
        v = variants.get(sc)
        if inp.island_study is None or v is None:
            verdict, value, note = ("unknown", None,
                                    "No island study since the island requirements were saved")
        elif v.get("status") == "ok":
            verdict, value, note = "pass", "feasible", "The plan, sized with the island rows, is feasible"
        else:
            verdict, value = "fail", str(v.get("status"))
            note = (f"The island study's {SCENARIO_WORDS.get(sc, sc)} case: "
                    f"{v.get('reason') or v.get('condition') or v.get('status')}")
        if inp.island_study is not None:
            note += "; the study is the stored one, re-run it after a network edit"
        rows.append(_row(f"ride_through:{sc}", f"Island ride-through ({SCENARIO_WORDS.get(sc, sc)})",
                         limit=(f"bridge {bridge:g} s on {', '.join(storage) or 'the site storage'}"
                                if bridge is not None else "the island requirements"),
                         unit=None, source="The island requirements (each value tagged with its source)",
                         project_value=value, verdict=verdict,
                         assumed=_sidecar_assumed(inp.island_raw), note=note, section="assets",
                         target=target))
    return rows


def _sidecar_assumed(raw: dict) -> bool:
    req = (raw or {}).get("requirements") or {}
    return any(isinstance(v, dict) and v.get("source") == "assumed" for v in req.values())


def _certification(inp: _Inputs) -> dict | None:
    report = (inp.eh_study or {}).get("report")
    cert = ((report or {}).get("sections") or {}).get("certification") if isinstance(report, dict) else None
    payload = cert.get("payload") if isinstance(cert, dict) else None
    return payload if isinstance(payload, dict) else None


def _lole_row(inp: _Inputs) -> dict:
    """LOLE against the goal. The goal is the one the study certified against
    (its ``target_lole_h``, set in Scenarios) when a study has one, else the
    template's ``lole_goal`` marking. Verdict on the MC CI, annualised: pass
    when its upper bound is within the goal, fail when its lower bound is
    above it, warn (inconclusive) between or without a CI."""
    row = inp.marking("lole_goal")
    target = _target("scenarios", None, None, "target_lole_h", panel="simparams")
    req = "Loss of load expectation within the reliability goal"
    payload = _certification(inp)
    study_goal = _num((payload or {}).get("target_lole_h"))
    if study_goal is not None and (row is None or study_goal != float(row["value"])):
        limit, source, assumed = study_goal, "the reliability study's target_lole_h (Scenarios)", False
    elif row is not None:
        limit = float(row["value"])
        source, assumed = _marking_source(row)
    else:
        return _row("lole", req, limit=None, unit="h/yr", source="No LOLE goal in the template's assumptions",
                    project_value=None, verdict="unknown", assumed=True,
                    note="The project sets no LOLE goal (an ENS cap only, or none)",
                    section="scenarios", target=target)
    years = _num((payload or {}).get("horizon_years"))
    mean = _num((payload or {}).get("lole_h_per_year"))

    def unknown(note):
        return _row("lole", req, limit=limit, unit="h/yr", source=source, project_value=None,
                    verdict="unknown", assumed=assumed, note=note, section="scenarios", target=target)

    if payload is None or mean is None or years is None or years <= 0:
        return unknown("No reliability study for this project since the app was opened" if inp.eh_study is None
                       else "The reliability study did not certify a Monte-Carlo LOLE")
    if inp.study_edited is not False:
        return unknown("Stale: the network was edited since the study, re-run it" if inp.study_edited
                       else "The study's network revision is not known")
    ci = payload.get("lole_ci") or []
    lo, hi = (_num(ci[0]), _num(ci[1])) if len(ci) == 2 else (None, None)
    if lo is None or hi is None:
        verdict, why = "warn", "the study gives no confidence interval"
    else:
        lo, hi = lo / years, hi / years
        verdict = "pass" if hi <= limit else ("fail" if lo > limit else "warn")
        why = "pass needs the CI upper bound within the goal, fail the lower bound above it"
    return _row("lole", req, limit=limit, unit="h/yr", source=source,
                project_value={"lole_h_per_year": round(mean, 4),
                               "ci_h_per_year": None if hi is None else [round(lo, 4), round(hi, 4)],
                               "horizon_years": years},
                verdict=verdict, assumed=assumed,
                note=f"Monte-Carlo LOLE annualised over {years:g} modelled year(s); {why}",
                section="scenarios", target=target)


# ── after mitigation (DC 03) ───────────────────────────────────────────────


_AFTER_IDS = ("ramp_rate", "oscillation", "rapid_voltage_change")


def _after_battery(inp: _Inputs, rows: list[dict]) -> None:
    """Add ``after_battery: {value, verdict, rule, units, note}`` to the
    ramp, oscillation and voltage-change rows: the value the point of
    connection sees once the site's battery/UPS has done its smoothing duty
    (``services.adequacy.smoothing``), judged by the row's own limit under a
    declared rule. The row's own verdict stays before mitigation. Nothing is
    added without an AI-training envelope, an island sidecar or a committed
    battery/UPS; "not known yet" where the row itself has no value."""
    from services.adequacy.smoothing import after_mitigation, smoothing_screen

    by_id = {r["id"]: r for r in rows if r["id"] in _AFTER_IDS}
    ramp, osc = by_id.get("ramp_rate"), by_id.get("oscillation")
    if inp.envelope is None or inp.n is None or inp.island_raw is None or ramp is None:
        return
    screen = smoothing_screen(inp.n, inp.island_raw, inp.envelope,
                              ramp_limit_mw_per_min=ramp["limit"],
                              oscillation_limit_pct=(osc or {}).get("limit"),
                              connection_mw=inp.connection_mw, band_hz=OSCILLATION_BAND_HZ)
    screened = [(ph, after_mitigation(ph)) for ph in (screen or {}).get("phases", [])]
    screened = [(ph, after) for ph, after in screened if after is not None]
    if not screened:
        return
    units = sorted({u for ph, _ in screened for u in ph["units"]})
    # Each unit with its kind, assumed latency or walk-in, and where it is (DC 03).
    detail = list({x["unit"]: {**x, "target": _target("assets", "StorageUnit", x["unit"])}
                   for ph, _ in screened for x in ph.get("latency", [])}.values())
    before = ramp["project_value"]
    limit = ramp["limit"]

    def values(ph, after):
        """(ramp after, oscillation after, voltage change after, words) of one phase."""
        s = after["step_mw"]
        frac = after["residual_step_mw"] / s if s > 0 else 0.0
        a_res, period = after["residual_amplitude_mw"], after["period_s"]
        osc_slope = mw_per_s_to_mw_per_min(2.0 * math.pi * a_res / period) if period > 0 else 0.0
        # The batteries' share at the limit, the UPSs' at their walk-in rate,
        # the uncovered part at the envelope's rate (conservative); the residual
        # oscillation's slope 2π·A/T stands on its own.
        shaped = ((min(limit, before) if after["covered_battery_mw"] > 0 else 0.0)
                  + after["ups_ramp_mw_per_min"] + frac * before) if before is not None else None
        r = None if before is None else min(before, max(shaped, osc_slope))
        hz = 1.0 / period if period > 0 else math.inf
        o = (2.0 * a_res / inp.connection_mw * 100.0
             if inp.connection_mw and OSCILLATION_BAND_HZ[0] <= hz <= OSCILLATION_BAND_HZ[1] else None)
        v = after["residual_step_mw"] / sk * 100.0 if sk else None
        parts = [f"{ph['cod'][:4] if ph.get('cod') else 'the phase'}: of the {s:.3g} MW step, batteries "
                 f"hold {after['covered_battery_mw']:.3g} MW to the {limit:g} MW/min limit"]
        if after["covered_ups_mw"] > 0:
            parts.append(f"the UPS walks {after['covered_ups_mw']:.3g} MW in over {after['ups_walk_in_s']:g} s "
                         f"({after['ups_ramp_mw_per_min']:.3g} MW/min)")
        parts.append(f"the uncovered part ({after['residual_step_mw']:.3g} MW) is counted at the envelope's "
                     f"rate, conservatively, and a {a_res:.3g} MW oscillation remains")
        return r, o, v, "; ".join(parts) + f"; {ph['verdict']} by the smoothing screen: {ph['note']}"

    sk = _poc_sk_mva(inp.n)
    per_phase = [values(ph, after) for ph, after in screened]

    def worst(i):
        """The worst after value over the screened phases for result ``i``, and its words."""
        got = [(x[i], x[3]) for x in per_phase if x[i] is not None]
        return max(got, key=lambda t: t[0]) if got else (None, None)

    def attach(row, i, rule):
        if row is None:
            return
        value, how = worst(i)
        if row["project_value"] is None or row["limit"] is None or value is None:
            if row is osc and value is None and row["project_value"] is not None:
                return                     # outside the band of concern: nothing to smooth
            row["after_battery"] = {"value": None, "verdict": "unknown", "rule": dict(rule), "units": units,
                                    "unit_detail": detail, "note": "not known yet: the row itself has no value"}
            return
        v = round(value, 4)
        row["after_battery"] = {"value": v, "verdict": _judge(v, row["limit"], rule), "rule": dict(rule),
                                "units": units, "unit_detail": detail, "note": how}

    attach(ramp, 0, RULE_RAMP)
    attach(osc, 1, RULE_OSC)
    rvc = by_id.get("rapid_voltage_change")
    if rvc is not None and rvc.get("rule") and sk:
        attach(rvc, 2, RULE_RVC)


# ── the scorecard ──────────────────────────────────────────────────────────


def _load_profile(profile_id: str, profile_dirs) -> tuple[dict | None, str | None]:
    try:
        from gridspine.drivers.campus_study import load_grid_code
        from gridspine.schema.contracts import ContractError
    except ImportError as exc:                  # pragma: no cover - build-shape branch
        return None, f"the grid-code profiles need gridspine ({exc})"
    try:
        return load_grid_code(profile_id, extra_dirs=tuple(profile_dirs)), None
    except ContractError as exc:
        return None, str(exc)


def grid_readiness(project_dir, network, profile_id: str, *, profile_dirs=(),
                   eh_study: dict | None = None, study_edited: bool | None = None) -> dict:
    """The scorecard of the project at ``project_dir`` (``network`` its saved
    network, or None) against grid-code profile ``profile_id``.

    ``eh_study`` is the project's reliability-study record when the caller
    has one for it (``report.sections.certification``), ``study_edited``
    whether the network was edited since (``study_state.edited_since``)."""
    profile, error = _load_profile(profile_id, profile_dirs)
    inp = _Inputs(project_dir, network, profile, error, eh_study, study_edited)
    rows = [*_scr_rows(inp), *_peak_rows(inp), _ramp_row(inp), _oscillation_row(inp),
            _rvc_row(inp), _reactive_row(inp), *_support_rows(inp), *_island_rows(inp),
            _lole_row(inp)]
    _after_battery(inp, rows)
    counts = {v: sum(r["verdict"] == v for r in rows) for v in VERDICTS}
    after = sum(r["verdict"] in ("fail", "warn") and (r.get("after_battery") or {}).get("verdict") == "pass"
                for r in rows)
    return {"schema": SCHEMA, "profile": {"id": profile_id, "title": (profile or {}).get("title"),
                                          "error": error},
            "template_id": inp.meta.get("id"), "envelope": inp.envelope is not None,
            "rows": rows, "summary": {**counts, "assumed": sum(r["assumed"] for r in rows),
                                      "red_flags": counts["fail"] + counts["warn"],
                                      "pass_after_mitigation": after},
            "basis": ("advisory screening of the saved network and its sidecars; never blocks Run. "
                      "A fail is a red flag to take to the network operator; unknown means an "
                      "input is missing, never a pass")}


def grid_readiness_for(project, profile: str | None = None) -> dict:
    """The scorecard of a registered project: its saved network, its
    published grid codes, and the active session's reliability study when
    this project is the active one. ``profile`` defaults to the campus
    study's last-run profile, else its default."""
    import pypsa
    from fastapi import HTTPException

    from services import campus_electrical_service as ce
    from services import project_registry

    d = project_registry.project_dir(project)
    codes = d / ce.SUBDIR / ce.GRID_CODES_SUBDIR
    if profile is None:
        saved = _read_json(d / ce.SUBDIR / ce.SETTINGS_FILE)
        profile = (saved or {}).get("profile") or ce.DEFAULTS["profile"]
    known = _known_profiles(codes)
    if known is not None and profile not in known:
        raise HTTPException(status_code=422, detail={
            "error_kind": "unknown_grid_code_profile",
            "message": f"unknown grid-code profile {profile!r}; known {sorted(known)}"})
    nc = d / ce.NETWORK_FILE
    n = None
    if nc.is_file():
        with ce._netcdf_lock():
            n = pypsa.Network(str(nc))
    record, edited = _active_study(d)
    return grid_readiness(d, n, profile, profile_dirs=(codes,), eh_study=record, study_edited=edited)


def _known_profiles(codes: pathlib.Path) -> set | None:
    """The shipped and published profile ids, or None when they cannot be
    listed (no gridspine, a broken project file): the scorecard then reports
    the load error on its rows instead of refusing."""
    try:
        from gridspine.drivers.campus_study import grid_code_profiles
        return set(grid_code_profiles(extra_dirs=(codes,)))
    except Exception:  # noqa: BLE001 — listing is best effort; the rows carry the error
        return None


def _active_study(project_dir: pathlib.Path) -> tuple[dict | None, bool | None]:
    """The active context's reliability-study record and its edited-since
    flag, when the active context is this project; else (None, None)."""
    try:
        from services.pypsa_service import PyPSAService
        from services.study_state import edited_since

        ctx = PyPSAService.get_active_context()
    except Exception:  # noqa: BLE001 — no active context is "no study", not an error
        return None, None
    storage = getattr(ctx, "storage_dir", None)
    if not storage or pathlib.Path(storage).resolve() != pathlib.Path(project_dir).resolve():
        return None, None
    record: Any = ctx.solver_state.get("eh_study") if hasattr(ctx, "solver_state") else None
    if not isinstance(record, dict) or record.get("status") == "running":
        return None, None
    return record, edited_since(record)
