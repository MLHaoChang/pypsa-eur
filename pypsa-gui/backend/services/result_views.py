"""LC-03 (LC-C3): one typed registry over every result view.

A view is something the user can be shown: a Results tab, or a gated page
whose tools are still arriving. Each entry says which read grounds it (the
exact tool call to make before showing it), how far the browser can prove it
is displayed, what it is called in each mode, and what it may not claim.

The registry is data only. It grants nothing and computes nothing: the reads
stay the native tools and their own refusals, and navigation stays
ui_open_panel. A view whose read needs an entity (an asset, a sized
component) names it in `needs`; the model resolves that before reading.

`verification` is the strongest proof the browser can give today:
`displayed_data` means a source-bound (v2) ACK of the exact data shown;
`mounted_view` means the tab mounted, not which data it rendered. Only load
flow has a displayed-data adapter; the words say "showing" only with that.

`historical_section` names the run-evidence section that keeps this view for
past runs. None means past runs refuse it; current results never stand in.

`checked_profiles` are the bounded profiles that answer part of this view when
Check answers is on (`harness/calculations.py`); the reads above are refused in
a checked turn. `checked_gap` says why the rest is not checked: it is withheld
as "Requested results unavailable", never published as checked. A new view
starts withheld. See `.scratch/checked-results/audit.md`.

`card`, `sub_option` and `card_tab` place a results view in the card
workspaces (`.scratch/project-workflow/card-workspaces.md` §1.1, §3): the card
and sub-option ids are `services/flow_scope`'s, `card_tab` the card's results
tab. `list_result_views(card=…)` lists one card's views (H2a); navigation
does not read them yet. Summary (the landing) and Asset Detail (a drawer from
every card) have none.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping


@dataclass(frozen=True)
class Read:
    tool: str
    args: Mapping = field(default_factory=lambda: MappingProxyType({}))
    # A project-scoped tool (campus_*, gridspine_*) takes project_id. ONE rule for the
    # listed read and the probe: it is the active project, filled in when called.
    project: bool = False
    # The key that is empty (None / []) when the study has not produced this yet.
    empty_key: str | None = None
    # Whether the view's status probe runs this read (it runs the first read of a
    # view with no project-scoped reads).
    probed: bool = True

    def call(self) -> dict:
        args = dict(self.args)
        if self.project:
            args = {"project_id": shown_key(), **args}
        return {"tool": self.tool, "args": args}


def _r(tool: str, **args) -> Read:
    return Read(tool, MappingProxyType(args))


def _rp(tool: str, empty_key: str | None = None, probed: bool = True, **args) -> Read:
    """A read of a project-scoped tool: project_id is the active project."""
    return Read(tool, MappingProxyType(args), project=True, empty_key=empty_key, probed=probed)


@dataclass(frozen=True)
class ResultView:
    view_id: str
    panel: str
    tab: str | None
    label: str
    shows: str
    family: str
    reads: tuple[Read, ...]
    guided_label: str | None = None
    verification: Literal["displayed_data", "mounted_view"] = "mounted_view"
    historical_section: str | None = None
    claim_limits: str = ""
    units: str = ""
    needs: tuple[str, ...] = ()
    multi_period_only: bool = False
    requires_tools: tuple[str, ...] = ()
    checked_profiles: tuple[str, ...] = ()
    checked_gap: str | None = "outside_checked_scope"
    card: str | None = None
    sub_option: str | None = None
    card_tab: str | None = None

    def live(self, tool_names: set[str] | None = None) -> bool:
        """True when every tool this view depends on is in the catalogue."""
        if not self.requires_tools:
            return True
        if tool_names is None:
            from harness.catalogue import TOOLS

            tool_names = {t["name"] for t in TOOLS}
        return set(self.requires_tools) <= tool_names

    def label_for(self, mode: str | None) -> str | None:
        """The tab's label in this UI mode; None when the mode has no such tab."""
        return self.guided_label if mode == "guided" else self.label


_RESULTS = "results"
# flow_scope.CARDS' ids, in its order.
CARDS = ("capacity", "grid", "control", "reliability", "commercial", "handoff")
_PROXY = ("LOPF flows are an active-power proxy, not an AC thermal assessment: "
          "no N-1, no voltage limits.")

