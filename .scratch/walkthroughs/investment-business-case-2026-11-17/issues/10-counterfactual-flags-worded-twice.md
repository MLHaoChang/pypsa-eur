<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10 — Three counterfactual flags are worded twice on the investment case

Status: needs-triage
Severity: low (the same fact stated twice, once in a weaker wording)
Group: Investment and business case. Found by GK B2 (5) in the review of #1935 (issue 02), master 443ec313.

## What

Expert › Results › Commercial & finance › Investment case words these flags twice:
- in the counterfactual notes (`ic-cf-notes`), from the backend's `services/finance/baseline_text.flag_text`, which report.py serves as `counterfactual.notes`;
- in the flag list (`ic-project-flags`), from the frontend's `PROJECT_FLAG_WORDS` (financeModel.ts).

The flags:
- `co2_price_not_applied`
- `counterfactual_baseline_shed_unknown`
- `counterfactual_baseline_unserved`

#1935 fixed the same defect for `baseline_island_rows_relaxed`. `statedFlags` leaves it out of the list when the notes are served, and a backend contract test (`tests/test_ic_counterfactual_notes_contract.py`) pins that the notes carry its sentence.

## Expected

Each fact is said once, in the backend's full sentence. Fix it the way #1935 did: drop the three entries from `PROJECT_FLAG_WORDS`, add them to `statedFlags` and to `projectFlagKeys.json` `stated`, and extend the contract test to cover them. First confirm that `flag_text` words each flag for every value it can carry.
