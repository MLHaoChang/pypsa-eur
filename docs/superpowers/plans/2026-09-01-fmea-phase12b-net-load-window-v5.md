# Phase 12b — the net-load window (plan v5: the delta on v4)

v4 (`2026-09-01-fmea-phase12b-net-load-window-v4.md`) was rejected
*narrowly*: its architecture, premise, predicate, myopic treatment and
sanitiser analysis all survived verification; its acceptance section and two
contract surfaces did not. v5 is **v4 with the nine findings applied** and
nothing else. Every v4 section not named below is adopted **unchanged**, and
the v4 file with its review appended remains the record of why each decision
was made.

| v4 finding | v5 |
|---|---|
| B1 — B3b's fixture has a constant profile | a new vintage fixture whose cloned profile is non-constant, with the built size derived and stated (§4) |
| S2 — `netted` is two predicates; `None > 0` | stash `nettable` (conditions 1–2), payload `netted` (adds 3, null-safe) (§2.1, §2.2) |
| S3 — pydantic drops the new keys | `NetWindowBlock` + two asset fields on the models; report block pinned equal to route payload (§2.8) |
| S4 — B10 mis-described; "mirroring" false | two NaN rules, each stated; B10 asserts window content and `derate_net` (§2.3, §4) |
| M5 — NaN demand reaches the window | no finiteness assertion; degrade to a status (§2.3, §2.4) |
| M6 — line references | corrected (§0) |
| M7 — §3 wording | lead with the cap algebra; k× not 2×; qualify the test docstring too (§3) |
| M8 — period-constant copy | "constant in this period" (§5) |
| M9 — §7 quotation; §2.6 reason | fixed (§7, §2.6) |

---

## 0. Line references, corrected once

Post-`2aa4dcd` (`solver_service.py`): demand `fillna` for time-series loads
**3333**; the peak window **3502-3514**; `prm_peak_hours` **3507**; the
derate `mean()` over the window **3525-3527**; the wrapper's unconditional
stash assignment **3670**; myopic `extra_fn` per iteration **6470 / 6488 /
6684**. `validation_service.py`: `_profile_is_informative` **1673-1700**; the
myopic one-period warning **1604-1613**. `report.py`: the payload docstring
**81-95**, `_built` **147-155**. `vintage_service.py`: cloned-column writes
**~885-899**. The v4 sentence about `_normalise_dynamic_indexes` is
withdrawn: it replaces frames only on mismatch and runs before the wrapper,
so it cannot affect the stash.

---

## 2.1 Stash contract — `nettable`, not `netted`

Per asset row the stash carries `profile: pd.Series | None` and
**`nettable: bool`** = v4 §2.2 conditions 1 and 2 (a time series that is
non-constant over its finite values, in this period). Built capacity is
unknown at wrapper time, so the stash cannot carry condition 3, and a field
whose meaning changed downstream would be a field `test_stash_shape` pinned
wrongly. `demand_mw` per period as v4.

## 2.2 The netting population — condition 3 made null-safe

At payload time a row is **`netted`** when `nettable` and
`cap is not None and cap > 0` where `cap = _built(row)`. `_built` returns
`None` for an extendable with no live row and no `vintage_results` entry;
`None > 0` raises, lands in the payload's broad `except`, and drops the whole
margin block — so the `None` test is written out, and a `None`-capacity row
is `netted=False` with `derate_net` still computed (it needs no capacity).
Everything else in v4 §2.2 — the non-constant predicate, why it is stricter
than 12a's, maintenance schedules netted, copy says "profile-bearing
capacity" — stands.

## 2.3 NaN — two rules, both stated, no assertion

`derate` is `mean()` over the gross window, **skipna** (3525-3527): a NaN
availability hour is *absent* from the derate. v4 claimed the netting
"mirrors the facts loop" and it does not. v5 states two rules and why they
differ:

- **Window selection** nets NaN as **0** (`fillna(0.0)` on the stashed
  profile): an hour whose availability is unknown must not be assumed
  available when asking *when does the system run short*. pandas would
  otherwise skip the hour silently — the actual defect, a missing hour, not
  a NaN on the wire.
- **`derate_net`** is `mean()` over the net window, **skipna** — the same
  function as `derate` on a different window, so the two numbers differ only
  by the window and a NaN moves them identically.

**No finiteness assertion.** A static `p_set` of NaN passes through the
facts loop (`float(nan or 0.0)` is `nan`), the peak is NaN, and today the
sanitiser nulls it and the block ships. v4's assertion would have lost the
block. v5 degrades: if the net series has no finite value, or the window
comes back empty, `net_window.status = "no_finite_demand"` with the numeric
fields `null`. The static-branch wart is recorded here and left to its own
fix; it is not this phase's.

## 2.4 Status enum

`net_window.status ∈ {"ok", "nothing_netted", "no_finite_demand"}`; block
always present; everything else as v4 §2.4.

## 2.6 Lifecycle — the reason stated

