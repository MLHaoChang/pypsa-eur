# Nine economic surfaces, cross-checked against a solved golden network

**Date:** 2026-08-01 (opened) — **2026-08-03 (closed out, Task 9)**
**Method:** `pypsa-gui/backend/tests/golden/fixture.py`'s golden network
(Generator `gas` [overnight_cost], Generator `solar` [direct capital_cost],
Line `L_ab` [direct capital_cost], Link `electrolyzer` [overnight_cost],
StorageUnit `bess` [zero cost]), solved for real with HiGHS, multi-period
(2030 with 5 years, 2035 with 10 years, discount rate 7%). Every number below
was read directly off the running endpoints in
`pypsa-gui/backend/tests/test_golden_economics.py`, not inferred from code
— except where a paragraph is explicitly marked "inferred from a code read"
(no live measurement taken).

**FINAL STATUS (Task 9, 2026-08-03):** Both wrong-number defects found by
this investigation are now FIXED and MEASURED-clean:

- Asset Detail CAPEX (`asset_results` / `asset_results_xlsx`, §7–§8) — fixed
  in Task 5.
- `economics_by_carrier` / `compare_economics` 365× CAPEX overstatement
  (§4, §9) — fixed in Task 9, by making `routers/compare.py::_safe_capital_cost`
  delegate entirely to `services.solver_service.periodized_capital_costs`
  (the resolution `asset_economics` / `cost_breakdown` / `asset_costs` / Asset
  Detail all already used) instead of hand-rolling
  `overnight_cost * annuity(rate, lifetime)` with no `nyears` scaling.
  `test_economics_by_carrier_h2_capex_agrees_with_asset_economics`'s
  `xfail(strict=True)` marker has been REMOVED (not widened, not skipped) and
  the test now passes for real — MEASURED: `economics_by_carrier`'s `h2`
  carrier and `compare_economics`'s `per_asset_lcoh[electrolyzer]` both now
  report `166249.77136776928` EUR, identical to the last printed digit to
  `asset_economics`'s `fixed_cost_eur` and to the independent oracle. Ratio
  1.00× (was 365.00×). Full backend suite: **1957 passed, 1 skipped, 0
  failed** (`pixi run gui-tests`, 2026-08-03) — no other `test_*.py` file in
  the pytest-collected suite had encoded the pre-fix (365×-wrong) value as an
  expectation. Two standalone `qa_*.py` scripts (NOT pytest-collected —
  `pytest.ini`'s `python_files = test_*.py` excludes them, and they are not
  part of any required verification gate) did encode the old formula and were
  updated for consistency: `qa_safe_capital_cost.py` (probed
  `_safe_capital_cost`'s old hand-rolled-annuity signature directly, now
  rewritten to test the new lookup-into-`periodized_capital_costs` contract)
  and `qa_results_summary_compare.py` (one hand-derived `expected_capex`
  assertion omitted the `n.nyears` factor, coincidentally matching the old
  bug's omission — "two wrongs cancelling out" — now corrected to multiply by
  `n.nyears` explicitly).

Remaining open items are coverage/shape mismatches, not wrong numbers — see
"Deferred" in the Summary table at the bottom. Two surfaces (`asset_costs`,
`lcoh`) agreed cleanly from the start and needed no fix; that is recorded
explicitly below, not left to be inferred from the absence of a complaint.

**Original 2026-08-01 status (superseded by the above):** 2 wrong-number
defects found (1 already tracked, 1 new), 2 shape/coverage-claim mismatches
documented, everything else measured to agree.

## Headline numbers

For reference, the correct horizon-total CAPEX (2030–2035, 15 modelled
years) for every fixture asset, per `asset_economics` and the independent
`tests/golden/oracle.py` formulas (which agree exactly — see
`test_asset_economics_electrolyzer_capex_matches_the_oracle`):

| Asset | Class | Correct horizon CAPEX | Why |
|---|---|---|---|
| `gas` | Generator | €0.00 | LP-optimal capacity is 0 MW (solar alone clears the load) |
| `solar` | Generator | €82,500,000.00 | direct `capital_cost=27,500`, non-extendable, `p_nom=200` |
| `bess` | StorageUnit | €0.00 | genuinely zero-cost asset (`capital_cost=0` set deliberately) |
| `electrolyzer` | Link | €166,249.77 | `overnight_cost=1,500,000`, annuitised, `p_nom_opt≈28.57` |
| `L_ab` | Line | €7,500,000,000.00 | direct `capital_cost=1,000,000`, `s_nom=500`, not extendable |

