# Phase 12 — ELCC as a second opinion on the reserve margin (plan, v2)

Supersedes **v1** (`2026-08-30-fmea-phase12-elcc-derating.md`), which its
review rejected *"not as scoped"*. v1 is kept in full, review and all, per this
project's convention. This document is the rewrite that review demanded of
§§1–3, and it names every v1 claim it overturns rather than quietly dropping
it.

Two things changed between the documents. The review's step 1 — *"fix the
profile-discard inconsistency first"* — is **not** what shipped: Phase 12a
shipped the *warning* (`outage_shadows_profile`), not the fix. §1 confronts
that rather than proceeding as if it were closed. And v1's cost claims, which
the review correctly called out as asserted rather than measured, are now
measured; two of them were wrong, one in v1's favour and one in the review's.
A third claim — my own, that ELCC's non-additivity might change sign on a
diverse fleet — was measured and did not survive (§0.3).

---

## 0. What v1 got wrong, and what replaces it

| v1 blocker | v2's answer | § |
|---|---|---|
| **1** — "the must-take VRE members" is not one set: ELCC's `kind="vre"` population is `source == "missing"`, the margin credits any member carrying a profile | The comparison is scoped to the **intersection**, and the difference — the shadowed set — is excluded *and named*, with its capacity reported as coverage | §1, §3.1 |
| **2** — the two numbers are measured against different load series (solve-time scaled vs restored/unscaled) | Both numbers are computed **inside one call** to `reserve_margin_facts`, from one `demand` series and one member list. There is no second network to disagree with | §2.2 |
| **3** — A2 already exists as a shipped test, and its *direction* is not a law | A2 is deleted. The replacement acceptance asserts non-additivity **without sign**, and the sign is measured instead (§0.2) | §5 |
| **4** — a synchronous GET contradicts `post_mc`'s own written decision | Step B is an opt-in field on the existing `POST /results/mc` worker. Step A is free enough to be synchronous, and is not a study at all | §2, §3.2 |
| **§0.3** — "it costs MC runs, so it cannot live in a preflight" (asserted) | True for step B, and now with numbers. **False for step A**, which is 0.91 ms | §0.1, §6 |

### 0.1 The cost claims, measured

Fixture: 8760 h, 40 sampled 25 MW units at q = 0.05, six 120 MW profile-bearing
farms, `draws=200`, `seed=0`, engine defaults otherwise. One machine, this
container.

| operation | wall clock |
|---|---|
| headline `mc_adequacy` (adaptive; ran to the 2000-draw cap) | **24.3 s** |
| one marginal ELCC (`elcc_for_asset`, kind `vre`) | **263.9 s** |
| portfolio ELCC over all six farms | **311.3 s** |
| step A's net-load window, per period | **0.91 ms** |

So v1's "minutes" was right for step B and wrong for step A by the distance
between 0.91 ms and five minutes — which is the whole reason the review put
step A first.

**A finding the review got wrong, checked in the source.** The review said the
modal `unidentifiable` outcome arrives *"after paying the full bisection
bill"*. It does not: `elcc_of_removal` computes the baseline, tests it against
the resolution floor, and returns before `lole_at` is ever called
(`elcc.py:247-270`). The bill for a refusal is one baseline, not a bisection.

**A real inefficiency the review missed, and it is shipped.** Every
`elcc_of_removal` computes its own baseline with `mc_adequacy(inputs,
draws=draws, seed=seed, cov_target=cov_target)` — argument-for-argument the
call `post_mc`'s worker already made for the headline metrics, and deterministic
in `seed`. A study with N ELCC assets therefore pays **N+1 identical baseline
runs**. Measured above, that is 24.3 s of the 263.9 s a marginal costs — **9 %**
— so it is worth fixing and is *not* the headline. It is listed as work in §3.3
rather than folded silently into this phase's timings.

### 0.2 Non-additivity, and why its DIRECTION is not this phase's to claim

`elcc.py`'s docstring states that a sum of last-in credits **understates** a
portfolio. The review's objection was structural: a per-asset bracket ceiling is
`max_h(profile_i)`, the group's is `max_h(Σ profile_i)`, and for members that do
not peak together the second is strictly smaller than the sum of the first.
Measured on the fixture above:

