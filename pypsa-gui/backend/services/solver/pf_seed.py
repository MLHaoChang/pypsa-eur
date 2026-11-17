"""Seed the AC power flow when a transformer shifts phase (owner decision DGS-SEED-1).

PowerFactory Dyn-type transformers import as a pypsa ``phase_shift`` of
30° x clock number (as pandapower maps them); PSS/E and PLEXOS imports can
carry shifts too. Newton-Raphson from a flat start (every angle 0) can
diverge on such a network, while a linear power flow followed by
``pf(use_seed=True)`` converges. Networks without an active shifting
transformer keep the flat-start call exactly as the caller wrote it.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np


def phase_shift_transformers(network, snapshots=None) -> list[str]:
    """Transformers active in ``snapshots`` whose ``phase_shift % 360 != 0``.

    Activity is PyPSA's own (``active`` plus build year and lifetime per
    investment period on a multi-period network), the same mask its
    power flow uses.
    """
    t = network.transformers
    if t.empty:
        return []
    shifted = (t["phase_shift"].fillna(0.0).astype(float) % 360.0) != 0.0
    if not shifted.any():
        return []
    periods = None
    snaps = network.snapshots if snapshots is None else snapshots
    if getattr(snaps, "nlevels", 1) > 1:
        periods = list(dict.fromkeys(snaps.get_level_values(0)))
    active = (
        network.components["Transformer"]
        .get_active_assets(investment_period=periods)
        .reindex(t.index)
        .fillna(False)
        .astype(bool)
    )
    return list(t.index[shifted & active])


def run_pf(network, phase: Callable[[str], None] | None = None, **pf_kwargs):
    """``network.pf(**pf_kwargs)``, seeded by ``lpf()`` when a transformer shifts phase.

    Without an active shifting transformer this is exactly
    ``network.pf(**pf_kwargs)``. With one, it runs ``network.lpf()`` on the
    same snapshots and then ``pf`` with ``use_seed=True``, and says so
    through ``phase``. When ``lpf()`` raises or leaves a non-finite angle,
    it logs that and makes the caller's unseeded call instead.

    Returns ``(pf_result, seeded_by)``: ``seeded_by`` lists the transformers
    that triggered the seed, empty for a flat start
    (no shift, or the seed skipped).
    """
    shifted = phase_shift_transformers(network, pf_kwargs.get("snapshots"))
    if not shifted:
        return network.pf(**pf_kwargs), []
    lpf_kwargs = (
        {"snapshots": pf_kwargs["snapshots"]} if "snapshots" in pf_kwargs else {}
    )
    # A seed that fails or is not finite must not cost the flow its flat
    # start: fall back to the caller's own call (GK A on #1663).
    try:
        network.lpf(**lpf_kwargs)
        v_ang = network.buses_t.v_ang
        if "snapshots" in pf_kwargs:
            v_ang = v_ang.reindex(pf_kwargs["snapshots"])
        finite = np.isfinite(v_ang.to_numpy(dtype=float)).all()
        problem = None if finite else "non-finite voltage angles"
    except Exception as exc:
        problem = f"{type(exc).__name__}: {exc}"
    if problem is not None:
        if phase is not None:
            phase(f"AC PF seed skipped: lpf() failed ({problem}); flat start.")
        return network.pf(**pf_kwargs), []
    if phase is not None:
        names = ", ".join(map(str, shifted[:5])) + ("..." if len(shifted) > 5 else "")
        phase(
            f"AC PF seeded: lpf() then pf(use_seed=True), since {len(shifted)} "
            f"transformer(s) shift phase ({names})."
        )
    return network.pf(**{**pf_kwargs, "use_seed": True}), shifted
