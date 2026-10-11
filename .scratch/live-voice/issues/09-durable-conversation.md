<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Durable prolonged conversations and authoritative deployment ownership

Status: claimed
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

2026-10-11: First TDD slice — `services/durable_conversation.py` persists an
ACL-protected parent thread (goals/constraints/units, current vs discussed refs,
tasks/jobs/evidence, profile lineage, budget stubs) under the project dir, distinct
from ChatSession. Compaction trims a child message deque via `trim_session_messages`
without rewriting parent facts. Wrong owner/project → 404 `thread_not_found`.
`resume_episode` drops the prior episode (and its pending approvals), mints a new
owned ChatSession, bumps generation. B-U2 ledger: `reserve_budget` /
`settle_budget` count once across resume. Tests:
`tests/test_durable_conversation_parent_thread.py` (10 passed). Still open for 09:
checkpoint/resume HTTP + stale generation rejection, rebind credential refresh,
worker pin / deployment smoke, B-C1 prolonged journey. Do not integrate issue 02
media acceptance until those land.

2026-10-11: B-I1 HTTP — `routers/durable_conversation.py` at
`/api/durable-conversations` (create/get/checkpoint/resume) with
`require_user` + generation compare-and-swap (`stale_generation` 409). Wrong
actor/project stays 404 `thread_not_found`. Tests:
`tests/test_durable_conversation_http_b_i1.py` (4 passed). Still open: rebind
credential refresh, worker pin, deployment smoke, B-C1.

2026-10-11: B-I2 rebind — `rebind_scope` + `POST …/rebind` bumps generation,
mints opaque bridge credentials, invalidates prior tokens, clears episode
pending approvals, and bumps utterance-admission generation. Parent facts
preserved. Tests: `tests/test_durable_conversation_rebind_b_i2.py` (5 passed).
Still open: worker pin, real LiveKit credentials, deployment smoke, B-C1.

2026-10-11: B-I3 worker pin — durable threads stamp `owner_replica_id` at
create; checkpoint/resume/rebind/get/bridge-auth refuse `wrong_replica` 409
on another process. ChatSession pins the creating replica; stream/confirm/
abort/rewind/progress refuse failover without clearing pending work. Tests:
`tests/test_durable_conversation_worker_pin_b_i3.py` (5 passed). Still open:
real LiveKit credentials, deployment smoke, B-C1, multi-worker coordination.
