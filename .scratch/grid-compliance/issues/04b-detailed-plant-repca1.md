<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-04b: plant controller (REPCA1) on a detailed plant

Status: in-progress
Model tier: opus
Blocked by: 12 (package); #1607–#1609
Type: task
Parent: `.scratch/grid-compliance/issues/04-psse-pack.md`
Gated: yes (GK A, gridspine)

## Why

GC-04 PR3 writes one REPCA1 per PoC branch, attached to the aggregated machine (GC-01), and
refuses two machines on one PoC branch: N REPCA1 records that each measure the whole plant's
flow on their own MBASE multiply the plant loop gain by N. A detailed plant (every converter
its own machine) therefore has no plant controller in the .dyr today.

## What to build (options to triage)

1. Per-machine REPCA1 with shared measurement and the gain scaled per machine (Kc / gain
   sharing), stated and tested against the aggregated response.
2. A plant-level master controller driving the machines' references (a WECC plant-level
   model), once its public record layout is confirmed.

## Done when

- A detailed plant with N machines writes plant control whose PoC step response matches the
  aggregated model's within a stated tolerance.

## Comments

**2026-11-17 triage (SCALE-1; GK A2 4 priority view).** GK A2 pick: option 1. One REPCA1 per machine, all measuring the same PoC branch, each machine's gains (Kp, Ki, the frequency-droop terms, Kc) scaled by its share MBASE_i/ΣMBASE, so the summed gain equals the aggregate's single loop gain, not N×. Test = "Done when": the PoC step matches the aggregated model within a stated tolerance, plus N=1 byte-equal to today's aggregate record. Option 2 refused for now (record layout unconfirmed; GC04-DEFAULTS-1 admits public generic models only); all values stay tagged assumed. The pick came from GK A2 4; tell GK A. Gated GK A.

**2026-11-27 GK A (39) plan GO (S1 dyr_writer, S2 psse_pack + scripts).** The scaling corrects the triage note's "all x w". w = MBASE_i/sum(MBASE), and REPCA1 works on its machine's MBASE (OpenIPSL a8c84de9 BaseREPC.mo:73, REPCA1.mo:154-157; WECC REMWG "Clarification on Proper Use of REPC models", Dec 2021, pp. 4 and 6).
- P path: Kpg and Kig x w; Ddn, Dup, femax and femin x 1/w.
- V mode (RefFlag 1): Kp and Ki unscaled.
- Q mode (RefFlag 0): Kp and Ki x w; dbd1/2 and emax/emin x 1/w.
- Kc, Rc and Xc x w. The rest is unscaled.

Conditions:
- C1: the derivation and sources go in the `share_repca1` docstring, and "multiple REPCA1 monitoring one branch" goes in UNVERIFIED.
- C2: the equivalence test covers a Plant_pref step, an over- and an under-frequency step, a V-mode Vref step and a Q-mode Qref step. femax/femin and emax/emin bind in a case.
- C3: mutants, each missing by more than 1e-2 in a named case: unscaled Kpg/Kig; droop x w; V-mode Kp/Ki x w; Kc, Rc and Xc each unscaled; Q-mode Kp/Ki unscaled; each rescaled limit unscaled or inverted.
- C4: the reduced model lives in tests only, and its blocks cite the WECC REPC_A diagram. The tolerance is 1e-6 pu on sum(MBASE) over 20 s, pinned as a literal.
- C5: unequal initial pu dispatch (spread above 1e-6) is not refused; the pack's assumptions name the machines. Test an equal and an unequal case.
- C6: N=1 is byte-equal to today's record. Undeclared sharing stays refused, and a declared plant with different PoC branches is refused.
- C7 (S2): every machine's REPCA1 is stepped, the status poll covers every machine, and N=3 is tested with one machine tripping. Each slice adds a release-note fragment. Values stay tagged assumed.

**S1 builder notes (2026-11-27).**
- `write_dyr`/`converter_records` take `plants` (name -> unit ids).
- A gain-shared REPCA1 writes its CONs with 8 decimals (`SHARED_DECIMALS`), because 5 decimals would round a share-scaled gain off the aggregate response by more than 1e-6. N=1 records keep 5 decimals and stay byte-equal.
- `unequal_dispatch` returns the C5 assumption lines; S2 puts them in the pack.
- S2 also has to step each machine's Plant_pref and Qref by the same MW, so each controller's step is the aggregate step / w in its own pu (`repca1_reduced.simulate` does this).
