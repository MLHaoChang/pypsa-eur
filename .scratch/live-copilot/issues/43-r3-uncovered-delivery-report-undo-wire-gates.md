<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-43 — Owner of the pending LC-D20 to LC-D23 gate rows

Status: ready-for-human
Type: task

Opened under owner decision LV14-OWNER-1 (live-voice issue 14). Issue 32 is done
(LC32-CLOSE-1), so the `r3-uncovered:LC-D20` to `LC-D23` blockers in
`full_coverage` (`pypsa-gui/backend/smoke/assistant_control_matrix.py`, `R3_ISSUE`) point here.

This issue owns the follow-up list that issue 32 left pending in
`smoke/family_manifest.py`:

- `LC-D20.delivery_gates`
- `LC-D20.path_injection`
- `LC-D21.report_gates`
- `LC-D22.concurrency_gates`
- `LC-D23.wire_gates`

It stays open until each row is covered by a behavioural fixture or the owner decides to
drop it. LC-D03 and LC-D24 went to fix packages 33 and 35.

## Comments

2026-11-11, GK B (46) plan GO with conditions (owner decision: "Cover them with tests"):

- C1. A sub-item with no gate in the code stays `_pending` as its own row, pointing here; never `_unavailable`. Each covered row's oracle names only what its tests read. Dropping a row is an owner decision.
- C2. Probe the fail-closed half before filing a not-built item: a pending approval after restart does not execute or reads expired; `undo_last` after another write: assert what is right, file a ticket and a `_pending` row for a defect. No xfail or skip.
- C3. One mutant per gate, sibling layers mutated together, a base-class or except-widening mutant per typed refusal, each on its single test with `-x` and a timeout; the table goes in the PR body.
- C4. Exact refusal kind or status; "nothing changed" means equal bytes or fingerprint, version count and upload count. D23 timeout: never success or completed, and the text says the outcome is unknown. D20: check `filename*` and that the stored path stays in the project's upload dir. D22: the foreign lock is a real holder, not a monkeypatched check.
- C5. Re-run `test_assistant_control_matrix.py`, the `assistant_control_matrix.py --check` and `assistant_inventory.py --check` smokes, `test_json_fragments` and the family-manifest tests; add one release-note fragment.
