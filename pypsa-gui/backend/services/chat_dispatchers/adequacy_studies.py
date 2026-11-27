"""
Adequacy studies: the feature guide, the Energy Hub review and setup, the
six reliability study starters and their abort, the campaign budget and the
study report's dispatchers.

Moved out of `services/chat_tools.py` (RETRO-FRAG-1), function bodies
unchanged. Schemas and routes: `harness/catalogue_tools/adequacy_studies.py`.

The chat_tools helpers and tables stay in chat_tools and are reached
through the proxies and views below, at call time: chat_tools imports this
module part-way through its own import, and tests that patch
`chat_tools.<helper>` still apply.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from fastapi import HTTPException

from services.pypsa_service import PyPSAService


def _ct():
    from services import chat_tools

    return chat_tools


def _campaign_gated(*args, **kwargs):
    return _ct()._campaign_gated(*args, **kwargs)


def _resolve_adequacy_handler(*args, **kwargs):
    return _ct()._resolve_adequacy_handler(*args, **kwargs)


def get_adequacy_results(*args, **kwargs):
    return _ct().get_adequacy_results(*args, **kwargs)


def get_asset_health(*args, **kwargs):
    return _ct().get_asset_health(*args, **kwargs)


class _CtTable(Mapping):
    """A read-only view of a chat_tools dict, looked up on every access, so a
    patched or rebound ``chat_tools.<name>`` is what the moved bodies see."""

    def __init__(self, name: str):
        self.name = name

    def _table(self) -> Mapping:
        return getattr(_ct(), self.name)

    def __getitem__(self, key):
        return self._table()[key]

    def __iter__(self) -> Iterator:
        return iter(self._table())

    def __len__(self) -> int:
        return len(self._table())

    def __contains__(self, key) -> bool:
        return key in self._table()


_ADEQUACY_NO_DATA_HINTS = _CtTable("_ADEQUACY_NO_DATA_HINTS")
_ADEQUACY_ABORT_HANDLER_NAMES = _CtTable("_ADEQUACY_ABORT_HANDLER_NAMES")


def get_feature_guide(tour: str | None = None, field: str | None = None) -> dict:
    """The in-app guide (P21) the GUI's tours and hover tips show — the same
    wording, so explanations match the screen. ``tour`` returns one tour's
    steps; ``field`` one field's help; neither returns the index."""
    from services.guides import load_guide
    guide = load_guide("eh_fmea")
    if field is not None:
        text = guide["fields"].get(field)
        if text is None:
            raise HTTPException(
                404, f"no guide entry for field {field!r}; known: "
                f"{sorted(guide['fields'])}")
        return {"field": field, "help": text}
    if tour is not None:
        t = guide["tours"].get(tour)
        if t is None:
            raise HTTPException(
                404, f"no tour {tour!r}; known: {sorted(guide['tours'])}")
        return {"tour": tour, **t}
    return {"tours": {k: {"title": v["title"], "intro": v.get("intro"),
                          "steps": len(v["steps"])}
                      for k, v in guide["tours"].items()},
            "fields": sorted(guide["fields"])}


def review_eh_study() -> dict:
    """Analyse the latest Energy Hub study (P22): findings with evidence,
    recommendations and exact tool actions the user may choose to apply.
    One source with ``GET /api/results/eh_review`` (P24):
    ``eh_review.review_latest``."""
    from routers import results as R
    from services.adequacy.eh_review import review_latest

    record = R.get_eh_study()
    return review_latest(
        R._state, record if isinstance(record, dict) else None,
        no_data_message=_ADEQUACY_NO_DATA_HINTS["eh_reference_design"])


def suggest_eh_setup(archetype: str | None = None) -> dict:
    """Suggested Energy Hub tags for the live network (P25): the grid import
    Link, the point-of-connection bus, critical buses, and units without
    outage data — each with a reason and a ready update_component /
    bulk_update_components action. READ: nothing is applied; the write tools
    confirm whatever the user picks."""
    from services.adequacy.eh_setup import suggest_eh_setup as _suggest
    return _suggest(PyPSAService.get_network(), archetype=archetype)


def start_campaign(objective: str, budget_solves: int | None = None) -> dict:
    """Open a reliability campaign with one budget across every study."""
    from services.adequacy import campaign
    return campaign.start(objective, budget_solves)


