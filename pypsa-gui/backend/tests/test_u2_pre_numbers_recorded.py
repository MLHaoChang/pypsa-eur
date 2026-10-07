"""
U2 plan WP0: the pre-U2 numbers are frozen, and the current code still
gives them (docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md
§7 WP0, §4 judging rule).

``tests/fixtures/u2_pre_numbers.json`` was recorded by
``tests/u2_record_pre_numbers.py`` (``RECORD_U2_PRE=1``) on the pure
guided-study engine, before the master merge and before any U2 change.

1. Every key WP5-WP10 read exists, and is a finite number wherever the GS
   engine gives one; the verdict is the §4 record; the recording commit is
   an ancestor of HEAD (it predates WP1).
2. The figures re-derive on the current code within the fixture's stated
   ``tolerances``: 1e-9 relative for arithmetic downstream of the dispatch
   (money, LCOS, bills), 1e-9 absolute for IRR, paybacks and rates, 1e-6
   relative for LP objectives, 1e-4 MW for sizes and peaks. That is what
   catches a master merge that moves a number.

Real solves: the S5 golden and seed-bill checks reuse
``site_fixture.solve_site_option``'s per-process cache (the three solves
``test_proforma_golden.py`` already makes); the toy is one 2,160-h solve;
the driver re-run (``slow``) is ``qa_decision_study.py`` in a subprocess
(about 70 s). From WP8 on the driver's figures move by design and the §4
judging rule takes over from its re-derivation here.

A figure a U2 work package moves BY DESIGN is never re-frozen: it is a row of
``tests/fixtures/u2_deltas.json`` whose ``test`` is the comparing test's id
and whose ``figure`` is the frozen dotted path; the comparison checks the
row's ``pre`` IS the frozen value, then compares against its ``post``
(:func:`_with_recorded_deltas`). Since WP7: the driver's
``upfront_cost_series_eur_per_mw`` (C1 closes the S4 gap), the FOM line's
rounding and the engine's LCOS.
"""
from __future__ import annotations

import json
import math
import subprocess

import pytest

from tests import u2_record_pre_numbers as REC

BILL_KEYS = ("energy", "demand", "capacity", "fixed", "network", "export_credit")
DRIVER_OPTIONS = ("none", "bess_1h", "bess_2h", "bess_4h")
TORNADO_KEYS = ("demand_charge_price", "discount_rate", "battery_storage_eur_per_kwh",
                "battery_inverter_eur_per_kw")
MONTHS = REC.TOY_MONTHS

_MW_KEYS = {"p_nom_opt", "e_nom_opt", "battery_p_nom_mw", "pv_p_nom_mw", "p_nom_mw"}
_RATE_YEAR_KEYS = {"irr", "payback_simple", "payback_discounted", "discount_rate"}


def _frozen() -> dict:
    if not REC.FIXTURE.is_file():
        pytest.fail(f"{REC.FIXTURE.name} is not recorded: run "
                    "`RECORD_U2_PRE=1 python tests/u2_record_pre_numbers.py` before any U2 change")
    return json.loads(REC.FIXTURE.read_text(encoding="utf-8"))


def _bill_paths(prefix: str) -> list[str]:
    return [f"{prefix}.total", f"{prefix}.annual_bill",
            *(f"{prefix}.by_component.{k}" for k in BILL_KEYS)]