RESULT_VIEWS: tuple[ResultView, ...] = (
    ResultView("summary", _RESULTS, "summary", "Summary",
               "headline system cost and generation by carrier", "results",
               (_r("get_results", result_kind="statistics"),
                _r("get_results", result_kind="cost_breakdown")),
               guided_label="Summary",
               units="EUR, MWh",
               checked_profiles=("generator-dispatch/1", "network-linear-cost/1",
                                 "carrier-energy/1")),
    ResultView("overview", _RESULTS, "overview", "Overview",
               "the mix and build per investment period", "results",
               (_r("get_vintage_results"),),
               multi_period_only=True,
               units="MW, MWh, EUR per period",
               checked_gap="unsupported_snapshot_scope",
               card="capacity", sub_option="sizing", card_tab="build"),
    ResultView("capex", _RESULTS, "capex", "Capacity Expansion",
               "sized assets and their capital cost", "results",
               (_r("get_results", result_kind="cost_breakdown"),
                _r("get_results", result_kind="economics_by_carrier")),
               units="MW, MWh, EUR/a",
               checked_profiles=("generator-cost/1", "storage-unit-cost/1", "capacity/1"),
               card="capacity", sub_option="sizing", card_tab="build"),
    ResultView("dispatch", _RESULTS, "dispatch", "Dispatch",
               "hourly generation and storage operation", "results",
               (_r("get_results", result_kind="generators"),
                _r("get_results", result_kind="storage_dispatch")),
               units="MW per snapshot, MWh",
               checked_profiles=("generator-dispatch/1", "storage-unit-dispatch/1",
                                 "carrier-energy/1"),
               card="capacity", sub_option="dispatch", card_tab="operation"),
    # Gated: the copilot read for energy flows lands with the catalogue work
    # (site commercial UX A1); the tab itself is navigable now.
    ResultView("flows", _RESULTS, "flows", "Energy flow",
               "where the site's energy came from, what it passed through and where it went",
               "results", (_r("get_energy_flows"),),
               guided_label="Energy flow",
               requires_tools=("get_energy_flows",),
               claim_limits="Energy carriers only; a flow the server cannot prove to conserve "
                            "energy is unavailable, not zero.",
               checked_profiles=("energy-balance/1",),
               units="MWh",
               card="capacity", sub_option="dispatch", card_tab="operation"),
    # Gated like flows: the copilot read lands with the catalogue work.
    ResultView("loadshape", _RESULTS, "loadshape", "Load shape",
               "how import, export and the site's own generation are shaped by weekday, hour, week, month and year",
               "results", (_r("get_load_shape"),),
               guided_label="Load shape",
               requires_tools=("get_load_shape",),
               checked_profiles=("site-import/1",),
               claim_limits="Energy carriers only; a group the network does not model, or the run "
                            "cannot establish, is named, not shown as zero.",
               units="MW, MWh",
               card="capacity", sub_option="dispatch", card_tab="operation"),
    ResultView("loadflow", _RESULTS, "loadflow", "Load Flow",
               "line and transformer loading against their ratings", "results",
               (_r("get_loadflow_summary"),),
               verification="displayed_data", claim_limits=_PROXY,
               checked_profiles=("branch-loading/1", "network-loading/1"),
               historical_section="loadflow",
               units="%, MW (active-power proxy) or MVA, weighted hours",
               card="grid", sub_option="load_flow", card_tab="load_flow"),
    # R2 (card-workspaces.md §3.2 C): the grid study's results. Both are mounted-view
    # only and outside the checked scope; the campus study and the planning->dynamics
    # study are their own runs, so a view without one says so rather than showing zero.
    ResultView("grid_code", _RESULTS, "grid_code", "Grid code & connection",
               "campus PCC compliance against the chosen grid code; connection capacity and "
               "connection-point assessment of a planning-to-dynamics study", "results",
               (_rp("campus_get_study", "results"), _rp("gridspine_get_capacity", "rows"),
                _rp("gridspine_get_connection_assessments", probed=False)),
               claim_limits="A steady-state screen at the selected hours, not a compliance "
                            "certificate. Campus rows exist only after a campus study run. A "
                            "planning-to-dynamics project's tables come from gridspine_get_capacity "
                            "and gridspine_get_connection_assessments (study projects only).",
               units="MW, MVA, Mvar, kV, %, pu",
               card="grid", sub_option="grid_code", card_tab="grid_code"),
    ResultView("electrical_design", _RESULTS, "electrical_design", "Electrical design",
               "transformer sizing, reactive compensation, short circuit, critical hours and "
               "the campus investment", "results",
               (_rp("campus_get_study", "results"), _rp("campus_get_investment", probed=False),
                _rp("campus_get_milp", probed=False)),
               claim_limits="Proposals from the campus study at its critical hours; nothing is "
                            "adopted into the model until the user applies it.",
               units="MVA, Mvar, kA, EUR",
               card="grid", sub_option="grid_code", card_tab="electrical_design"),
    # R3 (card-workspaces.md §3.3 C): Control's results. The Run does not request the island
    # study yet (island_checks is "not yet available"), so both views show what a campus
    # island study already holds, read-only (O2); they are mounted-view only and outside the
    # checked scope. The load-smoothing screen is the scorecard's after-battery rows
    # (RLR-527), so the island view links to it and reads nothing from the readiness tool.
    ResultView("island", _RESULTS, "island", "Island & ride-through",
               "the island study of a hub project: report rows, pickup sequence, frequency "
               "cuts (the load-smoothing screen stays in the grid-readiness scorecard)", "results",
               (_rp("campus_island_config", "study"), _rp("campus_island_cuts", probed=False)),
               claim_limits="A screening on the hub LP, not a certificate; say which values "
                            "are assumed. Rows exist only after an island study run, and are "
                            "out of date when the inputs changed since.",
               units="h, s, Hz, Hz/s, MW",
               card="control", sub_option="island_checks", card_tab="island"),
    ResultView("grid_forming", _RESULTS, "grid_forming", "Grid-forming",
               "the recommended grid-forming settings per unit, the cases before and after, "
               "and the capacity needed when no setting passes", "results",
               (_rp("campus_island_gfm_settings", "recommendation"),),
               claim_limits="Heuristic screening, never a certificate. When no setting passes, "
                            "capacity is needed; settings do not fix it. Applying goes through "
                            "an edit card, never from the tab alone.",
               units="MVA, %, s, pu",
               card="control", sub_option="presets", card_tab="grid_forming"),
    ResultView("prices", _RESULTS, "prices", "Prices",
               "bus prices and what sets them", "results",
               (_r("get_results", result_kind="prices"),
                _r("get_results", result_kind="price_drivers")),
               units="EUR/MWh", checked_profiles=("bus-price/1",),
               card="capacity", sub_option="dispatch", card_tab="costs_prices"),
    ResultView("economics", _RESULTS, "economics", "Economics",
               "per-asset revenue, cost and LCOE", "results",
               (_r("get_results", result_kind="asset_economics"),),
               units="EUR, EUR/MWh",
               card="capacity", sub_option="dispatch", card_tab="costs_prices"),
    ResultView("emissions", _RESULTS, "emissions", "Emissions",
               "CO2 totals and any cap's shadow price", "results",
               (_r("get_results", result_kind="emissions"),),
               units="t CO2, EUR/t",
               checked_profiles=("generator-emissions/1", "network-emissions/1"),
               card="capacity", sub_option="dispatch", card_tab="operation"),
    ResultView("curtailment", _RESULTS, "curtailment", "Curtailment",
               "renewable energy available but not used", "results",
               (_r("get_results", result_kind="curtailment"),),
               units="MWh, %", checked_profiles=("curtailment/1",),
               card="capacity", sub_option="dispatch", card_tab="curtailment"),
    ResultView("lostload", _RESULTS, "lostload", "Lost load",
               "demand that could not be served", "results",
               (_r("get_results", result_kind="lost_load"),),
               units="MWh, MW", checked_profiles=("lost-load/1",),
               card="capacity", sub_option="dispatch", card_tab="operation"),
    ResultView("adequacy", _RESULTS, "adequacy", "Adequacy",
               "loss-of-load expectation against the reliability target", "adequacy",
               (_r("get_adequacy_results", kind="adequacy"),),
               guided_label="Adequacy",
               checked_profiles=("copt-screening/1",),
               units="h (LOLE), MWh (EUE) over the modelled horizon",
               card="reliability", sub_option="goal_study", card_tab="adequacy"),
    ResultView("storage", _RESULTS, "storage", "Storage cycling",
               "storage cycles and state of charge", "results",
               (_r("get_results", result_kind="storage"),),
               units="cycles, MWh",
               checked_profiles=("storage-unit-dispatch/1", "storage-cycles/1"),
               card="capacity", sub_option="dispatch", card_tab="operation"),
    ResultView("fmea", _RESULTS, "fmea", "FMEA",
               "failure modes ranked by their effect on supply", "adequacy",
               (_r("get_adequacy_results", kind="fmea_modes"),),
               guided_label="FMEA",
               units="MW, h/a",
               card="reliability", sub_option="stress", card_tab="stress_fmea"),
    ResultView("investment", _RESULTS, "investment", "Investment case",
               "the investment case verdict and its cash flows", "investment",
               (_r("get_investment_case"),),
               checked_profiles=("investment-case/1", "investment-lcoe/1"),
               units="EUR, %",
               card="commercial", sub_option="investment_case", card_tab="investment_case"),
    ResultView("asset", _RESULTS, "asset", "Asset Detail",
               "one asset's time series and KPIs", "results",
               (_r("get_asset_results"),), needs=("component_class", "name"),
               units="per metric, as each series returns it",
               checked_profiles=("generator-dispatch/1", "storage-unit-dispatch/1")),
    # Gated: the profile generator's reads (PR377). Live once its tools merge.
    ResultView("profiles_pending", "profiles", None, "Profile studio",
               "generated renewable profiles waiting to be applied", "profiles",
               (_r("list_pending_profiles"),),
               requires_tools=("list_pending_profiles",),
               units="p.u.", checked_gap="synthetic_estimate"),
    ResultView("profile_preview", "profiles", None, "Profile studio",
               "a synthetic daily estimate of a renewable profile", "profiles",
               (_r("preview_renewable_profile"),), needs=("points",),
               claim_limits="A synthetic estimate, not a weather-year profile.",
               requires_tools=("preview_renewable_profile", "get_profile_parameters",
                               "list_profile_presets"),
               units="p.u. (daily means)", checked_gap="synthetic_estimate"),
)

