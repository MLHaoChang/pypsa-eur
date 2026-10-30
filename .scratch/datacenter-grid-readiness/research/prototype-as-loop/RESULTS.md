<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# PROTOTYPE results: does the AS sizing loop converge?

Throwaway prototype for owner decision AS-PROTO-1. It tests the loop proposed in
[`../ai-load-smoothing-sizing-loop.md`](../ai-load-smoothing-sizing-loop.md), sections 4.2 to 4.4,
before any AS issue is filed. The script is [`as_loop_prototype.py`](as_loop_prototype.py). Run it with
`python as_loop_prototype.py`. It needs PyPSA 1.1 and HiGHS, takes about 2.5 minutes, and prints every table below.

**Verdict: converges with changes.** The note's cuts end in 2 or 3 rounds in 14 of 15 cases. In the
remaining case they creep for 9 rounds and fail the 5-round rule. They also buy 2 to 9 times more reactive
capacity (PCS oversize or STATCOM) than needed. Four changes fix both problems: all 15 cases then pass every
check in 2 or 3 rounds. Total cost is 0.04 to 0.5 % lower in 14 cases and 0.2 % higher in one (weak-flat B).
No case oscillates.

## 1. Setup

All numbers are round, illustrative values for a generic 100 MW campus. The limits are generic engineering
defaults marked "assumed". No client document was used (SAGC-1).

**Campus.** The LP covers one week at hourly resolution, annualised:

- PoC bus → transformer (an extendable `Link`, library 63/80/100/125 MVA) → MV bus;
- GPU load: 70 MW idle, plus 30 MW in training hours (about 80 % of hours), plus a 5 MW cooling cycle;
- critical load P_crit = 80 MW;
- price: a 60 ± 40 €/MWh daily shape plus a 15 €/MWh network charge; export earns 0.9 × the price.

**Storage.** Each storage technology is a `Store` with charger and discharger `Link`s (section 4.2.1), so
power and energy are separate variables, bounded by an E/P range in hours:

| Technology | E/P range | GFM-capable |
|---|---|---|
| Energy BESS | 2–8 h | yes |
| High-C BESS | 0.25–1 h | yes |
| Supercap | 5–60 s | no |

The LP also has:

- a smoothing slice per technology (P_sm, E_sm) with the per-hour headroom and SOC band of 4.2.4;
- PCS oversize S_x (MVA above rated P);
- STATCOM Mvar;
- a genset with p_nom ≥ P_crit and a 100 h/yr run-hour cap.

| Intent | Technologies offered | Backup constraint |
|---|---|---|
| A | energy BESS, high-C, supercap, STATCOM; no genset | SOC ≥ P_crit × **4 h** (AS-AUTONOMY-1) plus E_sm/2; power headroom ≥ P_crit every hour |
| B | high-C BESS, STATCOM, genset | genset only; the LV UPS is kept (not modelled) |
| D | energy BESS, high-C, STATCOM, genset | bridge SOC ≥ P_crit × 0.25 h; power headroom ≥ P_crit every hour. **No UPS: confirm against the grid code** (AS-NOUPS-1) |

**Envelope and limits.**

- Envelope: swing S = 30 MW, oscillation A = 10 MW at T = 2 s, checkpoint dip 20 MW for 30 s, load
  reactive swing tan φ_swing = 0.1.
- Limits: ramp 10 MW/min, ΔP 10 MW per 5 s, Pst ≤ 0.35, ESCR ≥ 3 with a GFM credit of 1.2 × MVA, PoC
  power factor ≥ 0.98.

**Forward pass.** All checks run on the solved, fixed capacities:

| Check | How it is computed |
|---|---|
| Transformer | Peak flow / pf, rounded up to the library |
| Reactive (PQ) | Worst-hour Q_need: load Q + transformer x·S²/S_n − allowed PoC Q. Q_avail is the **exact** PQ circle √(S²−P²) of each PCS plus the STATCOM. |
| ESCR | (S_sc + 1.2 × GFM MVA) / P_n |
| Ramp, ΔP, Pst | A 0.1 s, 30 min simulation. A ramp limiter sets the grid share; storage is clipped to P_sm and to the ±E_sm/2 SOC band. The STATCOM cancels the load's fast Q swing with whatever is left after steady Q. Pst uses the IEC 61000-3-3 analytic method on the largest voltage change d within 1 s, at 60 changes/min, with F = 1. |