def _required_numbers() -> list[str]:
    """Every figure a later comparison reads, as a dotted path; each must be a number."""
    paths = [
        "driver.sizes.bess_1h.battery.p_nom_opt", "driver.sizes.bess_2h.battery.p_nom_opt",
        "driver.sizes.bess_4h.battery.p_nom_opt", "driver.npv_bounds.npv_centre",
        *(f"driver.npv_bounds.tornado.{k}.{f}" for k in TORNADO_KEYS
          for f in ("low_value", "high_value", "npv_low", "npv_high")),
        "driver.capex.p_nom_mw", "driver.capex.upfront_eur_per_mw",
        "driver.capex.case_capex_total", "driver.fom.asset_economics_fom_cost_eur",
        "driver.fom.cost_breakdown_storageunit_fom", "driver.fom.sum_years_opex_fixed",
        *(f"driver.case_kpis.{k}" for k in (
            "npv", "irr", "payback_simple", "payback_discounted", "capex_total", "salvage_eur",
            "lcos_excl_charging", "lcos_incl_charging")),
        *(p for o in DRIVER_OPTIONS for p in _bill_paths(f"driver.bills.{o}")),
        "golden_s5.none.objective",
        "golden_s5.bess_pv_2h.pv_p_nom_mw",
        *(f"golden_s5.{o}.{k}" for o in REC.SITE_CASE_OPTIONS for k in (
            "objective", "battery_p_nom_mw", "capex_total", "replacements.10", "replacements.20",
            "fom_annual", "vom_annual", "bill_baseline", "bill_option", "savings_annual",
            "salvage_eur", "npv", "irr", "payback_simple", "payback_discounted",
            "lcos_excl_charging", "lcos_incl_charging", "market_revenue_at_duals",
            *(f"value_streams.{s}" for s in BILL_KEYS))),
        *(p for t in REC.SEED_TARIFFS for o in ("none", *REC.SITE_CASE_OPTIONS)
          for p in _bill_paths(f"seed_bills.{t}.{o}")),
        "toy_3month.objective", "toy_3month.battery_p_nom_mw", "toy_3month.demand_charge_eur",
        *(f"toy_3month.peak_import_mw.{m}" for m in MONTHS),
        *(f"toy_3month.monthly_max_import_mw.{m}" for m in MONTHS),
    ]
    return paths


_REQUIRED_STRINGS = [
    "driver.case_kpis.ledger_hash", "golden_s5.bess_2h.ledger_hash",
    "provenance.commit", "provenance.pypsa", "provenance.linopy", "provenance.highspy",
    "provenance.ledger_hash.site_golden", "provenance.ledger_hash.driver",
    "provenance.library_version",
]


def _get(d, path: str):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(path)
        cur = cur[part]
    return cur


# ── 1. every key the later comparisons read ──────────────────────────────

def test_every_key_the_u2_comparisons_read_is_recorded():
    frozen = _frozen()
    assert frozen["schema"] == REC.SCHEMA
    assert frozen["tolerances"] == REC.TOLERANCES
    missing, not_numbers = [], []
    for path in _required_numbers():
        try:
            v = _get(frozen, path)
        except KeyError:
            missing.append(path)
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            not_numbers.append((path, v))
    for path in _REQUIRED_STRINGS:
        try:
            v = _get(frozen, path)
        except KeyError:
            missing.append(path)
            continue
        if not (isinstance(v, str) and v):
            not_numbers.append((path, v))
    assert missing == [] and not_numbers == [], (missing, not_numbers)
    # The 60-check driver passed when it was recorded.
    assert frozen["driver"]["checks"]["failed"] == 0


def test_the_recorded_verdict_is_the_plans_judging_record():
    """Plan §4: `marginal`, driver `demand_charge_price`, `bess_1h`; 2 h and 4 h skipped."""
    v = _frozen()["driver"]["verdict"]
    assert v == {"class": "marginal", "drivers": ["demand_charge_price"], "option": "bess_1h",
                 "skipped_options": ["bess_2h", "bess_4h"]}


