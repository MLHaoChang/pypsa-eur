"""
The project's scope: which work cards, and which of their sub-options, the one
Run computes (project-workflow W4a; workspace-reconciliation §5.2,
modular-cards.md).

Six cards, each with at most three sub-options. The scope is stored per project
in ``flow_scope.json`` as ``{version, groups, options}``: ``groups`` the cards
switched on, ``options`` each card's chosen sub-options. A card switched off
keeps its sub-options, so switching it on again restores them (modular-cards
§2, "not lost"). The file travels with bundles, snapshots and forks
(``routers.projects._BUNDLE_FILES``).

A project with no ``flow_scope.json`` reads its template's preset
(``TEMPLATE_PRESETS``, keyed by ``eh_template.json``'s ``id``; the own site by
its ``site_type``), or the blank preset. A preset names the sub-options its
example intends; the ones not yet available are dropped from the effective
preset (a "not yet available" sub-option is never pre-selected) and are
listed as ``intended`` so the card can show them.

Availability (``AVAILABILITY``) follows modular-cards §1's last column, with
every "per W0" entry not yet available until W0's separability matrix marks it
supported (§11.1: unknown stays non-runnable). W0 owns flipping these rows.

Pure data plus the sidecar's read and write; ``flow_service`` reads it.
"""
from __future__ import annotations

import json
import pathlib
import threading
from dataclasses import dataclass
from typing import Any, Final

SCOPE_FILE: Final = "flow_scope.json"
SCOPE_VERSION: Final = 1

AVAILABLE: Final = "available"
REQUIRED: Final = "required"            # always on; it cannot be deselected yet
NOT_YET: Final = "not_yet_available"


@dataclass(frozen=True)
class Card:
    id: str
    label: str
    options: tuple[tuple[str, str], ...]       # (id, label), at most three
    # A card that cannot be switched off yet, and why.
    locked_reason: str | None = None
    # The input sections whose blocks this card needs (§5.1).
    sections: tuple[str, ...] = ()


_SIZING_OFF = ("Sizing off needs the fixed-size run option, which is not yet available "
               "(modular-cards §4).")

CARDS: Final[tuple[Card, ...]] = (
    Card("capacity", "Capacity expansion",
         (("sizing", "Asset sizing"), ("dispatch", "Dispatch & economics"),
          ("process_detail", "Process detail")),
         locked_reason="Capacity off needs a dispatch source, which is not yet available.",
         sections=("project", "carriers", "connection", "loads", "assets", "profiles",
                   "commercial", "scenarios")),
    Card("grid", "AC power flow & grid",
         (("load_flow", "Load flow"), ("grid_code", "Grid code & electrical design"),
          ("n1_sclopf", "N-1 security (SCLOPF)")),
         sections=("connection", "assets", "scenarios")),
    Card("control", "Control",
         (("presets", "Converter control presets"),
          ("island_checks", "Island & ride-through checks"),
          ("dynamics", "Frequency & dynamics")),
         sections=("assets", "scenarios")),
    Card("reliability", "Reliability",
         (("goal_study", "Reliability goal & study"), ("stress", "Stress conditions & FMEA"),
          ("outage_screening", "Outage-data screening")),
         sections=("assets", "scenarios")),
    Card("commercial", "Commercial & finance",
         (("tariffs", "Tariffs & bill"), ("contracts", "Contracts & PPAs"),
          ("investment_case", "Investment case & parties")),
         sections=("commercial",)),
    Card("handoff", "Hand-off",
         (("reports", "Reports"), ("exports", "Data exports"),
          ("specialist_packet", "Specialist packet")),
         locked_reason="Hand-off is always on (owner Q1).",
         sections=()),
)
CARD_BY_ID: Final[dict[str, Card]] = {c.id: c for c in CARDS}

_NEEDS_TEMPLATE = "Needs an Energy Hub site (a project made from an example or your own site)."