**Loop.** Each failure adds a cut. Cuts are never removed. The transformer is rounded up and frozen after
round 2. The loop allows up to 10 rounds, so we can see whether a case would have passed after round 5.

**Two cut sets.**

- **note:** section 4.4 as written.
- **revised:** the four changes in section 4 of this file.

**Cases.**

| Case | What differs |
|---|---|
| `strong` | S_sc = 2000 MVA, X/R = 10 |
| `weak` | S_sc = 250 MVA, X/R = 4 |
| `strong-tr100` | strong grid, largest transformer 100 MVA (the import-cap cut binds) |
| `weak-tanq0` | weak grid, no load reactive swing |
| `weak-flat` | weak grid, flat tariff (no arbitrage, so the BESS stays small) |

## 2. Per intent: rounds, cost per round and binding cuts

The cost is the annual total (capex plus energy) in M€/yr, given for each round. "Bound" lists the checks
that failed in each round and so added a cut.

| Case | Intent | Cuts | Cost per round | Bound | Rounds | Rule verdict |
|---|---|---|---|---|---|---|
| strong | A | note | 68.40 → 70.86 | trafo, pq, ramp, pst | 2 | converged |
| strong | A | revised | 68.40 → 70.60 | trafo, pq, ramp, pst | 2 | converged |
| strong | B | note | 66.45 → 67.28 | pq, ramp, pst | 2 | converged |
| strong | B | revised | 66.45 → 67.18 | pq, ramp, pst | 2 | converged |
| strong | D | note | 66.76 → 69.64 | trafo, pq, ramp, pst | 2 | converged |
| strong | D | revised | 66.76 → 69.38 | trafo, pq, ramp, pst | 2 | converged |
| weak | A | note | 68.40 → 70.86 → 70.86 | trafo, pq, ramp, pst > pst | 3 | converged |
| weak | A | revised | 68.40 → 70.63 | trafo, pq, ramp, pst | 2 | converged |
| weak | B | note | 66.45 → 67.28 → 67.46 | pq, escr, ramp, pst > pst | 3 | converged |
| weak | B | revised | 66.45 → 67.28 | pq, escr, ramp, pst | 2 | converged |
| weak | D | note | 66.76 → 69.64 → 69.65 | trafo, pq, ramp, pst > pst | 3 | converged |
| weak | D | revised | 66.76 → 69.41 | trafo, pq, ramp, pst | 2 | converged |
| strong-tr100 | A | note | 68.40 → 72.09 | trafo, pq, ramp, pst | 2 | converged |
| strong-tr100 | A | revised | 68.40 → 71.73 | trafo, pq, ramp, pst | 2 | converged |
| strong-tr100 | B | note | 66.45 → 69.00 | trafo, pq, ramp, pst | 2 | converged |
| strong-tr100 | B | revised | 66.45 → 68.89 | trafo, pq, ramp, pst | 2 | converged |
| strong-tr100 | D | note | 66.76 → 71.11 | trafo, pq, ramp, pst | 2 | converged |
| strong-tr100 | D | revised | 66.76 → 70.74 | trafo, pq, ramp, pst | 2 | converged |
| weak-tanq0 | A, B, D | both | as `strong`, ± 0.03 | as `weak`, round 1 only | 2 | converged |
| weak-flat | A | note | 70.42 → 71.11 → 71.29 | pq, escr, ramp, pst > pst | 3 | converged |
| weak-flat | A | revised | 70.42 → 71.06 | pq, escr, ramp, pst | 2 | converged |
| weak-flat | B | note | 66.36 → 67.28 → 67.46 | pq, escr, ramp, pst > pst | 3 | converged |
| weak-flat | B | revised | 66.36 → 67.42 → 67.60 | pq, escr, ramp, pst > pst | 3 | converged |
| weak-flat | D | note | 69.46 → 70.38 → 70.56 → … → 70.64 | pq, ramp, pst > pst × 7 | **9** | **not converged in 5** |
| weak-flat | D | revised | 69.46 → 70.31 | pq, ramp, pst | 2 | converged |

