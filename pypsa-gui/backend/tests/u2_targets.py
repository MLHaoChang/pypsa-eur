"""
Shared helpers of the U2 port targets (plan
docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md, WP2).

Not collected by pytest (no ``test_`` prefix). ``pending(wp)`` marks a target
whose engine seam is built by a LATER work package: it must fail now
(``strict=True``), so the day the seam lands the target flips to XPASS and the
marker has to go (the WP's acceptance is "the WP2 targets go green").

``WP0`` is the frozen pre-U2 record (``tests/fixtures/u2_pre_numbers.json``).
"""
from __future__ import annotations

import json
import math
import pathlib

import pandas as pd
import pytest

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
WP0 = json.loads((FIXTURES / "u2_pre_numbers.json").read_text(encoding="utf-8"))
DE, TOU = "de_industrial_illustrative", "tou_reference_illustrative"
SEEDS = (DE, TOU)
SIX = ("energy", "demand", "capacity", "fixed", "network", "export_credit")

# A study-shaped fake ref for in-memory binds (no Library, no org).
FAKE_REF = {"id": "decision-study:fake:" + "5" * 32 + ":export", "version": 1,
            "hash": "f" * 64, "source": "decision_study"}


def legacy_ledger(name: str = "de"):
    """
    A ledger the legacy `study_library` seeded on the golden site intake
    (`de` / `tou` seed, or `user` tariff): rows 1-20, frozen in
    `fixtures/legacy_study_library.json` before U2 WP10 deleted the library
    (gate U2-S1 C8 a). What a study stored before U2 WP8 holds.
    """
    from models.study import AssumptionsLedger

    data = json.loads((FIXTURES / "legacy_study_library.json").read_text(encoding="utf-8"))
    return AssumptionsLedger.model_validate(data["ledgers"][name])


def pending(wp: str, why: str = ""):
    """The target waits for ``wp`` (strict xfail: it must fail until then)."""
    return pytest.mark.xfail(strict=True, reason=f"U2 {wp}: {why or 'seam not built yet'}")


def close(got, want, *, rel: float = 1e-9, abs_: float = 1e-9) -> bool:
    if got is None or want is None:
        return got is want
    return math.isclose(float(got), float(want), rel_tol=rel, abs_tol=abs_)


def flat_resolver(price: float, snapshots):
    """`resolve_ref` for `binding.bind_commercial`: a flat series on the axis."""
    series = pd.Series(float(price), index=pd.DatetimeIndex(snapshots), name="price")
    return lambda _ref: series


def series_resolver(series: pd.Series):
    return lambda _ref: series


def golden_copy(option: str):
    """
    An independent copy of the S5 golden network of `option` (solved on the
    engine since U2 WP10, cached per process by `site_fixture`); the solver
    model is detached first (PyPSA refuses to copy a network with an attached
    solver model).
    """
    from tests.golden import site_fixture as SF

    n = SF.solve_site_option(option)[0]
    model = getattr(n, "_model", None)
    if model is not None and getattr(model, "solver_model", None) is not None:
        model.solver_model = None
    return n.copy()


def golden_dispatch(option: str):
    """
    A copy of the golden option's network as a DISPATCH only (U2 WP10), the
    way WP0's GS-solved golden network was: the engine's solve record
    (`n.meta["ic_*"]`, the committed price frame) removed, so a tariff the
    solve did not price (the TOU seed) is rated on the dispatch rather than
    refused as a changed config, and no total is vouched for
    (`solve_provenance_unknown`).
    """
    from services.commercial import lp_bindings as LP

    n = golden_copy(option)
    for key in [k for k in n.meta if str(k).startswith("ic_")]:
        del n.meta[key]
    df = getattr(n.links_t, LP.ENERGY_PRICE_ATTR, None)
    if df is not None and not df.empty:
        df.drop(columns=list(df.columns), inplace=True)
    return n


