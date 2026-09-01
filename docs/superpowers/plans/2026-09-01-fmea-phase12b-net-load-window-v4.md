# Phase 12b — the net-load window (plan v4)

Fourth plan for Phase 12; second for its step A. **v3**
(`2026-09-01-fmea-phase12b-net-load-window.md`) was rejected with two
blockers, four serious and five minor, every load-bearing one re-verified
before acceptance. Its review also found a **defect shipped in Phase 8** — the
reserve-margin payload credited zero to every vintage-expanded asset — and a
stash-lifecycle leak on both standards. **Both are fixed in `2aa4dcd`, before
this plan and as its precondition**, with three unit tests and a live suite
(S22) each demonstrated to bite. v4 is v3 with its two false premises replaced
and each of the eleven findings answered by number.

| v3 finding | v4's answer | § |
|---|---|---|
| **B1** vintage rows and their cloned profile columns are gone at payload time; shipped payload wrong there | payload defect fixed (`2aa4dcd`); **the profile leg is now stashed at wrapper time**, never read off the post-restore network; capacity read through the payload's own vintage-aware `_built` | §1 |
| **B2** `M` = "has a `p_max_pu_t` column", so thermal capacity nets out of "net load" | netting population = members whose stashed profile is a **non-constant** time series; a flat column cannot move a window and is never netted; copy says "profile-bearing capacity", never "VRE"; every row carries `netted: bool` | §2.2 |
| **S3** docstring mechanism backwards ("tight fleet") | corrected: a marginal firm block is counted in every hour; the group is capped at `max_h(Σ)`; tightness *dampens*; loose-fleet table carried | §3 |
| **S4** stash not reliably deleted; leaked stash republished | fixed in `2aa4dcd`: both stashes cleared at solve start, two tests | §2.6 |
| **S5** myopic unaddressed; live path untested here | stated: one period only, by the same mechanism every margin field already has; live myopic named as untested in this environment | §2.5 |
| **M6** 12c gate not actionable; three review items dropped | gate named as its own phase with its recorded requirements; the three items restored | §7 |
| **M7** `_built` vs `solved_capacity` are two rules | v4 uses **`_built`**, and says why | §1 |
| **M8** `_peak_window` omits the override; NaN unhandled | helper carries `prm_peak_hours`; net series built with `fillna(0.0)`; finiteness asserted; NaN in the sanitiser test | §2.3, B10 |
| **M9** `null` with a `reason` is self-contradictory; wrong emptiness test | a **status-bearing** block, always present; emptiness = "no row in P has `netted=True`" | §2.4 |
| **M10** stale line references | corrected throughout | — |

---

## 0. What did not change, and why

The architecture. `reserve_margin_payload` is still the one post-solve point
where the stashed (scaled) demand and the built plan meet, and its own
docstring still states the discipline (`report.py:81-95`): *every
demand-derived number comes from the stash; only the capacities are read
back.* v3's mistake was reading a **third** thing off the network — the
profiles — and assuming they survived restore. v4 removes that read. After
this plan the payload reads **exactly one** thing off the network: capacity,
through `_built`, which as of `2aa4dcd` knows how to find a vintage.

Post-solve rather than preflight: unchanged, for the reason v3 §0 gave.
The split of the phase: unchanged, and §7 makes the other half's gate
actionable.

---

## 1. The one place — with its three legs stated correctly

| leg | v3 said | what is true | v4 does |
|---|---|---|---|
| demand | stash | stash ✔ | stash `periods[P]["demand_mw"]` |
| capacity | `solved_capacity(row)` on the live row | the live row **does not exist** for a vintage; `solved_capacity` cannot be called on it (v3 review B1). And the payload already has its own capacity rule, `_built`, which is the rule `firm_mw` is computed with | **`_built(row)`** — vintage-aware since `2aa4dcd` — so the window and the credit it judges share one capacity rule. Two rules would be two standards (v3 review M7) |
| profile | `n.generators_t.p_max_pu` post-restore, "bit-for-bit the wrapper's" | **false for every vintage column** — `vintage_service.py:887-899` writes cloned columns and the restore drops them; `_normalise_dynamic_indexes` also reindexes every `_t` frame in place | **stash the profile at wrapper time**: `assets[i]["profile"]` = the member's `profile` Series restricted to the period, exactly the object the derate was computed from (`solver_service.py:3505-3510`), or `None` |

