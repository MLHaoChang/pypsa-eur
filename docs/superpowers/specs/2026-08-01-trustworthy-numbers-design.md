# Structurally trustworthy numbers — design

**Date:** 2026-08-01
**Status:** design, awaiting review
**Roadmap item:** 1 of 4 (see the roadmap section at the end)

## The problem, measured

On 2026-07-31, two tabs of the running app disagreed about the same metric, on
the user's own `4_nodes_system with electrolyzer`:

| Asset | Asset Detail "Annualised CAPEX" | Economics tab | Under-report |
|---|---:|---:|---:|
| Gas_B2 | €41,744,859 | €53,732,219 | **22.3%** |
| PV_B3 | €21,411,051 | €36,745,868 | **41.7%** |
| Electrolyzer 1 | €0 | €27,143,399 | **100%** |

Both are labelled "Annualised CAPEX". Both are user-visible. `registry.py:93`
declares the Asset Detail one with `formula="capital_cost × p_nom_opt"`, and
`compute.py:297` implements exactly that — reading the raw `capital_cost`
column. The correct value only exists inside `periodized_capital_costs`,
because the user parameterised these assets via `overnight_cost` and PyPSA
derives `capital_cost` from `overnight_cost × annuity(discount_rate, lifetime)`
at solve time only.

Asset Detail was written the day before it was measured. This is not legacy
debt: the pattern reproduces in new code, written by someone who had no way to
know the rule existed.

### It is one pattern with two mechanisms

Five incidents in roughly 24 hours share a symptom — *a value that is absent,
defaulted or derived-elsewhere renders as a confident number* — but they split
into two fixable mechanisms plus one already handled:

1. **Resolution.** The true value is derived elsewhere. → the table above;
   `n.statistics()` returning NaN CAPEX for every asset parameterised via
   `overnight_cost` (measured: all three NaN until `discount_rate` is injected).
2. **Coverage.** A whole component class is absent from a view. → the Link
   block missing from `/results/asset_economics` entirely;
   `_check_carrier_emissions` scanning only generators.
3. **Staleness** — correct value, stale inputs (line parameters after a
   coordinate change). **Out of scope**: this already got its countermeasure
   when the rescale prompt shipped.

### Why nothing caught it

Every existing test asserts one surface against itself. Nothing asserts two
surfaces against each other, and nothing asserts either against an independent
oracle. The two real bugs this session were both found by hand-comparing two
endpoints — a method that worked twice in two days and is not automated.

## Goals

Make it structurally impossible for a cost-bearing number to be **silently
wrong** (resolution) or **silently absent** (coverage), across every economic
surface.

## Non-goals

- Staleness (mechanism 3) — already addressed.
- Distinguishing "unset" from "deliberate zero" for `capital_cost`,
  `marginal_cost`, `fom_cost`. **Measured as infeasible**, see below.
- Extending the `registry.py` metric-declaration pattern to `results.py` and
  `compare.py`. Better architecture, roughly 5× the work; a candidate for a
  later roadmap item, deliberately not smuggled in here.
- Any performance or payload-size work (that is roadmap item 2).

## The nine economic surfaces

```
/api/results/cost_breakdown              /api/results/asset_economics
/api/results/economics_by_carrier        /api/results/lcoh
/api/results/statistics                  /api/simulation/asset_costs
/api/results/asset/{component_class}/{name}
/api/results/asset/{component_class}/{name}/export.xlsx
compare.py per-carrier + per-asset economics
```

`export.xlsx` is in the set deliberately. An export that disagrees with the
screen it came from is the worst version of this bug, because it is the number
that leaves the building.

## Mechanism: one golden fixture, one independent oracle

### The fixture

A single small network, **solved for real with HiGHS**, not a frozen `.nc` and
not a hand-built solved state.

Rationale: 11 backend test files already call `.optimize()` for real and only
2 fake a solved state, so this follows the established convention. More
importantly, a
hand-built fixture risks encoding a misunderstanding of PyPSA's sign and
weighting conventions — after which every surface agrees with every other and
all of them are wrong. Letting PyPSA produce the dispatch removes that failure
mode. No binary fixture is committed anywhere in this repo today — verified: no
tracked `.nc`, `.pkl`, `.xlsx` or `.zip` — and this design does not introduce
the first one.

**Composition — only shapes that have actually failed:**

