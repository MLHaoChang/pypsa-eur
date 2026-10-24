<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-02 — multi-energy buildings, process complexes and equipment

Status: needs-triage — specification proposed; not implemented
Type: task
Dependencies: SC-01; SC-00 target-lifecycle contract
Baseline: master aba252d2384f55116bbd15516a5b025951ba265e

## Scope and acceptance

Before code, obtain the required engine/UX plan review for native lifecycle/mutation seams. First prove rename/delete/same-name recreation behavior; do not use loose display-name matching or infer a stable UUID the model does not provide.

Write binding and native-model seam tests before implementation. Associate existing Loads per carrier, Process-unit Links (eh_process) and Inventory Stores with a building/group. Support acyclic complexes covering several buildings and outside equipment. Associate multiple visible enclosures with one logical component without multiplying capacity. Do not create a duplicate fixed electricity Load for a process Link's demand. Display unsupported conversions/allocations as unavailable.

Integrate real building footprints, roof eligibility, functional terminals and service envelopes into placement. Preserve exact physical scale for source-verified catalogue equipment; keep generic fitted equipment visibly approximate. Place existing is scene-only; Add model asset uses the existing native editor/approval path and separate receipt. If placement fails after creation, do not retry creating the asset.

Keep optional geometry-derived length behavior behind the current setting and existing native routes, preserving user routes. Test access revocation and lifecycle failure before Apply. No new energy/process physics or globally overwritten equipment library.

Done: V11–21, domain fixture bindings, lifecycle correctness, native mutation regression and Guided/Expert review evidence.

## Validation

V11–21, V37–38, V45–47, V50–51; definitions in ../validation-matrix.md. Tests before implementation at the frozen public seams; no passing evidence is claimed by this ticket.

## Coordination

One bounded implementation PR after applicable plan review; current master and open-PR overlap check first. Follow AGENTS and steward, add a release fragment for shipped behavior, never change compiled release notes or another task branch. Record exact tests, genuine worker evidence and unimplemented parts in the PR and handoff.