| bracket ceiling | MW |
|---|---|
| `Σ_i max_h(profile_i)` (what six marginals are bracketed against) | **702.9** |
| `max_h(Σ_i profile_i)` (what the portfolio is bracketed against) | **458.8** |

A **35 % narrower** bracket for the portfolio, on farms whose pairwise
correlation runs 0.03–0.62. That is a v1 bracket *policy* interacting with
diversity, not a fact about capacity credit, and it is sufficient reason for v2
to assert non-additivity without asserting its sign.

That is the argument for caution. As a *prediction* it failed: §0.3 measures
the sign and it did not invert. Read the two together — the caution survives,
the prediction does not.

### 0.3 The sign itself — measured, and it did NOT invert

All six marginals plus the portfolio, each one actually run on the §0.1
fixture (`draws=200`, `seed=0`; profile pairwise correlation 0.03–0.62, so
these farms are diverse, not clones):

| asset | ELCC (MW) | bracket ceiling (MW) |
|---|---|---|
| vre0 | 48.20 | 114.3 |
| vre1 | 28.75 | 116.8 |
| vre2 | 17.03 | 111.8 |
| vre3 | 32.34 | 120.0 |
| vre4 | 43.13 | 120.0 |
| vre5 | 40.78 | 120.0 |
| **Σ marginals** | **210.24** | 702.9 |
| **portfolio** | **272.39** | 458.8 |

**The sum understates the portfolio by 22.8 %** — the same direction
`elcc.py`'s docstring states, and the same direction as the review's own
independent 8760 h measurement (533.9 vs 631.6, understating by 15 %).

**So my §0.2 argument was sound as a caution and wrong as a prediction.** The
group's bracket really is 35 % narrower, and I argued that was enough reason to
expect the sign could invert on a diverse fleet. It did not: the last-in
penalty — each marginal charged for standing behind the other five — dominates
the bracket asymmetry by a wide margin at this diversity. Two fixtures now, both
understating.

That is **evidence for the docstring, not proof of it**, and §5's A7 still
asserts a difference rather than a direction. The distinction matters because
the failure mode is asymmetric: a UI that promises "understates" and is wrong
tells a user their fleet is worth more than it is. Nothing here has explored
genuinely anti-correlated members (these are all positively correlated, 0.03
being the weakest pair), and that is where the bracket asymmetry would have its
best chance.

Determinism was confirmed twice in passing: `vre0` and the portfolio each
returned bit-identical values to a separate earlier run at the same seed.

---

## 1. The precondition v2 does not get to skip

The review's recommended step 1 was *"fix the profile-discard inconsistency
first"*. **Phase 12a did not do that.** It shipped a preflight warning that
names the affected generators; the two engines still model them 4× apart. So
the question v2 has to answer honestly is whether an ELCC-vs-proxy comparison
can mean anything while that is true.

**Position: yes, but only on the population where the two halves already
agree — and the size of the disagreeing population is part of the answer, not
a footnote.**

Three sets are in play, and the code makes them precise:

| set | definition | where |
|---|---|---|
| **M** — margin's profile-credited members | any member with a `p_max_pu` series, *including* occurrence-bearing units | `solver_service.py:3360` |
| **V** — ELCC's `kind="vre"` candidates | `row["source"] == "missing"` | `copt.must_take_generators` |
| **S** — the shadowed set | `M \ V`: has a profile **and** resolvable outage data | Phase 12a's `outage_shadows_profile` population |

`elcc._resolve` already **refuses** a `kind="vre"` request for a name in the
sampled fleet, with a 422 that explains the double-count (`elcc.py:361-368`).
That refusal is correct and v2 does not touch it. It also means **S is not
priceable by ELCC at all** — not as a limitation of this phase, but because
crediting it would be arithmetically wrong.

So the comparison runs on **V ∩ M**, and the payload carries, as first-class
numbers beside the result:

- `covered_mw` — Σ capacity of V ∩ M,
- `shadowed_mw` and `shadowed_assets` — S, by name, linked to the Phase-12a
  warning that already names them,
- `unpriceable_mw` — the margin's existing excluded set, for completeness.

**No coverage threshold, and no refusal below one.** A "we hide the answer under
X % coverage" rule is a magic number, and this project has spent four phases
removing those. Instead the coverage is in the same sentence as the headline,
so a 20 %-coverage answer cannot be read as a fleet-wide one.

