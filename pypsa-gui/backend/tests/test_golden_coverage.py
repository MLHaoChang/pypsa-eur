"""
Exhaustive-by-default: every fixture class, on every surface, is either
COVERED or EXCLUDED with a written reason.

Opt-in was rejected because it reproduces the exact failure being fixed.
Forgetting to opt Links into asset_economics produces SILENCE, and silence is
how the missing Link block shipped. Inverting the default turns an absence into
a decision with a name on it.
"""
from __future__ import annotations

import ast
import pathlib

from tests.golden import coverage as cov


def test_every_class_on_every_surface_is_covered_or_excluded():
    holes = []
    for surface in cov.SURFACES:
        covered = cov.COVERAGE.get(surface, set())
        for cls in sorted(cov.FIXTURE_CLASSES):
            if cls in covered:
                continue
            if (surface, cls) in cov.EXCLUSIONS:
                continue
            holes.append(f"{surface} x {cls}")
    assert not holes, (
        "Undeclared surface/class pairs. Either the surface reports this class "
        "(add it to COVERAGE) or it deliberately does not (add it to EXCLUSIONS "
        "with a reason):\n  " + "\n  ".join(holes)
    )


def test_exclusion_reasons_are_real_sentences():
    # A reason of "n/a" is an absence with extra steps.
    weak = [
        f"{s} x {c}: {why!r}"
        for (s, c), why in cov.EXCLUSIONS.items()
        if len(why.strip()) < 25
    ]
    assert not weak, "Exclusion reasons must explain WHY:\n  " + "\n  ".join(weak)


def test_no_exclusion_contradicts_its_coverage_entry():
    both = [
        f"{s} x {c}"
        for (s, c) in cov.EXCLUSIONS
        if c in cov.COVERAGE.get(s, set())
    ]
    assert not both, "Listed as both covered and excluded:\n  " + "\n  ".join(both)


def test_every_surface_has_a_coverage_entry():
    missing = [s for s in cov.SURFACES if s not in cov.COVERAGE]
    assert not missing, f"surfaces with no COVERAGE entry: {missing}"


# ── IMPORTANT-3 (final review, 2026-08-03): a tenth surface must be caught ──
#
# `coverage.SURFACES` is a hand-maintained tuple with no link to the actual
# routes. `test_golden_economics.py::test_every_surface_agrees_on_every_
# covered_asset` asserts `set(ADAPTERS) | set(NO_ADAPTER_REASONS) ==
# set(SURFACES)` — that catches forgetting an ADAPTER for a known surface,
# not forgetting the SURFACE itself. A tenth economics endpoint added to
# routers/{results,simulation,compare,asset_results}.py is invisible to the
# entire apparatus until someone remembers to list it — the exact failure
# mode this whole task exists to eliminate, one level up the stack.
#
# CHOSEN APPROACH: an explicit allowlist of every route handler in the four
# modules, asserted (by AST scan) to be COMPLETE — any new `@router.get` /
# `.post` / `.put` in these files fails the test until a human adds it here
# and states whether it is an economics surface.
#
# REJECTED: a purely static scan for "route handlers whose OWN function body
# contains a dict literal or keyword argument named capex/fixed_cost-ish".
# Measured empirically against this codebase (2026-08-03) before writing
# this test: of the 9 surfaces already in `coverage.SURFACES`, only 3
# (`cost_breakdown`, `lcoh`, `asset_economics`) actually construct such a
# dict/keyword INSIDE their own route-handler body. The other 6 delegate to
# a helper — sometimes in the SAME file (`get_economics_by_carrier` calls
# `_compute_economics_summary`; `get_results_summary` calls
# `_compute_capacity_summary`, which builds a `CapacityComparison(...)` via
# keyword args several call-frames away), sometimes fully generic
# (`asset_results.py`'s `get_asset_results` / `export_asset_results_xlsx`
# never mention "capex" anywhere in that file at all — the metric ids live
# in `services/asset_results/registry.py`). A key/kwarg scan restricted to
# the handler's own AST subtree would therefore have missed 6 of the 9 real
# surfaces already known to exist — not a near-miss, a majority miss — so it
# cannot be trusted to catch a 10th surface built the same (common) way.
# Resolving this properly would need whole-program call-graph tracing, which
# is a lot of machinery for a regression guard. The allowlist is simpler and
# strictly stronger: it fails on EVERY new route, economics or not, which
# is over-inclusive (a new dispatch-only endpoint also trips it) but never
# under-inclusive — the direction that actually matters here.
ROUTE_FILES: dict[str, str] = {
    "routers/results.py": "results_router",
    "routers/simulation.py": "router",
    "routers/compare.py": "router",
    "routers/asset_results.py": "router",
}

