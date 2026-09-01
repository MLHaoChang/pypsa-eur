# Phase 12b — the net-load window (Phase 12 plan, v3: step A only)

Third plan for Phase 12. **v1** (`2026-08-30-fmea-phase12-elcc-derating.md`)
was rejected "not as scoped"; **v2** (`2026-09-01-fmea-phase12-elcc-v2.md`) was
rejected outright with six blockers, every one re-verified. Both are kept
whole, reviews included. This document does what the v2 review told it to:
it **splits the phase**, keeps only the half that has a sound design, and
names the gate the other half now waits behind.

---

## 0. The scope decision, and why it is not a retreat

v2 carried two pieces of work that its review showed fail for *unrelated*
reasons:

- **Step A**, the net-load window diagnostic, failed on its netting basis
  (BLOCKER 2), an inverted test (BLOCKER 3), and an undisclosed contract
  change (SERIOUS 8). All three have a clean answer, given below.
- **Step B**, the portfolio ELCC, failed on set algebra that collapses on
  expansion networks (BLOCKER 1), a route that silently returns a fabricated
  `0.0 MW, ok` on the *modal* input (BLOCKER 6), and a load basis it never
  stated (SERIOUS 7). Those are not fixable by a better plan; they are
  fixable by first fixing the profile-discard defect Phase 12a only *warned*
  about — because until the sampler and the margin agree what a shadowed
  generator *is*, there is no population on which "ELCC vs proxy" means
  anything.

So this plan is **step A only**. Step B becomes **Phase 12c**, and its gate is
the profile-discard **fix**, not a coverage measurement (§7). This is the v2
review's Q3 answer, adopted in full.

One more thing v2 said that this plan no longer says: that step A is a
*preflight* diagnostic. It is not. It is a **post-solve** diagnostic, for a
reason §1 makes structural, and the plan is honest about the consequence: a
user sees it after a solve, beside the derating table, never before one.

---

## 1. The one place this can be computed

The diagnostic needs three things in one scope: the **demand series the LP
enforced its margin against**, the **capacities the LP actually built**, and
each member's **availability profile**. v2's BLOCKER 2 was that no call site
of `reserve_margin_facts` has the second — `p_nom` is never written back from
`p_nom_opt`, and the only place that assigns it (the myopic freeze,
`solver_service.py:5992`) pushes the originals onto the undo list.

But the codebase already has a function whose whole job is to join those
three, and its docstring already states the discipline this plan needs
(`report.py:81-95`):

> *"Only the CAPACITIES are read back off the network here (`p_nom_opt` is the
> solve's answer and the restore step does not touch it); every demand-derived
> number comes from the stash, because recomputing a peak after restore reads
> different loads and drifts from the standard the LP enforced."*

That is `reserve_margin_payload`, invoked once post-solve at
`solver_service.py:1342` with the stash and the live network, then emitted into
solver state and served verbatim by `GET /results/reserve_margin`
(`results.py:5083-5105`), which is documented as **"NEVER a recomputation"**.
Step A is computed **there**, from:

| input | source | why that source |
|---|---|---|
| demand, per period | the **stash** (new key, §2.1) | it is the scaled series the constraint was built on; the network's is unscaled after restore |
| capacity, per member | `solved_capacity(row)` — `p_nom_opt` for extendables, `p_nom` for fixed | the adequacy engines' ONE capacity rule (`copt.py:183-213`), and what `_built()` in the payload already does |
| profile, per member | `n.generators_t.p_max_pu` post-restore | **verified**: no modelling assumption writes `p_max_pu` transiently — every reference in `solver_service.py` is a read — so the post-restore profile is bit-for-bit the one the wrapper derated on |

This answers three review findings at once and without a new mechanism:

- **BLOCKER 2** — the capacity basis is the built plan, which is the only
  honest one and the only available one.