**The condition under which this section is wrong**, stated so a reviewer can
attack it: if on real projects S routinely dominates M, then the comparison
describes a minority of the fleet while appearing to judge the standard, and
step B should be deferred until the profile-discard defect is *fixed* rather
than warned about. §5's **A6** exists to find that out on a real network before
step B is built, and §7 Q1 puts it to the reviewer.

---

## 2. Step A — the net-load window diagnostic

Free (0.91 ms per period), synchronous, not a study, and it targets the
textbook failure of peak-coincidence crediting: **VRE is credited on the hours when *gross* demand
peaks, but a system with VRE fails on the hours when *net* demand peaks, and
those are not the same hours.**

### 2.1 What is computed

`reserve_margin_facts` already selects, per period, a peak window
(`solver_service.py:3487-3498`): the top 1 % of snapshots capped at 100, every
snapshot tied with the Nth-highest included, and it then credits each member at
`d = (1−q) · mean(profile over that window)`.

Step A repeats **exactly that selection rule** on a second series —

```
net_p = demand_p − Σ_{m ∈ M, active in P} profile_m × capacity_m
```

— and reports, per member and per period, `derate_gross`, `derate_net`, their
delta, and the two firm-capacity totals they imply.

### 2.2 Why this answers BLOCKER 2 by construction

**The blocker's premise, verified rather than inherited.** `load_scalers` /
`load_scalers_by_carrier` multiply `n.loads_t.p_set` in
`_apply_modelling_assumptions` **before** the LP and revert it after
(`solver_service.py:165-173`, and the restore discipline at 100 and 416-419).
So `reserve_margin_facts` called from inside a solve reads scaled demand and
the same function called from a route afterwards reads unscaled demand. Any
design that measures one number in one context and the other in the other is
comparing two systems, exactly as the review said — and A2's bite is
constructible from this, not hypothetical.

Both windows are built from the **same `demand` series**, from the **same
`members` list**, inside the **same call**. Whichever network `reserve_margin_facts`
is handed — solve-time scaled, or the restored network a route passes it — gross
and net describe that one system. The v1 design compared a solve-time number
against a `snapshot_inputs` number and could not; this one cannot fail that way,
because there is no second reader.

### 2.3 The extendable question, and the trap in it

Netting needs a capacity per member, and for an extendable member capacity is a
decision variable. v2 nets at the member's **incumbent `p_nom`** and labels the
result — the window is a *selection* device, never a coefficient, so the
fixed-point objection of v1 §0.2 does not arise.

