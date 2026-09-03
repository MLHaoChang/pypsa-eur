"""
Phase 12c — the portfolio ELCC as a second opinion on the reserve margin.

Plan: docs/superpowers/plans/2026-09-03-fmea-phase12c-portfolio-elcc-v3.md
(v3.1 amendments A3–A9). The question: the reserve margin credits the
profile-bearing fleet by ``(1 − q) × mean(profile over the peak window)``;
what firm block would the sampler say that same group is worth, per
period, against its own loss-of-load? Two standards, neither a correction
of the other — the block carries three MW figures per period and no ratio.

Populations, capacity rules and refusals are the load-bearing part:

* **One population** (A3): the generators the membership walk admits whose
  ``p_max_pu`` COLUMN is informative (``copt.series_is_informative``), for
  BOTH halves — must-take farms (``kind="vre"``, un-netted from the residual)
  and profiled occurrence units (``kind="generator"``, excluded by position).
  A must-take with only a static value or no column stays an ELCC candidate
  on its own but is not in the portfolio; storage is out of scope.
* **Two capacity rules, and the rule for their disagreement** (A4): the
  engines' ``solved_capacity`` against the margin payload's vintage-aware
  built capacity, compared BY PARENT AGGREGATE per period (the restore
  writes the parent's ``p_nom_opt`` as the sum over vintages). A member
  with no payload row in a period is an ``activity_mismatch`` (the engines
  ignore ``build_year``/``lifetime``; the margin masks them — a recorded
  MC-wide item); a disagreeing aggregate is ``capacity_basis_mismatch``;
  a payload whose network fingerprint differs from the snapshot's is
  ``stale_report``. Every refusal names what it saw and carries NO number.
* **Refusals are data** (A5): block-level ``ok | no_population |
  activity_mismatch | capacity_basis_mismatch | stale_report |
  margin_unavailable``; per period ``ok | unidentifiable | not_bracketed |
  no_contribution`` (a period where the group's availability is zero
  everywhere is a no-op removal, reported as such — never ``ok 0.0``).

Everything the worker needs is computed IN THE REQUEST from the network
(``portfolio_population``, ``network_fingerprint``); the worker sees plain
data and the frozen snapshot only (A9).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace

import numpy as np

from services.adequacy.copt import _availability_mw, series_is_informative, solved_capacity
from services.adequacy.elcc import elcc_of_removal

#: rel tolerance for the by-parent capacity aggregate (A4).
_CAP_REL = 1e-9
_CAP_ABS = 1e-6


@dataclass(frozen=True)
class Member:
    kind: str            # "vre" | "generator"
    name: str
    capacity_mw: float   # solved_capacity — the engines' rule


# ── population (in-request) ────────────────────────────────────────────

def portfolio_population(n, inputs) -> dict:
    """
    ``{"members": [Member], "unbuilt": [names], "snapshot_names": set}`` —
    the profile-bearing population (A3) with the engines' capacity rule, and
    the full set of names the snapshot knows (for the activity check).

    ``inputs`` must have been snapshotted with ``vre_assets`` covering every
    must-take name whose column is informative (the route does that), or a
    must-take farm would be silently missing here.
    """
    from services.adequacy.copt import must_take_generators

    gens = getattr(n, "generators", None)
    p_max_pu_t = getattr(getattr(n, "generators_t", None), "p_max_pu", None)

    def _informative(name) -> bool:
        return (p_max_pu_t is not None
                and name in getattr(p_max_pu_t, "columns", [])
                and series_is_informative(p_max_pu_t[name]))

    members: list[Member] = []
    unbuilt: list[str] = []
    must_take = list(must_take_generators(n))
    for name in must_take:
        if name not in inputs.vre_profiles or not _informative(name):
            continue
        cap = float(solved_capacity(gens.loc[name])) if gens is not None and name in gens.index else 0.0
        if cap > 0.0:
            members.append(Member("vre", str(name), cap))
        else:
            unbuilt.append(str(name))
    for u in inputs.units:
        if getattr(u, "profile", None) is None:
            continue
        cap = float(u.capacity_mw)
        if cap > 0.0:
            members.append(Member("generator", str(u.name), cap))
        else:
            unbuilt.append(str(u.name))
    snapshot_names = set(must_take) | {str(u.name) for u in inputs.units} \
        | {str(s.name) for s in inputs.storage}
    return {"members": members, "unbuilt": unbuilt,
            "snapshot_names": snapshot_names}


def network_fingerprint(n) -> str:
    """sha256 over what the comparison depends on: generator names,
    capacities (``p_nom``, ``p_nom_opt``), every ``p_max_pu`` column, the
    load frame and static loads, storage sizes. Stamped on the margin
    payload at the report step and recomputed at request time; a difference
    is ``stale_report`` (A4, staleness)."""
    h = hashlib.sha256()
    gens = getattr(n, "generators", None)
    if gens is not None and not gens.empty:
        for col in ("p_nom", "p_nom_opt", "p_max_pu", "build_year", "lifetime"):
            if col in gens.columns:
                h.update(col.encode())
                h.update(np.ascontiguousarray(
                    gens[col].to_numpy(dtype=np.float64, na_value=np.nan)).tobytes())
        h.update("\x1f".join(map(str, gens.index)).encode())
    p_max_pu_t = getattr(getattr(n, "generators_t", None), "p_max_pu", None)
    if p_max_pu_t is not None and not p_max_pu_t.empty:
        for col in p_max_pu_t.columns:
            h.update(str(col).encode())
            h.update(np.ascontiguousarray(
                p_max_pu_t[col].to_numpy(dtype=np.float64)).tobytes())
    loads = getattr(n, "loads", None)
    if loads is not None and not loads.empty and "p_set" in loads.columns:
        h.update(np.ascontiguousarray(
            loads["p_set"].to_numpy(dtype=np.float64, na_value=np.nan)).tobytes())
    p_set_t = getattr(getattr(n, "loads_t", None), "p_set", None)
    if p_set_t is not None and not p_set_t.empty:
        h.update(np.ascontiguousarray(
            p_set_t.to_numpy(dtype=np.float64)).tobytes())
    su = getattr(n, "storage_units", None)
    if su is not None and not su.empty:
        for col in ("p_nom", "p_nom_opt", "max_hours"):
            if col in su.columns:
                h.update(np.ascontiguousarray(
                    su[col].to_numpy(dtype=np.float64, na_value=np.nan)).tobytes())
    return h.hexdigest()


# ── the group removal, per period ──────────────────────────────────────

def member_contributions(inputs, members) -> np.ndarray:
    """``(k, H)`` of ``a_{i,h}``: a must-take member's preserved
    ``profile × capacity``; a profiled unit's ``profile × capacity_mw``."""
    H = int(np.asarray(inputs.residual).shape[0])
    rows = []
    by_name = {u.name: u for u in inputs.units}
    for m in members:
        if m.kind == "vre":
            rows.append(np.asarray(inputs.vre_profiles[m.name], dtype=np.float64))
        elif m.kind == "generator":
            rows.append(_availability_mw(by_name[m.name], H))
        else:
            raise ValueError(f"portfolio member kind {m.kind!r} is not priceable")
    if not rows:
        return np.zeros((0, H), dtype=np.float64)
    return np.stack(rows)


