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
row for a limit gets the generic placeholder below, marked assumed. The user's
own limit (``grid_limits``, the bundled ``grid_readiness_limits.json``) comes
before either, and the row's source says whose value it is.

**Advisory.** It reads files and a network copy, solves nothing, and feeds
neither ``validate_for_run`` nor the flow's ``run_ready`` (project workflow
R1-R4). A ``fail`` is a red flag to take to the network operator, never a
refusal.

Each row: ``{id, requirement, limit, unit, source, project_value, verdict,
assumed, note, section, target}``, ``verdict`` one of pass|warn|fail|unknown
and ``target`` the flow's ``{section, panel, component, entity, field}``. The
ramp, oscillation and SCR rows also carry ``setting`` ``{item, user, default}``:
the user's own limit (``grid_limits``) or None, and the value a reset returns
to (the template's ledger row, else the placeholder).

Rules (gatekeeper review of 02):

* a missing input gives ``unknown``, never ``pass``;
* a requirement only a dynamic study can show (the profile's
  ``dynamic_requirements``) is always ``unknown``, "needs an RMS study" (EMT
  where the code asks for it);
* ``assumed`` is true unless the limit's own tag is ``code`` (an extracted,
  unconfirmed limit counts as assumed) or the user set it with a source other
  than ``assumed``, or an input it rests on is assumed
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
#: The harmonic row's THD (or, with no THD limit, its worst order) against its limit. The verdict also reads
#: the per-order limits, which the row does not store as a scalar.
RULE_HARMONIC = {"comparator": "<=", "otherwise": "warn", "tolerance": 1e-9}
#: The flicker row's Pst (or, with no Pst limit, its Plt) against its limit. The verdict reads both.
RULE_FLICKER = {"comparator": "<=", "otherwise": "warn", "tolerance": 1e-9}
#: The unbalance row's u2 against its limit (DC grid readiness 12): a screening upper bound, so over it warns.
RULE_UNBALANCE = {"comparator": "<=", "otherwise": "warn", "tolerance": 1e-9}
#: The current-distortion row's TDD against its limit (DC-SURFACE-1). The verdict also reads the per-order
#: limits, which the row does not store as a scalar.
RULE_TDD = {"comparator": "<=", "otherwise": "warn", "tolerance": 1e-9}
#: The oscillation row's after-mitigation value only (the before verdict also
#: reads the band of concern, which is not a scalar): in band, ≤ the limit.
RULE_OSC = {"comparator": "<=", "otherwise": "fail", "tolerance": 1e-9}
#: A motor-start row's voltage change against the rapid-voltage-change limit (grid-to-process issue 18): a
#: screening estimate, so over it warns, as the rapid-voltage-change row.
RULE_MOTOR_START = {"comparator": "<=", "otherwise": "warn", "tolerance": 1e-9}


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
        from services.adequacy.grid_limits import load_limits
        self.user_limits = load_limits(self.dir)
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
        """The limit's row: the user's own (``origin: user``) first, then the
        template's ledger row, else None (the caller's placeholder). The
        fault level's value lives on the bus, so the user's row for it keeps
        the ledger's value and entity and takes only the source and note."""
        row = self.template_marking(item)
        user = self.user_limits.get(item)
        if user is None:
            return row
        out = {**(self.ledger.get(item) or {}), "item": item, "source": user["source"],
               "note": user.get("note") or "", "origin": "user"}
        if user.get("value") is not None:
            out["value"] = user["value"]
        return out

    def template_marking(self, item):
        """The template's ledger row for ``item`` when it carries a value, else None."""
        row = self.ledger.get(item)
        return row if row is not None and _num(row.get("value")) is not None else None

    @property
    def link(self):
        return self.links[0] if self.links else None


def _profile_title(inp: "_Inputs") -> str:
    """The grid code by its title (a project profile may have none: then its id)."""
    return str((inp.profile or {}).get("title") or (inp.profile or {}).get("name") or "the grid code")


def _marking_assumed(row: dict | None) -> bool:
    """A marking is assumed unless it is there and tagged ``code``, or the
    user set it from a source other than ``assumed``."""
    if row is None:
        return True
    if row.get("origin") == "user":
        return str(row.get("source")) == "assumed"
    return str(row.get("source")) != "code"


def _envelope_assumed(inp: _Inputs) -> bool:
    return str((inp.envelope or {}).get("source", "assumed")) != "code"


def _marking_source(row: dict) -> tuple[str, bool]:
    src = str(row.get("source") or "assumed")
    note = row.get("note") or ""
    if note.lower().startswith("assumed: "):
        note = note[len("assumed: "):]
    if row.get("origin") == "user":
        lead = "Your assumption" if src == "assumed" else f"Your value ({src})"
    else:
        lead = "Template assumption" if src == "assumed" else f"Template value ({src})"
    return (f"{lead}: {note}" if note else lead, _marking_assumed(row))


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
                                   "until it is set; editing the period's series sets it"),
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
    """(value, source text, assumed) from the user's limit, else the ledger
    row, else the generic placeholder."""
    row = inp.marking(item)
    if row is not None:
        src, assumed = _marking_source(row)
        return float(row["value"]), src, assumed
    if generic is None:
        return None, generic_note, True
    return generic, f"assumed (generic placeholder): {generic_note}", True