Notes on the table:

- **Cost rises in every round.** Freezing the transformer at its library size adds a step of
  0.1–0.2 M€/yr.
- **Run time.** One LP round takes 1.6–2.9 s for 168 h with HiGHS. The forward pass takes 0.02 s. A
  full-year LP would be about 50 times larger, so the note's default of tsam for the inner rounds stands.

**Final sizes.** These are the final-round values for the main cases. P_sm is 39.9 MW everywhere, because
the ramp cut lifts it from the note's 20 MW floor.

| Case | Intent | PCS oversize S_x, note → revised (MVA) | STATCOM, note → revised (Mvar) | Capex, note → revised (M€/yr) |
|---|---|---|---|---|
| strong | A | 51.3 → 8.4 | 0 → 0 | 13.12 → 12.86 |
| strong | B | 24.2 → 7.9 | 0 → 0 | 5.61 → 5.52 |
| strong | D | 51.7 → 8.5 | 0 → 0 | 12.38 → 12.12 |
| weak | A | 47.2 → 8.4 | 4.0 → 3.4 | 13.13 → 12.89 |
| weak | B | 20.2 → 9.0 | 4.0 → 3.4 | 5.80 → 5.68 |
| strong-tr100 | A | 69.0 → 8.0 | 0 → 0 | 10.78 → 10.42 |
| weak-flat | D | 0 + 28.2 Mvar of STATCOM → 7.9 | 28.2 → 3.4 | 8.90 → 8.57 |

## 3. The failing case: weak-flat, intent D, note cuts

| Round | Cost (M€/yr) | Fails | Pst | S_x | STATCOM steady + dynamic |
|---|---|---|---|---|---|
| 1 | 69.46 | pq, ramp, pst | 4.53 | 0.0 | 0.0 |
| 2 | 70.38 | pst | 2.03 | 24.2 | 0.0 |
| 3 | 70.56 | pst | 2.03 | 20.2 | 4.0 |
| 4 | 70.57 | pst | 2.03 | 16.1 | 8.1 |
| 5 | 70.58 | pst | 2.03 | 12.1 | 12.1 |
| 6–8 | 70.59–70.60 | pst | 2.03 | 8.1 → 0.0 | 16.2 → 24.2 |
| 9 | 70.64 | – | 0.03 | 0.0 | 28.2 |

How the creep happens:

1. The note's PQ cut (Q_statcom + Σ S_x ≥ Q_need) and its Pst cut (P_sm + κ·Q_statcom ≥ …) **both count
   the same STATCOM Mvar**.
2. Each round the LP swaps 4 MVA of PCS oversize for 4 Mvar of STATCOM. The swap satisfies both cuts on
   paper.
3. The forward check charges steady Q first, so no Mvar is ever left for flicker.
4. Pst stays at 2.03, six times the limit, until the PCS oversize reaches zero.

This is a monotone creep, not an oscillation. The cost moves by 0.01–0.02 % per round, so a cost-only
stopping rule would have declared "converged" at round 3 with Pst six times over. The note's rule survives
only because it also requires capacities to move less than 2 %.

The other cases avoid the creep by luck of sizing. With arbitrage, or with intents A and B, the PCS of the
real battery is large enough that the exact circle covers steady Q. The STATCOM is then free for flicker.

## 4. What the prototype changes in the note

1. **Split the STATCOM into a steady slice and a dynamic slice.** Use Q_ss in the PQ cut and Q_dyn in the
   Pst cut, with Q_statcom = Q_ss + Q_dyn. This removes the creep (9 rounds → 2).