| Element | Why it is there |
|---|---|
| Generator with `overnight_cost` set, `capital_cost` unset, `discount_rate` supplied only via solver config | The exact shape that produced the 22–100% under-report |
| Line with `capital_cost` set directly, no `overnight_cost` | The shape that already works — proves the fix does not break it |
| Link (electrolyser, bus0 → bus1, efficiency < 1) | The class that was missing entirely; also two-sided economics |
| StorageUnit with a genuine zero cost | Proves a real zero still reports zero and is not flagged as broken |
| ≥2 investment periods with **different** `years` weightings | The 22% gap appears only in multi-period; `annuity × years` is where it goes wrong |
| One extendable and one non-extendable asset | CAPEX attribution differs between them |

Roughly 24 snapshots per period. Solves in well under a second.

**Deliberately excluded:** Store, Transformer, multi-port Link, AC PF. Each
adds fixture complexity for a shape that has not failed. The fixture grows by
incident, not by imagination — when one of these fails, it is added then.

**Accepted limitation:** the fixture's composition is load-bearing and
backward-looking. A metric on a component the fixture lacks stays unguarded.
This is a conscious trade against a maximal fixture that would be slower and
more fragile, and therefore avoided.

### The oracle

Two halves, both required.

**1. Absolute anchors, independently derived.** The test writes out the
arithmetic itself:

```
annuity(r, n)   = r / (1 - (1 + r) ** -n)
expected_capex  = overnight_cost
                × annuity(discount_rate, lifetime)
                × p_nom_opt
                × years_weighting[period]
```

**The test must never call `periodized_capital_costs` or
`with_periodized_cost_defaults` to build its own expectation.** An oracle that
shares an implementation with its subject asserts nothing. This session already
produced one such tautology (a changelog test whose `any()` ran unscoped over a
never-reset deque and passed against a byte-identical message from an earlier
test), which is why the rule is stated as a prohibition rather than a
preference.

**2. Cross-view agreement.** For every (asset, metric) pair the fixture
defines, every surface that reports it must report the same value within a
stated tolerance.

Anchors catch *consistent* wrongness — all surfaces agreeing on a wrong
annuity. Agreement catches coverage gaps and new surfaces that drift. Neither
alone is sufficient: this session's Economics-vs-LCOH check found agreement at
€246.02, and both would have agreed just as neatly had the annuity been wrong.

**Tolerance:** exact equality is wrong for floating-point sums over 26k
snapshots. Use a relative tolerance of 1e-9 for values derived from the same
dispatch, and state it once in a shared helper rather than per-assertion.

**Upstream-drift cost, accepted:** when PyPSA changes an annuity or weighting
convention, this test fails and it will be briefly ambiguous whether the app or
the oracle is wrong. That is the intended behaviour — a silent convention change
is what produced the NaN CAPEX — but it is real maintenance, and it will land on
a day nobody planned for it.

### The coverage matrix

Exhaustive-by-default:

```python
COVERAGE = {
    "asset_economics": {"Generator", "StorageUnit", "Store", "Link"},
    "cost_breakdown":  {"Generator", "StorageUnit", "Store", "Link", "Line"},
    "lcoh":            {"Link"},
    ...
}

EXCLUSIONS = {
    ("lcoh", "Generator"): "LCOH is per-electrolyser by design; a generator "
                           "has no hydrogen output to levelise.",
    ...
}
```

Every component class present in the fixture must, for every surface, either
appear in that surface's `COVERAGE` set or have a written reason in
`EXCLUSIONS`. Anything else fails the test.

Opt-in was rejected because it reproduces the exact failure being fixed:
forgetting to opt Links into `asset_economics` produces silence, and silence is
how the bug shipped. Exhaustive-by-default inverts the default — adding a class
to the fixture fails every surface until someone either implements it or writes
down why it does not apply, so an absence becomes a decision with a name on it.

Secondary benefit: `EXCLUSIONS` becomes the honest, reviewable answer to "what
does this app actually report?", which currently cannot be stated without
reading nine endpoints.

### Frontend — one step past the boundary

The backend can be perfectly self-consistent while the frontend maps the wrong
field. This is live code, written 2026-07-31:

```ts
revenue_eur:     l.gross_revenue_eur,   // GROSS into the revenue column
charge_cost_eur: l.input_cost_eur,
```

Deliberate and documented — and swapping those two lines would show a wildly
wrong net profit on the Economics tab while all nine backend surfaces still
agreed perfectly.

**In scope:** feed the golden fixture's actual payload through the pure mapping
functions — `makeGenRow`, `makeSURow`, `makeStoreRow`, `makeLinkRow` — and
assert each backend field lands in the intended column. Pure functions, no
jsdom, no React Flow, fast.

**Out of scope:** assertions on rendered DOM text. Those break when a column
header changes, which trains people to update tests without reading them.

**Fixture-drift risk and its mitigation:** the committed JSON payload can drift
from what the backend actually returns, after which the frontend test passes
against a fiction. The backend test writes the payload to
`frontend/src/pages/results/__fixtures__/asset-economics.golden.json` on every
run, and CI fails if that file is dirty afterwards. That makes drift a failing
build rather than a silent lie.