CHECKED_GAPS = ("outside_checked_scope", "unsupported_snapshot_scope", "synthetic_estimate")

_BY_ID = {v.view_id: v for v in RESULT_VIEWS}


def view(view_id: str) -> ResultView:
    return _BY_ID[view_id]


def for_results_tab(tab: str) -> ResultView | None:
    return next((v for v in RESULT_VIEWS if v.panel == _RESULTS and v.tab == tab), None)


def card_views(card: str) -> tuple[ResultView, ...]:
    """One card's live results views, in registry order."""
    return tuple(v for v in RESULT_VIEWS if v.panel == _RESULTS and v.card == card and v.live())


def card_hint(ui_context) -> str | None:
    """The card the browser has open (`ui_context.results_card`), or None.

    A hint, so only an exact card id is taken; anything else is dropped and
    never reaches the model as text.
    """
    raw = ui_context.get("results_card") if isinstance(ui_context, dict) else None
    return raw if isinstance(raw, str) and raw in CARDS else None


# ── list_result_views (LC-11, VS-2) ──────────────────────────────────────────
# The registry projected on the active project: per view, whether its own read
# has data now (status only, never a value), whether this conversation already
# showed it, and the read to call. "Show me something else" and the walk use
# it; every number still comes from the view's read, called next.