# (card, option) → (status, reason). modular-cards §1, last column; "per W0"
# reads not yet available until W0 classifies it supported.
AVAILABILITY: Final[dict[tuple[str, str], tuple[str, str | None]]] = {
    ("capacity", "sizing"): (REQUIRED, _SIZING_OFF),
    ("capacity", "dispatch"): (REQUIRED, "Dispatch runs with every capacity run."),
    ("capacity", "process_detail"): (NOT_YET, "Detailed process modelling is not yet "
                                     "classified as runnable (W0)."),
    ("grid", "load_flow"): (AVAILABLE, None),
    ("grid", "grid_code"): (AVAILABLE, None),
    ("grid", "n1_sclopf"): (AVAILABLE, None),
    ("control", "presets"): (AVAILABLE, None),
    ("control", "island_checks"): (NOT_YET, "The island and ride-through checks are not yet "
                                   "classified as runnable (W0)."),
    ("control", "dynamics"): (NOT_YET, "Frequency and time-domain studies are not yet "
                              "available."),
    ("reliability", "goal_study"): (AVAILABLE, None),
    ("reliability", "stress"): (AVAILABLE, None),
    ("reliability", "outage_screening"): (AVAILABLE, None),
    ("commercial", "tariffs"): (AVAILABLE, None),
    ("commercial", "contracts"): (AVAILABLE, None),
    ("commercial", "investment_case"): (NOT_YET, "The investment case is not yet classified "
                                        "as runnable (W0)."),
    ("handoff", "reports"): (AVAILABLE, None),
    ("handoff", "exports"): (AVAILABLE, None),
    ("handoff", "specialist_packet"): (AVAILABLE, None),
}

# Reliability runs the EH study pipeline, which needs the template's pack.
_TEMPLATE_ONLY: Final[frozenset[str]] = frozenset({"reliability"})


def option_status(card: str, option: str, *, has_template: bool) -> tuple[str, str | None]:
    """``(status, reason)`` of a sub-option for this project."""
    status, reason = AVAILABILITY[(card, option)]
    if card in _TEMPLATE_ONLY and not has_template and status != NOT_YET:
        return NOT_YET, _NEEDS_TEMPLATE
    return status, reason


# ── Presets (GS-6, modular-cards §3) ────────────────────────────────────────
#
# What each example intends, card → sub-options. A not-yet-available
# sub-option stays here (it lights up when W0 clears it) and is dropped from
# the effective preset. Proposals for the template authors, aligned with the
# research mapping (FIVE-CARD-MODULARITY §3); not domain-reviewed defaults.

Preset = dict[str, tuple[str, ...]]

_CAPACITY = ("sizing", "dispatch")
_PROCESS = ("sizing", "dispatch", "process_detail")

_DATACENTER: Preset = {
    "capacity": _CAPACITY, "grid": ("load_flow", "grid_code"),
    "control": ("presets", "island_checks"), "reliability": ("goal_study", "stress"),
    "commercial": ("tariffs", "investment_case"), "handoff": ("reports",)}
_MICROGRID: Preset = {
    "capacity": _CAPACITY, "grid": ("load_flow",),
    "control": ("presets", "island_checks"),
    "reliability": ("goal_study", "stress", "outage_screening"),
    "commercial": ("tariffs", "investment_case"), "handoff": ("reports",)}
_H2_HUB: Preset = {
    "capacity": _PROCESS, "reliability": ("goal_study",),
    "commercial": ("tariffs", "contracts", "investment_case"), "handoff": ("reports",)}
_AMMONIA: Preset = {
    "capacity": _PROCESS, "reliability": ("goal_study",),
    "commercial": ("tariffs", "investment_case"), "handoff": ("reports",)}
# The IPP hybrid (#485): commercial-led; grid, reliability and control off by
# default (research §3, "IPP controls are off by default").
_IPP: Preset = {
    "capacity": _CAPACITY, "commercial": ("tariffs", "contracts", "investment_case"),
    "handoff": ("reports",)}
# The own site keeps today's reliability study (B1 requests it for every EH
# template). A hospital adds the stress conditions its template ships.
_OWN_SITE: Preset = {
    "capacity": _CAPACITY, "reliability": ("goal_study",),
    "commercial": ("tariffs", "investment_case"), "handoff": ("reports",)}