def elcc_of_portfolio(inputs, members, *, seed, draws, cov_target: float = 0.05,
                      baseline=None, baseline_key=None, tol_mw=None,
                      **sim_kwargs) -> list[dict]:
    """
    One row per period label of ``inputs.periods``: the last-in credit of the
    whole group, priced against that period's LOLE (plan §3.2). The removal
    un-nets every vre member (``residual += Σ profile``) and excludes every
    generator member by position, in one ``elcc_of_removal`` call per period
    on ONE shared baseline and ONE shared Δ = 0 probe. The bracket top is the
    group's physical maximum in the period, ``max_{h∈P} Σ a_{i,h}`` — the
    dominance reason: a firm block of that size dominates the group hour by
    hour. A period where that maximum is zero is ``no_contribution``.

    Raises ``ValueError`` on an empty population: the block-level refusal
    is the caller's (``portfolio_block``), and pricing nothing as ``ok 0.0``
    is exactly what v2's review found the bare call would do.
    """
    members = list(members)
    if not members:
        raise ValueError("elcc_of_portfolio: empty population")
    contrib = member_contributions(inputs, members)
    total = contrib.sum(axis=0)
    vre_sum = np.zeros_like(total)
    exclude: set[int] = set()
    pos = {u.name: i for i, u in enumerate(inputs.units)}
    for m, row in zip(members, contrib):
        if m.kind == "vre":
            vre_sum = vre_sum + row
        else:
            exclude.add(pos[m.name])
    reduced = replace(inputs, residual=np.ascontiguousarray(inputs.residual + vre_sum))

    # ONE baseline (the caller's, keyed, or computed here once) and ONE Δ = 0
    # probe shared by every period: a single evaluation carries every
    # period's LOLE (A6 — the cost is n_periods × ~10 full evaluations plus
    # this baseline, and nothing is evaluated twice).
    from services.adequacy.elcc import _NEVER_CONVERGE, baseline_key as _key
    from services.adequacy.mc import MAX_DRAWS, mc_adequacy
    if baseline is None:
        baseline = mc_adequacy(inputs, draws=draws, seed=seed, cov_target=cov_target,
                               **sim_kwargs)
        baseline_key = _key(inputs, draws=draws, seed=seed, cov_target=cov_target,
                            max_draws=MAX_DRAWS, batch=250, sim_kwargs=sim_kwargs)
    n_fixed = int(baseline["n_samples"])
    zero_probe = mc_adequacy(
        reduced, draws=draws, seed=seed, cov_target=_NEVER_CONVERGE,
        max_draws=n_fixed, batch=250, exclude=frozenset(exclude),
        extra_firm_mw=0.0, **sim_kwargs)

    rows: list[dict] = []
    for label, start, end in inputs.periods:
        nameplate = float(np.max(total[start:end], initial=0.0)) if end > start else 0.0
        if not math.isfinite(nameplate) or nameplate <= 0.0:
            from services.adequacy.elcc import _lole_of
            rows.append({"period": str(label), "nameplate_mw": 0.0, "elcc_mw": None,
                         "elcc_share": None, "status": "no_contribution",
                         "reason": ("the group is available nowhere in this period "
                                    "(its summed availability is zero every hour), so "
                                    "removing it changes nothing and there is no "
                                    "credit to price"),
                         "baseline_lole_h": _lole_of(baseline, label),
                         "baseline_lole_ci": tuple(float(v) for v in baseline["lole_ci"])})
            continue
        row = elcc_of_removal(
            inputs, reduced=reduced, exclude=frozenset(exclude),
            nameplate_mw=nameplate, seed=seed, draws=draws,
            cov_target=cov_target, tol_mw=tol_mw, period=label,
            baseline=baseline, baseline_key=baseline_key,
            _zero_probe=zero_probe, **sim_kwargs)
        rows.append({"period": str(label), **row})
    return rows