DATA_STATUSES = ("available", "no_data", "unavailable", "not_applicable", "needs_entity")


def _probe(view: ResultView) -> tuple[str, str | None]:
    """Run the view's first read and keep only whether it has data."""
    from fastapi import HTTPException

    from services import chat_tools
    from services.pypsa_service import PyPSAService

    if view.needs:
        return "needs_entity", "needs " + ", ".join(view.needs)
    if view.multi_period_only:
        periods = getattr(PyPSAService.get_network(), "investment_periods", None)
        if periods is None or not len(periods):
            return "not_applicable", "single investment period"
    if any(r.project for r in view.reads):
        return _probe_project_reads(view, chat_tools)
    return _probe_read(view.reads[0], chat_tools)


def _probe_read(read: Read, chat_tools) -> tuple[str, str | None]:
    from fastapi import HTTPException

    try:
        out = chat_tools.DISPATCHERS[read.tool](**read.call()["args"])
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return "unavailable", str(detail.get("error_kind") or exc.status_code)[:60]
    except Exception:  # noqa: BLE001 — a failing read is unavailable, never data
        return "unavailable", "read_failed"
    if getattr(out, "status_code", None) == 204:
        return "no_data", None
    if isinstance(out, dict):
        status = out.get("status")
        if status == "no_data":
            return "no_data", None
        if status == "unavailable":
            return "unavailable", str(out.get("unavailable_kind") or "unavailable")[:60]
        if out.get("configured") is False:
            return "no_data", "not_configured"
        if read.empty_key and not out.get(read.empty_key):
            return "no_data", None
    return "available", None


