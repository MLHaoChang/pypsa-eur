<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-01c Torsional modes of a genset in the campus description

Status: done
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none
Gated: **yes** (GK A2, gridspine)
Model tier: opus
Size: S
Owner decision: GC-01c-TORSION-1 (2026-11-17), GC-SCOPE-1, GC-TEXT-1
TRGC: 20 (sub-synchronous resonance screening, GC-08)
Likely files: `gridspine/ingest/campus.py`, `gridspine/schema/campus.py`, `gridspine/drivers/campus_study.py` (the `ssti_screens` step passes the modes on), `tests/gridspine/test_campus_torsional_modes.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering.

## Why

GC-08's sub-synchronous scan takes `torsional_modes={machine: [f_mode_hz, ...]}`. The campus has no
way to enter them, so the series-resonance verdict reads `check` ("no torsional modes given") on
every project, and the scorecard's `subsync_series_resonance` row stays unknown. GK A2 asked for this
as its own issue, kept separate from GC-01b, whose plan already has its GO.

## What to build

1. An optional per-genset key, e.g. `torsional_modes_hz`: a list of positive finite frequencies
   below f0, each with a `source` (`code` / `datasheet` / `assumed`) like other campus values.
   Anything else is refused with a ContractError.
2. `campus_study.ssti_screens_campus` passes them to `subsync_scan(..., torsional_modes=...)`.
3. A genset with no modes keeps reading `check`; one with modes is judged against the clearance.

## Done when

- A campus with a genset whose modes are entered builds. The scan judges its series resonances
  against them (flag below the clearance, clear at or above it), and the scorecard row reads warn or pass.
- Bad input is refused: negative, non-finite, at or above f0, or a non-list.
- A genset without modes still reads `check`, and existing GC-08 tests pass unchanged.

## Comments

**2026-11-17.** Filed from GC-08 PR3 (the owner picked option 1 via the coordinator). Conditions
from GK A2's GC-08 rulings that carry over: an uncomputable row reads "not evaluated" with a reason,
and a reader round trip is not an oracle (test the margin against a hand-computed f0 − f_er).

**2026-11-23.** GK A2 plan GO on conditions C1–C7 (verbatim). Keep measured/datasheet/assumed; no `code` source is needed.

C1. Partial coverage never reads clear. Take a campus with two gensets where only GEN1 has modes. GEN2's row (or the machine-level result) reads not evaluated, never clear, and subsync_verdict is never `clear` while any genset lacks modes. If subsync_scan cannot express that per machine, say so before building, rather than letting the screen pass on the machines it knows. Test it.

C2. Assumed modes. A row judged on an `assumed` mode carries the mode's source on the row, and a `clear` that rests on assumed modes only says so in its reason (AS11B-RVC-1 analogue: never a silent pass on an assumed input). Test the reason text literally.

C3. Literal oracles (M6a).
- The margins (0.445…/0.928…, 2.945…/1.572…) are hand-computed literals, compared with a tight tolerance (rel 1e-9 or abs 1e-6), never pytest.approx's default.
- The clearance in the boundary test is a literal, never the profile's constant (GK A (33)).
- State whether margin == clearance flags or clears. Pin it with a case at exact equality, chosen so the numbers are exact in binary, or by monkeypatching f_er to a literal. The `<`↔`<=` mutant must die.

C4. Freeze and fingerprint identity. A campus without the key keeps its data/freeze hash and GC-01 model-set hash, pinned against literal digests recorded from master 46f3bdc7, never the same code run without the key (#1773 W1). Adding `Campus.torsional_modes` must not move them. A campus with the key moves the freeze hash: test that too.

C5. Refusals. Your list is right. Each one asserts ContractError and the literal field path `units.<g>.torsional_modes_hz[i]`. bool is refused before the number check (bool is an int). A 60 Hz campus refuses 60 and accepts 59.9. Show the f0 guard's `>=`→`>` mutant dies.

C6. Wiring. Every subsync_scan call in ssti_screens_campus (the PCC and each far-side bus) gets the modes. Test a far-side bus row with a mode, and show the mutant that drops the kwarg at each call site dies (#1548: race and wiring fixes are pinned per call site).

C7. Visibility. Name in the body where these rows reach the user: the scorecard through grid_readiness (flag→warn, clear→pass), the GC-09 pack tables, the GC-08 pack. If a campus the app accepts (upload or editor) can carry the key, check it end to end in the real app (E2E-DONE-1); otherwise say how a user gets modes in today. Respect ruling (b): a pack table never shows a judged value the scorecard refused. Add a release note.