So at payload time nothing demand- or profile-derived is read from the
network. Only capacity, through one function.

**Memory.** A stashed profile per (member, period) that passes §2.2's
predicate. Constant and absent profiles are not stashed (they are never
netted), so the set is the VRE-and-maintenance set, not the fleet. A 300-farm
clustered network at 8760 h × 1 period is ~2.6 M floats, ~21 MB, transient:
the stash is deleted at the report step and, since `2aa4dcd`, cleared at
solve start. §8 Q2 asks whether that needs a cap.

---

## 2. Design

### 2.1 Stash contract (spec §2.6 → v1.3)

Per period: `demand_mw: pd.Series` (that period's scaled demand, indexed by
its snapshots). Per asset row: `profile: pd.Series | None` (§1) and
`netted: bool` (§2.2). In memory only; never serialised. `test_stash_shape`
asserts the key sets with `==` and is updated in the same commit, docstring
saying why (v3 review S8).

### 2.2 The netting population, defined so a gas unit cannot enter it

A member is **netted** when all three hold:

1. its stashed `profile` is a time series, **not** `None`;
2. that series is **non-constant** — `max − min > 1e-9` over its finite
   values;
3. its built capacity, `_built(row)`, is `> 0`.

```
net_P = demand_P − Σ_{m netted, active in P} fillna(profile_m, 0) × _built(m)
```

**Why non-constant, and why that is stricter than Phase 12a's predicate.** A
window is selected by *ordering*, and subtracting a constant from every hour
changes no ordering. So a flat all-ones column (an upload artefact) or a flat
0.9 column (a static derate typed into a column) can never move the window
— and netting one would only pollute `netted_mw` and let the panel report a
gas unit as netted capacity, which is exactly v3's BLOCKER 2. Phase 12a's
`_profile_is_informative` (`validation_service.py:1673-1700`) accepts a flat
0.9, **correctly for its purpose**: a flat derate *is* a profile the engines
discard, so it deserves the shadowed-profile warning. The two predicates
differ because they answer different questions, and §2.2 says so rather than
reusing the wrong one.

**Thermal maintenance schedules ARE netted**, and this is a decision rather
than a side effect. A schedule that is 1.0 except for a fortnight at 0 is
non-constant, so it moves the window — and it should: the question is *when
does the system run short*, and a 200 MW unit on planned outage in that
fortnight is 200 MW the system does not have. The margin itself credits that
unit at `(1−q) · mean(profile over window)`, so netting it is consistent with
the margin's own model of the unit. What changes is the **copy**: the panel
says *profile-bearing capacity netted*, never *VRE netted*, and every asset
row carries `netted: bool` so a user sees precisely which units shaped the
window. §8 Q1 puts the decision to the reviewer.

Members the margin excluded (`unpriceable`) are not in the stash and are not
netted. Storage (`profile is None`) is not netted and reports `derate_net:
null` (v3 review M11, kept). Netting is at **availability**, not derated
availability — v3 decision 2, kept: outages are what the margin is for, and
the window is about the hours.

### 2.3 One window rule, shared, and carrying the override

`_peak_window(series, *, n_override: int | None)` is extracted from the
inline code (`solver_service.py:3487-3498`) and applied to both series with
the same `prm_peak_hours` override (`3488`; v3 review M8). NaN cannot reach
it: the net series is built from `demand_P` (already `fillna(0.0)` at
`3319`) and `fillna(0.0)` on every stashed profile, mirroring the facts
loop; the helper asserts finiteness and a test pins that a NaN profile value
nets as zero. An empty window is impossible by construction and asserted.