def campaign_status() -> dict:
    """The running campaign's objective, budget, spend and study log."""
    from services.adequacy import campaign
    return campaign.status()


def end_campaign(note: str | None = None) -> dict:
    """Close the campaign and return its final record."""
    from services.adequacy import campaign
    return campaign.end(note)


def run_fmea_sweep(scenarios: list | None = None) -> dict:
    """
    Start the class-B (single link outage) contingency sweep, plus any
    class-C scenarios passed in.

    `scenarios` are re-validated by the route. They come from
    `get_stress_scenarios`, which is where authorization lives — this route
    operates on the FOREGROUND network and carries no project name.
    """
    from routers.results import FmeaSweepRequest, post_fmea_sweep as _h
    rows = list(scenarios or [])
    return _campaign_gated(
        "fmea_sweep", lambda: _h(FmeaSweepRequest(scenarios=rows)),
        scenarios=rows)


def run_frontier_study(targets_permyriad: list | None = None) -> dict:
    """
    Start the ε-constraint cost-vs-availability sweep: ONE full
    capacity-expansion solve per target, so the plan is re-optimised at every
    point. Omitting `targets_permyriad` uses the engine's default spread.
    """
    from routers.results import FrontierRequest, post_frontier as _h
    return _campaign_gated(
        "frontier",
        lambda: _h(FrontierRequest(targets_permyriad=targets_permyriad)),
        targets_permyriad=targets_permyriad)


def run_mc_study(
    draws: int | None = None,
    seed: int | None = None,
    cov_target: float | None = None,
    elcc_assets: list | None = None,
    elcc_portfolio: bool | None = None,
) -> dict:
    """
    Start the sequential Monte-Carlo adequacy study (LOLE / EUE, optionally
    with an ELCC table).

    Alone among the four studies this SOLVES NOTHING and never mutates the
    network — its metrics are hours and MWh, not euros, so it needs no VOLL.
    It is still mutually exclusive with the others: the snapshot it samples
    must not be a half-mutated network.
    """
    from routers.results import McRequest, post_mc as _h
    return _campaign_gated("mc", lambda: _h(McRequest(
        draws=draws,
        seed=seed,
        cov_target=cov_target,
        elcc_assets=elcc_assets,
        elcc_portfolio=elcc_portfolio,
    )))


def run_coupling_loop(
    target_lole_h: float | None = None,
    draws: int | None = None,
    seed: int | None = None,
    eps0: float | None = None,
    max_solves: int | None = None,
    restore: str | None = None,
) -> dict:
    """
    Drive the ENS CAP (ε) until the sampled plan meets `target_lole_h`:
    solve at ε, measure LOLE by Monte Carlo, adjust, repeat.

    `target_lole_h` is HORIZON-basis hours, not h/yr — convert before calling
    on a multi-year horizon, and say which basis you used when reporting.
    Omitted, it and `draws` are the project's reliability goal (W3b-R); the
    result's `goal_sources` names where each came from.
    """
    from routers.results import CouplingLoopRequest, post_coupling_loop as _h
    return _campaign_gated(
        "coupling_loop",
        lambda: _h(CouplingLoopRequest(
            target_lole_h=target_lole_h,
            draws=draws,
            seed=seed,
            eps0=eps0,
            max_solves=max_solves,
            restore=restore,
        )),
        max_solves=max_solves)


def run_margin_loop(
    target_lole_h: float | None = None,
    draws: int | None = None,
    seed: int | None = None,
    max_solves: int | None = None,
    restore: str | None = None,
) -> dict:
    """
    Drive the PLANNING RESERVE MARGIN until the sampled plan meets
    `target_lole_h` — the firm-capacity lever, where run_coupling_loop turns
    the energy lever.

    There is deliberately no starting-margin parameter: the start is a
    MEASUREMENT taken by a probing solve, and a user-supplied one is the
    single number here that can silently make the study worthless.
    An omitted `target_lole_h` or `draws` is the project's reliability goal
    (W3b-R), named in the result's `goal_sources`.
    """
    from routers.results import MarginLoopRequest, post_margin_loop as _h
    return _campaign_gated(
        "margin_loop",
        lambda: _h(MarginLoopRequest(
            target_lole_h=target_lole_h,
            draws=draws,
            seed=seed,
            max_solves=max_solves,
            restore=restore,
        )),
        max_solves=max_solves)


