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

2026-10-11: B-C1 prolonged journey — `tests/test_durable_conversation_prolonged_b_c1.py`
(4 passed; fake/mock only, no LiveKit). HTTP create → checkpoint → rebind →
resume → resume → rebind plus service-ledger reserve/settle: goals/units/
current+discussed refs/evidence revisions survive every generation bump, open
reservations carry across resume, a key settles once (re-settle/re-reserve →
`budget_already_settled`), six compaction+resume/rebind cycles keep refs and
sum final charges exactly, and a stale-generation checkpoint/rebind after the
journey is 409 without clobbering facts. Pre-rebind bridge token is 401 after
resume. Still open: real LiveKit credentials, deployment smoke, multi-worker
coordination.

2026-11-03: Rebaseline on master a6bd3972d (FILL-10-2 lifted the voice hold).
B-I1, B-I2, B-I3 and B-C1 still hold: `tests/test_durable_conversation_*.py`
28/28 pass. Rebind credential refresh (bridge tokens) and wrong-replica
refusal are done. Gap found: the replica pin had no expiry, so a restarted
single worker (new `replica_id`) refused its own threads forever.
GK B (16) GO for three PRs, with conditions:
- PR1 restart/rotation: (a) a live in-flight turn refuses resume 409, an
  abandoned one (owner lease lapsed, reusing `harness.runner.LEASE_SECONDS`
  wall-time leases) resumes and lists its effects as unverified; (b) reconcile
  never replays a tool or write (spy); (c) lineage bounded, cap and drop tested,
  N rotations keep ledger totals and refs exact.
- PR2 profile switch: (d) stale generation 409 writes nothing, in-flight turn
  refused like (a), one typed refusal for an unknown/unconfigured profile that
  never reveals another user's key state, no credential value or env name in
  responses/ledger; (e) a non-portable switch carries facts and budgets only,
  no foreign-provider block in the child history; (f) the new route passes
  route_chat_disposition, project_route_acl_policy, write_surface_lock_policy,
  with route-parity and tool-error-kinds fragments.
- PR3 one worker + smoke: (g) say how the worker count is detected (uvicorn
  --workers, WEB_CONCURRENCY, desktop launcher), test each source, undetectable
  counts as one only if that is the documented default; deployment doc under
  pypsa-gui docs or doc/, not docs/agents.
- All: keep the harness README contract.

2026-11-03: PR1 restart/rotation (`claude/lv09-restart-rotation`). The thread
carries `owner_lease_until` (wall time, `runner.LEASE_SECONDS`), renewed by
every control on the owner replica. Another replica is `wrong_replica` 409
while the lease is live; after it lapses only `resume` may adopt (re-pin,
`takeover` in `episode_lineage`); other controls say `resume_required`.
Abandoned = the owner's lease lapsed (its process is gone); in the owner
process a turn is live while `_turn_in_flight`, and resume then refuses
`turn_already_in_flight` without dropping the episode. Resume reads the old
episode's event log read-only: an unended or interrupted last turn and its
unanswered tool calls (ids and names only, no inputs) go into `reconcile` as
`unverified`; a takeover also lists the thread's `job_ids`. `reconcile` is
capped at 64 (oldest kept), `episode_lineage` at 32 (newest kept), overflow
counted in `*_dropped`. Tests: `tests/test_durable_conversation_restart_rotation.py`
(8 passed). Nothing clears `reconcile` yet; revalidation lands with the voice
bridge. Multi-worker idle-owner takeover is unsafe until PR3 refuses >1 worker.

2026-11-03: PR2 profile switch (`claude/lv09-profile-switch`, after PR1).
`switch_profile` + `POST /api/durable-conversations/{id}/profile` with
generation CAS. The router resolves the profile (`llm_config.resolve_profile`)
and its readiness (`routers.chat._profile_chat_ready`); unknown and keyless
profiles are both 422 `profile_unavailable` with one fixed message. A same-wire
switch (or an unbound episode) rebinds the episode in place; a cross-wire switch
rotates to a child episode (shared `_rotate_episode`, lineage reason
`profile_switch`) whose history is empty, so no foreign thinking/tool block
carries. `profile_lineage` holds ids, wire and episode only, capped at 32.
Stale generation and an in-flight turn refuse without writing. Reads
(`get`, bridge auth) no longer renew the lease in the returned view.
Tests: `tests/test_durable_conversation_profile_switch.py` (7 passed).

2026-11-03: PR3 one worker + smoke (`claude/lv09-one-worker-smoke`, after PR2).
`services/app_workers.py` reads the worker count from: a launcher declaration
(the desktop launcher pins `workers=1` and declares it) > `--workers N`,
`--workers=N` or gunicorn `-w N` in argv (spawned workers inherit argv) >
`WEB_CONCURRENCY` > 1, uvicorn's documented default. Unreadable values fall
through. Every durable control (create/get/checkpoint/resume/rebind/profile/
bridge auth) is `503 multi_worker_unsupported` above one worker. Deployment
smoke procedure: `pypsa-gui/docs/live-voice-deployment-smoke.md` (mocked suite
+ curl steps for stale generation, rebind, profile, restart takeover after the
90 s lease, multi-worker refusal). Tests:
`tests/test_durable_conversation_one_worker.py` (13 passed).
Gates (need a real credential or deployment, not built): LiveKit room-token
refresh on rebind; hosted audio worker network/CPU/memory/concurrency
provisioning and load test; paid STT/TTS; multi-worker distributed pin/lease
coordination. Running the deployed smoke itself is an owner/deployment gate.