# (file, function name) -> frozenset of `coverage.SURFACES` ids this route
# contributes to (empty frozenset for a route that reports no CAPEX/fixed-cost
# economics at all). One entry per `@<router>.get/post/put(...)`-decorated
# top-level function in the four files above, verified complete by
# `test_every_route_is_declared_or_the_scan_below_catches_it`.
ROUTE_SURFACES: dict[tuple[str, str], frozenset[str]] = {
    # ── routers/results.py ──────────────────────────────────────────────
    ("routers/results.py", "get_cost_breakdown"):          frozenset({"cost_breakdown"}),
    ("routers/results.py", "get_objective_decomposition"):  frozenset(),
    ("routers/results.py", "get_economics_by_carrier"):     frozenset({"economics_by_carrier"}),
    ("routers/results.py", "get_statistics"):                frozenset({"statistics"}),
    ("routers/results.py", "get_generator_results"):         frozenset(),
    ("routers/results.py", "get_storage_dispatch_results"):  frozenset(),
    ("routers/results.py", "get_store_dispatch_results"):    frozenset(),
    ("routers/results.py", "get_store_energy_results"):      frozenset(),
    ("routers/results.py", "get_storage_results"):            frozenset(),
    ("routers/results.py", "get_line_results"):               frozenset(),
    ("routers/results.py", "get_link_results"):               frozenset(),
    ("routers/results.py", "get_lcoh"):                       frozenset({"lcoh"}),
    ("routers/results.py", "get_ac_pf_status"):               frozenset(),
    ("routers/results.py", "get_losses_summary"):             frozenset(),
    ("routers/results.py", "get_carrier_kpis"):               frozenset(),
    ("routers/results.py", "get_emissions"):                  frozenset(),
    ("routers/results.py", "get_transformer_results"):        frozenset(),
    ("routers/results.py", "get_unit_commitment"):            frozenset(),
    ("routers/results.py", "get_line_duals"):                 frozenset(),
    ("routers/results.py", "get_voltages"):                   frozenset(),
    ("routers/results.py", "get_line_reactive"):               frozenset(),
    ("routers/results.py", "get_transformer_reactive"):        frozenset(),
    ("routers/results.py", "get_prices"):                     frozenset(),
    ("routers/results.py", "get_price_drivers"):               frozenset(),
    ("routers/results.py", "get_curtailment"):                 frozenset(),
    ("routers/results.py", "get_lost_load"):                   frozenset(),
    # Adequacy / solution-FMEA surfaces. None of them reports an economics
    # SURFACE id: they carry reliability metrics and failure-mode rows, whose
    # € figures are derived from VoLL × unserved energy inside the adequacy
    # services rather than from the economics roll-ups this allowlist tracks.
    ("routers/results.py", "get_adequacy"):                    frozenset(),
    ("routers/results.py", "get_copt"):                        frozenset(),
    ("routers/results.py", "get_fmea_modes"):                  frozenset(),
    ("routers/results.py", "get_fmea_sweep"):                  frozenset(),
    ("routers/results.py", "post_fmea_sweep"):                 frozenset(),
    ("routers/results.py", "get_frontier"):                    frozenset(),
    ("routers/results.py", "post_frontier"):                   frozenset(),
    ("routers/results.py", "get_mc"):                          frozenset(),
    ("routers/results.py", "post_mc"):                         frozenset(),
    ("routers/results.py", "get_load_results"):                frozenset(),
    ("routers/results.py", "get_asset_economics"):             frozenset({"asset_economics"}),
    # ── routers/simulation.py ───────────────────────────────────────────
    ("routers/simulation.py", "get_solver_config"):    frozenset(),
    ("routers/simulation.py", "update_solver_config"):  frozenset(),
    ("routers/simulation.py", "check_solvers"):         frozenset(),
    ("routers/simulation.py", "capabilities"):          frozenset(),
    ("routers/simulation.py", "asset_costs"):           frozenset({"asset_costs"}),
    ("routers/simulation.py", "preflight"):             frozenset(),
    ("routers/simulation.py", "run"):                   frozenset(),
    ("routers/simulation.py", "lock_status"):           frozenset(),
    ("routers/simulation.py", "force_reset"):            frozenset(),
    ("routers/simulation.py", "abort"):                 frozenset(),
    ("routers/simulation.py", "run_ac_pf"):              frozenset(),
    ("routers/simulation.py", "get_status"):             frozenset(),
    ("routers/simulation.py", "get_log_history"):        frozenset(),
    ("routers/simulation.py", "log_stream"):             frozenset(),
    # ── routers/compare.py ──────────────────────────────────────────────
    # Task 20: `installed_capacity_by_carrier` / `storage_capacity_by_
    # carrier` on CompareState are exactly the same shape of per-carrier
    # capacity claim `compare_capacity` makes on ResultsSummary — give it its
    # own coverage.SURFACES id (compare_overview) rather than leaving it the
    # one Compare-tab route with no entry at all.
    ("routers/compare.py", "get_compare_state"): frozenset({"compare_overview"}),
    # One endpoint, nine surfaces: each optional field of the same
    # ResultsSummary response is its own coverage.SURFACES id because each
    # is computed by a different `_compute_*_summary` function with its own
    # component-class coverage (Task 20 brought the other seven tabs in
    # alongside the original two).
    ("routers/compare.py", "get_results_summary"): frozenset({
        "compare_capacity", "compare_economics", "compare_dispatch",
        "compare_loading", "compare_prices", "compare_emissions",
        "compare_curtailment", "compare_lost_load", "compare_storage_cycling",
    }),
    # ── routers/asset_results.py ────────────────────────────────────────
    ("routers/asset_results.py", "list_assets"):                frozenset(),
    ("routers/asset_results.py", "export_asset_results_xlsx"):   frozenset({"asset_results_xlsx"}),
    ("routers/asset_results.py", "get_asset_results"):           frozenset({"asset_results"}),
}