`2aa4dcd` clears both stashes at solve start only, not in the outer handlers
as v3's review also asked. That suffices **because nothing reads either
attribute between solves** — the reviewer grepped: no reader outside
`solver_service.py`, and `_diagnose_infeasibility` reads only `periods` and
`margin` from a local copy. A stash that lingers after a failed run is inert
until the next solve's first line deletes it.

## 2.8 Contract surfaces — the one v4 missed

`models/adequacy.py` gains `NetWindowBlock` (the §2.4 fields, `status` as a
`Literal`), `ReserveMarginPeriod.net_window: NetWindowBlock`,
`ReserveMarginAsset.derate_net: float | None` and `netted: bool`. Neither
model sets `extra`, so pydantic's default `ignore` would otherwise discard the
keys silently and the adequacy report would drift from the route. **A test
pins `report.reserve_margin.by_period[i].net_window == route
payload["by_period"][i]["net_window"]`** — the two surfaces amendment v1.2(7)
requires to agree. Spec §2.6/§4/§6, `test_stash_shape`, and the sanitiser
descent as v4.

---

## 3. The docstring — the algebra first

The loose-fleet table stands (re-run by the reviewer, every row exact). The
wording leads with the cap: for **k** members that never overlap, each
marginal's bracket ceiling is its own `max_h(p_i)` while the group's is
`max_h(Σ p_i)`, so when every marginal saturates the sum is
`Σ_i max_h(p_i) / max_h(Σ p_i)` = **k×** the portfolio — 2× on the two-farm
fixture, not a bound. Tightness *dampens* it: with shared-hour LOLE a
marginal is a firm block credited in every hour, including those the removed
member never served, and that offsets part of its own hours, hence 60 rather
than 100 at zero slack. Wording: *"On fixtures whose members share peak hours
the sum of last-in credits understates the portfolio. It OVERSTATES when
members do not overlap — by up to k× for k such members, since each is
bracketed by its own peak while the group is capped at the most it can
deliver at once — and the effect grows as the fleet loosens."*

Applied to **`elcc.py:228`** (the one UNDERSTATES sentence), and to
`tests/test_adequacy_elcc.py:300-310`, whose docstring states the direction
as a general claim; the module docstring and `McPanel.tsx` are already
direction-neutral and untouched.

---

## 4. Acceptance — the changed tests

★ **B3b (replaced)** — a vintage fixture that §2.2 nets. `_vintage_network`
with wind's profile **alternating 1, 0, 1, 0** across each period's four
hours (cloned to the vintages by the expansion). Flat load ⇒ the gross window
is all four hours (tied) ⇒ `derate = mean = 0.5` (must-take, q = 0). Required
225, base firm 190 ⇒ 35 MW firm ⇒ **the LP builds 70 MW of `wind@2030`**
(within its 100 MW bound). Assertions, per period: `netted=True` for
`wind@2030`; `netted_mw = 35` (70 × mean 0.5); the net series is
`150 − 70·[1,0,1,0] = [80,150,80,150]` ⇒ **net window = hours 1 and 3**;
`derate_net = 0.0` — the wind is available only when load is already served,
which is the phase's whole point on one fixture. *Bite: look the vintage up in
the live `p_nom_opt` table → `None` → `netted=False` → `nothing_netted`.* Run
before it counts; the 70 is derived, and if the LP builds something else the
derivation is wrong, not the test.

★ **B10 (rewritten)** — a NaN at hour *h* in a stashed profile: (a) hour *h*
is **in** the net window (netted as zero, so net load there is the gross
load); (b) `derate_net` equals the skipna mean over that window; (c) the
payload serialises. *Bite: skip `fillna` → hour h drops out of the window
(pandas skips it) → (a) fails.* Not a NaN on the wire — that was never the
defect.

★ **B12 (new)** — `_built` returning `None` on a netted-candidate row yields
`netted=False`, a computed `derate_net`, and an intact margin block. *Bite:
`cap > 0` without the `None` test → `TypeError` → the whole block is gone.*

★ **B13 (new)** — a static `p_set` of NaN yields `status="no_finite_demand"`
and the block still ships. *Bite: assert finiteness → block lost.*

★ **B14 (new, contract)** — the adequacy report's `net_window` equals the
route payload's, field for field, on the B3b fixture. *Bite: leave the
pydantic models unchanged → the report's block is `None` while the route's
is populated.*

B1, B2, B3, B4, B5′, B6, B7, B7b, B8, B9, B11: as v4, unchanged; their bites
were verified by the v4 review.

---

## 5. Copy

As v4 §5, plus: a row whose profile is constant *within this period* but not
elsewhere renders `derate_net` as `—` with the title *"constant in this
period — window-independent"*, not "no profile".

## 7. Phase 12c — the quotation fixed

12a §2(a) *recorded the fix as a decision for its own phase with its own
benchmark re-run* — paraphrase, not quotation. Everything else in v4 §7
stands: Phase 12c-pre as the named gate, its brief, the carried items.

## 8. Open questions

v4's four stand. One added: **§2.3's two NaN rules** — is it acceptable that
the window treats an unknown hour as unavailable while the derate ignores it,
on the grounds that they answer different questions, or should `derate_net`
(and therefore `derate`, which it must match) also treat NaN as zero?
