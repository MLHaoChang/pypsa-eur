"""Sequential-MC study HTTP runner — validation, snapshot, worker, publish.

Lifted from ``routers/results.py`` (MC study router lift). The route handler
keeps mesh refusal and the FastAPI decorator; this module owns the
synchronous 422/404 set through ``publish_study``. State is injected so this
module never imports ``routers.*``.
"""
from __future__ import annotations

import contextvars as _contextvars
import logging
import threading as _threading

from fastapi import HTTPException
from pydantic import BaseModel as _BaseModel

from models.schemas import Finite as _Finite
from services.pypsa_service import PyPSAService

logger = logging.getLogger("pypsa_gui.results")


class McElccAsset(_BaseModel):
    # ``kind`` is a plain str rather than a Literal: the authoritative kind
    # list lives in services/adequacy/elcc.py, and duplicating it in a pydantic
    # Literal here would fork it — the day a fourth kind lands, the route would
    # reject it with a schema error that names no asset. An unknown kind still
    # ends up a 422, raised by the resolver that owns the list.
    kind: str
    name: str


class McRequest(_BaseModel):
    # All optional: the bare POST is the useful default (a headline LOLE/EUE
    # with no ELCC study), and every field below has an engine-side default
    # that this route must not fork.
    draws: int | None = None
    seed: int | None = None
    cov_target: _Finite | None = None
    elcc_assets: list[McElccAsset] | None = None
    # Phase 12c: price the whole profile-bearing fleet as one portfolio, per
    # period, beside the reserve margin's own credit for the same group. A
    # boolean, not a pseudo-asset: the row must never land in `elcc` (a
    # consumer summing that list would double-count), and the population is
    # the engines' to derive, not the caller's to name.
    elcc_portfolio: bool | None = None