_HOSPITAL: Preset = {**_OWN_SITE, "reliability": ("goal_study", "stress")}
BLANK_PRESET: Final[Preset] = {
    "capacity": _CAPACITY, "commercial": ("tariffs",), "handoff": ("reports",)}

TEMPLATE_PRESETS: Final[dict[str, Preset]] = {
    "eh_datacenter": _DATACENTER, "eh_datacenter_staged": _DATACENTER,
    "eh_microgrid": _MICROGRID, "eh_microgrid_staged": _MICROGRID,
    "eh_h2_hub": _H2_HUB, "eh_h2_hub_staged": _H2_HUB,
    "eh_ammonia_staged": _AMMONIA,
    "eh_ipp_hybrid": _IPP,
}
# `eh_site` by its `site_type` (`project_templates/eh_site.SITE_TYPES`).
SITE_TYPE_PRESETS: Final[dict[str, Preset]] = {
    "industrial": _OWN_SITE, "commercial": _OWN_SITE, "hospital": _HOSPITAL,
    "campus": _OWN_SITE, "other": _OWN_SITE,
}


def preset_for(template: dict | None) -> tuple[str, Preset]:
    """``(preset id, intended preset)`` for a project's template metadata."""
    if template is None:
        return "blank", BLANK_PRESET
    tid = str(template.get("id") or "")
    if tid == "eh_site":
        st = str(template.get("site_type") or "industrial")
        return f"eh_site:{st}", SITE_TYPE_PRESETS.get(st, _OWN_SITE)
    return (tid, TEMPLATE_PRESETS[tid]) if tid in TEMPLATE_PRESETS else ("eh_generic", _OWN_SITE)


def _required(card: str, *, has_template: bool) -> list[str]:
    return [o for o, _ in CARD_BY_ID[card].options
            if option_status(card, o, has_template=has_template)[0] == REQUIRED]


def effective_preset(preset: Preset, *, has_template: bool) -> dict:
    """The stored form of a preset: not-yet-available sub-options dropped,
    required ones and locked cards added."""
    groups, options = [], {}
    for card in CARDS:
        intended = preset.get(card.id, ())
        chosen = [o for o, _ in card.options
                  if (o in intended or o in _required(card.id, has_template=has_template))
                  and option_status(card.id, o, has_template=has_template)[0] != NOT_YET]
        options[card.id] = chosen
        if chosen and (card.id in preset or card.locked_reason):
            groups.append(card.id)
    return {"version": SCOPE_VERSION, "groups": groups, "options": options}


# ── The sidecar ─────────────────────────────────────────────────────────────

class ScopeError(ValueError):
    """A scope the store refuses: ``error_kind`` is the typed refusal."""

    def __init__(self, error_kind: str, message: str):
        super().__init__(message)
        self.error_kind = error_kind
        self.message = message


_write_lock = threading.Lock()