def _probe_project_reads(view: ResultView, chat_tools) -> tuple[str, str | None]:
    """A view fed by a study: the read that fits the project kind answers. A 404 or 409
    (no study yet, or the other kind of project) is no data, not a failure."""
    best: tuple[str, str | None] = ("no_data", None)
    for read in (r for r in view.reads if r.probed):
        status, reason = _probe_read(read, chat_tools)
        if status == "available":
            return status, reason
        if status == "unavailable" and reason not in ("404", "409"):
            best = (status, reason)
    return best


def shown_key() -> str:
    from services.reference_resolution import active_project_key

    return active_project_key()


def note_shown(session, results_tab: str | None) -> None:
    """Remember that this conversation showed a results tab in this project."""
    if session is None or not isinstance(results_tab, str) or for_results_tab(results_tab) is None:
        return
    shown = session.shown_results.setdefault(shown_key(), [])
    if results_tab not in shown:
        shown.append(results_tab)


# Below harness/results.py's 4000-character tool-result cap, so the listing degrades by
# its own rule (compact rows) and never by truncation.
LISTING_BUDGET = 3600


def list_result_views(exclude_shown: bool = False, card: str | None = None) -> dict:
    from fastapi import HTTPException

    from services import chat_tools

    if not isinstance(exclude_shown, bool):
        raise HTTPException(422, {"error_kind": "invalid_result_views",
                                  "message": "exclude_shown must be true or false."})
    if card is not None and card not in CARDS:
        raise HTTPException(422, {"error_kind": "invalid_result_views",
                                  "message": "card must be one of: " + ", ".join(CARDS) + "."})
    session = chat_tools.chat_session()
    hints = getattr(session, "turn_ui_context", None) or {}
    mode = "guided" if hints.get("ui_mode") == "guided" else "expert"
    # The tab the user has on screen counts as shown: a section they opened
    # themselves ends the walk there. A hint, so only a registry tab is taken.
    on_screen = hints.get("results_tab") if hints.get("panel") == "results" else None
    on_screen = on_screen if isinstance(on_screen, str) and for_results_tab(on_screen) else None
    if on_screen:
        note_shown(session, on_screen)
    shown = list((getattr(session, "shown_results", None) or {}).get(shown_key(), []))
    views = (list(card_views(card)) if card is not None
             else [v for v in RESULT_VIEWS if v.panel == _RESULTS and v.live()])
    if mode == "guided":
        views.sort(key=lambda v: v.guided_label is None)
    out = []
    for v in views:
        if exclude_shown and v.tab in shown:
            continue
        data, reason = _probe(v)
        # A results view's id is its tab id (ui_open_panel results_tab).
        row = {"id": v.view_id, "label": v.label_for(mode) or f"{v.label} (advanced)",
               "data": data, "shown": v.tab in shown}
        if v.card is not None:
            row["card"] = v.card
        # The reads and the one-line summary come with `card=`, so the plain listing keeps
        # inside the tool-result cap (harness/results.py, 4000 chars) as views are added.
        # A view that belongs to no card (Summary, Asset Detail) carries them itself.
        if card is not None or v.card is None:
            row["read"] = [r.call() for r in v.reads]
            if data == "available" and v.tab not in shown:
                row["shows"] = v.shows
        if v.verification == "displayed_data":
            row["verification"] = v.verification
        if reason:
            row["reason"] = reason
        out.append(row)
    listing = {"status": "ok", "mode": mode, "shown": shown, "on_screen": on_screen, "views": out,
               "note": ("Statuses only, no values: call a view's read before you open its tab, "
                        "and quote numbers from that read." if card is not None else
                        "Statuses only, no values. Call list_result_views(card=…) for a card's "
                        "reads, then call a view's read before you open its tab and quote "
                        "numbers from that read.")}
    if card is None and len(json.dumps(listing)) > LISTING_BUDGET:
        # Degrade explicitly rather than let the tool-result cap cut the listing.
        listing["views"] = [{"id": r["id"], "data": r["data"], **({"card": r["card"]} if "card" in r else {})}
                            for r in out]
        listing["compact"] = True
    if card is not None:
        listing["card"] = card
    return listing
