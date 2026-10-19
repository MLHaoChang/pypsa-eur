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
               guided_label="Summary"),
    ResultView("overview", _RESULTS, "overview", "Overview",
               "the mix and build per investment period", "results",
               (_r("get_vintage_results"),),
               multi_period_only=True),
    ResultView("capex", _RESULTS, "capex", "Capacity Expansion",
               "sized assets and their capital cost", "results",
               (_r("get_results", result_kind="cost_breakdown"),
                _r("get_results", result_kind="economics_by_carrier"))),
    ResultView("dispatch", _RESULTS, "dispatch", "Dispatch",
               "hourly generation and storage operation", "results",
               (_r("get_results", result_kind="generators"),
                _r("get_results", result_kind="storage_dispatch"))),
    ResultView("loadflow", _RESULTS, "loadflow", "Load Flow",
               "line and transformer loading against their ratings", "results",
               (_r("get_loadflow_summary"),),
               verification="displayed_data", claim_limits=_PROXY),
    ResultView("prices", _RESULTS, "prices", "Prices",
               "bus prices and what sets them", "results",
               (_r("get_results", result_kind="prices"),
                _r("get_results", result_kind="price_drivers"))),
    ResultView("economics", _RESULTS, "economics", "Economics",
               "per-asset revenue, cost and LCOE", "results",
               (_r("get_results", result_kind="asset_economics"),)),
    ResultView("emissions", _RESULTS, "emissions", "Emissions",
               "CO2 totals and any cap's shadow price", "results",
               (_r("get_results", result_kind="emissions"),)),
    ResultView("curtailment", _RESULTS, "curtailment", "Curtailment",
               "renewable energy available but not used", "results",
               (_r("get_results", result_kind="curtailment"),)),
    ResultView("lostload", _RESULTS, "lostload", "Lost load",
               "demand that could not be served", "results",
               (_r("get_results", result_kind="lost_load"),)),
    ResultView("adequacy", _RESULTS, "adequacy", "Adequacy",
               "loss-of-load expectation against the reliability target", "adequacy",
               (_r("get_adequacy_results", kind="adequacy"),),
               guided_label="Adequacy"),
    ResultView("storage", _RESULTS, "storage", "Storage cycling",
               "storage cycles and state of charge", "results",
               (_r("get_results", result_kind="storage"),)),
    ResultView("fmea", _RESULTS, "fmea", "FMEA",
               "failure modes ranked by their effect on supply", "adequacy",
               (_r("get_adequacy_results", kind="fmea_modes"),),
               guided_label="FMEA"),
    ResultView("investment", _RESULTS, "investment", "Investment case",
               "the investment case verdict and its cash flows", "investment",
               (_r("get_investment_case"),)),
    ResultView("asset", _RESULTS, "asset", "Asset Detail",
               "one asset's time series and KPIs", "results",
               (_r("get_asset_results"),), needs=("component_class", "name")),
    # Gated: the profile generator's reads (PR377). Live once its tools merge.
    ResultView("profiles_pending", "profiles", None, "Profile studio",
               "generated renewable profiles waiting to be applied", "profiles",
               (_r("list_pending_profiles"),),
               requires_tools=("list_pending_profiles",)),
    ResultView("profile_preview", "profiles", None, "Profile studio",
               "a synthetic daily estimate of a renewable profile", "profiles",
               (_r("preview_renewable_profile"),), needs=("points",),
               claim_limits="A synthetic estimate, not a weather-year profile.",
               requires_tools=("preview_renewable_profile", "get_profile_parameters",
                               "list_profile_presets")),
)

_BY_ID = {v.view_id: v for v in RESULT_VIEWS}


def view(view_id: str) -> ResultView:
    return _BY_ID[view_id]


def for_results_tab(tab: str) -> ResultView | None:
    return next((v for v in RESULT_VIEWS if v.panel == _RESULTS and v.tab == tab), None)