But there is a trap that must not be shipped silently: **pre-solve, an
extendable's `p_nom` is typically 0**, so almost nothing is netted, `net ≈
gross`, and the diagnostic reports a near-zero delta. A user would read that as
*"my proxy is fine"* when it means *"nothing was netted"*. The payload
therefore carries `netted_mw` per period, and a delta near zero with a small
`netted_mw` renders as **"nothing to net yet — run a solve first"**, not as an
all-clear. This is the Phase-4 lesson (a report that summed periods and read as
headroom) applied before it can happen.

### 2.4 Per period

Yes, and without a new decision: the margin's window is already per period and
so is this one. v1's open question 2 is settled **for step A**. It remains open
for step B (§7 Q2), where the MC is horizon-wide.

### 2.5 What it is NOT

A net-load window is a **second proxy**, not a truth. The delta measures *how
much the window choice moves the credit* — it does not say the net-load number
is right. The panel copy must say that in those terms; "corrected derate" is
forbidden wording, for the same reason "a met margin is not a met reliability
target" is already in force.

### 2.6 Does it have anything to say?

Prototyped on the §0.1 fixture, replicating the window rule exactly:

| | gross window | net window |
|---|---|---|
| window size | 88 h | 88 h |
| peak | 1146.3 MW gross | 898.2 MW net (gross load there: 971.7 MW) |
| firm credit, six farms | **350.6 MW** | **144.8 MW** (−58.7 %) |

The two windows share **five hours in 88**, and the credit falls by more than
half. The magnitude is fixture-specific — these farms are strongly diurnal —
but the diagnostic is emphatically not vacuous, and a −58.7 % answer on a real
project is exactly the thing the standing PR limitation ("the seam for real
ELCC is the derating factor itself") has been asserting is small without
checking.

---

## 3. Step B — the portfolio ELCC

Only after step A ships and A6 (§5) reports, per §1.

### 3.1 Membership, nameplate, and the group primitive

Population **V ∩ M** (§1). The removal is the one `elcc_of_removal` already
supports directly — the docstring names a portfolio removal as its reason for
existing (`elcc.py:226-232`):

```
reduced      = replace(inputs, residual = inputs.residual + Σ_i profile_i)
nameplate_mw = max_h(Σ_i profile_i)
```

`nameplate_mw` is the group generalisation of what `_resolve` does for one
asset, and it inherits that function's recorded caveat verbatim: `MCInputs`
carries profile × capacity, so installed capacity is not recoverable, and this
equals it exactly when the summed profile attains its cap somewhere in the
horizon and is otherwise conservative — **a narrower bracket, never a wider
one**.

For a *group* that condition is far stricter than for one asset: it needs every
member at 1.0 in the **same hour**, which no diverse portfolio does. So the
group bracket is systematically much narrower than the sum of the per-asset
ones — 458.8 MW against 702.9 MW on the §0.2 fixture — and that gap is not an
error to be corrected but the reason the two numbers are not comparable
term-by-term.

New code: one `elcc_of_portfolio(inputs, names)` in `elcc.py`. It resolves
names through the same must-take test `snapshot_inputs` applies, sums the
preserved profiles, and calls `elcc_of_removal` unchanged.

### 3.2 The route (BLOCKER 4)

An opt-in **boolean** on `McRequest` — `elcc_portfolio: bool | None` — not a
pseudo-asset in `elcc_assets`. Two reasons, both structural:

1. `elcc_assets` entries are `(kind, name)` pairs validated by `_resolve`, which
   owns the 404/422 split. A pseudo-name would fork exactly that mapping.
2. The portfolio row must **not** land in the `elcc` list. Any consumer that
   sums `elcc_mw` across that list — the panel's own table is one — would
   silently include a number that is not a member of the sum. It goes in the
   payload as a **sibling key** `elcc_portfolio`, beside `elcc`.

Everything else it inherits for free, which is what makes v1's A4 actually true
here rather than aspirational: it is inside the existing `mc` study record, so
it cannot outlive its network (Phase 10) and it refuses a swap while running
(Phase 11), **without one line of new lifecycle code**.

`MAX_ELCC_ASSETS = 10` does not apply — a portfolio removal is one evaluation
regardless of group size (v1 open question 3, settled). The flag costs one
bisection, so the cap it needs is the wall-clock warning of §6, not a count.

### 3.3 The N+1 baseline, fixed here or not at all

§0.1's finding. `elcc_of_removal` gains an optional `baseline=` parameter; when
the caller has already computed `mc_adequacy` with identical arguments it passes
it in. 9 % off every ELCC study, and it is a precondition for calling the
portfolio row "nearly free on top of a study that already ran" — which,
without this, it is not.

---

## 4. Out of scope, and it goes in the PR body rather than being skipped quietly

- **Changing the constraint's coefficients.** Unchanged from v1 §2, and both of
  v1's reasons stand: the sum of last-in credits is a known-biased estimate of
  the fleet's joint worth, and a derate on an extendable is a fixed point the LP
  cannot express. Doing it properly needs an allocation rule (the industry
  approach is a portfolio ELCC *allocated* across members — CPUC/E3's "ELCC
  surface" / delta method) plus a decision about extendables, designed against
  evidence *this* phase produces.
- **Per-asset ELCC-weighted derates.** Same reason, more so.
- **Fixing the profile-discard defect.** Phase 12a warns; the fix is a change to
  how the MC and COPT model a shadowed unit and belongs with a spec of its own.
  §7 Q1 asks whether it should come *before* step B rather than after.

---

## 5. Acceptance (self-calibrated — the Phase-8 lesson, kept)

Every ★ names the broken variant it must fail against, and every restore is
verified by hash (the Phase-9 lesson, kept).

**Step A**

★ **A1** — the net window is selected by the *same rule* as the gross window:
on a fixture where net and gross orderings coincide the two windows are
identical, including ties. *Bite: use `nlargest` instead of the ≥-threshold —
the tie-inclusive fixture then returns the first N by index order.*

★ **A2** — gross and net derates for one member are computed from one `demand`
and one member list; feeding `reserve_margin_facts` a load-scaled network moves
**both** numbers together, and their ratio is unchanged. *Bite: recompute the
net window from a re-read of the unscaled network — the ratio then moves, which
is BLOCKER 2 reproduced.* This is the blocker's own regression test.

★ **A3** — a period where nothing is netted (`netted_mw ≈ 0`, all VRE
extendable and unbuilt) reports **"nothing to net yet"** and NOT a zero delta.
*Bite: drop the `netted_mw` branch — the payload then reads as an all-clear.*

★ **A4** — the diagnostic changes no solve: with and without it, an identical
plan and an identical margin block. *Bite: build the net window from the same
mutable series the constraint reads and let the sort alias it.*

**Membership / coverage**

★ **A5** — on a two-farm fixture where one farm carries outage data and one
does not, the covered set is the second farm alone, and the first appears in
`shadowed_assets` with its MW. *Bite: scope coverage to M instead of V ∩ M —
the shadowed farm is then silently counted as covered, which is BLOCKER 1.*

**A6 (measurement, not a test)** — run the coverage split on a real clustered
PyPSA-Eur network and report `covered_mw / (covered_mw + shadowed_mw)`. §1's
stated failure condition is that this is routinely small. **This runs before
step B is built**, and its result decides §7 Q1.

**Step B**

★ **A7** — non-additivity, **without sign**: the portfolio credit differs from
the sum of the member marginals by more than the bisection tolerance on a
fixture where the members do not peak together. *Bite: have the group removal
share the baseline's residual — the portfolio then removes nothing and the two
agree exactly.* This **replaces** v1's A2, which duplicated the shipped
`test_portfolio_credit_is_not_the_sum_of_marginal_credits` and pinned a
direction §0.2 shows is not a law.

★ **A8** — an `unidentifiable` portfolio (baseline LOLE at or below the
resolution floor) renders its reason, invents no number, and costs **one
baseline, not a bisection** — asserted by counting `mc_adequacy` calls.
*Bite: move the floor test below the Δ = 0 probe.*

★ **A9** — the portfolio row is NOT in the `elcc` list: a consumer summing
`elcc_mw` over `result["elcc"]` gets the member sum only. *Bite: append the
portfolio row to `rows` — the panel's own total then double-counts.*

★ **A10** — group nameplate is `max_h(Σ profile)`, not `Σ max_h(profile)`, on
an anti-correlated two-farm fixture where the two differ. *Bite: sum the
per-asset nameplates — the bracket widens and the credit can exceed what the
group can physically deliver at any hour.*

---

## 6. What the user is told, and when

Step A needs no warning: it is 0.91 ms per period, it runs with the rest of
`reserve_margin_facts`, and it appears in the reserve-margin panel beside the
derating table it judges.

Step B is minutes. §0.1's numbers are the honest story and the panel states
them **before** the study starts, in the same place the draw count is chosen —
not in a tooltip, and not after a spinner has been running for four minutes.
The existing MC panel already carries a study-cost sentence; this extends it
rather than inventing a second convention.

---

## 7. Open questions for the review

1. **§1 is the load-bearing judgement.** Is "compare on V ∩ M and report
   coverage" defensible while the profile-discard defect is warned-about rather
   than fixed — or should the fix precede step B outright? A6 is designed to
   inform this; I would rather the reviewer set the rule before the measurement
   than after it.
2. **Per-period for step B.** The margin is enforced per period; the MC is
   horizon-wide; a per-period ELCC is not obviously well defined. Step A settles
   this for itself (§2.4). Step B does not, and shipping a horizon-wide ELCC
   beside a per-period proxy invites exactly the Phase-4 mistake.
3. **Is step A alone the whole phase?** It answers the user's actual question
   ("is my margin over-crediting my VRE?") in milliseconds, per period, on the
   margin's own units and demand series. Step B costs minutes, covers only V ∩ M,
   and answers a *different* question from a *different* standard. A defensible
   reading of the review is that step B should be its own phase gated on A6 —
   and I lean that way.
4. **§0.3 vs a shipped docstring — resolved, but confirm the reading.**
   `elcc.py` tells the user a sum of last-in credits *understates* a portfolio.
   Measured, it does, on two independent fixtures. My proposal is therefore to
   leave the docstring alone and NOT let the phase quote it as a guarantee in
   UI copy, because two positively-correlated fixtures are not the
   anti-correlated case where the bracket asymmetry would bite. Is "observed
   on every fixture we have, not proved" the right standard for shipped text
   here, or should the docstring itself carry that qualifier?

---

# v2 REVIEW OUTCOME: **reject**. Do not implement this document.

Recorded rather than quietly rewritten, per this project's convention — the
same treatment v1 got. §§1, 2, 3 and 5 all need rewriting. **Every blocker
below was re-verified independently before being accepted**; two of them were
found to be understated, and one of the review's own conclusions is overturned
at the end.

## The six blockers, and what I checked

**BLOCKER 1 — §1's three sets are not the sets the code has.** `V` and `M` are
taken from walks with different `keep_zero_capacity`, and `V`'s capacity rule
is `solved_capacity`, which for an extendable returns `p_nom_opt` — *including
0.0*. **Verified in the source**: `copt.py:183-213`, whose own docstring says
"on an UNSOLVED network PyPSA's `p_nom_opt` default is 0.0, so an extendable
row reads as 0 MW until a solve writes a size"; `must_take_generators` walks at
the default `False` (`copt.py:305`) while `reserve_margin_facts` walks at
`True`. Two consequences, both fatal to §1 as written: an unbuilt extendable
VRE farm with *no* outage data lands in `M \ V` and would be reported as
`shadowed` — which Phase 12a's warning will never name, because that check is
fed `source != "missing"`; and on a capacity-expansion network, the case the
reserve margin exists for, every VRE candidate is extendable at
`p_nom_opt = 0`, so **`V` is empty and the comparison covers nothing**. A6 as
designed reports that as `0/(0+0)`, indistinguishable from "no VRE". §1's
stated failure condition does not cover it. `covered_mw` also has no defined
capacity rule, and `unpriceable_mw` is not computable at all — the generator
branch `continue`s before capacity is recorded (`solver_service.py:3364`).

**BLOCKER 2 — §2.3's netting basis does not exist where §2 computes it.**
`p_nom` is never written back from `p_nom_opt`: the only assignment is the
myopic freeze (`solver_service.py:5992`), and it pushes the originals onto
`undo_actions` (6066-6067) so the restore puts `p_nom` back. **Verified**: the
grep finds no other writeback, and `reserve_margin_facts` reads the raw
`p_nom` column (`solver_service.py:3390`). So `netted_mw` for an extendable is
**0 at every call site** — preflight, the LP wrapper, and every route — and
§2.3's user-facing "run a solve first" is wrong advice. The dilemma is
structural and the plan never states it: the one function that reads built
capacity back (`report.py:104-116`) runs *after* restore on unscaled loads, so
correct demand and real capacities are not available in the same place. §2.6's
−58.7 % is only reachable on an all-fixed VRE fleet, which contradicts §2.3's
own caveat.

**BLOCKER 3 — A2 is inverted.** Load scaling multiplies every electrical load
column in a period by one factor, and the window is a threshold on a
monotone-rescaled series, so `derate_gross` **cannot** move; but `s·D − N` is
not order-equivalent to `D − N`, so the net window does. **Verified
independently**: on an 8760 h series at ×1.25, the gross window is identical
(`True`) and the net window is not (`False`). So the ratio moves for the
*correct* implementation and is constant under the named bite — A2 as written
fails against correct code and passes against its bug. A consequence §2 does
not disclose: the margin's derating table is currently invariant to load
scaling and `derate_net` would not be, so preflight and the solve-time stash
would publish different values for one asset with no copy explaining why.

**BLOCKER 4 — A7 passes against its own bite.** Under the named bite the group
removal removes nothing, `elcc_of_removal` short-circuits at the Δ = 0 probe
and returns **0.0**, which differs from the marginal sum by far more than the
tolerance — so A7's "differs by more than tol" assertion holds. **Verified on
my own fixture**: marginals 60.16 + 60.16 = 120.31, correct portfolio 100.00
(A7 passes), bite portfolio 0.0 → `|0.0 − 120.31| = 120.31 > 0.5` → **A7
passes under its bite**. Dropping the direction is exactly what destroyed the
bite the shipped test still has.

**BLOCKER 5 — the bracket mechanism in §0.2 does not exist (but see the
correction below).** The bracket is a search interval, not a coefficient.
**Verified**: doubling the ceiling from `max_h(Σ)` = 100 MW to `Σ max_h` = 200
MW moved the credit by **0.0000 MW**. `max_h(Σ)` is still the right ceiling —
for the dominance reason at `elcc.py:61-69`, not the reason §0.2 gives — and
A10's bite does not bite.

**BLOCKER 6 — §3.2's boolean is not sufficient.** `snapshot_inputs` preserves a
profile only for names passed in `vre_assets` (`mc.py:255-276`), so with a bare
`elcc_portfolio: bool` there is nothing to sum; the route must compute the
`V ∩ M` list, which needs the margin's membership walk inside `post_mc`. And an
empty population is not refused: **verified**, `elcc_of_removal(nameplate_mw=0.0)`
returns `{'status': 'ok', 'elcc_mw': 0.0, 'reason': None}` — a fabricated
number, and given BLOCKER 1 the *modal* step-B answer on an expansion network.

Also **SERIOUS 7** (§3 never states its load basis, so BLOCKER 2 of v1 is
closed for step A only and reopened by step B), **SERIOUS 8** (step A is not
free plumbing: `reserve_margin_payload` builds rows from an explicit
whitelist, and `test_stash_shape` asserts stash keys with `==`, not `>=` —
**verified**, `tests/test_adequacy_reserve_margin.py:594-601`), **SERIOUS 9**
(A1 duplicates a shipped test and is defeated by the shared helper §2.1
promises), **MINOR 10** (§3.3's injected baseline can silently desynchronise
the CRN replay and has no ★ test), **MINOR 11** (storage and thermal members
report an identically zero delta, so the diagnostic says "no problem" for the
duration-limited battery whose credit is most window-sensitive).

## Where the review is itself wrong, measured

**BLOCKER 5 is right about the mechanism and too strong in its conclusion.**
Widening the bracket is inert *when the located step is strictly inside it* —
that is the reviewer's fixture and the six-farm one. It is **not** inert when
the credit saturates at the ceiling, and then `max_h(Σ)` binds as a genuine
physical bound: a group of non-overlapping farms cannot deliver more than
`max_h(Σ)` at any instant, however much nameplate it owns.

**And that overturns §0.3, the shipped docstring, and the review's own Q4.**
On two farms that never overlap (A on hours 0–9, B on 10–19), each is worth
60.16 MW alone — the other still covers its half — while the pair is capped at
100 MW:

| | MW |
|---|---|
| marginal A | 60.16 |
| marginal B | 60.16 |
| **Σ marginals** | **120.31** |
| **portfolio** | **100.00** (= `max_h(Σ)`, the group's physical maximum) |

**The sum OVERSTATES**, robustly: 7 of 8 runs across four seeds and two draw
counts. So `elcc.py`'s shipped claim that a sum of last-in credits "UNDERSTATES
a portfolio's joint credit" is **false in general**, and the review's Q4
recommendation — leave it alone because the anti-correlated case was measured
and did not invert — rests on a single fixture whose fleet was loose enough to
hide it. The sign is not determined by correlation alone: it turns on how much
of a removed asset's contribution the *remaining* fleet can absorb, which is a
property of fleet tightness as much as of profile shape.

So **A7's decision to assert a difference and not a direction was right** — for
the ceiling reason, not the bisection reason §0.2 gave. The docstring needs
qualifying, and that is now a finding about shipped user-facing text rather
than an open question.

## What v3 must do

1. State `V`, `M` and `S` as the code defines them, define `S` by Phase 12a's
   own predicate rather than as `M \ V`, give every reported MW a capacity
   rule, and redesign A6 to report `|V|`, `|M|`, `|V ∩ M|`, `|S|` and the
   extendable/fixed split separately so an empty `V` is its own outcome.
2. Resolve the netting-capacity dilemma explicitly — pre-solve indicative at
   `p_nom_max`/`p_nom_opt`, post-solve from the stash, or fixed members only —
   and name the cost of the choice.
3. Replace A1, A2, A7 and A10 with assertions that can fail.
4. Split the phase, per the review's Q3 and more strongly than v2 leaned: step
   A and step B fail for unrelated reasons and should not be re-reviewed as one
   document. Step B is gated on the profile-discard **fix**, not on A6.
5. Qualify `elcc.py`'s non-additivity docstring, with the counterexample above.
