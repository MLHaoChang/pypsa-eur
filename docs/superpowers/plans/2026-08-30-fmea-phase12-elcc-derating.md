# Phase 12 — ELCC-weighted derating (plan, v1)

## 0. The request, and why the obvious reading of it is unsound

The ask is to replace the reserve margin's **peak-coincidence** VRE credit with
real **ELCC**. The obvious implementation — set `d_i = ELCC_i / p_nom_i` and
keep the constraint `Σ d_i · P_i ≥ (1+m)·peak` — is wrong, for three reasons.
Two of them are already written down in this repository.

### 0.1 ELCC does not add up, and the codebase says so

`services/adequacy/elcc.py` computes **last-in credit**: remove the asset, find
the firm block that restores baseline LOLE. Its own docstring:

> "Last-in" is not a detail: the credit is conditional on everything else that
> is already built, **which is why these numbers do not add up**.

and, on `elcc_of_removal`:

> **the sum of last-in credits UNDERSTATES a portfolio's joint credit**,
> because each marginal evaluation charges the asset for standing behind the
> others.

So `Σ (ELCC_i/P_i)·P_i = Σ ELCC_i` is a **known-biased** estimate of what the
fleet is actually worth, biased low, by an amount nobody computed. Shipping it
as "real ELCC" would replace a proxy that is *labelled* a proxy with a number
that is wrong and *labelled* exact. That is strictly worse.

### 0.2 A derate on an extendable asset is a fixed point, not a coefficient

For extendable members the constraint multiplies `d_i` by a **decision
variable**. ELCC is defined for a *given* installed capacity — the credit of
100 MW of wind is not twice the credit of 50 MW (it saturates). So an
ELCC-derived `d` on an extendable is a function of the quantity being
optimised: the constraint stops being linear and becomes a fixed point that
the LP cannot express and this phase is not going to solve.

### 0.3 It costs MC runs, so it cannot live in a preflight

Each ELCC is a bisection, each step a full MC evaluation, all pinned to a
replayed baseline. That is minutes, not milliseconds, and the margin
constraint is built inside every solve's preflight. So an ELCC-derived derate
has to be **computed once and referenced later** — which is precisely the
stale-record hazard Phases 10 and 11 just spent two phases eliminating.

## 1. What is actually sound, and what it buys

**Use ELCC to MEASURE the proxy, not (yet) to define the constraint.**

The decision-relevant question a user has today is not "what coefficient
should the LP use" — it is **"is my reserve margin over- or under-crediting my
VRE, and by how much?"** That question is answerable soundly, cheaply enough,
and without touching the LP:

