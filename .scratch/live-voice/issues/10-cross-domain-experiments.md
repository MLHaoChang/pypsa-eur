<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Typed cross-domain experimentation and resumable task advancement

Status: ready-for-agent
Type: task
Blocked by: 02, 08, 09, 11

Implement steps 4 and 6 of [the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md)
using [coverage contract](../application-coverage.md). Extend numeric network
sensitivity with typed adapters for scalar/bool/enum/list/nested/unit/file/topology
changes where actual schemas permit. Preview changes/affected results and
revalidate fingerprints at commit.

Persist isolated baseline/version, cases, settings/seeds/objectives, resource/
time/cost limits, stop conditions, dependencies, job IDs and artifacts.
Advance steps through shared admission/ACL/approval/lock/budget gates; existing
start_task persists plans without executing them. No hidden framework agent or
arbitrary model code. Reconcile uncertain outcomes before retry.

Preserve baseline, expose partial/all failures, compare compatible evidence,
support verified cancellation/resume and export provenance. Refinement cannot
silently expand approval scope. Done when real small fixtures cover network,
commercial/settings, reliability/studies and GridSpine as supported by inventory,
plus typed parameters, approval, isolation, partial failure and interruption/export.
Unsupported required domains block a full experiment claim.

## Comments