2. **Make the PQ cut per hour and linear in the transformer flow,** not a constant Q_need taken from the
   failing iterate:

   Q_ss + c·ΣS_x − k·p_tr(t) ≥ Q_load(t), with k = x·p_cap/(pf²·S_n) − tan φ_req (a chord bound on
   x·S²/S_n).

   - Why: the first iterate can be far from the answer. With arbitrage, round 1 builds a 267 MW
     transformer and a 1.1 GWh BESS. A constant Q_need from that round (51–69 Mvar) is never removed.
     The PCS then stays 2–3 times oversized even in the note's own terms.
   - In the same cut, use the **tighter inner bound on the PQ circle**: cap S_x ≤ r·P, which allows a
     credit of c = √(1 + 2/r) per MVA (3.3 at r = 0.2). The note's (S − P) credit gives 1 Mvar per MVA,
     while the exact circle gives about 3–5.
   - Also stop S_x from sitting on a technology with no P. A PCS with no battery behind it is a STATCOM
     and should be priced as one.
3. **Write the Pst cut on the voltage, with the load's reactive swing:**

   cos ψ·2·P_res + sin ψ·Q_res ≤ d_lim·S_sc, where
   - P_res ≥ S + A − ΣP_sm and P_res ≥ 0;
   - Q_res ≥ tan φ_swing·(S + A) − Q_dyn and Q_res ≥ 0;
   - ψ is the PoC impedance angle.

   The note's form P_sm + κ·Q ≥ S − d_lim·S_sc has three problems:
   - it misses the load's own Q swing, which P smoothing does not remove;
   - it keeps crediting P_sm above S + A, where extra P_sm buys nothing;
   - it drops the 1/cos ψ factor.

   On a weak grid it therefore needs an extra round every time. With tan φ_swing = 0 both forms converge
   in 2 rounds.
4. **Make the smoothing floors coincide-safe.** P_floor = S + A, not max(S − ΔP_lim, A − ΔP_lim/2): with
   a ramp limiter the grid takes no instant share, and the step and the oscillation coincide. The energy
   floor should **sum** the step, checkpoint and oscillation energies, not take their max (1.82 against
   1.5 MWh). The ramp cut corrects both in one round, so this is a first-round accuracy fix, not a
   convergence fix.

What the prototype confirms as written:

- The ESCR cut is exact and binds once, in the weak-grid cases for intents A and B.
- The transformer import cap binds whenever arbitrage drives charging peaks (EirGrid's recharge point)
  and settles in one round.
- "Cuts are never removed" gives monotone cost.
- The 5-round cap is enough once the four changes are in.

## 5. Effect on the proposed AS issue split

The split stays the same. Four issues change scope:

| Issue | Scope change |
|---|---|
| AS-03 | Floors P ≥ S + A and E = sum of the event energies (change 4) |
| AS-04 | The envelope also carries the reactive swing tan φ_swing, and the Pst cut uses cos ψ and sin ψ (change 3) |
| AS-05 | The STATCOM steady/dynamic split, the per-hour PQ cut, and the S_x ≤ r·P inner bound with its √(1+2/r) credit (changes 1 and 2) |
| AS-06 | Keeps the capacity-move condition in the stopping rule. A "tolerance" stop with any check still failing is reported as **not converged**, never as converged. |

## 6. What this prototype skipped, and the risk

- **Not modelled:**
  - degradation and throughput cost (4.2.7);
  - the GFM droop/H tuning check;
  - harmonics;
  - N-1 transformers;
  - discrete STATCOM blocks;
  - intents C and E;
  - intent D without a genset.

  Risk: a degradation cost changes which tier carries P_sm. It does not change the convergence argument.
- **The 0.1 s simulation treats all storage as one fast tier,** since every technology's latency is below
  the 0.32 s band limit at T = 2 s. A slower tier, or a shorter T, would need the f_c split, which is not
  tested here.
- **The transformer freeze rule was never exercised.** The import-cap cut always pushed the transformer to
  the largest library size by round 2, so no case could flip between sizes.
- **Pst uses the analytic method at 60 changes/min,** at the edge of its validity, with F = 1. That is
  conservative. A flickermeter (IEC 61000-4-15) on the simulated voltage would be the real check.
- **One week and one envelope only.** A full year (or tsam), more phases, and a measured trace could move
  the first iterate further away. That is what made the stale constant Q_need cut costly in point 2.
- **Illustrative numbers only.** Costs, S_sc and tan φ_swing are round values. tan φ_swing in particular
  decides whether flicker needs a STATCOM at all on a weak grid, so it should come from vendor data in
  AS-04.