1. Take the must-take VRE members the margin already identifies.
2. Compute **ONE portfolio ELCC** for that group with `elcc_of_removal`
   (which already supports a group removal — §0.1's own testability note), not
   a sum of per-asset ELCCs.
3. Compare it with what the peak-coincidence proxy credited the same group:
   `Σ d_i · P_i` over those members.
4. Report both, the ratio, and the direction — with every refusal ELCC can
   legitimately return (`unidentifiable`, `not_bracketed`) rendered as itself
   rather than as a number.

That is a **diagnostic**, and it is honest by construction: it never enters a
constraint, so §0.2's fixed point does not arise and §0.3's staleness cannot
corrupt a plan — the worst case is a stale *diagnostic*, which the study
record already carries a plan hash for.

It also directly serves the PR's own standing limitation: *"the reserve
margin's VRE credit is a peak-coincidence proxy, not ELCC … the seam for real
ELCC is the derating factor itself."* This phase measures the size of that
seam instead of asserting it is small.

## 2. Scope

**IN**
- A portfolio-ELCC-vs-proxy comparison for the must-take VRE group, on the
  incumbent plan, exposed at `GET /results/reserve_margin/elcc_check`
  (name to be settled in the spec) and rendered in the reserve-margin panel
  beside the derating table it judges.
- The comparison states its baseline, its draws/seed, and the plan it was
  measured against — reusing the Phase-10/11 study machinery so it is a
  first-class study record that cannot outlive its network.
- Every ELCC refusal reaches the panel as a reason, never as a silent zero or
  a fabricated 1.0 (the "nothing may default to 1.0" rule already in force).

**OUT, and stated in the PR body rather than quietly skipped**
- Changing the constraint's coefficients. §0.1 and §0.2 have to be answered
  first, and answering them properly means an allocation rule (the industry
  approach is a portfolio ELCC *allocated* across members — CPUC/E3's "ELCC
  surface" / delta method — not marginal sums) plus a decision about
  extendables. That is its own phase, and it should be designed against
  evidence this phase produces.
- Per-asset ELCC-weighted derates. Same reason, more so.

## 3. Acceptance (self-calibrated — the Phase-8 lesson, kept)

★ **A1** — on a fixture with must-take VRE, the portfolio ELCC and the proxy
credit are BOTH produced, and the comparison is arithmetically the two
numbers it claims to compare (no third quantity smuggled in).
★ **A2** — the portfolio ELCC is **not** the sum of per-asset ELCCs on the
same fixture, and the direction matches the documented bias (the sum is the
smaller). This is the phase's central claim and the reason the naive design
was rejected; if it cannot be demonstrated on a real fixture, the rejection
was wrong and the plan must be revised, not the test.
★ **A3** — an ELCC refusal (`unidentifiable` on a system with no shortfall to
hold constant) renders as its reason, and no number is invented.
★ **A4** — the record is a study: it cannot outlive its network (Phase 10) and
it refuses a swap while running (Phase 11), by construction rather than by new
code.
★ **A5** — the comparison does not change any solve. A solve with and without
the diagnostic produces the identical plan and the identical margin block.

## 4. Open questions for the review

1. Is the **diagnostic-first** framing right, or is there a defensible way to
   put ELCC into the constraint for FIXED members only in this phase? (My
   position: fixed-only is defensible arithmetically but splits the derating
   table across two incomparable bases, which is a worse user story than one
   honest proxy plus a measured correction.)
2. Should the comparison be per-PERIOD? The margin is enforced per period; the
   MC is horizon-wide. A per-period ELCC is not obviously well defined.
3. `MAX_ELCC_ASSETS = 10` bounds the per-asset route. A portfolio removal is
   one evaluation regardless of group size — does any bound still apply?
4. What is the honest wall-clock story, and where is it stated to the user
   BEFORE they start a study that takes minutes?

---

# v1 REVIEW OUTCOME: **not as scoped**. Do not implement this document.

Recorded rather than quietly rewritten, per this project's convention. v2 must
rewrite §§1–3 before any code is written.

## What the review found (verified independently where it mattered)

**BLOCKER 1 — "the must-take VRE members" is not one set.** ELCC's
`kind="vre"` population is `copt.must_take_generators`, which is
`row["source"] == "missing"` — generators with **no resolvable outage data**,
NOT "VRE". The margin's profile-credited members are any member carrying a
`p_max_pu` series, occurrence-bearing units included. **Verified**:
`copt.py:280-305` and the walk's `else` branch at `copt.py:341-359`.

**BLOCKER 2 — the two numbers are measured against different load series.**
The margin's peaks and derates are solve-time truth taken while the
load-scaling transforms are applied; `snapshot_inputs` reads the restored,
unscaled network. On any project with `load_scalers` the ratio is arithmetic
between two different systems.

**BLOCKER 3 — A2 already exists, and its direction is not a law.**
`tests/test_adequacy_elcc.py::test_portfolio_credit_is_not_the_sum_of_marginal_credits`
has asserted exactly this since Phase 6 (portfolio 34 MW vs sum 16 MW). The
plan called it "the phase's central claim" while being unaware of it. Worse,
pinning the *direction* is unsafe: the per-asset bracket ceiling is
`max_h(profile_i)` while a portfolio's is `max_h(Σ profile_i)`, so
anti-correlated members (night wind + day solar) can invert it for a reason
that is a v1 bracket policy, not a fact about capacity credit.

**BLOCKER 4 — a synchronous GET contradicts a decision already written down.**
`post_mc`'s own docstring: *"ASYNCHRONOUS BY CONSTRUCTION, not as an
optimisation … running it inline would hold a request open long past every
proxy and browser timeout"*. The portfolio ELCC is the same order of cost. It
belongs as an opt-in row on the existing `POST /results/mc` worker, which also
makes A4 ("a study by construction, not new code") actually true — a new study
key would have meant edits in five places.

**§0.3's cost claim was mine, and it was asserted rather than measured.**
Measured since, on this machine:

| fixture | portfolio ELCC | all marginals |
|---|---|---|
| 168 h, 2 units, 2 farms, 1000 draws | 0.1 s | 0.1 s |
| 8760 h, 60 units, 6 farms, 200 draws | **50 s** | **276 s** |

So "minutes" is right for a medium network and optimistic for a clustered one
— but "cannot live in a preflight" was true for the wrong stated reason. The
modal outcome is also worse than the plan assumed: on a *well-built* plan the
baseline LOLE sits at or below the resolution floor and ELCC returns
`unidentifiable` **after** paying the full bisection bill. That happened twice
by accident while calibrating the fixture above.

**Non-additivity, measured independently** (8760 h, 60 units, 6 correlated
farms, 200 draws): portfolio **631.6 MW** vs sum of six marginals **533.9 MW**
— the sum understates by 15 %, same direction as the Phase-6 fixture. Note
this was only right after fixing my own method: an earlier run extrapolated
six marginals from one and got the direction backwards.

## THE FINDING THAT MATTERS MORE THAN THIS PHASE

While establishing BLOCKER 1 I found a **shipped defect**, verified on a
two-farm fixture. Two identical 100 MW wind farms, same 25 % capacity-factor
profile, differing only in whether an outage rate was entered:

| farm | membership | how the sequential MC models it | credit |
|---|---|---|---|
| `wind_no_for` | must-take | profile netted into the residual | 25 MW mean |
| `wind_with_for` | sampled fleet | **profile DISCARDED**, flat two-state at `p_nom` | 90 MW = (1−q)·p_nom |

`CoptUnit` has no profile field (`copt.py:44-51`) and `snapshot_inputs`
preserves a profile only for must-take names (`mc.py:252-276`), so a
profile-bearing generator that has outage data is simulated as **firm
capacity**. Entering *more* data credits the asset ~3.6× more.

And the same asset, on the same network, is credited by the reserve margin at
`(1−q)·profile̅ = 0.225` → **22.5 MW** (measured via `reserve_margin_facts`).
So the constraint says 22.5 MW and the sampler that certifies the constraint
says 90 MW — **a 4× disagreement inside one product**, affecting the MC, ELCC,
the COPT, and both planning loops, which tune against MC-LOLE. No preflight
warning is emitted.

The one defensible reading — that `p_max_pu` on an occurrence-bearing unit is
a dispatch limit rather than an availability profile — does not survive:
the reserve margin already uses BOTH the profile and `q` for that same asset,
nothing tells the user, and the error direction is unsafe.

**An ELCC-vs-proxy comparison is meaningless until the two halves agree about
what the asset is.** This defect is therefore a precondition for Phase 12, not
a detour from it.

## The shape v2 should take (review's recommendation, endorsed)

1. Fix the profile-discard inconsistency first.
2. Then a **free** diagnostic: recompute every must-take derate over the top-1 %
   **net-load** window instead of the gross-load window, and report the delta.
   Milliseconds, no study record, no 409 mesh, in the margin's own units, on
   the margin's own demand series, per period — and it targets the textbook
   failure of peak-coincidence crediting.
3. Only then the portfolio ELCC, as an opt-in row on `POST /results/mc`, with
   membership, load basis, `nameplate_mw = max_h(Σ profile_i)` and the
   per-period question settled in a spec, and presented as a second opinion
   from a different standard — never as a correction to the margin.