def start_mc(
    body: McRequest | None,
    *,
    solver_state: dict,
    publish_study,
):
    """
    Start a sequential-MC adequacy study — optionally with an ELCC table —
    in a worker thread (spec §4).

    ASYNCHRONOUS BY CONSTRUCTION, not as an optimisation: a ten-asset ELCC
    run is a baseline plus ~10 bisected MC evaluations per asset, i.e. minutes
    of arithmetic. Running it inline would hold a request open long past every
    proxy and browser timeout, and would block the event loop for the whole
    process while doing it.

    Unlike the frontier and the class-B/C sweep this engine SOLVES NOTHING and
    never mutates the network, so it does NOT require a VoLL (spec §4): its
    metrics are hours and MWh, not euros. It is still in the mutual-exclusion
    mesh — the snapshot it takes must not be a half-mutated network, and the
    sweep/frontier re-solve the one it is reading.

    Validation is deliberately SYNCHRONOUS wherever it is cheap: an empty
    fleet, an inconsistent (q, MTTR) pair and an unknown ELCC asset are all
    knowable from the snapshot alone, and a user who typed a wrong asset name
    must learn that now rather than after seven minutes of spinner. The
    in-thread KeyError/ValueError mapping stays as belt-and-braces for the
    cases only the run can discover.
    """
    import time

    from services.adequacy.elcc import (
        MAX_ELCC_ASSETS,
        elcc_for_asset,
    )
    # Private on purpose: it is the ONE place asset-kind resolution lives, and
    # re-implementing the name lookup here to keep the import public would fork
    # the very mapping (kind → removal semantics) the 404/422 split depends on.
    from services.adequacy.elcc import _resolve as _resolve_elcc_asset
    from services.adequacy.mc import (
        MAX_DRAWS,
        MC_WARNING_V1,
        mc_adequacy,
        snapshot_inputs,
        transition_probs,
    )

    draws = getattr(body, "draws", None)
    draws = 500 if draws is None else int(draws)
    if draws < 1:
        raise HTTPException(422, "draws must be a positive number of samples")
    if draws > MAX_DRAWS:
        # A product cap, not a numerical one: the benchmark harness runs far
        # deeper budgets by calling the engine directly (spec §7).
        raise HTTPException(
            422,
            f"draws={draws} exceeds the engine cap of {MAX_DRAWS} draws per "
            "study — the adaptive batching stops at that budget anyway")
    seed = getattr(body, "seed", None)
    seed = 0 if seed is None else int(seed)
    cov_target = getattr(body, "cov_target", None)
    cov_target = 0.05 if cov_target is None else float(cov_target)

    assets = [(a.kind, a.name) for a in (getattr(body, "elcc_assets", None) or [])]
    want_portfolio = bool(getattr(body, "elcc_portfolio", None) or False)
    if len(assets) > MAX_ELCC_ASSETS:
        raise HTTPException(
            422,
            f"{len(assets)} ELCC assets requested; the cap is "
            f"{MAX_ELCC_ASSETS} (each asset costs a baseline plus ~10 full "
            "MC evaluations)")

    # The ONE snapshot, taken under the mutation lock (spec §1). Everything
    # after this line — validation and the worker alike — reads plain arrays,
    # so the network is free the moment the lock is released.
    from services.adequacy.activity import activity_summary as _activity_summary

    n = PyPSAService.get_network()
    population = None
    snapshot_fp = None
    margin_payload = None
    with PyPSAService.get_lock():
        vre_names = [nm for kind, nm in assets if kind == "vre"]
        if want_portfolio:
            # The portfolio's must-take half needs its profiles PRESERVED in
            # the snapshot (`snapshot_inputs` keeps only the names it is
            # asked for): every must-take whose column is informative.
            from services.adequacy.copt import (
                must_take_generators,
                series_is_informative,
            )
            pmp = getattr(getattr(n, "generators_t", None), "p_max_pu", None)
            for nm in must_take_generators(n):
                if (nm not in vre_names and pmp is not None
                        and nm in getattr(pmp, "columns", [])
                        and series_is_informative(pmp[nm])):
                    vre_names.append(nm)
        try:
            inputs = snapshot_inputs(
                n, vre_assets=vre_names, cfg=solver_state.get("solver_config"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Phase 12d: computed HERE, from the network, under the lock — the
        # worker never touches `n` (plan A9), and the must-take half of the
        # disclosure is not in the fleet (shipped-code review, finding 1).
        activity_block = _activity_summary(n, inputs.periods)
        if want_portfolio:
            # Everything the worker needs from the NETWORK and from request-
            # scoped state is captured here (plan 12c v3.1 A9): the
            # population with the engines' capacity rule, the fingerprint the
            # margin payload is checked against, and that payload itself —
            # the worker never touches `solver_state` or `n`.
            import copy as _copy

            from services.adequacy.portfolio import (
                network_fingerprint,
                portfolio_population,
            )
            population = portfolio_population(n, inputs)
            snapshot_fp = network_fingerprint(n)
            margin_payload = _copy.deepcopy(solver_state.get("last_reserve_margin"))

    if not inputs.units:
        raise HTTPException(
            422,
            "nothing to sample: no electrical generator carries resolvable "
            "occurrence data (unavailability + MTTR), so the sampled fleet is "
            "empty — an empty fleet would report the entire horizon as loss of "
            "load, which is a statement about missing input data, not about "
            "the system")

    # §2.2's inconsistent-pair rejection, pulled forward: it is a property of
    # the (q, MTTR) pair alone, so there is no reason to discover it a batch
    # into a background run and report it as a failed study.
    for u in inputs.units:
        try:
            transition_probs(u.q, u.mttr_hours, name=u.name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    for kind, name in assets:
        try:
            _resolve_elcc_asset(inputs, kind, name)
        except KeyError as exc:
            msg = str(exc.args[0]) if exc.args else str(exc)
            raise HTTPException(
                404, f"unknown ELCC asset {name!r} (kind {kind!r}): {msg}"
            ) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    # The record is closed over by the worker rather than reached through
    # `solver_state` inside it: `solver_state` resolves the *request-scoped*
    # project context, and a worker thread has no request context — it would
    # resolve a different dict and write its result where no reader looks.
    stop_event = _threading.Event()
    record: dict = {"status": "running", "result": None, "error": None,
                    "started_at": time.time(), "thread": None,
                    "stop_event": stop_event}

    def worker():
        # Phase 12h: the rule that decides which profiled units had no
        # outages sampled. Imported from the COPT so `/mc`'s lists and
        # `split_fleet`'s buckets cannot disagree about the same fleet.
        from services.adequacy.copt import is_flag_deterministic as _is_flag_deterministic, rate_is_zero as _rate_is_zero
        try:
            # The ONLY call in the codebase that may carry the flag: this
            # is the study's own baseline, not a replay of one. Every ELCC
            # and loop call passes `stop_event=None` (see `mc_adequacy`).
            metrics = mc_adequacy(inputs, draws=draws, seed=seed,
                                  cov_target=cov_target, stop_event=stop_event)
            # Phase 12c: the headline metrics ARE the baseline every ELCC
            # row needs, argument for argument; injected with a content key
            # the callee recomputes (the N+1 baseline, closed with CRN kept).
            from services.adequacy.elcc import baseline_key as _baseline_key
            from services.adequacy.mc import MAX_DRAWS as _MAX_DRAWS
            key = _baseline_key(inputs, draws=draws, seed=seed,
                                cov_target=cov_target, max_draws=_MAX_DRAWS,
                                batch=250)
            rows = []
            for kind, name in assets:
                # Between assets: a stopped study keeps the rows it priced and
                # never starts another. The bisection inside each asset is
                # checked too, so the worst case is one probe, not one asset.
                if stop_event.is_set():
                    break
                try:
                    rows.append(elcc_for_asset(
                        inputs, kind, name, seed=seed, draws=draws,
                        cov_target=cov_target, baseline=metrics,
                        baseline_key=key, stop_event=stop_event))
                except KeyError as exc:
                    # Belt-and-braces for the 404 the POST already raised
                    # synchronously: the only way to reach this is a name that
                    # resolved at POST and stopped resolving mid-run. Caught
                    # HERE rather than around the whole worker so an internal
                    # KeyError from the sampler cannot be mislabelled as a
                    # missing asset — and so the message names WHICH asset.
                    msg = str(exc.args[0]) if exc.args else str(exc)
                    record.update(
                        status="failed", result=None, finished_at=time.time(),
                        error=f"unknown ELCC asset {name!r} "
                              f"(kind {kind!r}): {msg}")
                    return
            portfolio = None
            if want_portfolio:
                from services.adequacy.portfolio import portfolio_block
                portfolio = portfolio_block(
                    inputs, population, margin_payload=margin_payload,
                    snapshot_fingerprint=snapshot_fp, seed=seed, draws=draws,
                    cov_target=cov_target, baseline=metrics, baseline_key=key,
                    stop_event=stop_event)
            record.update(
                status="aborted" if stop_event.is_set() else "done",
                error=None, finished_at=time.time(),
                result={
                    # A SIBLING payload, deliberately not folded into
                    # AdequacyReport: the MC is an engine-local study (like the
                    # COPT), and merging it would grow the one report shape
                    # every other consumer parses (spec §4, recorded decision).
                    "engine": "mc",
                    "fidelity": "sequential_mc",
                    "metrics": metrics,
                    "elcc": rows,
                    # Phase 12c: a SIBLING of `elcc`, never a row in it.
                    "elcc_portfolio": portfolio,
                    "warning": MC_WARNING_V1,
                    # Phase 12c-pre: the units whose outages were sampled ON
                    # their availability series rather than at nameplate.
                    #
                    # Phase 12h: a rate-zero unit carries a profile but has
                    # NO outages sampled on it, so leaving it here would
                    # make this list's documented meaning false. The MC
                    # never calls `split_fleet`, so the two lists are built
                    # here and are DISJOINT by construction.
                    "profile_units": [
                        str(u.name) for u in inputs.units
                        if getattr(u, "profile", None) is not None
                        and not _rate_is_zero(u)],
                    # M5: EVERY unit the flag zeroed — profiled or folded —
                    # so the disclosure is symmetric across the two shapes.
                    "deterministic_units": [
                        str(u.name) for u in inputs.units
                        if _is_flag_deterministic(u)],
                    # F8: the typed-zero half of the same disclosure — see
                    # `/copt`. Without it a unit whose rate the user typed
                    # as 0 is in NO list here, and this payload has no row
                    # note to fall back on.
                    "rate_zero_units": [
                        str(u.name) for u in inputs.units
                        if _rate_is_zero(u)
                        and not _is_flag_deterministic(u)],
                    "folded_units": [
                        {"name": str(u.name),
                         "folded_constant": float(u.folded_constant),
                         "source": "static"}
                        for u in inputs.units
                        if getattr(u, "folded_constant", None) is not None],
                    # Phase 12d: the activity disclosure (see /copt),
                    # captured in the request.
                    "activity": activity_block,
                })
        except Exception as exc:                              # noqa: BLE001
            record.update(status="failed", result=None, error=str(exc),
                          finished_at=time.time())

    # The loops' pattern: the record is already closed over; Phase 12e adds
    # the request's context (a bare Thread does not inherit the ContextVar the
    # active project lives in) and publish-and-start under one lock hold.
    _ctx = _contextvars.copy_context()
    t = _threading.Thread(target=lambda: _ctx.run(worker), daemon=True,
                          name="adequacy-mc")
    record["thread"] = t
    publish_study("mc", record, t)
    return {"status": "running", "draws": draws, "seed": seed,
            "cov_target": cov_target, "elcc_assets": len(assets),
            "elcc_portfolio": want_portfolio}