### 2.4 What is reported — a status-bearing block, always present

Per period, `net_window` is **always** an object (v3 review M9):

| key | meaning |
|---|---|
| `status` | `"ok"` or `"nothing_netted"` |
| `netted_assets` | names of rows with `netted=True` in this period |
| `snapshots`, `n_hours` | the net window (form of `peak_snapshots`) — `[]`, `0` when `nothing_netted` |
| `net_peak_mw`, `gross_at_net_peak_mw`, `netted_mw`, `overlap_hours` | as v3 §2.4 — `null` when `nothing_netted` |
| `firm_gross_mw`, `firm_net_mw` | Σ `derate × built` and Σ `derate_net × built` over rows with a profile, at `_built` capacities |

**Emptiness** is *"no asset row in P has `netted=True`"* — not `netted_mw ==
0`, which v3's review showed reads as empty for a built vintage member whose
capacity the old payload could not find. The panel renders
`nothing_netted` as *"no profile-bearing capacity in the built plan; the
net-load window is the gross window"*, and never publishes a net window
identical to the gross one with zero deltas as if it were a finding.

Per asset row: `derate_net` (`null` when `profile is None`), `netted`.

### 2.5 Myopic: one period, by the mechanism every margin field already has

**Verified**: the wrapper's `n._reserve_margin_targets = stash` is
unconditional per call (`solver_service.py:3670`), and `_run_myopic_foresight`
passes `extra_functionality=extra_fn` once per period iteration with that
period's snapshots (`6470`, `6488`, `6684`). So each iteration overwrites the
stash, and at payload time only the **last** period's stash exists — for
`peak_mw`, `firm_mw`, `met`, and now `net_window` alike. The preflight already
warns exactly this (`validation_service.py:1585-1600`). v4 adds nothing new
to that behaviour and says so in the panel copy: *last period solved*.

**Not verified here**: the live myopic + margin path crashes in this
environment inside PyPSA's `_set_dynamic_data` (pypsa 1.3.0 / pandas 3.0.5),
with and without a margin, and the shipped
`test_myopic_build_period_visibility.py` fails identically — an environment
fault, but it means no live coverage exists for that path in this container.
§8 Q3 asks whether `net_window` should be **withheld** under myopic rather
than published for one period.

### 2.6 Lifecycle

Fixed in `2aa4dcd`: both `_reserve_margin_targets` and `_ens_cap_targets` are
cleared at solve start, so a run that fails between the wrapper and the
report step can no longer leak a stash — and now a demand Series and a set of
profiles — into the next solve. Two tests, one per stash, each bitten.

### 2.7 What it is NOT

A second proxy, in the margin's own units. `derate_net` says what the derate
*would have been* on the net-load window; it is not called "corrected". And
**netted capacity is not VRE**: it is every unit whose availability varies
over the horizon, and the row-level flag says which.

### 2.8 Contract changes, all named

Spec §2.6 (`demand_mw`, `profile`, `netted` in the stash), §4 (`net_window`
per period; `derate_net`, `netted` per asset), §6 (panel column and copy) →
**amendment v1.3**. `test_stash_shape` and the payload-shape tests updated in
the same commit. `sanitize_reserve_margin_payload` gains a descent into
`net_window` so a NaN or `inf` there can never 500 the route (v3 review M8),
with a test that puts one there.

---

## 3. The docstring — mechanism corrected, measured, and carried

`elcc.py` says, twice, that a sum of last-in credits **UNDERSTATES** a
portfolio. v3 proposed qualifying that with *"when members do not overlap and
the fleet is tight"*. The second clause was **backwards**. Re-measured,
varying only the thermal fleet against two farms that never overlap (A on
hours 0–9, B on 10–19, 100 MW each; seed 0, draws 64):

| thermal MW | slack vs load | Σ marginals | portfolio | Σ / portfolio |
|---|---|---|---|---|
| 150 | +0 | 120.31 | 100.00 | **1.20** |
| 180 | +30 | 200.00 | 100.00 | **2.00** |
| 210 | +60 | 200.00 | 100.00 | **2.00** |
| 240 | +90 | 200.00 | 100.00 | **2.00** |

