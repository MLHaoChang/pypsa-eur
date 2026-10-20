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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping


@dataclass(frozen=True)
class Read:
    tool: str
    args: Mapping = field(default_factory=lambda: MappingProxyType({}))

    def call(self) -> dict:
        return {"tool": self.tool, "args": dict(self.args)}


def _r(tool: str, **args) -> Read:
    return Read(tool, MappingProxyType(args))


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
_PROXY = ("LOPF flows are an active-power proxy, not an AC thermal assessment: "
          "no N-1, no voltage limits.")

RESULT_VIEWS: tuple[ResultView, ...] = (
    ResultView("summary", _RESULTS, "summary", "Summary",
               "headline system cost and generation by carrier", "results",
               (_r("get_results", result_kind="statistics"),
                _r("get_results", result_kind="cost_breakdown")),
               guided_label="Summary",
               units="EUR, MWh"),
    ResultView("overview", _RESULTS, "overview", "Overview",
               "the mix and build per investment period", "results",
               (_r("get_vintage_results"),),
               multi_period_only=True,
               units="MW, MWh, EUR per period"),
    ResultView("capex", _RESULTS, "capex", "Capacity Expansion",
               "sized assets and their capital cost", "results",
               (_r("get_results", result_kind="cost_breakdown"),
                _r("get_results", result_kind="economics_by_carrier")),
               units="MW, MWh, EUR/a"),
    ResultView("dispatch", _RESULTS, "dispatch", "Dispatch",
               "hourly generation and storage operation", "results",
               (_r("get_results", result_kind="generators"),
                _r("get_results", result_kind="storage_dispatch")),
               units="MW per snapshot, MWh"),
    # Gated: the copilot read for energy flows lands with the catalogue work
    # (site commercial UX A1); the tab itself is navigable now.
    ResultView("flows", _RESULTS, "flows", "Energy flow",
               "where the site's energy came from, what it passed through and where it went",
               "results", (_r("get_energy_flows"),),
               guided_label="Energy flow",
               requires_tools=("get_energy_flows",),
               claim_limits="Energy carriers only; a flow the server cannot prove to conserve "
                            "energy is unavailable, not zero.",
               units="MWh"),
    # Gated like flows: the copilot read lands with the catalogue work.
    ResultView("loadshape", _RESULTS, "loadshape", "Load shape",
               "how import, export and the site's own generation are shaped by weekday, hour, week, month and year",
               "results", (_r("get_load_shape"),),
               guided_label="Load shape",
               requires_tools=("get_load_shape",),
               claim_limits="Energy carriers only; a group the network does not model, or the run "
                            "cannot establish, is named, not shown as zero.",
               units="MW, MWh"),
    ResultView("loadflow", _RESULTS, "loadflow", "Load Flow",
               "line and transformer loading against their ratings", "results",
               (_r("get_loadflow_summary"),),
               verification="displayed_data", claim_limits=_PROXY,
               historical_section="loadflow",
               units="%, MW (active-power proxy) or MVA, weighted hours"),
    ResultView("prices", _RESULTS, "prices", "Prices",
               "bus prices and what sets them", "results",
               (_r("get_results", result_kind="prices"),
                _r("get_results", result_kind="price_drivers")),
               units="EUR/MWh"),
    ResultView("economics", _RESULTS, "economics", "Economics",
               "per-asset revenue, cost and LCOE", "results",
               (_r("get_results", result_kind="asset_economics"),),
               units="EUR, EUR/MWh"),
    ResultView("emissions", _RESULTS, "emissions", "Emissions",
               "CO2 totals and any cap's shadow price", "results",
               (_r("get_results", result_kind="emissions"),),
               units="t CO2, EUR/t"),
    ResultView("curtailment", _RESULTS, "curtailment", "Curtailment",
               "renewable energy available but not used", "results",
               (_r("get_results", result_kind="curtailment"),),
               units="MWh, %"),
    ResultView("lostload", _RESULTS, "lostload", "Lost load",
               "demand that could not be served", "results",
               (_r("get_results", result_kind="lost_load"),),
               units="MWh, MW"),
    ResultView("adequacy", _RESULTS, "adequacy", "Adequacy",
               "loss-of-load expectation against the reliability target", "adequacy",
               (_r("get_adequacy_results", kind="adequacy"),),
               guided_label="Adequacy",
               units="h/a (LOLE), MWh/a (EENS)"),
    ResultView("storage", _RESULTS, "storage", "Storage cycling",
               "storage cycles and state of charge", "results",
               (_r("get_results", result_kind="storage"),),
               units="cycles, MWh"),
    ResultView("fmea", _RESULTS, "fmea", "FMEA",
               "failure modes ranked by their effect on supply", "adequacy",
               (_r("get_adequacy_results", kind="fmea_modes"),),
               guided_label="FMEA",
               units="MW, h/a"),
    ResultView("investment", _RESULTS, "investment", "Investment case",
               "the investment case verdict and its cash flows", "investment",
               (_r("get_investment_case"),),
               units="EUR, %"),
    ResultView("asset", _RESULTS, "asset", "Asset Detail",
               "one asset's time series and KPIs", "results",
               (_r("get_asset_results"),), needs=("component_class", "name"),
               units="per metric, as each series returns it"),
    # Gated: the profile generator's reads (PR377). Live once its tools merge.
    ResultView("profiles_pending", "profiles", None, "Profile studio",
               "generated renewable profiles waiting to be applied", "profiles",
               (_r("list_pending_profiles"),),
               requires_tools=("list_pending_profiles",),
               units="p.u."),
    ResultView("profile_preview", "profiles", None, "Profile studio",
               "a synthetic daily estimate of a renewable profile", "profiles",
               (_r("preview_renewable_profile"),), needs=("points",),
               claim_limits="A synthetic estimate, not a weather-year profile.",
               requires_tools=("preview_renewable_profile", "get_profile_parameters",
                               "list_profile_presets"),
               units="p.u. (daily means)"),
)

