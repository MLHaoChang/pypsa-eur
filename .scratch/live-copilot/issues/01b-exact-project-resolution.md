<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-01B — exact project names before companion shortcuts

Status: implementation in progress (Claude, branch `claude/copilot-target-resolution-b`)
Resume: [2026-10-19 implementation handoff](../handoff-2026-10-19-target-resolution.md).
Parent: [LC-01](01-discovery-and-scope.md)
Starting actual master: `d006f4beaa302b97432c65757762ae180222731a` (PR376).
Branch: `claude/copilot-target-resolution`; worktree `/workspace/copilot-target-resolution`.
The owner said continue after PR372; ordinary validated PR/merge authorization persists.

## Concrete contract

Add one ordinary read-tier `resolve_project_target(project_name)` tool. Resolve
an exact, nonblank string name (at most256characters), with native current
acting-user/org/tree ACL checks. No case-folding, trimming, partial matching,
name-to-UUID guessing, file reads or fallback to the active project. Names are
unique within an org. UUID-shaped names are still names: they must not select a
different project whose UUID equals the label. Extend the native registry's
lookup with an opt-in exact-name mode; preserve existing route resolution's
UUID-first/name-fallback defaults.

Return a bounded v1 descriptor with canonical project_id and project_name and
an explicit note that every subsequent native use must recheck authority. Do
not activate, select a discussed run, navigate, execute, change permissions or
export directories/settings/project contents. Unknown, foreign, inaccessible,
revoked and missing names retain indistinguishable native not-found behavior.
Existing listing supplies authorized candidates; incomplete names are never
silently completed. Existing activation/confirmation/lock/rebind/checked policy
remains authoritative. Strict checked policy continues to refuse an unmapped
new tool. Catalogue, dispatcher, toolsets and generated inventory must agree;
preserve existing provider offer caps.

Local companion shortcuts accept only complete, explicit generic UI commands.
A named/qualified/negated/compound request must not be consumed as a generic
project picker or a tour of previous/current results. Do not parse a user label
into a trusted ID in JavaScript. Forward the full original text once through the
existing shared harness path (or its existing busy queue); preserve readiness,
project mismatch, decisions, ordinary approvals and UI generation behavior.
Keep the existing generic project-list and supported current-results walkthrough
commands usable without a model. A missing key must not claim a named target
was opened. Current/discussed context stays unchanged by name resolution.

## Authorized public test seams

The approved LC-T01/T02/T06/T10/T17 native HTTP/SSE/ordinary dispatch and real
browser seams apply; no new owner permission is needed for these agreed seams.
Start with one real ordinary-dispatch RED for exact named project resolution,
verified against authenticated native project listing/activation reads. Then
regress same-org ACL and cross-org name collisions, unknown/partial/case variants,
UUID-shaped-name collisions, native rename/revocation between resolution/use,
unchanged active/discussed scope, personal read-only and strict refusal. Mock
only external provider HTTP for full wire discovery/resolve/use journeys and
keep official offer limits. Do not mock successful native application HTTP.

For shortcuts, first observe a named request wrongly recognized at the exported
module interface; then verify actual rendered companion routing and native
browser behavior. Generic commands remain positive controls; named project,
sensitivity/run/entity/window qualifiers, negations and compound instructions
must not silently start a current-source shortcut. Keep no-key/busy/mismatch
cases and record what browser/native evidence actually executes. Paid vendor
probes remain pending by explicit owner choice; report fixture-controlled wire
coverage separately.

## Remaining scope

This first exact-target slice does not finish LC-01 or LC-C2. Canonical run/case/
entity resolution, relationship/baseline/window/revision bindings, richer
eligibility and non-component schemas remain separate slices. No engine changes,
paid model/media calls, external contacts, deployment/activation or domain/full
coverage claim. Independent domain review and real-use calibration stay pending.