**The overstatement grows as the fleet loosens.** Mechanism: a marginal
credit is a firm block counted in *every* hour — including the ten the
removed farm never served — while the group's credit is capped at
`max_h(Σ profile)`, the most the pair can deliver at any instant. Non-overlap
alone produces it; tightness *dampens* it, because shared-hour LOLE trades
some of the block back (hence 60 rather than 100 at zero slack). Note the
loose-fleet marginals sit exactly on their bracket ceiling — the v2-review
BLOCKER 5 correction in action.

Wording for both docstrings: *"On fixtures whose members share peak hours the
sum of last-in credits understates the portfolio. It can OVERSTATE — by up to
2× measured — when members do not overlap, and the effect grows as the fleet
loosens: each marginal is a firm block credited in every hour, including those
the removed asset never served, while the group is capped at the most it can
deliver at once."* The module docstring carries the fixture in prose so a
reader can reconstruct it (v3 §8 Q4: yes). B7 pins the zero-slack row; B7b
pins one loose-fleet row.

**Task 0**, landed on its own before §2.

---

## 4. Acceptance

Every ★ names its bite; a bite is demonstrated to fail before the test
counts; every restore is verified by hash. Tests marked ✔ were verified to
bite by the v3 review and are kept as designed.

★ **B1 ✔** — net window content on a flat-load / on-off-profile fixture: net
window = hours 10–19 exactly, ties included. *Bites: select on gross (20
hours); `nlargest` (1 hour).*

★ **B2 ✔** — demand comes from the stash: overwrite `loads_t.p_set` after
facts, guard that the two candidate windows differ, assert the stashed one is
used. *Bite: re-read demand from `n`.*

★ **B3 ✔ + B3b (new)** — capacity through `_built`: an extendable with
`p_nom_opt = 80` set by hand is netted at 80 (*bite: read `p_nom`*); **and a
vintage row** — the `2aa4dcd` fixture, `wind@2030` built to 36.84 MW — is
netted at 36.84 (*bite: look the name up in the live `p_nom_opt` table → None
→ 0 → the empty branch fires for a plan that built it*). This is v3 review
B1 as a regression test on the netting path.

★ **B4 ✔** — `profile is None` ⇒ `derate_net` null, `netted=False`.

★ **B5′** — the empty case is a status: no netted rows ⇒ `net_window.status
== "nothing_netted"`, `snapshots == []`, numeric fields `null`, and the
block is present. *Bite: publish the gross window as the net window with
zero deltas.*

★ **B6 ✔** — a shadowed farm (profile + outage data) IS netted; `netted_mw`
asserted **exactly** against both farms' contribution, not approximately
against half.

★ **B7 ✔ + B7b (new)** — the sum can overstate: zero-slack row (60.16 /
60.16 / 100.0 at `tol 0.5`; *bite: group removal shares the baseline residual
→ 0.0*), and one loose-fleet row (100 / 100 / 100 at +30 MW slack; *bite:
same*).

★ **B8 (new)** — the netting predicate: an all-ones column and a flat 0.9
column are **not** netted (`netted=False`, `netted_mw` excludes them); a
maintenance schedule (1.0 except ten hours at 0) **is**. *Bite: use Phase
12a's `_profile_is_informative` — the flat 0.9 column is then netted.* This is
v3 review BLOCKER 2 pinned.

★ **B9 (new)** — the profile comes from the stash: after facts, **drop the
member's `p_max_pu` column from the network** (what restore does to a vintage
column), then call the payload; `derate_net` is still computed and equals the
value from the stashed series. *Bite: read `generators_t.p_max_pu[name]` in
the payload → the column is gone → `derate_net` null or KeyError.* This is v3
review BLOCKER 1(b) pinned.

★ **B10 (new)** — a NaN in a stashed profile nets as zero, the window is
non-empty, and the payload serialises with no NaN on the wire. *Bite: skip
`fillna` → `net_peak_mw` NaN → the sanitiser test fails.*

