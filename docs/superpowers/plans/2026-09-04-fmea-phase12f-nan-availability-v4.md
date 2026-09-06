# Phase 12f — a missing `_pu` bound must mean what an absent one means (plan v4)

**Status:** proposed. v1, v2 and v3 were all **rejected** — ten blockers
between them, every one reproduced, every one mine. v4 keeps only the
diagnosis, which has survived three reviews, and rebuilds the fix around the
finding that killed v3: **routing the repair into the read paths makes the
adequacy surfaces more optimistic, not less.**

## 0. The defect (unchanged through three reviews)

A non-finite hour in a varying `_pu` column does not clamp a bound — linopy
**masks that hour's constraint row out of the problem**. Measured, PyPSA 1.3.0
/ HiGHS:

| network | dispatch |
|---|---|
| `p_max_pu=[0.5,NaN,1.0]`, `p_nom=100`, load 500 | `[50, **500**, 100]` — 5× nameplate |
| `p_min_pu=[0,NaN,0.9]`, dear unit, cheap slack | `[0, **−900**, 90]` — runs as a load |

`routers/network.py:2705` states the belief that hides it — *"PyPSA's
default-fallback handles those at solve time"*. It does not.

## 1. What the three rejections established

1. **Fill `0`** — wrong for half the columns (`StorageUnit.p_min_pu` defaults
   `−1.0`; `Store.e_max_pu`, `Line.s_max_pu` default `1.0`), and it makes a
   must-run generator (`p_min_pu=0.5`) with one NaN `p_max_pu` hour go
   `ok/optimal` → **`warning/infeasible`**.
2. **`unpriceable`** — it is `_err` → `validation_failed`; reusing it stops
   networks solving.
3. **The class-defaults table is the wrong number.** It has no asset
   dimension. An *absent* column means **that asset's own static value**:
   with static `p_max_pu = 0.4`, absent gives `[40,40,40]`, the class default
   gives `[100,100,100]`, the static value gives `[40,40,40]`. v3 would have
   credited **2.5×** the asset's own bound — on exactly the units this
   codebase already worries about (a static capacity factor; the PyPSA-Eur
   nuclear CF case).
4. **`varying AND Input (optional)` is far too wide** — measured **59**
   members, only **12** of them bounds. It admits `Load.p_set` (repairing it
   deletes a third of the demand, measured), `marginal_cost` (a €200/MWh unit
   becomes free in the repaired hour; objective 123000 → 82000), `efficiency`,
   `inflow`, `Bus.v_mag_pu_set`, and 12 attributes whose default is itself NaN.
5. **Repairing the READ paths loosens the standard.** On the `_m1_network`
   fixture the margin derate goes `0.2121` → `0.2981` (**+40.5 %**, and more
   optimistic than the true `0.2250`), and `/copt`'s availability for the NaN
   hour goes `0.00` → `1.00`. That collides head-on with
   `validation_service.py:1548` — *"crediting it would mean defaulting its
   derate to 1.0, giving a unit the tool knows nothing about MORE firm credit
   than a gas unit on a class average"* — and it breaks
   `tests/test_adequacy_profiled_units.py:145`, a shipped test that
   deliberately pins `prof[2] == 0.0` and passes today.

## 2. The fix: repair the LP's inputs, and nothing else

**[H1] Scope: the LP only.** The defect is that the LP builds plans the
network cannot deliver. That is what gets fixed. The adequacy engines keep
`NaN → 0` (`copt.py:501`) and `series_is_informative` unchanged, so every
reporting surface stays exactly as conservative as it is today and
`test_a_NaN_hour_is_availability_zero_at_attachment` stays green. v3's G4 —
routing the repair into `/copt` and the margin lever — is **withdrawn**; §1.5
is the measurement that withdraws it.