def run_eh_study(
    archetype: str | None = None,
    stages: list | None = None,
    budget_solves: int | None = None,
    pack_overrides: dict | None = None,
    dtc_config: dict | None = None,
    dsr_buses: list | None = None,
    mc: dict | None = None,
    dtc_attribution: str | None = None,
) -> dict:
    """
    Start the Energy Hub reference-design study for one archetype pack.

    Applies the pack, runs the EH pipeline, and persists a
    ``ReferenceDesignReport`` for ``get_adequacy_results('eh_reference_design')``.
    Poll ``get_adequacy_results('eh_study')`` while it runs.
    An omitted ``archetype``, ``pack_overrides`` LOLE / ENS target or
    ``mc.draws`` is the project's reliability goal (W3b-R); a value passed
    here wins for this run, and ``goal_sources`` names each one's source.
    """
    from routers.results import EhStudyRequest, post_eh_study as _h
    return _campaign_gated(
        "eh_study",
        lambda: _h(EhStudyRequest(
            archetype=archetype,
            stages=stages,
            budget_solves=budget_solves,
            pack_overrides=pack_overrides,
            dtc_config=dtc_config,
            dtc_attribution=dtc_attribution,
            dsr_buses=dsr_buses,
            mc=mc,
        )),
        budget_solves=budget_solves)


def abort_adequacy_study(study: str) -> dict:
    """
    Ask a running study to stop at its next boundary.

    IDEMPOTENT, and 200 even when the run has already finished. The closing
    base restore still runs, so an abort costs the work in flight plus that
    restore — it does NOT leave the network mid-contingency. 404 only when
    the named study has never run in this session.
    """
    handler = _resolve_adequacy_handler(study, _ADEQUACY_ABORT_HANDLER_NAMES)
    return handler()


def build_study_report(project: str | None = None) -> dict:
    """
    Assemble the client-facing reliability write-up from everything this
    session established — and everything it did not.

    `project` names the asset-health ledger to fold in. Omitted, or naming a
    project that is not in the foreground, means the provenance gap is simply
    not reported rather than reported against somebody else's network — the
    same guard `get_asset_health` applies, for the same reason.
    """
    from services.adequacy import campaign as _campaign
    from services.adequacy.asset_health import provenance_report
    from services.adequacy.study_report import build_study_report as _build

    n = PyPSAService.get_network()

    health = None
    if project and PyPSAService.get_loaded_project() == project:
        ledger = get_asset_health(project)
        health = ledger.get("provenance")
    elif project is None and PyPSAService.get_loaded_project():
        # No project named, but one IS bound: use it. A report that silently
        # skipped the provenance gap because the caller omitted an argument
        # would be missing the finding that undermines every number in it.
        active = PyPSAService.get_loaded_project()
        try:
            ledger = get_asset_health(active)
            health = provenance_report(n, ledger.get("entries", []))
        except Exception:  # noqa: BLE001 — no ledger is not a report failure
            health = None

    status = _campaign.status()
    return _build(n, get_adequacy_results,
                  campaign=status if status.get("active") else None,
                  health=health)


DISPATCHERS = {
    "get_feature_guide": get_feature_guide,
    "review_eh_study": review_eh_study,
    "suggest_eh_setup": suggest_eh_setup,
    "run_fmea_sweep": run_fmea_sweep,
    "run_frontier_study": run_frontier_study,
    "run_mc_study": run_mc_study,
    "run_coupling_loop": run_coupling_loop,
    "run_margin_loop": run_margin_loop,
    "run_eh_study": run_eh_study,
    "abort_adequacy_study": abort_adequacy_study,
    # campaign (3) — one budget across a chain of studies
    "start_campaign": start_campaign,
    "campaign_status": campaign_status,
    "end_campaign": end_campaign,
    # study report (1) — the write-up, and what it does not establish
    "build_study_report": build_study_report,
}

# Routeless writes the lock seam gates (moved from chat_tools' hand list):
# they mutate the campaign record in the SHARED resident context's
# solver_state, so a non-holder could set a solve budget on the holder's
# context or close the campaign they are running.
LOCK_GATED = frozenset({"start_campaign", "end_campaign"})