**B11 — contract pins (not bites):** `test_stash_shape` gains `demand_mw`,
`profile`, `netted`; payload-shape tests gain `net_window` and the two asset
fields; the sanitiser test gains a `net_window` with a NaN and an `inf`
beside an unbounded extendable.

Dropped from v3: **A4-style "changes no solve"** (true by construction for a
post-solve computation); v3's **B5** (null-with-reason, self-contradictory).

---

## 5. Frontend

`ReserveMarginPanel.tsx`: a `derate_net` column beside `derate` (test-id
`rm-asset-derate-net-${id}`, `—` with a title when null) and a `netted`
marker on each row. Period summary: *net-load window N h; K of M hours shared
with the gross window; profile-bearing capacity netted X MW; firm credit Y →
Z MW*. `nothing_netted` renders its sentence. Under myopic the block carries
*last period solved*. Copy never says "corrected" and never says "VRE" for
the netted set. Mount tests in both statuses. `ReserveMarginAsset` /
`ReserveMarginPeriod` in `src/api/simulation.ts` gain the fields.

---

## 6. Cost

Window selection 0.91 ms per period (measured, v2 §0.1). One pass over the
netted set. Stash memory as §1. No study, no thread, no 409 mesh.

---

## 7. Phase 12c, and a gate that names its work

The portfolio ELCC is deferred behind **Phase 12c-pre: modelling a
generator that carries both a profile and outage data** — the fix Phase 12a
§2(a) recorded as "its own phase, with a benchmark re-run", and which nothing
has yet scheduled. Its brief, so it is actionable rather than indefinite: a
spec for how the COPT and the sequential MC represent a profile-bearing
occurrence unit (a multi-state unit from the profile, or a two-state unit
whose available capacity follows the profile), the four RTS-79/RBTS anchors
re-run and held, and the `outage_shadows_profile` warning retired or
re-scoped by that spec. **Only after 12c-pre** does an ELCC-vs-proxy
comparison have a population on which it means anything.

Carried into 12c's brief, including the three v3 dropped (v3 review M6):

- define `V`, `M`, `S` as the code defines them, `S` by 12a's predicate;
- the `|V|, |M|, |V ∩ M|, |S|, extendable/fixed` census in place of v2's A6;
- a synchronous 422 for an empty population and a status row, never `0.0, ok`
  (v2 review B6);
- the load basis for the ELCC half (v2 review S7);
- `max_h(Σ)` is the right ceiling **for the dominance reason** and a wider
  bracket is inert when the step is interior — so 12c's A10 needs a bite
  that can fail (v2 review B5, as corrected);
- `_resolve`'s 422 for a shadowed name is the mechanism that makes `S`
  unpriceable — reuse it, do not fork it;
- the N+1 baseline fix with a CRN-safety test (v2 review M10);
- reconcile the two timing sets (264 s vs 46 s per marginal).

---

## 8. Open questions for the review

1. **§2.2 — maintenance schedules are netted.** The argument is that the
   margin already models the unit that way. The counter-argument is that a
   user reading "profile-bearing capacity netted 200 MW" beside a wind panel
   will read it as VRE regardless of the flag. Net them, or exclude
   occurrence-bearing units from the netting and report them separately?
2. **§1 — the profile stash.** ~21 MB transient on a large clustered network.
   Accept, cap the number of stashed members, or stash only the period-window
   candidates (which would need the window first — circular)?
3. **§2.5 — myopic.** Publish `net_window` for the last period with the
   caveat every other margin field already carries, or withhold it under
   myopic entirely, on the grounds that a one-period window beside a
   per-period proxy invites the Phase-4 mistake?
4. **§1 — one capacity rule.** v4 reads through `_built`. Should
   `solved_capacity` also learn vintages (via `n.meta["vintage_results"]`) so
   the adequacy engines and the payload agree on every row, rather than only
   on non-vintage ones — and is that this phase's work?