**[H2] Repair value: the asset's own static value.** `n.static(c).at[name,
attr]`, which already carries the class default when the user never set one.
Never `n.components[c].defaults`. This is provably neutral: measured
identical, hour for hour, to the column being absent.

**[H3] Scope: an enumerated list of bounds, not a metadata rule.**

| component | attributes |
|---|---|
| Generator, Link, StorageUnit, Process | `p_max_pu`, `p_min_pu` |
| Line, Transformer | `s_max_pu` |
| Store | `e_max_pu`, `e_min_pu` |

Twelve pairs, written out. Everything else is out by name, with the reason
recorded: `Load.p_set` deletes demand; `marginal_cost` makes a unit free;
`efficiency`, `inflow`, `standing_loss`, `phase_shift` are not bounds;
`Bus.v_mag_pu` is an `Output` and `Bus.v_mag_pu_set` is a setpoint; the
`*_set` and `ramp_limit_*` attributes default to NaN, so repairing them writes
NaN over NaN and would emit a "repaired" record for a no-op.

**[H4] Where: before the reindex, on the solve path, and it returns a record.**
The fill runs **before** `_normalise_dynamic_indexes`' `if
df.index.equals(snap): continue` (`solver_service.py:4985`), or the commonest
real case — a PUT on the exact snapshot index carrying `null` — is skipped.
It does not mutate the live network: it produces the repaired frame the solve
uses plus `{(component, attribute, column): non_finite_hours}`.

**[H5] Preflight reads the same repaired view.** `validate_for_run`
(`solver_service.py:813`) runs before the normalisation at `:925`, and
`_check_reserve_margin` (`validation_service.py:1858`) emits **ERROR**
severity, which aborts the run (`solver_service.py:821`). Without this a
network can be blocked at preflight on a `max_achievable_mw` computed from a
frame the LP it would have built never uses. This is the one read-side change,
and it is required for correctness, not tidiness. *(Raised as v2's F2, silently
dropped by v3 — recorded here so it cannot be dropped again.)*

**[H6] Disclose from the record.** A **warning**-severity issue naming each
asset and its non-finite hour count. From H4's record, not from the frame:
after any repair there is nothing left in the frame to name.

**[H7] The broadcast fix carries both guards.** Copying the flat→MultiIndex
broadcast (`routers/network.py:2725-2732`) without them replicates a stale
**output** frame across periods — measured: `generators_t.p = [10,20,30]`
becomes `[10,20,30,10,20,30]`, period-0 dispatch served as 2040 results, where
today it is correctly all-NaN. So the copy takes the per-column path's
`_is_input_attr` filter (`:2744-2757`) and its zero-overlap guard (`:2790`),
and output frames stay invalidated. A zero-overlap flat frame must **not** be
repaired: it is not data.

**[H8] No `FINGERPRINT_VERSION` bump.** A bump reports *every* persisted
payload as `stale_report`, including on finite networks. Stated honestly: a
payload persisted before this phase, on a NaN-bearing network, keeps a
matching fingerprint and is compared as current. That is a pre-existing gap
this phase does not close.

## 3. What moves for a user

- **Complete, finite series on a matching index: nothing.** Enumerated in H10.
- **A NaN-bearing network:** the LP stops producing undeliverable plans. A
  generator bounded at its own ceiling instead of unbounded; no more −900 MW.
- **Every adequacy number is unchanged**, because H1 does not touch them. This
  is the difference between v4 and v3, and it is the point.
- **A stale flat frame on a multi-period network** stops silently becoming
  all-NaN — a live data-loss path, fixed as a precondition of H4.

## 4. ★ tests

Each states the path its fixture reaches, because eight tests in this program
have now asserted through a path their fixture never took.

| ★ | claim | fixture / path | bite |
|---|---|---|---|
| **H2a** | static `p_max_pu=0.4` + NaN hour dispatches **40**, identical to the column being absent | index-matched | repair to the class default → 100 |
| **H2b** | `StorageUnit.p_min_pu` static `−0.5` + NaN repairs to `−0.5`, not `−1.0` | index-matched | class default |
| **H3a** | `Load.p_set` and `marginal_cost` are **untouched** by the repair | NaN in each | use the `varying AND Input (optional)` rule → demand deleted, objective moves |
| **H4a** | an index-**matched** frame carrying `null` is repaired | index-matched, via PUT | place the fill after the `equals` short-circuit |
| **H4b** | the live frame still reads `null` at `GET /timeseries` after a solve | HTTP | mutate in place |
| **H5a** | preflight's derate equals the derate the LP is held to | NaN-bearing, margin set | leave `validate_for_run` on the raw frame |
| **H6a** | the warning names the asset and the count; silent on a finite network | both | emit from the frame instead of the record |
| **H7a** | a stale **output** frame stays all-NaN across periods | flat `generators_t.p`, multi-period | copy the broadcast without `_is_input_attr` |
| **H7b** | a zero-overlap flat frame is not repaired to full availability | disjoint timestamps | repair it |
| **H9** | the engines are untouched: `test_a_NaN_hour_is_availability_zero_at_attachment` and the `/copt` payload byte-identical | `_m1_network` | apply v3's G4 |
| **H10** | the anchor | enumerated below | repair to the class default (moves a finite-network number) |

**[H9] is the regression that v3 would have broken**, kept as a named test so
the withdrawn G4 cannot return by accident.

**[H10] The anchor, enumerated and checked to be non-vacuous.** v1 cited a
harness that does not exist; v2 named an unenumerated set; v3 named four files
of which three produce no `/copt` payload at all (`tests/golden/fixture.py`
builds no `p_max_pu` series and no outage data, so `/copt` is 204;
`test_adequacy_report.py` and `test_adequacy_reserve_margin.py` contain zero
copt/mc references). H10 is built from the fixtures that **do** produce one —
`tests/test_adequacy_profiled_units.py`'s networks and
`tests/test_adequacy_copt.py`'s — and the plan asserts up front that each
yields a non-204 payload, so the anchor cannot be vacuous. Its bite (repair to
the class default) moves a finite-network number, so an identity-only variant
cannot pass it.

## 5. Out of scope

- Any change to the engines' `NaN → 0`, `series_is_informative`, `/copt` or
  the margin lever's read semantics (H1).
- Rejecting non-finite input at the six write paths; `GET /timeseries` renders
  non-finite as `null`, so a rejection needs a read-surface change too.
- The static-CF per-asset flag; the `validate` route's TOCTOU.

## 6. Live area

**S29:** upload a profile containing a `null` over the API, solve, assert the
served dispatch respects the asset's own ceiling and the disclosure names it —
bitten live by removing the repair. `QA_E2E_PLAN.md:535` ends at S28.

---

## v4 REVIEW — rejected (4 blockers). Recorded rather than deleted.

1. **"Non-destructive" is unimplementable.** The LP reads `n` directly
   (`solver_service.py:1247`, `:1186`, `:6480`), so there is no seam to
   substitute a frame into: H4 necessarily mutates. Both escapes break a
   different v4 claim — mutating without an undo falsifies H1/H4b/H9
   (measured: the engines' profile goes `0.0 → 1.0`, i.e. the H9 regression),
   and repairing early enough for H5 is wiped at `:906`, where
   `_reapply_user_ts_to_network` concats the raw `_user_ts` back
   (`routers/network.py:2812`). This is v3's contradiction relocated.
2. **The STATIC frame carries the same defect and v4 never mentions it.**
   Measured: static `p_max_pu = NaN` with **no varying column at all**
   dispatches `[500, 500, 500]` — 5× nameplate every hour. Reachable over
   HTTP: `PATCH /network/_bulk` with `null` writes `float("nan")`
   (`routers/network.py:2027`), which is what the bulk toolbar sends for a
   blank cell (`BottomPanel.tsx:120`). And because H2 takes its repair value
   *from* the static frame, a NaN static makes the repair a NaN-over-NaN
   no-op — the fix's own source is broken.
3. **`ramp_limit_*` was excluded on a rationale that contradicts H2.** v4
   excluded it because the *class* default is NaN; H2's repair value is the
   *asset's static*, which is well defined. Measured, static
   `ramp_limit_up = 0.1`: `[0.1, NaN, 0.1, 0.1]` dispatches
   `[0, 100, 100, 100]` against `[0, 10, 20, 30]` — one NaN hour removes the
   ramp constraint for the **whole horizon**. Six pairs.
4. **H7a's premise is false on the primary path.**
   `_reapply_user_ts_to_network` runs at `:906`, before the repair point, and
   its pre-pass at `routers/network.py:2723-2732` already performs the
   unguarded flat→MultiIndex broadcast. So "today it is correctly all-NaN"
   holds only where the reapply is skipped, and the shipped bug at `:2725` is
   left unfixed.

Majors: H2 can turn a solving network infeasible (static `p_min_pu = 0.5`
with a NaN hour) and §3 does not say so; the record's behaviour across the
helper's four invocations is unspecified; and H10 dropped
`test_adequacy_reserve_margin.py` on a false premise — it has 20 `p_max_pu`
references and builds outage-bearing networks.

**Verdict on the approach, not just the version.** Four plans, fourteen
blockers, none of which reached code. The errors are narrowing, but the
finding that ends v4 is a scope finding: the defect is not "an availability
hour" — it is **any non-finite value in any LP bound, in either the static or
the dynamic frame, reachable from several HTTP surfaces**. A repair-based fix
has to answer "what value?" separately for each of those, and every version so
far has got that answer wrong in a new way. Rejecting non-finite values at the
write boundaries needs no answer to that question at all, cannot make anything
infeasible, and cannot loosen any standard. That is the pivot v5 should
consider before another repair design.