def _scan_route_handlers() -> set[tuple[str, str]]:
    """
    AST-walk the four router modules for every top-level function decorated
    with `<router_var>.get(...)` / `.post(...)` / `.put(...)` / `.delete(...)`
    / `.patch(...)`. Top-level only (`tree.body`, not `ast.walk`) — a nested
    helper decorated for unrelated reasons should not masquerade as a route.
    Handles `async def` too (`log_stream` is an SSE route) — `ast.walk`-style
    single-type checks silently skip `AsyncFunctionDef`, which is exactly how
    that route was nearly missed while building this test.
    """
    backend_root = pathlib.Path(__file__).resolve().parents[1]
    found: set[tuple[str, str]] = set()
    methods = {"get", "post", "put", "delete", "patch"}
    for rel_path in ROUTE_FILES:
        src = (backend_root / rel_path).read_text(encoding="utf-8")
        tree = ast.parse(src, filename=rel_path)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in methods
                ):
                    found.add((rel_path, node.name))
                    break
    return found


def test_every_route_handler_is_declared_in_the_surfaces_allowlist():
    scanned = _scan_route_handlers()
    declared = set(ROUTE_SURFACES)
    assert scanned == declared, (
        "routers/{results,simulation,compare,asset_results}.py route "
        "handlers drifted from ROUTE_SURFACES in this file -- new routes: "
        f"{scanned - declared}, routes removed/renamed: {declared - scanned}. "
        "Add (or remove) the entry, and if it's a new economics endpoint "
        "give it a coverage.SURFACES id (or an empty frozenset with a "
        "reason if it deliberately reports none)."
    )


def test_every_declared_surface_is_a_real_coverage_surface():
    claimed = frozenset().union(*ROUTE_SURFACES.values()) if ROUTE_SURFACES else frozenset()
    assert claimed == set(cov.SURFACES), (
        "ROUTE_SURFACES and coverage.SURFACES disagree -- claimed by a route "
        f"but not in SURFACES: {claimed - set(cov.SURFACES)}; in SURFACES "
        f"but no route claims it: {set(cov.SURFACES) - claimed}"
    )