### Unset-vs-zero — NaN-defaulted fields only

Measured defaults, from `n.components[cls].defaults`:

| field | default | "never set" detectable in memory? |
|---|---|---|
| `capital_cost` | `0.0` | **no** |
| `marginal_cost` | `0.0` | **no** |
| `fom_cost` | `0.0` | **no** |
| `overnight_cost` | `NaN` | yes |
| `discount_rate` | `NaN` | yes |
| `lifetime` | `inf` | yes |

PyPSA splits its own defaults: price-like fields collapse to `0.0`,
parameter-like fields to `NaN`. And absence survives only on disk — measured:
`links_marginal_cost` was absent from the user's netCDF entirely (PyPSA omits
all-default columns) and still read `0.0` in memory after load.

**Therefore the zero-defaulted fields are out of scope, and the reason is
recorded here so the next person does not spend an afternoon rediscovering that
PyPSA erased the evidence at load time.**

**In scope:** the NaN-defaulted fields, where detection is possible and where
the real damage occurred. When a derived cost cannot be resolved because an
input is unset, the surface states the reason instead of returning `0` or
`NaN`:

> CAPEX cannot be annualised: `discount_rate` is unset on this asset and no
> solver-config default applies.

This is the highest-harm instance — `discount_rate = NaN` is what turned every
annuity into `NaN` and made `n.statistics()` report €0 CAPEX for the user's
generators while the LP itself had costed them correctly.

## Execution: the discovery gate

The remediation is **not** sized, and this design does not pretend otherwise.
Two of nine surfaces have been measured against each other; the other seven
have not.

**Phase 1 — bounded.** Build the fixture, the oracle, the coverage matrix and
the frontend mapping test. Run across all nine surfaces. Produce the
disagreement list as the first deliverable, in the repo.

**Phase 2 — sized by phase 1's evidence.** Triage against one rule:

- **A user-visible number is wrong → fix in this pass.**
- **A shape, naming or rounding mismatch → defer, and record it.**

Asset Detail's `capex_annual` is in scope regardless: already known, already
quantified, already user-visible.

This ordering means the certain work has a certain cost, and the uncertain work
gets sized with evidence before it is committed to. The scope decision lands
midway through, with data, rather than now, without.

## File structure

| Path | Responsibility |
|---|---|
| `backend/tests/golden/__init__.py` | package marker |
| `backend/tests/golden/fixture.py` | builds + solves the golden network; session-scoped |
| `backend/tests/golden/oracle.py` | independent arithmetic — annuity, expected values. Imports nothing from `services/` |
| `backend/tests/golden/coverage.py` | `COVERAGE` + `EXCLUSIONS` tables |
| `backend/tests/test_golden_economics.py` | the assertions across all nine surfaces |
| `frontend/src/pages/results/__fixtures__/asset-economics.golden.json` | payload emitted by the backend test |
| `frontend/src/pages/results/Economics.mapping.test.ts` | pure mapping-function assertions |

`oracle.py` importing nothing from `services/` is a structural guarantee of the
independence rule, not merely a convention — a reviewer can verify it by
reading the import block.

## Risks

**Unbounded phase 2.** Stated above; the gate exists to convert it into a
sized decision rather than a surprise.

**Concurrent session ownership.** `services/asset_results/compute.py` — which
holds the primary bug at line 297 — was last committed at 08:58 on 2026-08-01
by the concurrent session's Asset Detail review pass (`e1f8dc47`). Coordinate
before editing it; check `git status` and the branch immediately before any
commit, per CLAUDE.md.

**Fixture solve time in CI.** One HiGHS solve per session. Measured comparable
fixtures solve in well under a second; if it regresses, the fixture has grown
past "moderate" and should be cut back rather than cached.

## Where this sits in the roadmap

1. **Structurally trustworthy numbers** — this spec.
2. **Give `/results/*` a range and a resolution.** Measured: 200 assets ×
   26,280 snapshots is a **101.5 MB** single JSON response; 1000 × 8,760 is
   168 MB. No endpoint accepts a snapshot range, and no downsampling exists
   anywhere in the codebase. Backend compute is not the constraint — 1000 buses
   returns in 12 ms.
3. **Study → one deliverable.** Solved network to a single document:
   assumptions, results, figures, scenario comparison.
4. **Modelling depth, chosen from evidence after 1–3.** Deliberately unspecified.
   SCLOPF, vintages, myopic/perfect foresight, curtailment cost and VOLL are
   already implemented, so the depth gap is narrower than it appears.