def test_the_recording_commit_predates_wp1():
    commit = _frozen()["provenance"]["commit"]
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REC._BACKEND,
                              capture_output=True, text=True, check=True).stdout.strip()
        anc = subprocess.run(["git", "merge-base", "--is-ancestor", commit, head],
                             cwd=REC._BACKEND, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("no git checkout to check ancestry in")
    assert anc.returncode == 0, f"{commit} is not an ancestor of HEAD {head}"


# ── 2. the numbers still reproduce ───────────────────────────────────────

def _close(key: str, parent: str, want: float, got: float, tol: dict) -> bool:
    if key == "objective":
        return abs(got - want) <= tol["lp_objective_rel"] * max(abs(want), abs(got), 1.0)
    if key in _MW_KEYS or parent.endswith("_mw"):
        return abs(got - want) <= tol["mw_abs"]
    if key in _RATE_YEAR_KEYS:
        return abs(got - want) <= tol["rate_and_years_abs"]
    return abs(got - want) <= tol["arithmetic_rel"] * max(abs(want), abs(got)) + tol[
        "arithmetic_abs"]


def _diff(want, got, tol: dict, path: str = "", key: str = "", parent: str = "") -> list[str]:
    """Every leaf of `want` that `got` does not reproduce, by path (empty = all reproduce)."""
    if isinstance(want, dict):
        if not isinstance(got, dict):
            return [f"{path}: {got!r} is not a mapping"]
        out = [f"{path}.{k}: missing" for k in want if k not in got]
        out += [f"{path}.{k}: not in the recording" for k in got if k not in want]
        for k in want:
            if k in got:
                out += _diff(want[k], got[k], tol, f"{path}.{k}", str(k), key)
        return out
    if isinstance(want, list):
        if not isinstance(got, list) or len(got) != len(want):
            return [f"{path}: {got!r} != {want!r}"]
        return [e for i, (w, g) in enumerate(zip(want, got))
                for e in _diff(w, g, tol, f"{path}[{i}]", key, parent)]
    if isinstance(want, bool) or want is None or isinstance(want, str):
        return [] if got == want else [f"{path}: {got!r} != {want!r}"]
    if isinstance(got, bool) or not isinstance(got, (int, float)):
        return [f"{path}: {got!r} is not a number (recorded {want!r})"]
    return [] if _close(key, parent, float(want), float(got), tol) else [
        f"{path}: {got!r} != {want!r} (Δ {float(got) - float(want):.3e})"]


DELTAS = REC.FIXTURE.parent / "u2_deltas.json"
DRIVER_TEST_ID = "test_u2_pre_numbers_recorded.py::test_the_driver_evidence_reproduces"


def _with_recorded_deltas(want: dict, section: str, test_id: str, tol: dict,
                          rows: list[dict] | None = None) -> dict:
    """
    `want` (a frozen section) with each recorded delta of `test_id` applied:
    the row's `figure` is `<section>.<dotted path>`, its `pre` must be the
    frozen value (within the fixture's tolerance: a row cannot paper over a
    different number) and its `post` replaces it. A row with a `post_figure`
    (a sibling path in the same section) moves the figure to that key: the
    frozen key is gone and the post is read under the new one (gate U2-WP7
    X5 (c): a key that would otherwise misname what it holds). A row on a
    path the section does not have fails.
    """
    rows = rows if rows is not None else json.loads(DELTAS.read_text(encoding="utf-8"))["deltas"]
    out = json.loads(json.dumps(want))
    for r in rows:
        if r.get("test") != test_id or not str(r.get("figure", "")).startswith(f"{section}."):
            continue
        *parents, leaf = r["figure"].split(".")[1:]
        node = out
        for k in parents:
            node = node[k]
        assert leaf in node, f"{r['figure']}: not a frozen figure"
        assert _close(leaf, parents[-1] if parents else "", float(r["pre"]), float(node[leaf]),
                      tol), f"{r['figure']}: the row's pre {r['pre']!r} is not the frozen " \
                            f"{node[leaf]!r}"
        if r.get("post_figure"):
            *new_parents, new_leaf = r["post_figure"].split(".")[1:]
            assert r["post_figure"].startswith(f"{section}.") and new_parents == parents, \
                f"{r['figure']}: post_figure {r['post_figure']!r} is not a sibling key"
            assert new_leaf not in node, f"{r['post_figure']}: already a frozen figure"
            del node[leaf]
            node[new_leaf] = r["post"]
        else:
            node[leaf] = r["post"]
    return out


def _json_round_trip(obj):
    """Compare what the fixture would hold (tuples as lists, -0.0 kept)."""
    return json.loads(json.dumps(obj))


@pytest.mark.live_solve
def test_the_golden_s5_table_reproduces():
    frozen = _frozen()
    got = _json_round_trip(REC.golden_s5())
    assert _diff(frozen["golden_s5"], got, frozen["tolerances"], "golden_s5") == []


@pytest.mark.live_solve
def test_both_seed_bills_on_the_golden_dispatch_reproduce():
    frozen = _frozen()
    got = _json_round_trip(REC.seed_bills())
    assert _diff(frozen["seed_bills"], got, frozen["tolerances"], "seed_bills") == []


@pytest.mark.live_solve
def test_the_three_month_toy_reproduces_with_the_gs_wrapper():
    frozen = _frozen()
    got = _json_round_trip(REC.toy_3month())
    assert _diff(frozen["toy_3month"], got, frozen["tolerances"], "toy_3month") == []


@pytest.mark.slow
@pytest.mark.live_solve
def test_the_driver_evidence_reproduces():
    frozen = _frozen()
    got = _json_round_trip(REC.driver_section(REC.run_driver()))
    assert got["checks"]["failed"] == 0, got["checks"]
    want = {k: v for k, v in frozen["driver"].items() if k != "checks"}
    want = _with_recorded_deltas(want, "driver", DRIVER_TEST_ID, frozen["tolerances"])
    got = {k: v for k, v in got.items() if k != "checks"}
    assert _diff(want, got, frozen["tolerances"], "driver") == []


def test_a_recorded_delta_applies_only_on_its_frozen_pre():
    """
    The delta mechanism cannot re-freeze silently: a row moves exactly its
    figure from its frozen `pre` to its `post`, and a row whose `pre` is not
    the frozen value is refused.
    """
    frozen = _frozen()
    tol = frozen["tolerances"]
    want = frozen["driver"]
    pre = want["upfront_cost_series_eur_per_mw"]
    row = {"test": "t", "figure": "driver.upfront_cost_series_eur_per_mw", "pre": pre,
           "post": 730000.0}
    got = _with_recorded_deltas(want, "driver", "t", tol, [row])
    assert got["upfront_cost_series_eur_per_mw"] == 730000.0
    assert _diff(want, got, tol, "driver") == [
        f"driver.upfront_cost_series_eur_per_mw: 730000.0 != {pre!r} (Δ {730000.0 - pre:.3e})"]
    with pytest.raises(AssertionError, match="is not the frozen"):
        _with_recorded_deltas(want, "driver", "t", tol, [{**row, "pre": pre + 1.0}])
    assert _with_recorded_deltas(want, "driver", "other", tol, [row]) == want


def test_a_recorded_delta_with_a_post_figure_renames_the_key():
    """
    Gate U2-WP7 X5 (c): the driver's case LCOS is the engine's, charging
    included, so it is recorded under `lcos_finance_engine`; the delta row
    moves the frozen `lcos_excl_charging` there, and the comparison then
    wants the new key and refuses the old one.
    """
    frozen = _frozen()
    tol = frozen["tolerances"]
    want = frozen["driver"]
    [row] = [r for r in json.loads(DELTAS.read_text(encoding="utf-8"))["deltas"]
             if r.get("test") == DRIVER_TEST_ID
             and r["figure"] == "driver.case_kpis.lcos_excl_charging"]
    assert row["post_figure"] == "driver.case_kpis.lcos_finance_engine"
    got = _with_recorded_deltas(want, "driver", DRIVER_TEST_ID, tol, [row])
    assert "lcos_excl_charging" not in got["case_kpis"]
    assert got["case_kpis"]["lcos_finance_engine"] == row["post"]
    assert want["case_kpis"]["lcos_excl_charging"] == row["pre"]
    stale = {**got["case_kpis"], "lcos_excl_charging": row["post"]}
    assert _diff(got["case_kpis"], stale, tol) == [".lcos_excl_charging: not in the recording"]
    with pytest.raises(AssertionError, match="not a sibling key"):
        _with_recorded_deltas(want, "driver", DRIVER_TEST_ID, tol,
                              [{**row, "post_figure": "driver.lcos_finance_engine"}])


def test_the_comparison_goes_red_on_a_one_euro_npv_move():
    """The mutation, kept as a test: 1 EUR on a frozen NPV is outside tolerance."""
    frozen = _frozen()
    tol = frozen["tolerances"]
    for section in (frozen["golden_s5"]["bess_2h"], frozen["golden_s5"]["bess_pv_2h"],
                    frozen["driver"]["case_kpis"]):
        moved = {**section, "npv": section["npv"] + 1.0}
        assert _diff(section, moved, tol) == [f".npv: {moved['npv']!r} != {section['npv']!r} "
                                              f"(Δ {1.0:.3e})"]
    peaks = frozen["toy_3month"]["peak_import_mw"]
    # A peak is MW: 1e-4 MW, not the arithmetic tolerance.
    assert _diff(peaks, {k: v + 2e-4 for k, v in peaks.items()}, tol, "peak_import_mw",
                 "peak_import_mw") != []
    assert _diff(peaks, {k: v + 5e-5 for k, v in peaks.items()}, tol, "peak_import_mw",
                 "peak_import_mw") == []
