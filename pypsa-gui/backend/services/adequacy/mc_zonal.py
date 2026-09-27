"""
Two-area sequential MC for the Energy Hub certification: the hub, and the
grid behind its import Link(s).

Plan docs/superpowers/plans/2026-09-27-eh-zonal-mc-import-outages.md, WP2.
The single-area engine (``mc.py``) certifies the hub side with the import
Link as one more sampled unit (v1). That still assumes the grid always has
the power to send. Here the grid side is a second area with its own sampled
fleet and its own demand, and each hour the hub receives

    import_h = min(Σ link_up × cap_h  (+ firm-block Links),
                   max(grid_available_h − grid_residual_h, 0) × delivery_h)

— the grid serves its own load first and only its surplus crosses the Link.

WHY A SEPARATE MODULE. ``_simulate_blocks`` is the load-bearing single-area
kernel behind the MC endpoint, ELCC and both coupling loops; nothing here
edits it. ``mc.mc_adequacy`` gained one keyword (``blocks_fn``) so the
batching, convergence and aggregation stay in one place, and this module
supplies the two-area block function.

THE CRN CONTRACT, KEPT. The hub half is the v1 ``MCInputs`` with the import
Link unit(s) at their v1 positions, sampled with the SAME seed through
``sample_capacity``: generators come from one call (Links excluded, their
paths generated and discarded as the contract requires), the Links from a
second call in which every other unit is a zero-rate placeholder (which
consumes no stream), so each Link draws from its own positional substream.
When the grid never binds, the hub sees exactly the v1 capacity — the
float32 sum is formed in the same order — and the per-draw LOLE / EUE are
bit-identical to v1 (pinned by a test). The grid fleet samples from its own
tagged substream, so adding the grid area moves no hub draw.

WHAT IT DOES NOT MODEL (disclosed on the payload): grid-side storage is not
dispatched (conservative for the hub), the grid is one area (a grid side
that is several disconnected components is refused upstream), and grid
outages are independent of hub outages like every other unit (MC_WARNING_V1).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from services.adequacy.mc import (
    SHORTFALL_TOL,
    MCInputs,
    _active_storage,
    _dispatch,
    block_store_arrays,
    mc_adequacy,
    sample_capacity,
)

#: The grid area's substream key, appended to the batch seed's spawn key.
#: Unit substreams are ``spawn_key + (i,)`` for positions ``i < len(units)``,
#: so a key this large cannot collide with any fleet position.
GRID_STREAM_KEY = 2**32 - 1


@dataclass(frozen=True)
class ZonalInputs:
    """Both areas, frozen as plain arrays (the ``MCInputs`` rule)."""

    hub: MCInputs                 # v1 inputs: hub units + import Link unit(s)
    grid: MCInputs                # grid-side units / residual (storage unused)
    import_idx: tuple             # positions of the Link unit(s) in hub.units
    firm_import_mw: np.ndarray    # (H,) firm-block Links, already netted
    #                               out of hub.residual in the v1 snapshot
    delivery_ratio: np.ndarray    # (H,) MW delivered at the hub per MW
    #                               withdrawn from the grid (Link efficiency)


def _seed_sequence(seed) -> np.random.SeedSequence:
    if isinstance(seed, np.random.SeedSequence):
        return seed
    return np.random.SeedSequence(seed)


def _fresh(ss: np.random.SeedSequence, extra_key: tuple = ()) -> np.random.SeedSequence:
    """
    A NEW SeedSequence equal to ``ss`` (plus an optional key). Needed
    because ``sample_capacity`` spawns from the sequence it is given, and
    spawning advances the sequence's child counter — reusing one object for
    two calls would hand the second call different children.
    """
    return np.random.SeedSequence(ss.entropy,
                                  spawn_key=(*ss.spawn_key, *extra_key),
                                  pool_size=ss.pool_size)


def simulate_zonal_blocks(z: ZonalInputs, *, draws: int, seed,
                          storage_enabled: bool = True,
                          initial_soc_frac: float = 1.0) -> dict:
    """
    Per-period per-draw ``(lole_h, eue_mwh)`` — ``_simulate_blocks``'s
    shape, for the hub, with the import bounded by the grid's surplus.
    """
    hub = z.hub
    residual = hub.residual
    weights = hub.weights
    H = residual.size
    draws = int(draws)
    ss = _seed_sequence(seed)
    units = tuple(hub.units)
    imp = frozenset(int(i) for i in z.import_idx)

    hub_cap = sample_capacity(units, H, draws, _fresh(ss), exclude=imp,
                              periods=hub.periods)
    link_only = tuple(u if i in imp else dataclasses.replace(u, q=0.0)
                      for i, u in enumerate(units))
    link_cap = sample_capacity(link_only, H, draws, _fresh(ss),
                               exclude=frozenset(range(len(units))) - imp,
                               periods=hub.periods)
    grid_cap = sample_capacity(z.grid.units, H, draws,
                               _fresh(ss, (GRID_STREAM_KEY,)),
                               periods=z.grid.periods)
    hub_t = np.ascontiguousarray(hub_cap.T)
    link_t = np.ascontiguousarray(link_cap.T)
    grid_t = np.ascontiguousarray(grid_cap.T)

    firm = np.asarray(z.firm_import_mw, dtype=np.float64)
    # The v1 residual has the firm-block Links netted out; here they are
    # import capacity bounded by the grid like any other, so add them back.
    residual_raw = residual + firm
    grid_res = z.grid.residual
    ratio = np.asarray(z.delivery_ratio, dtype=np.float64)

    all_stores = _active_storage(hub, storage_enabled, ())
    any_series = any(getattr(s, "capacity_series", None) is not None
                     for s in all_stores)

    out: dict = {}
    for label, start, end in hub.periods:
        n_store, p_nom, e_nom, eff_s, eff_d = block_store_arrays(
            all_stores, start, end, any_series=any_series)
        single_order = np.zeros((1, draws), dtype=np.intp) if n_store == 1 else None
        lole = np.zeros(draws, dtype=np.float64)
        eue = np.zeros(draws, dtype=np.float64)
        soc = np.repeat((e_nom * float(initial_soc_frac))[:, None], draws,
                        axis=1) if n_store else np.zeros((0, draws))
        for h in range(start, end):
            surplus = np.maximum(
                grid_t[h].astype(np.float64) - grid_res[h], 0.0) * ratio[h]
            offered = link_t[h].astype(np.float64) + firm[h]
            imp_h = np.minimum(offered, surplus).astype(np.float32)
            # Same float32 accumulation order as sample_capacity's: the hub's
            # units, then the Link(s) — so an unbound grid replays v1 exactly.
            cap = (hub_t[h] + imp_h).astype(np.float64)
            deficit = residual_raw[h] - cap
            if n_store:
                order = (single_order if n_store == 1
                         else np.argsort(soc, axis=0, kind="stable"))
                deficit = _dispatch(deficit, soc, p_nom, e_nom, eff_s, eff_d,
                                    order)
            w_h = weights[h]
            lole += w_h * (deficit > SHORTFALL_TOL)
            eue += w_h * np.maximum(deficit, 0.0)
        out[label] = (lole, eue)
    return out


def zonal_mc_adequacy(z: ZonalInputs, *, draws: int = 500, seed=0,
                      cov_target: float = 0.05, max_draws: int | None = None,
                      batch: int = 250, stop_event=None, **sim_kwargs) -> dict:
    """
    ``mc.mc_adequacy`` on the two-area block function — the same
    batching, convergence rule, CI and payload keys as the single-area MC.
    """
    kw = {} if max_draws is None else {"max_draws": max_draws}

    def _blocks(_inputs, *, draws, seed, **kwargs):
        return simulate_zonal_blocks(z, draws=draws, seed=seed, **kwargs)

    return mc_adequacy(z.hub, draws=draws, seed=seed, cov_target=cov_target,
                       batch=batch, stop_event=stop_event, blocks_fn=_blocks,
                       **kw, **sim_kwargs)
