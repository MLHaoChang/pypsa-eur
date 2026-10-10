<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Durable prolonged conversations and authoritative deployment ownership

Status: ready-for-agent
Type: task

Implement persistence/deployment in steps 1, 3 and 5 of
[the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md).
Read [coverage contract](../application-coverage.md) and session/admission code.
Persist an ACL-protected parent thread distinct from ChatSession/audio: exact
goals/corrections/constraints/units, current and discussed project/run/asset/source/
window, tasks/experiments/jobs, evidence revisions, provider lineage and cumulative
budgets. Provide bounded compaction/checkpoints, guarded rotation under existing
caps and compatible profile switching. Structured facts remain authoritative;
compaction/restart/rotation never reset parent accounting.

Refresh scoped credentials/generation on authorized rebind. Reject old callbacks
and approvals. Restart creates a new episode, invalidates pending approval tokens
and reconciles uncertain effects/jobs before resume; never replay tools to rebuild
history. Revalidate evidence/ACL. Integrate issue 02 before media acceptance.

Support ONE authoritative app worker initially. Pin bridge, stream/confirm/abort/
history/task controls to it; refuse wrong-owner/failover access. Unknown local
sessions cannot clear another owner's pending work. Provision/test the hosted
audio worker's network/CPU/memory/concurrency; LiveKit media does not deploy it.
Multi-worker remains disabled until distributed coordination has separate tests.
Done when ownership/rebind/restart/rotation/profile-switch fixtures pass, exact
references/budgets survive long conversations and deployment has a smoke procedure.

## Comments