def _read_raw(project_dir: pathlib.Path) -> dict | None:
    try:
        raw = json.loads((project_dir / SCOPE_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def normalise(raw: dict, *, has_template: bool) -> dict:
    """A stored scope read leniently: unknown ids dropped, not-yet-available
    sub-options dropped, required ones and locked cards put back. A stored
    file never fails the flow; ``validate`` is the strict check for writes."""
    groups_in = raw.get("groups") if isinstance(raw.get("groups"), list) else []
    opts_in = raw.get("options") if isinstance(raw.get("options"), dict) else {}
    groups, options = [], {}
    for card in CARDS:
        want = opts_in.get(card.id)
        want = want if isinstance(want, list) else []
        req = _required(card.id, has_template=has_template)
        options[card.id] = [o for o, _ in card.options
                            if (o in want or o in req)
                            and option_status(card.id, o, has_template=has_template)[0]
                            != NOT_YET]
        if card.locked_reason or card.id in groups_in:
            groups.append(card.id)
    return {"version": SCOPE_VERSION, "groups": groups, "options": options}


def validate(raw: Any, *, has_template: bool) -> dict:
    """The strict check for a write. Raises ``ScopeError``."""
    if not isinstance(raw, dict) or raw.get("version") != SCOPE_VERSION:
        raise ScopeError("flow_scope_invalid", f"a scope is {{version: {SCOPE_VERSION}, "
                         "groups: [...], options: {card: [...]}}")
    groups, options = raw.get("groups"), raw.get("options")
    if not isinstance(groups, list) or not isinstance(options, dict):
        raise ScopeError("flow_scope_invalid", "groups must be a list and options an object")
    for g in groups:
        if g not in CARD_BY_ID:
            raise ScopeError("flow_scope_unknown", f"unknown card {g!r}")
    for card_id, chosen in options.items():
        card = CARD_BY_ID.get(card_id)
        if card is None:
            raise ScopeError("flow_scope_unknown", f"unknown card {card_id!r}")
        if not isinstance(chosen, list):
            raise ScopeError("flow_scope_invalid", f"options.{card_id} must be a list")
        known = {o for o, _ in card.options}
        for o in chosen:
            if o not in known:
                raise ScopeError("flow_scope_unknown", f"unknown sub-option {card_id}.{o!r}")
            status, reason = option_status(card_id, o, has_template=has_template)
            if status == NOT_YET:
                raise ScopeError("flow_scope_unavailable",
                                 f"{card.label} › {dict(card.options)[o]} is not yet "
                                 f"available: {reason}")
    for card in CARDS:
        label = card.label
        if card.locked_reason and card.id not in groups:
            raise ScopeError("flow_scope_required", f"{label} cannot be switched off: "
                             f"{card.locked_reason}")
        for o in _required(card.id, has_template=has_template):
            if o not in (options.get(card.id) or []):
                raise ScopeError("flow_scope_required",
                                 f"{label} › {dict(card.options)[o]} cannot be switched off: "
                                 f"{option_status(card.id, o, has_template=has_template)[1]}")
    return normalise(raw, has_template=has_template)


def read_scope(project_dir: pathlib.Path, template: dict | None) -> dict:
    """The project's scope: ``{version, groups, options, source, preset_id}``.
    ``source`` is ``project`` (the stored file) or ``preset`` (none stored)."""
    has_template = template is not None
    preset_id, preset = preset_for(template)
    raw = _read_raw(project_dir)
    if raw is None:
        scope = effective_preset(preset, has_template=has_template)
        source = "preset"
    else:
        scope = normalise(raw, has_template=has_template)
        source = "project"
    return {**scope, "source": source, "preset_id": preset_id}


def write_scope(project_dir: pathlib.Path, raw: Any, template: dict | None) -> dict:
    """Check and store a scope atomically; returns what ``read_scope`` now reads."""
    from services.atomic_io import atomic_write_text

    scope = validate(raw, has_template=template is not None)
    with _write_lock:
        atomic_write_text(project_dir / SCOPE_FILE,
                          json.dumps(scope, indent=2, sort_keys=True) + "\n")
    return read_scope(project_dir, template)


def selected(scope: dict, card: str, option: str | None = None) -> bool:
    """A card is on when it is in ``groups`` with at least one sub-option."""
    if card not in scope.get("groups", ()):
        return False
    chosen = scope.get("options", {}).get(card) or []
    return bool(chosen) if option is None else option in chosen


def registry(template: dict | None) -> dict:
    """The cards as the scope component draws them, with this project's
    availability, the effective preset and what the preset intends."""
    has_template = template is not None
    preset_id, preset = preset_for(template)
    cards = []
    for card in CARDS:
        opts = []
        for o, label in card.options:
            status, reason = option_status(card.id, o, has_template=has_template)
            opts.append({"id": o, "label": label, "status": status, "reason": reason})
        cards.append({"id": card.id, "label": card.label, "options": opts,
                      "locked_reason": card.locked_reason,
                      "intended": list(preset.get(card.id, ()))})
    return {"cards": cards, "preset_id": preset_id,
            "preset": effective_preset(preset, has_template=has_template)}


def needed_sections(scope: dict) -> set[str]:
    """The input sections some card in scope needs (§5.2: a section none needs
    is ``not_needed``)."""
    return {s for card in CARDS if selected(scope, card.id) for s in card.sections}
