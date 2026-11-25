<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-08g SSTI notes: one sentence shape for every SSTI note

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: GC-08e (`08e-ssti-island-judged.md`)
Gated: **yes** (GK A2 for the GC-08 note contract; UX for the text)
Owner decision: GC-TEXT-1
Likely files: `pypsa-gui/backend/services/adequacy/grid_readiness.py`, `pypsa-gui/backend/tests/test_grid_readiness_ssti.py`, `pypsa-gui/backend/tests/test_grid_compliance_ssti_source.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering.

## Why

GC-08e gave the UIF note of a campus that islands UX 29's sentence shape ("Worst UIF 1.97 · bus MV · islanded,
max fault level · machine GAS_B. This is above …, so an EMT SSTI study is warranted for …"). The rest of the
SSTI note set kept GC-08's wording, so that the whole set changes in one step and GK A2's ruling (the
non-islanding note unchanged in GC-08e) held. Until this lands, the scorecard mixes the two shapes.

## What to build (UX 29's asks inherited from GC-08e)

1. The shared ending `SSTI_SCREENING` ("Screening; the binding answer is the EMT study.") becomes two sentences:
   "This is a screening. The EMT study gives the binding answer." It is on all three SSTI rows (UIF, series
   resonance, SSCI).
2. The note of a campus that does not island takes the new shape: "Worst UIF 0.25 · bus MV · max fault level ·
   machine GAS. This is above the threshold 0.1, so an EMT SSTI study is warranted for the grid-connected state
   only."
3. The grid-connected record/table disagreements of the UIF row ("The run's UIF table's worst UIF … is not the
   worst UIF … its record gives", "The run's worst UIF … contradicts its verdict …", "The screen returned …",
   "… is not a finite number (…)") read as one plain line, as the islanded ones do since GC-08e: "The UIF result
   could not be checked: the run's results disagree with each other. Run the grid study again." The specific
   cause stays in the log and the tests, never in the note (UX-RULES-1122). Check the series-resonance row's
   twins for the same.

## Done when

- Every SSTI note ends in the two-sentence screening caveat, and each test that pins one is updated in the same PR.
- The non-islanding UIF note and the grid-connected disagreement lines read as above, with UX 29's OK on shots.
