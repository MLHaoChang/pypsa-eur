<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-32 — Behaviour coverage for the families LC-06 leaves out

Status: done

Closed under owner decision LC32-CLOSE-1: the remaining `_pending` rows stay as the
follow-up list; tickets 33 (`33-timeseries-upload-guards.md`) and 35
(`35-ui-tools-unknown-asset-reports-success.md`) are their own fix packages.
Type: task

Split from live-voice issue 08 (owner decision LV08-SHRINK-1; see
[the comparison](../../live-voice/issues/08-lc06-comparison.md)). LC-06 states
"D01/02/20–23 stay out", and D24 was not re-audited. In `smoke/family_manifest.py` these
variants are `pending`, so five capability-matrix families have no covered journey:

| Suite | Variant id | Gap in the manifest |
|---|---|---|
| LC-D01 | `LC-D01.projects` | seam tests check identity/ACL; no independent file/ID oracle |
| LC-D02 | `LC-D02.topology` | seam tests check shape; no reference-count/KCL oracle |
| LC-D03 | `LC-D03.chronology_edges` | DST/leap/duplicate/nonmonotonic times not mapped |
| LC-D20 | `LC-D20.delivery` | seam only; delivered bytes not independently parsed |
| LC-D21 | `LC-D21.reports` | seam only; document facts not independently inspected |
| LC-D22 | `LC-D22.undo_checkpoints` | seam only; no before/after fingerprint oracle |
| LC-D23 | `LC-D23.provider_parity` | dispatch parity only (`test_assistant_provider_parity.py`) |
| LC-D24 | `LC-D24.ui` | frontend e2e; not re-audited in LC-06 |

Families affected: projects, network-topology (part), import-export, reports, audit-undo,
harness-workflows, ui-monitoring.

For the owner to rank: who builds these (an LC-06 follow-up session), in what order, and
whether D23 should run one journey on both the Claude and OpenAI wires. Same method as LC-06:
a public-seam test with a hand oracle per variant, a mutation that fails it, and the manifest
row flipped to `covered` only with the node ids.