# ── the block: the comparison with the margin's own payload ────────────

def _parent(name: str) -> str:
    parent, sep, suffix = str(name).rpartition("@")
    if not sep:
        return str(name)
    try:
        int(suffix)
    except ValueError:
        return str(name)
    return parent


def portfolio_block(inputs, population: dict, *, margin_payload, snapshot_fingerprint,
                    seed, draws, cov_target, baseline, baseline_key, **sim_kwargs) -> dict:
    """
    The ``elcc_portfolio`` payload (A5): population, the comparison rows per
    period, and a status that names every refusal. ``margin_payload`` is the
    last solve's reserve-margin payload captured in the request (or None);
    ``snapshot_fingerprint`` is ``network_fingerprint(n)`` at request time.
    """
    members: list[Member] = list(population.get("members") or [])
    unbuilt = list(population.get("unbuilt") or [])
    snapshot_names = set(population.get("snapshot_names") or ())
    block: dict = {
        "status": "ok",
        "reason": None,
        "population": {
            "members": [{"kind": m.kind, "name": m.name, "capacity_mw": m.capacity_mw}
                        for m in members],
            "unbuilt": unbuilt,
            "n_vre": sum(1 for m in members if m.kind == "vre"),
            "n_generator": sum(1 for m in members if m.kind == "generator"),
        },
        "margin_available": margin_payload is not None,
        "periods": [],
        "load_basis": "lp",
    }
    if not members:
        block.update(status="no_population",
                     reason=("no generator with both an informative availability "
                             "series and built capacity: nothing to price as a "
                             "portfolio" + (f" ({len(unbuilt)} unbuilt: "
                                            f"{', '.join(unbuilt[:10])})" if unbuilt else "")))
        return block

    credits: dict[str, dict] = {}
    if margin_payload is not None:
        payload_fp = margin_payload.get("fingerprint")
        if payload_fp != snapshot_fingerprint:
            block.update(status="stale_report",
                         reason=("the last solve's reserve-margin payload describes a "
                                 "network that has since changed (fingerprint "
                                 f"{str(payload_fp)[:12]}… vs {snapshot_fingerprint[:12]}…); "
                                 "solve again before comparing"))
            return block
        rows = list(margin_payload.get("assets") or [])
        by_period = {str(p.get("period")): p for p in (margin_payload.get("by_period") or [])}
        labels = [str(b[0]) for b in inputs.periods]
        activity: list[str] = []
        capacity: list[str] = []
        for label in labels:
            rows_p = [r for r in rows if str(r.get("period")) == label
                      and str(r.get("kind")) == "generator"]
            names_p = {_parent(r.get("name")) for r in rows_p}
            for m in members:
                mine = [r for r in rows_p if _parent(r.get("name")) == m.name]
                if not mine:
                    activity.append(f"{m.name} has no margin row in {label}")
                    continue
                caps = [r.get("capacity_mw") for r in mine]
                if any(c is None for c in caps):
                    capacity.append(f"{m.name} in {label}: payload capacity unknown")
                    continue
                agg = float(sum(float(c) for c in caps))
                if not math.isclose(agg, m.capacity_mw, rel_tol=_CAP_REL, abs_tol=_CAP_ABS):
                    capacity.append(f"{m.name} in {label}: margin {agg:.6g} MW vs "
                                    f"engines {m.capacity_mw:.6g} MW")
            # A margin row the engines do not have is a mismatch only when
            # it carries capacity: an unbuilt extendable (0 MW) is in the
            # margin's walk (keep_zero_capacity) and legitimately absent from
            # the sampled fleet.
            credited = {_parent(r.get("name")) for r in rows_p
                        if float(r.get("capacity_mw") or 0.0) > 0.0}
            for nm in sorted(credited - snapshot_names):
                activity.append(f"margin row {nm} in {label} is absent from the snapshot")
        if activity:
            block.update(status="activity_mismatch",
                         reason=("the margin and the engines disagree about who is "
                                 "present: " + "; ".join(activity[:8])
                                 + (" …" if len(activity) > 8 else "")))
            return block
        if capacity:
            block.update(status="capacity_basis_mismatch",
                         reason=("the margin's built capacity and the engines' "
                                 "solved capacity disagree: " + "; ".join(capacity[:8])
                                 + (" …" if len(capacity) > 8 else "")))
            return block
        member_names = {m.name for m in members}
        for label in labels:
            rows_p = [r for r in rows if str(r.get("period")) == label
                      and str(r.get("kind")) == "generator"
                      and _parent(r.get("name")) in member_names]
            gross = 0.0
            net = 0.0
            for r in rows_p:
                cap = float(r.get("capacity_mw") or 0.0)
                d = float(r.get("derate") or 0.0)
                dn = r.get("derate_net")
                gross += d * cap
                net += (float(dn) if dn is not None else d) * cap
            nw = (by_period.get(label) or {}).get("net_window") or {}
            credits[label] = {"credit_gross_mw": gross,
                              "credit_net_mw": net if nw.get("status") == "ok" else None}
    else:
        block["status"] = "margin_unavailable"
        block["reason"] = ("the last solve set no reserve margin, so there is "
                           "nothing to compare the portfolio credit with; the "
                           "ELCC rows stand alone")

    rows_out = elcc_of_portfolio(inputs, members, seed=seed, draws=draws,
                                 cov_target=cov_target, baseline=baseline,
                                 baseline_key=baseline_key, **sim_kwargs)
    for r in rows_out:
        c = credits.get(r["period"], {"credit_gross_mw": None, "credit_net_mw": None})
        r["credit_gross_mw"] = c["credit_gross_mw"]
        r["credit_net_mw"] = c["credit_net_mw"]
        r["baseline_lole_ci"] = list(r["baseline_lole_ci"])
    block["periods"] = rows_out
    return block