def _setting(inp: _Inputs, item: str, generic: float | None) -> dict:
    """What the panel's inline field edits (DC 03 slice 6): the user's own
    limit (or None) and the value a reset returns to, the template's ledger
    row or the generic placeholder."""
    row = inp.template_marking(item)
    if row is not None:
        default = {"value": float(row["value"]), "source": str(row.get("source") or "assumed"),
                   "origin": "template"}
    else:
        default = {"value": generic, "source": "assumed", "origin": "placeholder"}
    user = inp.user_limits.get(item)
    # Who set it stays in the sidecar: the scorecard also reaches the copilot.
    shown = {k: v for k, v in user.items() if k != "set_by"} if user is not None else None
    return {"item": item, "user": shown, "default": default}


def _generic_ramp(inp: _Inputs) -> float | None:
    """The placeholder ramp limit: a share of the connection per minute."""
    return GENERIC_RAMP_SHARE_PER_MIN * inp.connection_mw if inp.connection_mw else None


def _ramp_row(inp: _Inputs) -> dict:
    generic = _generic_ramp(inp)
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


def _rvc_infrequent_row(inp: _Inputs) -> dict:
    """The infrequent tier of a two-tier RVC limit (``rvc_limit_infrequent_pct``):
    the whole connection switched at once, connection / fault level × 100."""
    target = _target("connection", "Bus", (inp.marking("fault_level") or {}).get("entity"),
                     "eh_sk_mva")
    req = "Rapid voltage change at the PoC on an infrequent event (the whole connection switched)"
    unit = "% of nominal"
    lim = (inp.profile or {}).get("rvc_limit_infrequent_pct")
    if not isinstance(lim, dict):
        return _row("rapid_voltage_change_infrequent", req, rule=RULE_RVC, limit=None, unit=unit,
                    source=inp.profile_error or "no limit set", project_value=None, verdict="unknown",
                    assumed=True, note="No limit set: the grid code sets no infrequent rapid-voltage-change tier",
                    section="connection", target=target)
    limit, source = float(lim["value"]), f"{_profile_title(inp)}: {lim['clause']}"
    lim_assumed = lim.get("source") != "code"
    sk = _poc_sk_mva(inp.n) if inp.n is not None else None
    fault_assumed = _marking_assumed(inp.marking("fault_level"))
    if not inp.connection_mw or sk is None:
        return _row("rapid_voltage_change_infrequent", req, rule=RULE_RVC, limit=limit, unit=unit, source=source,
                    project_value=None, verdict="unknown", assumed=lim_assumed or fault_assumed,
                    note="No short-circuit level at the point of connection" if sk is None
                    else "No connection capacity", section="connection", target=target)
    d = inp.connection_mw / sk * 100.0
    return _row("rapid_voltage_change_infrequent", req, rule=RULE_RVC, limit=limit, unit=unit, source=source,
                project_value=round(d, 4), verdict=_judge(d, limit, RULE_RVC), assumed=lim_assumed or fault_assumed,
                note=(f"Voltage change ≈ connection capacity / fault level × 100 = {inp.connection_mw:g} MW / "
                      f"{sk:g} MVA: a screening upper bound (IEC 61000-3-7 style) that ignores the grid X/R; "
                      "above the limit a power-flow or EMT study is needed"),
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


_HARMONIC_SCREENING = ("a screening estimate (IEC 61000-3-6 summation, U_h = I_h · h / S_k) that ignores network "
                       "resonance, background distortion and the operator's allocation of the planning level; above "
                       "the limit a harmonic study is needed")


def _harmonic_row(inp: _Inputs) -> dict:
    """The harmonic voltage distortion the process units cause at the PoC (grid-to-process issue 17,
    ``gridspine.static.harmonics``): every unit lumped at the PoC, S_k from ``_poc_sk_mva``, judged on whichever
    of ``thd_limit_pct`` and ``harmonic_limits_pct`` the profile sets."""
    import pandas as pd

    rid, req = "harmonic_distortion", "Harmonic voltage distortion at the PoC from the site's drives and heaters"
    unit_target = _target("assets", "Link", None, "harmonic_source")
    try:
        from gridspine.drivers.campus_study import harmonic_units
        from gridspine.schema.contracts import ContractError
        from gridspine.static import harmonics as hm
    except ImportError as exc:  # pragma: no cover - build-shape branch
        note = f"The harmonic screen needs gridspine ({exc})"
        return _row(rid, req, rule=RULE_HARMONIC, limit=None, unit="% of nominal", source=note,
                    project_value=None, verdict="unknown", assumed=True, note=note, section="assets",
                    target=unit_target)
    limits, cited = hm.limits_from_profile(inp.profile)
    if not cited:
        return _row(rid, req, rule=RULE_HARMONIC, limit=None, unit="% of nominal",
                    source=inp.profile_error or "The grid code sets no harmonic limit", project_value=None,
                    verdict="unknown", assumed=True, note="The grid code sets no harmonic limit",
                    section="assets", target=unit_target)
    lim_assumed = any(c["source"] != "code" for c in cited)
    by_thd = limits.thd_pct is not None
    limit = limits.thd_pct if by_thd else None
    unit = "% THD" if by_thd else "% of nominal"
    part = {"thd_limit_pct": "THD", "harmonic_limits_pct": "Individual orders"}
    source = f"{_profile_title(inp)}: " + "; ".join(f"{part[c['key']]}: {c['clause']}" for c in cited)
    units = harmonic_units(inp.n, "PoC") if inp.n is not None else None
    if units is None or units.empty:
        return _row(rid, req, rule=RULE_HARMONIC, limit=limit, unit=unit, source=source, project_value=None,
                    verdict="unknown", assumed=True,
                    note="No process unit on this project: the screen reads each drive's or heater's Harmonic "
                         "source and Rated apparent power",
                    section="assets", target=_target("assets", "Link", None, "eh_process"))
    sk = _poc_sk_mva(inp.n)
    fault_assumed = _marking_assumed(inp.marking("fault_level"))
    if sk is None:
        return _row(rid, req, rule=RULE_HARMONIC, limit=limit, unit=unit, source=source, project_value=None,
                    verdict="unknown", assumed=True, note="No short-circuit level at the point of connection",
                    section="assets",
                    target=_target("connection", "Bus", (inp.marking("fault_level") or {}).get("entity"),
                                   "eh_sk_mva"))
    for rec in units.to_dict("records"):
        try:
            hm.unit_spectrum(rec["harmonic_source"], rec["harmonic_spectrum"])
        except ContractError as exc:
            # One unit's malformed stored input reads as unknown here, not a failed scorecard.
            field = "harmonic_spectrum" if str(exc).startswith("Harmonic spectrum") else "harmonic_source"
            return _row(rid, req, rule=RULE_HARMONIC, limit=limit, unit=unit, source=source, project_value=None,
                        verdict="unknown", assumed=True, note=f"{rec['unit']}: {exc}", section="assets",
                        target=_target("assets", "Link", rec["unit"], field))
    study = hm.harmonic_table(units, {"PoC": sk}, limits)
    poc = study.pocs.iloc[0]
    demo = study.units.loc[study.units["spectrum_source"].astype(str).str.startswith("demo"), "unit"].tolist()
    missing = list(poc["no_input_units"])
    first = missing[0] if missing else (demo[0] if demo else None)
    field = ("rated_mva" if missing and "rated_mva" in study.units.set_index("unit").at[first, "note"]
             else "harmonic_source" if missing else "harmonic_spectrum" if demo else "harmonic_source")
    target = _target("assets", "Link", first, field)
    thd, worst = _num(poc["thd_pct"]), _num(poc["worst_u_h_pct"])
    value = thd if by_thd else worst
    if not by_thd:
        limit = _num(poc["worst_limit_pct"])
    notes = []
    if thd is not None:
        notes.append(f"THD {thd:.4g} %" + (f" (limit {limits.thd_pct:g} %)" if by_thd else ""))
    if worst is not None and pd.notna(poc["worst_order"]):
        lim_h = _num(poc["worst_limit_pct"])
        notes.append(f"worst order {int(poc['worst_order'])}: {worst:.4g} %"
                     + (f" (limit {lim_h:g} %)" if lim_h is not None else ""))
    if poc["failing_orders"]:
        notes.append("orders over their limit: " + ", ".join(map(str, poc["failing_orders"])))
    if missing:
        notes.append("no input for " + ", ".join(map(str, missing)) + ": a unit without its inputs blocks a pass")
    if demo:
        notes.append("demo default spectrum for " + ", ".join(map(str, demo))
                     + " (no vendor spectrum entered)")
    notes.append(f"S_k {sk:g} MVA; {_HARMONIC_SCREENING}")
    verdict = {"pass": "pass", "fail": RULE_HARMONIC["otherwise"]}.get(poc["verdict"], "unknown")
    row = _row(rid, req, rule=RULE_HARMONIC, limit=limit, unit=unit, source=source, project_value=value,
               verdict=verdict, assumed=lim_assumed or fault_assumed or bool(demo), note="; ".join(notes),
               section="assets", target=target)
    row["harmonics"] = {"sk_mva": sk, "thd_pct": thd, "worst_order": None if pd.isna(poc["worst_order"])
                        else int(poc["worst_order"]), "worst_u_h_pct": worst,
                        "failing_orders": list(poc["failing_orders"] or []), "no_input_units": missing,
                        "demo_spectrum_units": demo}
    return row


_TDD_UNIT = "%\u00a0of\u00a0I_L"
#: Where the TDD row's short-circuit level and load current come from (``current_tdd.tdd_table``'s ``basis``).
#: S_sc is the entered S_k'' as is: the Isc/IL ratio derives no impedance, so no c factor (GK ruling on #754).
_TDD_SK_NOTE = ("S_sc: the fault level entered at the PoC, not IEEE 519's maximum short-circuit current, so the "
                "band can differ from the standard's")
_TDD_IL_IMPORT = "I_L: the connection's rated import at the PoC, not IEEE 519's average monthly maximum demand"
_TDD_IL_PROCESS = ("I_L: the process units' rated load only, not the site's maximum demand; the band may be "
                   "looser than IEEE 519's")


def _poc_vn_kv(n) -> float | None:
    """The nominal voltage of the PoC bus whose S_k'' is ``_poc_sk_mva``'s (the smallest)."""
    if "eh_poc" not in n.buses.columns or "eh_sk_mva" not in n.buses.columns:
        return None
    pocs = [(_num(n.buses.at[b, "eh_sk_mva"]), _num(n.buses.at[b, "v_nom"])) for b in n.buses.index
            if n.buses.at[b, "eh_poc"] is True or str(n.buses.at[b, "eh_poc"]).lower() in ("true", "1")]
    pocs = [(sk, vn) for sk, vn in pocs if sk is not None and sk > 0]
    return min(pocs)[1] if pocs else None


def _tdd_row(inp: _Inputs) -> dict:
    """The current distortion the process units draw at the PoC (DC grid readiness 12 part 1,
    ``gridspine.static.current_tdd``): the single-PoC form of ``tdd_table``, every unit lumped at the PoC,
    S_sc from ``_poc_sk_mva``, I_L the connection's rated import (else the process units' rated load), judged on
    the standard the profile's ``current_tdd_standard`` names. Every row is assumed: I_L is not IEEE 519's."""
    import pandas as pd

    rid, req = "current_distortion", "Current distortion (TDD) at the PoC from the site's drives and heaters"
    unit_target = _target("assets", "Link", None, "harmonic_source")
    try:
        from gridspine.drivers.campus_study import harmonic_units
        from gridspine.schema.contracts import ContractError
        from gridspine.static import current_tdd as td
        from gridspine.static import harmonics as hm
    except ImportError as exc:  # pragma: no cover - build-shape branch
        note = f"The current-distortion screen needs gridspine ({exc})"
        return _row(rid, req, rule=RULE_TDD, limit=None, unit=_TDD_UNIT, source=note, project_value=None,
                    verdict="unknown", assumed=True, note=note, section="assets", target=unit_target)
    standard, cited = td.standard_from_profile(inp.profile)
    if standard is None:
        return _row(rid, req, rule=RULE_TDD, limit=None, unit=_TDD_UNIT,
                    source=inp.profile_error or "The grid code names no current-distortion standard",
                    project_value=None, verdict="unknown", assumed=True,
                    note="The grid code names no current-distortion standard", section="assets",
                    target=unit_target)
    source = f"{_profile_title(inp)}: {cited[0]['clause']}"
    units = harmonic_units(inp.n, "PoC") if inp.n is not None else None
    if units is None or units.empty:
        return _row(rid, req, rule=RULE_TDD, limit=None, unit=_TDD_UNIT, source=source, project_value=None,
                    verdict="unknown", assumed=True,
                    note="No process unit on this project: the screen reads each drive's or heater's Harmonic "
                         "source and Rated apparent power",
                    section="assets", target=_target("assets", "Link", None, "eh_process"))
    sk = _poc_sk_mva(inp.n)
    if sk is None:
        return _row(rid, req, rule=RULE_TDD, limit=None, unit=_TDD_UNIT, source=source, project_value=None,
                    verdict="unknown", assumed=True, note="No short-circuit level at the point of connection",
                    section="assets",
                    target=_target("connection", "Bus", (inp.marking("fault_level") or {}).get("entity"),
                                   "eh_sk_mva"))
    demo = []
    for rec in units.to_dict("records"):
        try:
            _, spectrum_source = hm.unit_spectrum(rec["harmonic_source"], rec["harmonic_spectrum"])
        except ContractError as exc:
            # One unit's malformed stored input reads as unknown here, not a failed scorecard.
            field = "harmonic_spectrum" if str(exc).startswith("Harmonic spectrum") else "harmonic_source"
            return _row(rid, req, rule=RULE_TDD, limit=None, unit=_TDD_UNIT, source=source, project_value=None,
                        verdict="unknown", assumed=True, note=f"{rec['unit']}: {exc}", section="assets",
                        target=_target("assets", "Link", rec["unit"], field))
        if spectrum_source.startswith("demo"):
            demo.append(rec["unit"])
    rated = pd.to_numeric(units["rated_mva"], errors="coerce")
    if inp.connection_mw:
        i_l, i_l_note = inp.connection_mw, _TDD_IL_IMPORT
    else:
        i_l, i_l_note = (float(rated.sum()) if rated.notna().any() else None), _TDD_IL_PROCESS
    vn_kv = _poc_vn_kv(inp.n)
    poc = td.tdd_table(units, {"PoC": sk}, {"PoC": i_l}, {"PoC": vn_kv}, standard,
                       basis=(_TDD_SK_NOTE, i_l_note)).iloc[0]
    tdd, limit = _num(poc["tdd_pct"]), _num(poc["tdd_limit_pct"])
    missing = list(poc["no_input_units"] or [])
    if poc["verdict"] == "unknown" and poc["band"] is None:
        # tdd_table's own guards: no voltage, or no load current at the PoC. Without a connection rating I_L
        # is the process units' rated load, so Go to field opens the first unit without a rating.
        unrated = units.loc[rated.isna(), "unit"]
        if vn_kv is None:
            target = _target("connection", "Bus", None, "v_nom")
        elif i_l_note == _TDD_IL_PROCESS and len(unrated):
            target = _target("assets", "Link", unrated.iloc[0], "rated_mva")
        else:
            target = _target("connection", "Link", inp.link, "p_nom")
        return _row(rid, req, rule=RULE_TDD, limit=None, unit=_TDD_UNIT, source=source, project_value=None,
                    verdict="unknown", assumed=True, note=str(poc["note"]), section="assets", target=target)
    if missing:
        first = missing[0]
        rated = pd.to_numeric(units.set_index("unit")["rated_mva"], errors="coerce").get(first)
        target = _target("assets", "Link", first, "rated_mva" if pd.isna(rated) else "harmonic_source")
    elif demo:
        # As the harmonic row: Go to field opens the first unit whose spectrum is a topology's demo default.
        target = _target("assets", "Link", demo[0], "harmonic_spectrum")
    else:
        target = unit_target
    notes = []
    if missing:
        notes.append(f"{', '.join(map(str, missing))} {'has' if len(missing) == 1 else 'have'} no Harmonic source "
                     "or Rated apparent power; a unit without them blocks a pass.")
    else:
        notes.append(f"Current distortion {tdd:.4g} % of the load current against {limit:g} %.")
    notes.append(f"IEEE 519-2014 {poc['table']}: short-circuit ratio {poc['isc_il']:.3g}, "
                 f"band {str(poc['band']).replace('<=', '≤')}.")
    worst, worst_lim = _num(poc["worst_i_h_pct"]), _num(poc["worst_limit_pct"])
    if not missing and worst is not None and pd.notna(poc["worst_order"]):
        notes.append(f"Worst order {int(poc['worst_order'])}: {worst:.3g} %"
                     + (f" (limit {worst_lim:g} %)." if worst_lim is not None else "."))
    if poc["failing_orders"]:
        notes.append("Orders over their limit: " + ", ".join(map(str, poc["failing_orders"])) + ".")
    if demo and not missing:
        notes.append("Demo default spectrum for " + ", ".join(map(str, demo)) + " (no vendor spectrum entered).")
    notes.append(f"{_TDD_SK_NOTE}. {i_l_note}. Screening: above the limit, run a harmonic load-flow study.")
    verdict = {"pass": "pass", "fail": RULE_TDD["otherwise"]}.get(poc["verdict"], "unknown")
    row = _row(rid, req, rule=RULE_TDD, limit=limit, unit=_TDD_UNIT, source=source,
               project_value=None if missing else round(tdd, 4), verdict=verdict, assumed=True,
               note=" ".join(notes), section="assets", target=target)
    row["tdd"] = {"sk_mva": sk, "i_l_mva": i_l, "isc_il": float(poc["isc_il"]), "table": poc["table"],
                  "band": poc["band"], "tdd_pct": tdd, "tdd_limit_pct": limit,
                  "worst_order": None if pd.isna(poc["worst_order"]) else int(poc["worst_order"]),
                  "failing_orders": list(poc["failing_orders"] or []), "no_input_units": missing,
                  "label": td.LABEL}
    return row


#: The scorecard has one S_k'' per PoC and no sequence network, so its row is the single-PoC form of
#: ``gridspine.static.unbalance``: S_sc2 = S_k'' / c_max (S_k'' = c U^2 / |Z|; the largest c gives the smallest
#: S_sc2, conservative whichever case S_k'' is) with Z2 = Z1, no transfer from other PoCs and no min/max bands.
_UNBALANCE_BAND = "PoC S_k''"
#: Non-breaking spaces: the unit wraps to the next line as a whole, never inside "positive sequence".
_UNBALANCE_UNIT = "%\u00a0of\u00a0positive\u00a0sequence"
_UNBALANCE_SCREENING = ("It assumes every unit sits on one phase pair at the PoC, equal negative- and "
                        "positive-sequence impedance, and no background unbalance. The negative-sequence "
                        "short-circuit power is the entered fault level divided by the IEC 60909 voltage factor "
                        "(c max). Above the limit, run an unbalanced load-flow study.")


def _unbalance_row(inp: _Inputs) -> dict:
    """The negative-sequence voltage unbalance the single-phase process load causes at the PoC (DC grid
    readiness 12, ``gridspine.static.unbalance``): S_unbal = Σ single_phase_share · rated_mva over the
    process units, S_sc2 from ``_poc_sk_mva``, judged on the profile's ``unbalance_limit_pct``."""
    import pandas as pd

    rid, req = "voltage_unbalance", "Voltage unbalance at the PoC from the site's single-phase load"
    unit_target = _target("assets", "Link", None, "single_phase_share")
    try:
        from gridspine.drivers.campus_study import harmonic_units
        from gridspine.schema.contracts import ContractError
        from gridspine.static import unbalance as ub
        from gridspine.static.island_sc_seq import c_factor
    except ImportError as exc:  # pragma: no cover - build-shape branch
        note = f"The unbalance screen needs gridspine ({exc})"
        return _row(rid, req, rule=RULE_UNBALANCE, limit=None, unit=_UNBALANCE_UNIT, source=note,
                    project_value=None, verdict="unknown", assumed=True, note=note, section="assets",
                    target=unit_target)
    limit, cited = ub.limit_from_profile(inp.profile)
    if not cited:
        return _row(rid, req, rule=RULE_UNBALANCE, limit=None, unit=_UNBALANCE_UNIT,
                    source=inp.profile_error or ub.NO_LIMIT, project_value=None, verdict="unknown",
                    assumed=True, note=ub.NO_LIMIT, section="assets", target=unit_target)
    source = f"{_profile_title(inp)}: {cited[0]['clause']}"
    lim_assumed = cited[0]["source"] != "code"
    units = harmonic_units(inp.n, "PoC") if inp.n is not None else None
    if units is None or units.empty:
        return _row(rid, req, rule=RULE_UNBALANCE, limit=limit, unit=_UNBALANCE_UNIT, source=source,
                    project_value=None, verdict="unknown", assumed=True,
                    note="No process unit on this project: the screen reads each unit's Single-phase share "
                         "and Rated apparent power",
                    section="assets", target=_target("assets", "Link", None, "eh_process"))
    shares = (inp.n.links["single_phase_share"] if "single_phase_share" in inp.n.links.columns
              else pd.Series(None, index=inp.n.links.index, dtype=object))
    loads = pd.DataFrame({"load": units["unit"], "poc": units["poc"], "s_mva": units["rated_mva"],
                          "single_phase_share": list(shares.reindex(units["unit"]))})
    try:
        s_unbal, _, missing = ub.unbalanced_mva(loads)
    except ContractError as exc:
        # One unit's malformed stored share reads as unknown here, not a failed scorecard.
        return _row(rid, req, rule=RULE_UNBALANCE, limit=limit, unit=_UNBALANCE_UNIT, source=source,
                    project_value=None, verdict="unknown", assumed=True, note=str(exc), section="assets",
                    target=unit_target)
    missing = missing["PoC"]
    sk = _poc_sk_mva(inp.n)
    if sk is None:
        return _row(rid, req, rule=RULE_UNBALANCE, limit=limit, unit=_UNBALANCE_UNIT, source=source,
                    project_value=None, verdict="unknown", assumed=True,
                    note="No short-circuit level at the point of connection", section="assets",
                    target=_target("connection", "Bus", (inp.marking("fault_level") or {}).get("entity"),
                                   "eh_sk_mva"))
    s_sc2 = sk / c_factor(None, "max")
    u2 = ub.u2_pct(s_unbal["PoC"], s_sc2)
    verdict, margin = ub.judge(u2, limit)
    if missing:
        if verdict != "fail":
            verdict = "unknown"
        # A unit without inputs leaves u2 a floor, so the row shows no number; a floor above 0 is said in words.
        notes = [f"{', '.join(map(str, missing))} {'has' if len(missing) == 1 else 'have'} no Single-phase share "
                 "or Rated apparent power; a unit without them blocks a pass."]
        if u2 > 0:
            notes.append(f"At least {u2:.4g} % from the units with inputs.")
    else:
        notes = [f"Screening upper bound: {s_unbal['PoC']:.4g} MVA of single-phase load ÷ {s_sc2:.4g} MVA "
                 f"negative-sequence short-circuit power = {u2:.2g} %."]
    notes.append(_UNBALANCE_SCREENING)
    verdict = {"pass": "pass", "fail": RULE_UNBALANCE["otherwise"]}.get(verdict, "unknown")
    row = _row(rid, req, rule=RULE_UNBALANCE, limit=limit, unit=_UNBALANCE_UNIT, source=source,
               project_value=None if missing else round(u2, 4), verdict=verdict,
               assumed=True if missing else lim_assumed or _marking_assumed(inp.marking("fault_level")),
               note=" ".join(notes), section="assets",
               target=_target("assets", "Link", missing[0], "single_phase_share") if missing else unit_target)
    row["unbalance"] = {"s_unbalanced_mva": s_unbal["PoC"], "s_sc2_mva": s_sc2, "margin_pct": margin,
                        "band": _UNBALANCE_BAND, "no_input_units": missing, "label": ub.LABEL}
    return row


_FLICKER_SCREENING = ("a screening estimate (IEC 61000-3-7 analytic method, d = step / S_k, rectangular steps, "
                      "Plt = Pst for a steady pattern) that ignores the grid X/R and the operator's allocation of the "
                      "planning level; units summed with alpha 3 (assumed; units stepping together are not "
                      "covered); S_k is the entered S_k'' with the IEC 60909 c factor, so d may be understated "
                      "by up to 1.1; above the limit a flicker-meter study is needed")


def _flicker_row(inp: _Inputs) -> dict:
    """The flicker severity the process units cause at the PoC (grid-to-process issue 17,
    ``gridspine.static.flicker``): every unit lumped at the PoC, S_k from ``_poc_sk_mva``, judged on whichever
    of ``pst_limit`` and ``plt_limit`` the profile sets."""
    rid, req = "flicker", "Flicker severity at the PoC from the site's stepping loads"
    unit_target = _target("assets", "Link", None, "flicker_step_mva")
    try:
        from gridspine.drivers.campus_study import flicker_units
        from gridspine.static import flicker as fl
    except ImportError as exc:  # pragma: no cover - build-shape branch
        note = f"The flicker screen needs gridspine ({exc})"
        return _row(rid, req, rule=RULE_FLICKER, limit=None, unit="Pst", source=note, project_value=None,
                    verdict="unknown", assumed=True, note=note, section="assets", target=unit_target)
    limits, cited = fl.limits_from_profile(inp.profile)
    if not cited:
        return _row(rid, req, rule=RULE_FLICKER, limit=None, unit="Pst",
                    source=inp.profile_error or "The grid code sets no flicker limit", project_value=None,
                    verdict="unknown", assumed=True, note="The grid code sets no flicker limit",
                    section="assets", target=unit_target)
    lim_assumed = any(c["source"] != "code" for c in cited)
    # Plt equals Pst in this screen, so the smaller limit binds: the row shows that quantity.
    by_pst = limits.pst is not None and (limits.plt is None or limits.pst <= limits.plt)
    limit, unit = (limits.pst, "Pst") if by_pst else (limits.plt, "Plt")
    part = {"pst_limit": "Pst", "plt_limit": "Plt"}
    source = f"{_profile_title(inp)}: " + "; ".join(f"{part[c['key']]}: {c['clause']}" for c in cited)
    units = flicker_units(inp.n, "PoC") if inp.n is not None else None
    if units is None or units.empty:
        return _row(rid, req, rule=RULE_FLICKER, limit=limit, unit=unit, source=source, project_value=None,
                    verdict="unknown", assumed=True,
                    note="No process unit on this project: the screen reads each unit's Largest power step and "
                         "Changes per minute",
                    section="assets", target=_target("assets", "Link", None, "eh_process"))
    sk = _poc_sk_mva(inp.n)
    fault_assumed = _marking_assumed(inp.marking("fault_level"))
    if sk is None:
        return _row(rid, req, rule=RULE_FLICKER, limit=limit, unit=unit, source=source, project_value=None,
                    verdict="unknown", assumed=True, note="No short-circuit level at the point of connection",
                    section="assets",
                    target=_target("connection", "Bus", (inp.marking("fault_level") or {}).get("entity"),
                                   "eh_sk_mva"))
    labels = {"flicker_step_mva": fl.STEP_LABEL, "flicker_changes_per_min": fl.RATE_LABEL}
    for rec in units.to_dict("records"):
        for field, col in (("flicker_step_mva", "step_mva"), ("flicker_changes_per_min", "changes_per_min")):
            v = _num(rec[col])
            if v is not None and v < 0:
                # One unit's malformed stored input reads as unknown here, not a failed scorecard.
                return _row(rid, req, rule=RULE_FLICKER, limit=limit, unit=unit, source=source,
                            project_value=None, verdict="unknown", assumed=True,
                            note=f"{rec['unit']}: {labels[field]} must be 0 or more, got {v:g}", section="assets",
                            target=_target("assets", "Link", rec["unit"], field))
    study = fl.flicker_table(units, {"PoC": sk}, limits)
    poc = study.pocs.iloc[0]
    by_unit = study.units.set_index("unit")
    missing, outside = list(poc["no_input_units"]), list(poc["outside_range_units"])
    notes, first = [], None
    for name in missing:
        u = by_unit.loc[name]
        fields = [f for f, v in (("flicker_step_mva", u["step_mva"]), ("flicker_changes_per_min",
                                                                       u["changes_per_min"])) if _num(v) is None]
        first = first or (name, fields[0])
        notes.append(f"{name}: " + " and ".join(labels[f] for f in fields)
                     + (" is" if len(fields) == 1 else " are") + " not set")
    for name in outside:
        u = by_unit.loc[name]
        too_fast = 60.0 / u["changes_per_min"] < fl.MIN_INTERVAL_S - fl.TOLERANCE if u["changes_per_min"] else False
        first = first or (name, "flicker_changes_per_min" if too_fast else "flicker_step_mva")
        notes.append(f"{name}: outside the analytic method ({u['note']})")
    known = study.units.dropna(subset=["pst"])
    if first is None and not known.empty:
        first = (known.loc[known["pst"].idxmax(), "unit"], "flicker_step_mva")
    target = _target("assets", "Link", first[0] if first else None, first[1] if first else "flicker_step_mva")
    p, plt = _num(poc["pst"]), _num(poc["plt"])
    if p is not None:
        notes.insert(0, f"Pst {p:.4g}" + (f" (limit {limits.pst:g})" if by_pst else "")
                     + f", Plt {plt:.4g}" + (f" (limit {limits.plt:g})" if limits.plt is not None else ""))
    if limits.pst is None or limits.plt is None:
        notes.append(f"judged on {unit} only: the grid code sets no {'Plt' if by_pst else 'Pst'} limit")
    elif p is not None:
        notes.append(f"{unit} binds: Plt equals Pst for a steady pattern")
    if missing or outside:
        notes.append("a unit without its inputs or outside the method blocks a pass")
    notes.append(f"S_k {sk:g} MVA; {_FLICKER_SCREENING}")
    verdict = {"pass": "pass", "fail": RULE_FLICKER["otherwise"]}.get(poc["verdict"], "unknown")
    row = _row(rid, req, rule=RULE_FLICKER, limit=limit, unit=unit, source=source,
               project_value=p if by_pst else plt, verdict=verdict, assumed=lim_assumed or fault_assumed,
               note="; ".join(notes), section="assets", target=target)
    row["flicker"] = {"sk_mva": sk, "pst": p, "plt": plt, "pst_limit": limits.pst, "plt_limit": limits.plt,
                      "no_input_units": missing, "outside_range_units": outside}
    return row


_MOTOR_START_SCREENING = ("a screening estimate (dU = (S_start · sin φ_start − Q_gfm) / S_sc, the grid's R ignored, "
                          "one motor starting at a time); the start current multiples and starting power factors are "
                          "demo values; above the limit a motor-start load-flow or RMS study is needed")
_MOTOR_FIELDS = ("motor_kw", "start_method", "i_start_pu")


def _poc_sk_min(inp: _Inputs):
    """``(S_k''min MVA, PoC kV)`` from the project's campus description when it states a minimum case of its own
    for a PoC bus (``campus_study._sk_source`` reads SK_CAMPUS: not the bus's S_k'' copied, not the typical
    fallback), else ``(None, None)``."""
    try:
        import yaml
        from gridspine.drivers.campus_study import SK_CAMPUS, _pcc_sk_min, _sk_source
    except ImportError:  # pragma: no cover - build-shape branch
        return None, None
    try:
        campus = yaml.safe_load((inp.dir / "campus_electrical" / "campus_input.yaml").read_text())
        pcc = campus["campus"]["pcc"]
        bus = pcc.get("pypsa_name")
        if bus not in inp.n.buses.index or not _truthy(inp.n.buses.at[bus, "eh_poc"]):
            return None, None
        if _sk_source(campus, inp.n)[0] != SK_CAMPUS:
            return None, None
        return _pcc_sk_min(pcc), _num(pcc.get("vn_kv"))
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError):
        # No, or no readable, campus description: the entered S_k'' is used, and the row says so.
        return None, None