- **v1's BLOCKER 2** — the gross window (from the stash's `peak_snapshots`)
  and the net window (from the stash's demand) are selected from **one
  series**, and the network is consulted for nothing demand-derived. This is
  the same discipline the payload already enforces for `peak_mw`, extended to
  one more number.
- **BLOCKER 3's undisclosed consequence** — there is no preflight
  `derate_net` to disagree with the solve-time one, because there is no
  preflight `derate_net`. The preflight (`validation_service.py:1616`) is
  untouched.

---

## 2. Design

### 2.1 The stash carries the demand series (contract change, §2.6)

`reserve_margin_facts` already builds `demand_p` per period. It now also
stashes it: `stash["periods"][P]["demand_mw"]`, a `pd.Series` indexed by the
period's snapshots. In memory only — the stash lives on the network object
from wrapper time to payload time and is deleted at `solver_service.py:1329`;
it is never serialised. 8760 floats per period.

This is a **deliberate change to the spec's §2.6 contract**, and
`test_stash_shape` (`tests/test_adequacy_reserve_margin.py:594-601`) asserts
the key set with `==` and will fail. It is updated in the same commit, with a
sentence saying why. v2 presented this as free plumbing; the review's SERIOUS
8 was right that it is not.

### 2.2 Which members are netted, at what, and why it is not the MC's residual

Net load is **gross electrical demand minus the availability of every
profile-bearing member of the margin's own member list** — the set the margin
already calls `M`, including occurrence-bearing profile carriers (the
shadowed set):

```
net_P = demand_P − Σ_{m ∈ M, active in P} profile_m × solved_capacity(m)
```

Three decisions inside that line, each stated so it can be attacked:

1. **All of `M`, not the MC's must-take set.** The net-load window asks *when
   does the system run short*, and physically every farm's output reduces
   that, whether or not it has an outage rate entered. This is **deliberately
   not** the sequential MC's residual, which nets only must-take units and
   samples a shadowed farm as flat firm capacity — that is the Phase-12a
   defect, and a diagnostic that inherited it would be measuring the seam
   with the seam. The divergence is disclosed in the payload copy.
2. **Availability, not derated availability.** Netting at `profile × cap`,
   not `(1−q) · profile × cap`. Outages are what the margin is *for*; the
   window is about weather. Deriving `q` into the window would make the
   diagnostic depend on the very derating it judges.
3. **`solved_capacity`**, so a candidate the LP built to 80 MW is netted at
   80, one it declined is netted at 0, and a fixed farm at its `p_nom`.

`V` — ELCC's population — does not appear anywhere in step A. That is why
v2's BLOCKER 1 does not arise here rather than being answered here.

### 2.3 The window rule is shared, and tested on content

One helper, `_peak_window(series)`, extracted from the inline code at
`solver_service.py:3487-3498` and applied to both series: top 1 % capped at
100, minimum 1, **every snapshot tied with the Nth-highest included**. The
gross path calls it on `demand_P`, the net path on `net_P`.

The review's SERIOUS 9 stands: a test that the two windows are "identical
under coincidence" cannot bite a shared helper. So §4's B1 asserts the net
window's **content** against a hand-computed set on a fixture where the net
ordering is known — the same standard the shipped tie-inclusion test applies
to the gross window.

### 2.4 What is reported

Per period, a new `net_window` block on the payload row:

| key | meaning |
|---|---|
| `snapshots`, `n_hours` | the net window, same form as `peak_snapshots` / `n_peak_hours` |
| `net_peak_mw` | max of `net_P` |
| `gross_at_net_peak_mw` | mean gross demand over the net window — so a reader sees the window moved *off* the gross peak |
| `netted_mw` | mean of the netted availability over the period, i.e. how much VRE actually moved the series |
| `overlap_hours` | `|gross window ∩ net window|` |
| `firm_gross_mw`, `firm_net_mw` | Σ `derate × built` under each window, over members with a profile |

Per asset row: `derate_net` beside the existing `derate`, and `derate_net`
is **`null` for a member with no profile** — storage's duration haircut and a
thermal unit's static `p_max_pu` are window-independent, so a numeric
"delta = 0" there would read as a clean bill for exactly the duration-limited
battery whose credit is most sensitive to which hours the window picks
(MINOR 11). Null, with the panel saying "no profile — window-independent".

### 2.5 The empty case is its own outcome

If `netted_mw == 0` in a period — no built VRE, every candidate declined —
the `net_window` block is **`null`** with `reason: "nothing_netted"`, and the
panel says *"no profile-bearing capacity in the built plan; the net-load
window is the gross window"*. It does **not** publish a net window identical
to the gross one with every delta at zero, because that renders as an
all-clear. v2's A3 named this case and then gave it wrong advice ("run a
solve first"); this is post-solve, so the advice is now true by construction
and the case is simply reported.

### 2.6 What it is NOT

A second proxy. `derate_net` says what the derate *would have been* had the
constraint been built on the net-load window. It does not say that number is
right, and the panel does not call it "corrected". It is the margin's own
question — *were my VRE credited on the hours that matter?* — answered in the
margin's own units, on the margin's own demand series, per period.

### 2.7 Contract changes, all named

- spec §2.6: stash gains `demand_mw` per period → **amendment v1.3**.
- spec §4: payload period rows gain `net_window`; asset rows gain
  `derate_net` → v1.3.
- spec §6: the panel's derating table gains a column → v1.3.
- `test_stash_shape` and the payload-shape tests are updated **in the same
  commit** as the change, each with the reason in its docstring.
- `sanitize_reserve_margin_payload`: `net_window` carries no `inf`, so no new
  work — asserted by a test that serialises a payload with an unbounded
  extendable and a net block.

---

## 3. The docstring, because the evidence is in hand

`elcc.py` tells the user, in two places, that a sum of last-in credits
**UNDERSTATES** a portfolio's joint credit. The v2 review measured one
anti-correlated fixture and agreed. I then measured a tighter one and it
**inverted**: two 100 MW farms that never overlap (A on hours 0–9, B on
10–19), each worth 60.16 MW alone because the other still covers its half,
and the pair capped at 100 MW — the group's physical maximum at any instant.
**Sum 120.31, portfolio 100.00, robust across four seeds and two draw counts
(7 of 8).**

So the sign is not a law. It turns on how much of a removed asset's
contribution the *remaining* fleet can absorb — fleet tightness — as much as
on profile shape. This plan qualifies both docstrings to say *"on the fixtures
measured so far the sum understates; it can overstate when members do not
overlap and the fleet is tight"*, and pins the counterexample with a ★ test
(B7) whose bite was already demonstrated to bite.

Small, separable, and shipped user-facing text — so it is **Task 0**, landed
on its own before any of §2.

---

## 4. Acceptance

Every ★ names its bite. Per this project's rule, a bite is demonstrated to
FAIL before the test counts, and every restore is verified by hash. Two of
v2's tests (A2, A7) passed against their own bites; each of these was
designed by asking *what wrong implementation would still pass?* first.

★ **B1 — net window content.** Flat gross load 150 MW over 20 snapshots; one
fixed 100 MW farm with `p_max_pu = 1` on hours 0–9 and `0` on 10–19. Net load
is 50 on 0–9 and 150 on 10–19; `n_target = 1`; the ≥-threshold rule returns
**all ten** hours tied at 150. Assert `net_window.snapshots == hours 10–19`
exactly. *Bites: (a) select the net window on gross demand — flat, so all 20
hours; (b) `nlargest` — one hour.* Both differ from ten.

★ **B2 — demand comes from the stash, never the network.** Build facts, then
**overwrite `n.loads_t.p_set`** with a different series before calling
`reserve_margin_payload` — simulating the restore. Guard, up front: the net
window computed from the stashed series and from the overwritten series
*differ* (the Phase-9 lesson: a fixture that cannot see the defect is not a
test). Assert the payload's net window equals the stashed-series one. *Bite:
re-read demand from `n` in the payload.* This is v1's BLOCKER 2 as a
regression test, and unlike v2's A2 it asserts which series was used rather
than an invariant that happened to be false.

★ **B3 — built capacity, not nameplate.** An extendable farm with `p_nom = 0`;
set `p_nom_opt = 80` by hand after facts (what a solve does). Assert
`netted_mw` reflects 80 MW. *Bite: read `p_nom` → netted 0 → the empty-case
branch fires instead.*

★ **B4 — no profile ⇒ `derate_net` is null.** A storage member with a
duration haircut. *Bite: compute it anyway → a number equal to `derate`.*

★ **B5 — the empty case is reported, not faked.** No built VRE. Assert
`net_window is None` and `reason == "nothing_netted"`. *Bite: compute anyway
→ a block whose window equals the gross window and whose deltas are zero.*

★ **B6 — a shadowed farm IS netted.** Two identical farms, one with an
outage rate entered. Assert `netted_mw` includes both. *Bite: net only
`source == "missing"` → half.* This is the §2.2 decision, pinned.

★ **B7 — the sum can overstate.** The §3 fixture; assert marginals ≈ 60.16
each and portfolio ≈ 100.0 within `default_tol_mw`, hence `sum > portfolio`.
*Bite: the group removal shares the baseline residual → portfolio 0.0.*
**Already demonstrated**: 0.0 vs 100.0.

**B8 — contract pins (not bites).** `test_stash_shape` gains `demand_mw`;
a payload-shape test gains `net_window` and `derate_net`; the sanitiser test
gains a net block beside an unbounded extendable. These assert the contract,
and say so.

Dropped from v2, with reasons: **A1** (defeated by the shared helper — B1
replaces it on content); **A2** (inverted — B2 replaces it); **A4** ("changes
no solve" is true *by construction* for a post-solve computation, so a test
would assert the architecture, not behaviour); **A5/A6/A7/A8/A9/A10** (step B,
deferred with it).

---

## 5. Frontend

`ReserveMarginPanel.tsx`'s derating table gains a `derate_net` column beside
`derate` (cell test-id `rm-asset-derate-net-${id}`), rendering `—` with a
title for null. The period summary gains one line: *net-load window N h,
overlap K of M with the gross window, VRE netted X MW, firm credit Y → Z MW*.
The `null` block renders its reason. Copy never says "corrected".

Mount tests in both states (block present, block null), per the Phase-3
no-early-return lesson.

---

## 6. Cost

Window selection is **0.91 ms per period** at 8760 snapshots (measured, v2
§0.1). Everything else is one pass over `M`. The diagnostic is computed once,
in the payload, and served from state. No study, no thread, no 409 mesh.

---

## 7. Deferred: Phase 12c, and its gate

The portfolio ELCC (v2 §3) is deferred to **Phase 12c**, gated on **fixing
the profile-discard defect** — giving the COPT and the sequential MC a way to
model a generator that carries both a profile and outage data, rather than
discarding the profile. Only once that is fixed is there a population on
which an ELCC-vs-proxy comparison is well-posed, and only then does v2's
BLOCKER 1 (the `V`/`M` split) have an answer other than "report the split".

Carried forward into 12c's brief, so they are not lost:

- the `|V|, |M|, |V ∩ M|, |S|, extendable/fixed` census the review asked for
  in place of A6;
- the synchronous 422 for an empty population and a status row instead of
  `0.0, ok` (v2 BLOCKER 6);
- the load basis for the ELCC half (SERIOUS 7);
- the N+1 baseline fix with a CRN-safety test (MINOR 10);
- reconciling the two sets of timings (v2 264 s/marginal vs the v1 review's
  46 s on a larger fleet — unexplained, and one sentence would do).

---

## 8. Open questions for the review

1. **§2.2 decision 1** — netting all of `M` is a *physical* choice that
   knowingly diverges from the MC's residual. Is disclosure enough, or should
   the payload carry both windows (all-`M` and must-take-only) so the size
   of the Phase-12a seam is visible in the window itself?
2. **§2.2 decision 2** — availability, not derated availability. The
   alternative (`(1−q)·profile`) is what the margin credits; is there a
   reading under which that is the right series for the window?
3. **§2.5** — is `netted_mw == 0` the right test for "empty", or should it
   be "no member of `M` is active and built", which differs on a period
   where a built farm's profile happens to be zero throughout?
4. **§3** — the docstring wording. Is a qualified sentence enough, or should
   the module carry the counterexample fixture in prose so a reader can
   reconstruct it?