`gas`'s LP-chosen capacity of 0 MW is a property of the fixture's solve
(HiGHS declines to build gas because solar is cheaper and sufficient), not
something either this task or an earlier one controls. It makes `gas` a weak
test subject for anything multiplied by capacity — a broken formula and a
correct one both land on zero — so every check below that needs to exercise
the annuity/CRF arithmetic for real uses `electrolyzer` instead, which the LP
DOES build.

## Surface-by-surface

| # | Surface | Per-asset? | Checked against | Result |
|---|---|---|---|---|
| 1 | `asset_economics` (`/results/asset_economics`) | Yes — the baseline | independent oracle (`gas`, `electrolyzer`) | **AGREE** (exact match, see below) |
| 2 | `cost_breakdown` (`/results/cost_breakdown`) | No — by (class[, carrier]) only | oracle (Line `L_ab`), `asset_economics` (`solar`, via carrier coincidence) | **AGREE** where checked; shape mismatch elsewhere |
| 3 | `statistics` (`/results/statistics`) | No — by (class, carrier) only | `asset_economics` (`electrolyzer`, via carrier `H2`) | **AGREE** |
| 4 | `economics_by_carrier` (`/results/economics_by_carrier`) | No — by carrier only | `asset_economics` (`electrolyzer`, via carrier `h2`) | **FIXED (Task 9) — was wrong number, 365× too high on this fixture; general factor was `1/n.nyears`, see §4. Now AGREE, exact match** |
| 5 | `lcoh` (`/results/lcoh`) | Yes, for electrolyser-like Links | `asset_economics` (`electrolyzer`) | **AGREE** |
| 6 | `asset_costs` (`/api/simulation/asset_costs`) | Yes, for every class | `asset_economics` (`gas`, `solar`, `bess`, `electrolyzer`), oracle (`L_ab`) | **AGREE**, all 5 assets |
| 7 | `asset_results` (Asset Detail, `capex_annual`/`fixed_cost_eur`) | Yes | `asset_economics` (`electrolyzer`) | **FIXED (Task 5) — was wrong number, 100% low. Now AGREE** |
| 8 | `asset_results_xlsx` | Yes (same compute path as #7) | code-verified only, not re-measured | inherits #7's fix (same compute path) |
| 9 | `compare_economics` (`_compute_economics_summary`, feeds Compare tab) | Partial — `by_carrier` no, `per_asset_lcoh` Link-only | `asset_economics` (`electrolyzer`, via `per_asset_lcoh`) | **CAPEX number FIXED (Task 9, same root cause as #4) — now AGREE, exact match.** Coverage-claim mismatch (`per_asset_lcoh` is Link-only) remains DEFERRED — documentation-only, no field to fix it against |

Nine surfaces, nine rows. Zero now carry a wrong number — both defects this
investigation found are fixed (§4/§9 here, plus the Asset-Detail defect from
Task 5). The rest that were directly comparable agreed from the start; three
of the nine (`cost_breakdown`,
`statistics`, `economics_by_carrier`) and part of a fourth
(`compare_economics.by_carrier`) simply don't have a per-asset field to
compare in the first place.

## 1. `asset_economics` — the baseline, verified against the oracle

`test_asset_economics_electrolyzer_capex_matches_the_oracle` computes the
electrolyzer's expected horizon CAPEX from `tests/golden/oracle.py` alone
(no import of `services/solver_service.py`) and compares it to the live
endpoint:

```
oracle.annualised_capital_cost(overnight_cost=1_500_000, rate=0.07,
                                lifetime=20, snapshots_per_period=24)
    = 387.91613319146165  EUR/MW/yr
oracle.horizon_capex(rate_per_mw=387.916..., p_nom_opt=28.571428571428573,
                      years=(5, 10))
    = 166249.77136776928  EUR

asset_economics["links"][electrolyzer]["fixed_cost_eur"]
    = 166249.77136776928  EUR
```

Identical to the last printed digit. `test_asset_economics_gas_capex_matches_the_oracle`
(the brief's original check) also passes, but only confirms `asset_economics`
returns a clean `0.0` for a sized-to-zero extendable asset — `gas`'s 0 MW
optimum means it can't exercise the annuity formula's magnitude.
`test_a_genuinely_zero_cost_asset_reports_zero_not_unresolvable` confirms
`bess` (a deliberately zero-`capital_cost` asset) also reports a clean `0.0`,
not `None`/`NaN`/an error — the "zero vs unresolvable" distinction the brief
called out. `test_asset_economics_reports_the_link_class_at_all` guards the
regression that started this whole investigation (no `links` key at all).

## 2. `cost_breakdown` — no per-asset field; carrier-level spot check agrees

**Shape mismatch, not a wrong number.** The real payload
(`routers/results.py:159`) has:

- `by_component`: one row per **component class** (`{"component": "Generator", "capex": 82500000.0, ...}`) — `gas` and `solar` are summed together.
- `by_carrier`: one row per **(component class, carrier)** pair — one level finer, still not per-asset.

Neither row carries a `name` field. The task-4 brief's worked example for
`_find_component_capex` assumed `by_component` rows had one — they don't; the
helper was rewritten to search `by_carrier`, matching `carrier == name`. That
only works because in this fixture `gas` and `solar` both happen to have a
carrier equal to their own name, and each is the only Generator on its
carrier — a coincidence of the fixture, not a general capability (documented
in the helper's docstring so nobody reuses it for e.g. two generators sharing
a carrier).

**The test targets `solar`, not `gas`.** `gas`'s LP-optimal capacity is 0 in
this fixture (§1), so a `gas`-only check would only ever compare `0.0` to
`0.0` — passing against a `cost_breakdown` that reported zero for
*everything*, which is exactly the class of vacuous check this task is
supposed to catch, not commit. `solar` carries a real, non-zero cost. Measured:

```
cost_breakdown.by_carrier: {component: "Generator", carrier: "solar", capex: 82500000.0, ...}
asset_economics solar fixed_cost_eur: 82500000.0
```

**AGREE**, non-trivially (`test_cost_breakdown_agrees_with_asset_economics_on_solar`).
A second, independent nonzero check was added for the one class
`cost_breakdown` DOES resolve one-to-one in this fixture — Line, since there
is only one (`L_ab`), so its class total IS its per-asset total:

```
cost_breakdown.by_component[Line].capex = 7,500,000,000.00
oracle.horizon_capex(1,000,000, 500.0, (5, 10)) = 7,500,000,000.00
```

**AGREE**, exactly (`test_line_capex_agrees_with_the_oracle_across_cost_breakdown_and_asset_costs`).

## 3. `statistics` — same structural limitation, spot check agrees

`/results/statistics` is a raw `df_to_json(n.statistics())` pass-through,
indexed by `(level_0=component_class, level_1=carrier)` with one column per
`"<Metric> | <period>"`. No per-asset field, same as `cost_breakdown`.
`"Capital Expenditure | <period>"` is PyPSA's own **per-period annual rate**
(not yet horizon-scaled):

```
statistics[Link/H2]["Capital Expenditure | 2030"] = 11083.31809
statistics[Link/H2]["Capital Expenditure | 2035"] = 11083.31809   (same — the
    rate itself doesn't vary by period; p_nom_opt and years do)
asset_economics electrolyzer fixed_cost_eur / 15 = 11083.318091184619
```

**AGREE** for both periods (`test_statistics_h2_capex_matches_asset_economics`).

## 4. `economics_by_carrier` — FIXED (Task 9). Was: wrong number, 365× too high ON THIS FIXTURE

**Status as of 2026-08-03: FIXED.** The bug described below (found 2026-08-01)
was corrected in Task 9 by making `_safe_capital_cost` delegate to
`services.solver_service.periodized_capital_costs` instead of hand-rolling
the annuity — see the "FIXED" postscript at the end of this section for the
fix and its verification. The rest of this section is kept as the historical
record of what was found and how, unedited from the original investigation.

This was the significant new finding from the original task. `economics_by_carrier`
(`routers/results.py:771`) delegates to
`routers/compare.py::_compute_economics_summary`, which prices an
`overnight_cost`-parameterised asset via a **second, independent**
implementation of the annuity math —
`_safe_capital_cost()` at `routers/compare.py:341-374`:

```python
if oc and oc > 0 and lt > 0:
    return oc * _annuity(dr, lt)      # <-- no horizon-fraction scaling
```

**The missing factor, precisely.** PyPSA's own `capital_cost` accessor
(the one `asset_economics`/`asset_costs`/`cost_breakdown` all read, correctly)
resolves an `overnight_cost`-parameterised asset via
`pypsa.costs.periodized_cost(..., nyears=n.nyears)`, which multiplies the
annuitised overnight cost by `n.nyears`. `n.nyears`
(`pypsa/network/index.py:648-654`) is:

```
n.nyears = Σ snapshot_weightings["objective"] (per investment period) / 8760
```

`_safe_capital_cost` omits this factor entirely — it returns
`overnight_cost * annuity(rate, lifetime)` with no `nyears` multiplication at
all. **The trigger is the snapshot WEIGHTS, not the snapshot COUNT** — an
earlier draft of this finding conflated the two, which matters because it
flips which networks are affected:

| Network shape | `Σweights` per period | `n.nyears` (= `Σweights/8760`) | Missing factor `1/n.nyears` |
|---|---|---|---|
| This golden fixture: 24 unit-weighted snapshots | 24 | 24/8760 | **365×** |
| A unit-weighted 168-hour representative week | 168 | 168/8760 | **52.14×** |
| A full 8760-snapshot year | 8760 | 1.0 | **1× — invisible** |
| A correctly weighted representative-week sample (`sample_representative_weeks`, `routers/network.py:1467-1469` — weights are set so the weighted total reconstructs the full year, Σ≈8760h) | ≈8760 | ≈1.0 | **≈1× — invisible** |

So a properly weighted representative-week project is **not** affected — the
bug is invisible there because the missing factor happens to be ≈1. What IS
affected is the ordinary UNIT-WEIGHTED small snapshot set (24h, 48h, 168h),
which is the common case in this GUI, not a corner case. The bug also
**returns** at 52.14× if a representative-week project is later promoted to
multi-period: `n.set_snapshots(MultiIndex)` resets `snapshot_weightings` back
to the default 1.0 (documented in `CLAUDE.md`, "`n.set_snapshots(MultiIndex)`
resets snapshot_weightings to default 1.0" / "…in `sample_representative_weeks`"),
silently discarding the rep-week scaling that had been keeping the surface
correct.

On this golden fixture (24 unit-weighted snapshots/period, `n.nyears =
24/8760`), the missing factor is exactly `8760/24 = 365`. Measured:

```
economics_by_carrier["by_carrier"]["h2"]["capex_meur"]["total"] * 1e6
    = 60,681,166.549...   EUR

asset_economics electrolyzer fixed_cost_eur
    = 166,249.77          EUR

ratio: 60,681,166.549 / 166,249.77 = 365.0 (exact)
relative difference: 36,400%
```

Assets priced via a **direct** `capital_cost` (not `overnight_cost`) are
unaffected regardless of snapshot weighting — `_safe_capital_cost`'s `else`
branch just returns the raw column, which is already correctly scaled
(whatever the user typed; no `nyears` multiplication applies to it in PyPSA's
own accessor either). Measured on `solar` (direct `capital_cost=27,500`):

```
economics_by_carrier["by_carrier"]["solar"]["capex_meur"]["total"] * 1e6
    = 82,500,000.00   EUR
asset_economics solar fixed_cost_eur = 82,500,000.00   EUR
```

**AGREE** for `solar`. So the bug is specific to the `overnight_cost` code
path in `_safe_capital_cost`, not the function as a whole.

Was recorded as `pytest.mark.xfail(strict=True)` in
`test_economics_by_carrier_h2_capex_agrees_with_asset_economics`. This was a
**wrong number** (fix), not a shape mismatch — the value returned was too
large by a factor of `1 / n.nyears` for any overnight_cost-parameterised
asset, on any network whose snapshot weights didn't happen to sum to ≈8760
per period.

**FIXED (Task 9, 2026-08-03).** `_safe_capital_cost` (`routers/compare.py`)
no longer hand-rolls `overnight_cost * annuity(rate, lifetime)`. It now
delegates entirely to `services.solver_service.periodized_capital_costs(n,
cfg)` — the SAME resolution `asset_economics`, `cost_breakdown`,
`asset_costs` and (since Task 5) Asset Detail all already used — via a new
`_periodized_lookup(n)` helper that builds that function's per-asset dict
ONCE per network (not once per row: `periodized_capital_costs` walks every
cost-bearing component class in a single pass, so a per-row call would be
O(rows) times more expensive for the identical answer). `_safe_capital_cost`
is now a pure lookup: given a pandas Series `row` (its `.name` is the asset
name), the precomputed `pcc` dict, and which top-level bucket (`"generators"`,
`"links"`, …) the row belongs to, it returns `pcc[comp_attr][name]
["capital_cost"]` or `0.0` if absent. This removes the second, independent
annuity implementation entirely rather than patching a `× 1/n.nyears`
correction factor on top of it — the brief's explicit instruction, because a
correction factor bolted onto a duplicate implementation is exactly the kind
of drift that produced this bug in the first place (`_safe_capital_cost` was
originally written to match `periodized_capital_costs`'s behaviour by
re-deriving it, and drifted the moment PyPSA's `nyears` scaling was added
upstream without a matching update here).

**The overnight-preferred-over-stale-capital_cost behaviour survives
unchanged.** `_safe_capital_cost`'s docstring used to say it deliberately
prefers `overnight_cost` over a non-zero `capital_cost` column "because
networks often carry a stale straight-line figure alongside overnight_cost".
Checked (code read, `services/solver_service.py::periodized_capital_costs`
and PyPSA's own `capital_cost` accessor, `n.c[comp_class].capital_cost`):
`periodized_capital_costs` reads `capital_cost` through that same PyPSA
accessor, which for an asset with `overnight_cost` set returns the
annuitised-and-`nyears`-scaled overnight figure, NOT the raw `capital_cost`
column — i.e. PyPSA's own accessor already makes the identical
overnight-over-stale-column choice `_safe_capital_cost` used to hand-implement.
So there is **no behavioural difference** on that front; delegating changed
only the missing `nyears` factor, not the overnight-vs-capital_cost
preference. This was a finding worth checking and reporting (per the brief),
not one that turned up an actual discrepancy to paper over.

**MEASURED on the golden fixture (2026-08-03, post-fix):**

```
economics_by_carrier["by_carrier"]["h2"]["capex_meur"]["total"] * 1e6
    = 166249.77136776928   EUR

asset_economics electrolyzer fixed_cost_eur
    = 166249.77136776928   EUR

ratio: 1.00 (exact, was 365.00)
```

`test_economics_by_carrier_h2_capex_agrees_with_asset_economics`'s
`xfail(strict=True)` marker has been REMOVED — the test now asserts the
agreement directly and passes for real; leaving the marker in place would
have made the (now-passing) test FAIL, since `strict=True` turns an
unexpected pass into a hard failure.

## 5. `lcoh` — per-Link, agrees

`/results/lcoh` (`routers/results.py:896`) reports one row per
electrolyser-like Link with `capex_eur_per_year` — PyPSA's correctly-scaled
annual rate (it reads `n.c["Link"].capital_cost` inside
`with_periodized_cost_defaults`, the same wrapper `asset_economics` uses, not
a second hand-rolled annuity calculation like `compare.py`'s does):

```
lcoh.rows[electrolyzer].capex_eur_per_year = 11083.31809118462
× sum(GOLDEN_YEARS)=15 = 166249.77136776928

asset_economics electrolyzer fixed_cost_eur = 166249.77136776928
```

**AGREE**, exactly. Included in the automated cross-surface loop
(`ADAPTERS["lcoh"]`). Note: `lcoh` only walks Links whose carrier matches an
electrolyser-like token list (`routers/results.py:939-949`) — narrower than
"every Link" (`coverage.COVERAGE["lcoh"] = {"Link"}` doesn't capture that
subtlety), though the fixture's one Link (carrier `H2`) happens to qualify.

## 6. `asset_costs` — per-asset for every class, agrees on all 5 fixture assets

`/api/simulation/asset_costs` (`services/solver_service.py::periodized_capital_costs`)
is the only surface among the nine that is genuinely per-asset for every
class it returns, keyed by asset **name**. It resolves the annuity through
the SAME `with_periodized_cost_defaults` wrapper `asset_economics` uses —
correctly, unlike `compare.py`'s independent reimplementation. Included in
the automated cross-surface loop; measured to agree on all five assets:

| Asset | `asset_costs` horizon CAPEX | `asset_economics` fixed_cost_eur |
|---|---|---|
| `gas` | €0.00 | €0.00 |
| `solar` | €82,500,000.00 | €82,500,000.00 |
| `bess` | €0.00 | €0.00 |
| `electrolyzer` | €166,249.77 | €166,249.77 |
| `L_ab` (Line — not covered by `asset_economics`, checked against the oracle instead) | €7,500,000,000.00 | oracle: €7,500,000,000.00 |

**AGREE**, all five.

## 7 & 8. `asset_results` / `asset_results_xlsx` (Asset Detail) — wrong number, 100% low (KNOWN, already tracked)

This is the defect the task brief opened with — measured on the user's real
project as Gas 22.3% low, Solar PV 41.7% low, electrolyser €0 against
€27,143,399. It reproduces on the golden fixture too, though not on the
asset the brief's worked example targeted:

`services/asset_results/compute.py::capex_annual` (used for both the
`capex_annual` and, for Link, `fixed_cost_eur` metric ids) is:

```python
def capex_annual(ctx: Ctx):
    cc, opt = _static(ctx, "capital_cost"), nom_capacity_opt(ctx)
    return None if cc is None or opt is None else cc * opt
```

`_static(ctx, "capital_cost")` reads the **raw** `capital_cost` column
directly off the network — never through `periodized_capital_costs` /
`with_periodized_cost_defaults`. For an asset parameterised via
`overnight_cost` with no direct `capital_cost` typed, that raw column is
PyPSA's untouched default of `0.0`.

Measured on `electrolyzer` (overnight_cost, `p_nom_opt≈28.57` — a real,
positive built capacity):

```
Asset Detail scalars["capex_annual"]  =  0.0   EUR/yr
asset_economics fixed_cost_eur / 15   =  11,083.32   EUR/yr

relative difference: 100% low
```

Measured on `solar` (direct `capital_cost`, unaffected by the bug) and `bess`
(genuinely zero-cost) for contrast — both **agree**:

```
Asset Detail scalars["capex_annual"] (solar) = 5,500,000.0
asset_economics fixed_cost_eur / 15 (solar)  = 5,500,000.0     AGREE

Asset Detail scalars["capex_annual"] (bess) = 0.0
asset_economics fixed_cost_eur / 15 (bess)  = 0.0              AGREE
```

**The brief's own worked example targeted Generator `gas`, not `electrolyzer`.**
On this golden fixture that would have been a false negative: `gas`'s LP
optimum is 0 MW, so `0.0 (buggy raw capital_cost) × -0.0 (p_nom_opt) = -0.0`
equals the correct `0.0` — the xfail marker would have unexpectedly PASSED
(`XPASS`), which `strict=True` turns into a hard failure. The test in
`test_golden_economics.py` was retargeted to `electrolyzer`, which the LP
actually builds and which cleanly reproduces the defect.

`asset_results_xlsx` (`services/asset_results/export.py`) imports
`build_response` from the exact same `services/asset_results/service.py`
module `asset_results` uses (`from .service import build_response`), so it
shares this defect by construction. Not independently re-measured at runtime
— exporting and parsing an xlsx workbook just to re-observe a number already
proven identical by the shared code path wasn't worth the added test
complexity, but the equivalence is a direct code read, not a guess.

Recorded as `pytest.mark.xfail(strict=True)` in
`test_asset_detail_capex_agrees_with_asset_economics`. **Wrong number** (fix)
— tracked for Task 5.

## 9. `compare_economics` — CAPEX FIXED (Task 9, same root cause as #4); coverage-claim mismatch remains DEFERRED

**Status as of 2026-08-03: the wrong-number part is FIXED; the
shape/coverage-claim mismatch below is unchanged and DEFERRED** (it was
never a wrong number — see the "Deferred" discussion at the end of this
section for why it stays open).

`compare_economics` is the `economics` field of `GET
/compare/{name}/results-summary`, computed by
`_compute_economics_summary(temp_n, ...)` at `routers/compare.py:2652` — the
**exact same function** `economics_by_carrier` calls (§4). Its
`per_asset_lcoh` list is genuinely per-asset (unlike `by_carrier`) and its
`capex_meur` was built through the same `_safe_capital_cost` /
`_capex_commitment` path, so before the Task 9 fix it inherited the identical
missing-`nyears` overstatement (365× on THIS fixture's unit-weighted
24-snapshot periods — see §4 for why the factor was `1/n.nyears`, not a
fixed 365, and why a correctly weighted representative-week network would
not have shown this). MEASURED, pre-fix (2026-08-01):

```
per_asset_lcoh[electrolyzer].capex_meur.total * 1e6 = 60,681,166.549...  EUR
asset_economics electrolyzer fixed_cost_eur         =    166,249.77     EUR
```

That was a **DISAGREE**, same root cause and same ratio as §4 on this
fixture at the time. **FIXED (Task 9, 2026-08-03)** — same
`_safe_capital_cost` delegation fix described in §4 (this surface calls the
identical function, so one fix closed both). MEASURED, post-fix:

```
per_asset_lcoh[electrolyzer].capex_meur.total * 1e6 = 166249.77136776928  EUR
asset_economics electrolyzer fixed_cost_eur         = 166249.77136776928  EUR

ratio: 1.00 (exact, was 365.00)
```

**AGREE**, exact match. Verified by calling `_compute_economics_summary`
directly on the golden network (the real HTTP endpoint needs a project saved
to disk via `ProjectAccessDep`, which this task did not spin up — the
golden-network call site is identical code, not a different implementation,
so this is a direct measurement of the same function, just not routed
through the disk-backed endpoint).

**Separately, a shape/coverage-claim mismatch — DEFERRED, not a wrong number.**
`coverage.COVERAGE["compare_economics"] = {"Generator", "StorageUnit",
"Link"}` — correct in the sense that `_compute_economics_summary`'s CAPEX
walk touches all three classes (into the carrier-aggregated `by_carrier`).
But the ONLY genuinely per-asset field on this surface, `per_asset_lcoh`, is
**Link-only** (`routers/compare.py:1938`: `if n.links is not None and not
n.links.empty:` — Generators and StorageUnits never enter that list at all).
So "per-asset CAPEX for Generator/StorageUnit via compare_economics" is not
actually obtainable from this surface under any field — only the
carrier-aggregated total is. This was not force-fitted into the cross-surface
loop (`ADAPTERS`); it's documented here instead, per the brief's own guidance
not to bend a surface into a shape it doesn't have.

## Not in the automated cross-surface loop, and why

`test_every_surface_agrees_on_every_covered_asset` drives its comparison from
`ADAPTERS = {"asset_economics", "lcoh", "asset_costs"}`. The other six
surfaces are deliberately absent:

- **`cost_breakdown`, `statistics`, `economics_by_carrier`** — no per-asset
  field exists in any of the three payloads (§2, §3, §4). Spot-checked by
  dedicated tests instead.
- **`asset_results`, `asset_results_xlsx`** — genuinely per-asset, both run
  through the exact `capex_annual` compute path the dedicated
  `test_asset_detail_capex_agrees_with_asset_economics` test already covers
  directly (§7, §8). That test's defect was fixed in Task 5 (its `xfail`
  marker is gone; it now passes for real) — the loop-exclusion reason today
  is "already covered by a dedicated test", not "hiding a known bug behind an
  xfail". Not added to `ADAPTERS` because doing so would duplicate that
  dedicated coverage rather than add anything new.
- **`compare_economics`** — no general per-class adapter is possible
  (§9): `by_carrier` is carrier-aggregated, `per_asset_lcoh` is
  genuinely per-asset but Link-only. (This is the one remaining item in this
  whole investigation that is still open — see the Summary table below.)

These six reasons are recorded as DATA, not comments — `test_golden_economics.py`'s
`NO_ADAPTER_REASONS: dict[str, str]` holds one entry per surface, and
`test_every_surface_agrees_on_every_covered_asset` asserts
`set(ADAPTERS) | set(NO_ADAPTER_REASONS) == set(coverage.SURFACES)` before
doing anything else. Without that assertion, a tenth surface added to
`coverage.SURFACES` in the future would silently fall through
`{sid: ADAPTERS[sid](golden) for sid in cov.SURFACES if sid in ADAPTERS}` and
never be compared — the same silence-reads-as-agreement failure this whole
task exists to eliminate, one level up (at "which surfaces did we even look
at" rather than "does this asset's number agree").

## Line is structurally never checked by the automated loop

`asset_economics` never reports Line at all
(`coverage.EXCLUSIONS[("asset_economics", "Line")]` — lines carry no
dispatchable energy of their own, so per-asset revenue/unit-cost are
undefined). Since the loop's baseline IS `asset_economics`, and the loop only
walks `(class, name)` pairs present in the baseline, Line can never be
exercised by `test_every_surface_agrees_on_every_covered_asset` — regardless
of how many of the other four surfaces (`cost_breakdown`, `statistics`,
`asset_costs`, `asset_results`/`asset_results_xlsx`) claim to cover Line in
`coverage.COVERAGE`. This is a structural property of anchoring the loop on
`asset_economics`, not a bug — but it means Line coverage needed its own,
separate test: `test_line_capex_agrees_with_the_oracle_across_cost_breakdown_and_asset_costs`
checks `L_ab` against the oracle directly through `cost_breakdown` and
`asset_costs`, both of which **agree** (§2, §6).

## Summary — final state (Task 9, 2026-08-03)

This is the authoritative close-out record for the whole investigation
(originally opened 2026-08-01, all nine surfaces now accounted for). Every
row below states whether it was directly MEASURED against a running endpoint
or the oracle, or INFERRED from a code read (used only where re-measuring
would duplicate an existing test without learning anything new — e.g. the
xlsx export sharing its parent's compute path by construction).

### Fixed in this plan

| Finding | Kind | Status |
|---|---|---|
| Asset Detail (`asset_results`/`asset_results_xlsx`) `capex_annual`/`fixed_cost_eur` read raw `capital_cost`, ignoring `overnight_cost` annuitisation — 100% low for the electrolyzer | Wrong number | **FIXED — Task 5.** MEASURED: `test_asset_detail_capex_agrees_with_asset_economics` passes with no `xfail` marker. |
| `economics_by_carrier` / `compare_economics` (`_safe_capital_cost` in `routers/compare.py`) omitted the `1/n.nyears` scaling PyPSA's own `capital_cost` accessor applies for `overnight_cost`-parameterised assets (`n.nyears` = `Σ snapshot_weightings["objective"] per period / 8760`) — 365× too high for the electrolyzer on this fixture's unit-weighted 24-snapshot periods. General factor was `1/n.nyears` = `8760 / (Σ snapshot_weightings["objective"] per period)`: 52.14× on a unit-weighted 168-h week, invisible (≈1×) on a full year or a correctly weighted representative-week sample — and would have returned at 52.14× if a rep-week project were later promoted to multi-period (weights reset to 1.0). Affected the common unit-weighted small-snapshot case, not just this fixture | Wrong number | **FIXED — Task 9.** `_safe_capital_cost` now delegates to `services.solver_service.periodized_capital_costs` instead of hand-rolling the annuity — no `× 1/n.nyears` patch, the duplicate implementation was removed. MEASURED: both surfaces now report `166249.77136776928` EUR, exact match to `asset_economics` and the oracle (ratio 1.00×, was 365.00×). `xfail(strict=True)` marker REMOVED from `test_economics_by_carrier_h2_capex_agrees_with_asset_economics`; it passes for real. |

### Deferred — with reason

| Finding | Kind | Status |
|---|---|---|
| `compare_economics.per_asset_lcoh` is Link-only, but `coverage.COVERAGE["compare_economics"]` lists it as covering Generator/StorageUnit/Link too | Shape / coverage-claim mismatch | **DEFERRED.** MEASURED (code read of `routers/compare.py:1938`, `if n.links is not None and not n.links.empty:` — Generators and StorageUnits never enter the `per_asset_lcoh` list). No per-asset Generator/StorageUnit CAPEX field exists on this surface under any name to assert against — there is nothing to fix without inventing a field the endpoint doesn't have. Reason to leave open: this is a coverage CLAIM inaccuracy (`coverage.COVERAGE` overclaims), not a wrong number reaching a user; correcting it is either (a) narrow `coverage.COVERAGE["compare_economics"]` to `{"Link"}` to match reality, or (b) add genuine Generator/StorageUnit per-asset LCOE/cost fields to `compare_economics` — the second is a feature addition, not a bug fix, and out of this plan's scope (fixing wrong numbers). |
| `cost_breakdown`, `statistics`, `economics_by_carrier.by_carrier`, `compare_economics.by_carrier` have no per-asset field at all (aggregate by class and/or carrier only) | Shape mismatch | **DEFERRED — by design, not a defect.** MEASURED: spot-checked via carrier-coincidence (`cost_breakdown` vs `solar`) or class-total-equals-single-asset (`cost_breakdown`/`asset_costs` vs `L_ab`, the fixture's only Line) where the fixture allows it — all such spot checks **AGREE**. These four aggregate-only payload shapes are the brief's own explicit example of "a surface that must not be forced into a per-asset shape it doesn't have" (see task-4 brief); reason to leave open: turning them per-asset would be a new feature (an API shape change), not a fix to a wrong number, and every number these surfaces DO report has been checked and agrees. |

**Total deferred residuals: 2** (both shape/coverage-claim mismatches, zero
wrong numbers).

### Agreed — measured, no fix needed

Explicit record so this list isn't confused with "not yet checked":

| Surface | Checked against | Result |
|---|---|---|
| `asset_economics` (`/results/asset_economics`) | independent oracle, `gas` + `electrolyzer` | **AGREE**, exact match — the baseline every other row is measured against |
| `asset_costs` (`/api/simulation/asset_costs`) | `asset_economics` (`gas`, `solar`, `bess`, `electrolyzer`) + oracle (`L_ab`) | **AGREE**, all 5 fixture assets, exact match — the only surface genuinely per-asset for every class it returns |
| `lcoh` (`/results/lcoh`) | `asset_economics` (`electrolyzer`) | **AGREE**, exact match |
| `cost_breakdown` (spot-checked, not per-asset) | `asset_economics` (`solar`), oracle (`L_ab`) | **AGREE** on every value checked |
| `statistics` (spot-checked, not per-asset) | `asset_economics` (`electrolyzer`, via carrier `H2`) | **AGREE** for both investment periods |
| `economics_by_carrier` (fixed in Task 9) | `asset_economics` (`electrolyzer`, via carrier `h2`) | **AGREE**, exact match, post-fix |
| `compare_economics.per_asset_lcoh` (fixed in Task 9) | `asset_economics` (`electrolyzer`) | **AGREE**, exact match, post-fix |
| `asset_results` / `asset_results_xlsx` (fixed in Task 5) | `asset_economics` (`electrolyzer`, `solar`, `bess`) | **AGREE** |

Nine surfaces went in; nine surfaces came out agreeing on every number they
report. Two needed a fix to get there (Task 5, Task 9); the rest agreed from
the first measurement.