def _truthy(v) -> bool:
    return v is True or str(v).lower() in ("true", "1")


def _q_gfm_mvar(inp: _Inputs) -> tuple[float | None, str | None]:
    """The reactive power the grid-forming storage at the PoC can give, Mvar: Σ rating · tan(acos pf_rated) over
    the island's battery units offering grid forming, as the reactive-range row counts it; None when the project
    has no island requirements. A unit without a size or rated power factor counts zero, named in the note."""
    services = _services(inp)
    if services is None:
        return None, None
    have, parts, unstated = 0.0, [], []
    for name, u in services["units"].items():
        if u["kind"] != "bess" or "grid_forming" not in u["services"]:
            continue
        pf = _num(inp.island["units"][name].get("values", {}).get("pf_rated"))
        rating, _ = _unit_rating(inp.n, name)
        if pf is None or not rating or not 0 < pf <= 1:
            unstated.append(name)
            continue
        q = rating * math.tan(math.acos(pf))
        have += q
        parts.append(f"{name} {q:.3g} Mvar")
    note = (f"Q_gfm {have:.4g} Mvar ({', '.join(parts) or 'no grid-forming storage'}) as rating × tan(acos rated "
            "power factor), an assumed capability")
    if unstated:
        note += f"; {', '.join(unstated)} counted zero: no size or no rated power factor"
    return have, note