_BY_ID = {v.view_id: v for v in RESULT_VIEWS}


def view(view_id: str) -> ResultView:
    return _BY_ID[view_id]


def for_results_tab(tab: str) -> ResultView | None:
    return next((v for v in RESULT_VIEWS if v.panel == _RESULTS and v.tab == tab), None)


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
    read = view.reads[0]
    try:
        out = chat_tools.DISPATCHERS[read.tool](**dict(read.args))
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
    return "available", None


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


def list_result_views(exclude_shown: bool = False) -> dict:
    from fastapi import HTTPException

    from services import chat_tools

    if not isinstance(exclude_shown, bool):
        raise HTTPException(422, {"error_kind": "invalid_result_views",
                                  "message": "exclude_shown must be true or false."})
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
    views = [v for v in RESULT_VIEWS if v.panel == _RESULTS and v.live()]
    if mode == "guided":
        views.sort(key=lambda v: v.guided_label is None)
    out = []
    for v in views:
        if exclude_shown and v.tab in shown:
            continue
        data, reason = _probe(v)
        # A results view's id is its tab id (ui_open_panel results_tab).
        row = {"id": v.view_id, "label": v.label_for(mode) or f"{v.label} (advanced)",
               "data": data, "shown": v.tab in shown,
               "read": [r.call() for r in v.reads]}
        if v.verification == "displayed_data":
            row["verification"] = v.verification
        if reason:
            row["reason"] = reason
        if data == "available" and v.tab not in shown:
            row["shows"] = v.shows
        out.append(row)
    return {"status": "ok", "mode": mode, "shown": shown, "on_screen": on_screen, "views": out,
            "note": ("Statuses only, no values: call a view's read before you open its tab, "
                     "and quote numbers from that read.")}