#: The pre-numbers test whose recorded deltas move WP0's seed bills (U2 WP10:
#: the golden dispatch is the engine's, which ties differently under the flat
#: DE energy price, so the TOU seed's energy line moves; `lp_degenerate_
#: dispatch_ties`).
SEED_DELTAS_TEST = ("test_u2_pre_numbers_recorded.py::"
                    "test_both_seed_bills_on_the_golden_dispatch_reproduce")


def wp0_seed_bill(tid: str, option: str) -> dict:
    """WP0's seed bill of `tid` on `option`'s golden dispatch, its recorded deltas applied."""
    out = json.loads(json.dumps(WP0["seed_bills"][tid][option]))
    rows = json.loads((FIXTURES / "u2_deltas.json").read_text(encoding="utf-8"))["deltas"]
    prefix = f"seed_bills.{tid}.{option}."
    for r in rows:
        if r.get("test") != SEED_DELTAS_TEST or not r["figure"].startswith(prefix):
            continue
        *parents, leaf = r["figure"][len(prefix):].split(".")
        node = out
        for k in parents:
            node = node[k]
        assert close(node[leaf], r["pre"]), r["figure"]
        node[leaf] = r["post"]
    return out


# ── WP6: the guided option on the engine's LP, in process ────────────────

def ic_solve(net, cfg, _variant_id=None):
    """
    Solve `net` with `cfg` through `run_simulation` in process (the chain the
    queue runs: IC's commercial bindings price the PoC and carry the demand
    charge). The tornado's `solve` signature; returns the solved network.
    """
    import queue
    import threading

    from services.solver_service import run_simulation

    status, condition = run_simulation(cfg, net, threading.RLock(), threading.Event(),
                                       queue.SimpleQueue())
    assert (status, condition) == ("ok", "optimal"), (status, condition)
    return net


def bound_option(intake, ledger, option_id: str, library, *, question=None):
    """
    (network, compiled) of one option as the runner builds it (U2 WP6): the
    pack (no Link prices), the commercial config compiled with the study's
    export series (`FAKE_REF`, resolved as the tariff's flat price) and bound
    on the in-memory network.
    """
    from services.study import packs

    kw = {} if question is None else {"question": question}
    n = packs.build_site_network(intake, ledger, option_id, library=library, **kw)
    c = packs.option_commercial(intake, ledger, library, n.snapshots, export_series=FAKE_REF)
    price = float(c.tariff_meta["export_price_per_mwh"])
    return n, packs.bind_option(n, c, resolve_ref=flat_resolver(price, n.snapshots))


_IC_SOLVED: dict = {}


def ic_site_option(option_id: str, ledger=None):
    """
    (solved network, its SolverConfig) of a site golden option on the
    engine's LP — the production path since WP6 — cached per process and
    ledger. (`site_fixture.solve_site_option` is the same engine solve on the
    fixture's own config, with the option's value flows, since U2 WP10.)
    """
    from services.study import packs
    from tests.golden import site_fixture as SF

    ledger = ledger or SF.site_ledger()
    key = (option_id, packs.ledger_hash(ledger))
    if key not in _IC_SOLVED:
        n, c = bound_option(SF.site_intake(), ledger, option_id, SF.site_library())
        cfg = packs.option_solver_config(ledger, c)
        _IC_SOLVED[key] = (ic_solve(n, cfg), cfg)
    return _IC_SOLVED[key]


# ── WP7: the option's fork as the engine-valued case reads it ──────────────

def ic_case_option(option: str):
    """
    (solved network, SolverConfig, compiled commercial, ledger) of a site
    golden option as WP8's runner makes its fork, once per process: the
    pack-seeded ledger (`library.load_defaults`, rows 21-34), the pack network
    (no prices; the battery's two upfront parts, C1), the compiled commercial
    bound on it (C6) with its `single_owner` value flows, solved through
    `run_simulation` with `compile.solver_config`. Since U2 WP10 this IS the
    site golden fixture (`site_fixture.site_case_option`).
    """
    from tests.golden import site_fixture as sf

    return sf.site_case_option(option)