def _motor_start_rows(inp: _Inputs) -> list[dict]:
    """The voltage change at the PoC when each process unit's motor starts (grid-to-process issue 18,
    ``gridspine.static.motor_start``): one row per process unit, for its own start method, judged on the
    profile's ``rvc_limit_pct``. With no process unit, one ``unknown`` row says so."""
    req = "Voltage change at the PoC when the unit's motor starts"
    unit_target = _target("assets", "Link", None, "motor_kw")
    try:
        import pandas as pd
        from gridspine.schema.contracts import ContractError
        from gridspine.static import motor_start as ms
    except ImportError as exc:  # pragma: no cover - build-shape branch
        note = f"The motor-start screen needs gridspine ({exc})"
        return [_row("motor_start", req, rule=RULE_MOTOR_START, limit=None, unit="% of nominal", source=note,
                     project_value=None, verdict="unknown", assumed=True, note=note, section="assets",
                     target=unit_target)]
    limit, clause = ms.limit_from_profile(inp.profile)
    source = (f"{_profile_title(inp)}: {clause}" if limit is not None
              else inp.profile_error or "The grid code sets no rapid-voltage-change limit")
    lim = (inp.profile or {}).get("rvc_limit_pct")
    lim_assumed = limit is None or lim.get("source") != "code"
    links = inp.n.links if inp.n is not None else None
    process = ([] if links is None or "eh_process" not in links.columns
               else [u for u in links.index if _truthy(links.at[u, "eh_process"])])
    if not process:
        return [_row("motor_start", req, rule=RULE_MOTOR_START, limit=limit, unit="% of nominal", source=source,
                     project_value=None, verdict="unknown", assumed=True,
                     note="No process unit on this project: the screen reads each unit's Motor rating and Start "
                          "method",
                     section="assets", target=_target("assets", "Link", None, "eh_process"))]
    units = pd.DataFrame({"unit": process, "poc": "PoC",
                          **{c: [links.at[u, c] if c in links.columns else None for u in process]
                             for c in _MOTOR_FIELDS}})
    sk_min, vn = _poc_sk_min(inp)
    sources = {"PoC": ms.Source(sk_mva=_poc_sk_mva(inp.n), sk_min_mva=sk_min,
                                vn_kv=vn if vn is not None else _poc_vn_kv(inp.n))}
    q_gfm, q_note = _q_gfm_mvar(inp)
    fault_assumed = _marking_assumed(inp.marking("fault_level"))
    rows = []
    for rec in units.to_dict("records"):
        rid, name = f"motor_start:{rec['unit']}", rec["unit"]
        try:
            r = ms.motor_start_table(pd.DataFrame([rec]), sources, {"PoC": q_gfm}, limit).iloc[0]
        except ContractError as exc:
            # One unit's malformed stored input reads as unknown on its own row, not a failed scorecard.
            rows.append(_row(rid, f"{req}: {name}", rule=RULE_MOTOR_START, limit=limit, unit="% of nominal",
                             source=source, project_value=None, verdict="unknown", assumed=True, note=str(exc),
                             section="assets", target=_target("assets", "Link", name, "motor_kw")))
            continue
        d = _num(r["du_pct"])
        if r["note"] == ms.NO_SK:
            target = _target("connection", "Bus", (inp.marking("fault_level") or {}).get("entity"), "eh_sk_mva")
        else:
            # A missing input first, else the start method: the lever on the voltage change.
            field = "motor_kw" if d is None and _num(rec["motor_kw"]) is None else "start_method"
            target = _target("assets", "Link", name, field)
        notes = [r["note"]]
        if d is not None:
            notes += [q_note] if q_note else []
            notes.append(_MOTOR_START_SCREENING)
        verdict = {"pass": "pass", "fail": RULE_MOTOR_START["otherwise"]}.get(r["verdict"], "unknown")
        # A computed value rests on the demo start multiples and power factors, so it is always assumed.
        row = _row(rid, f"{req}: {name}", rule=RULE_MOTOR_START, limit=limit, unit="% of nominal", source=source,
                   project_value=None if d is None else round(d, 4), verdict=verdict,
                   assumed=lim_assumed or fault_assumed or d is not None, note="; ".join(notes), section="assets",
                   target=target)
        row["motor_start"] = {k: (r[k] if isinstance(r[k], str) or r[k] is None else _num(r[k]))
                              for k in ("motor_kw", "start_method", "start_multiple", "start_pf", "s_start_mva",
                                        "q_gfm_mvar", "sk_mva", "sk_case", "c", "s_sc_mva", "du_pct")}
        rows.append(row)
    return rows


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
                         note=("Needs an RMS study (dynamic; EMT where the code asks for it); it applies to "
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
            _rvc_row(inp), _rvc_infrequent_row(inp), _harmonic_row(inp), _tdd_row(inp), _flicker_row(inp),
            *_motor_start_rows(inp), _unbalance_row(inp),
            _reactive_row(inp), *_support_rows(inp), *_island_rows(inp), _lole_row(inp)]
    _after_battery(inp, rows)
    settings = {"ramp_rate": ("connection_ramp", _generic_ramp(inp)),
                "oscillation": ("oscillation", GENERIC_OSCILLATION_PCT),
                "scr": ("fault_level", None)}
    for r in rows:
        if r["id"] in settings:
            r["setting"] = _setting(inp, *settings[r["id"]])
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
    out = grid_readiness(d, n, profile, profile_dirs=(codes,), eh_study=record, study_edited=edited)
    # SCORECARD-STALE-1: whether this profile is still the code the last campus run was held to.
    out["profile"]["since_run"] = ce.grid_code_since_run(project, profile)
    return out


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
