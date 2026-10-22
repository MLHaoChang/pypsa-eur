# CP-04 — Activity / Needs-you

Status: ready-for-human
Progress: Activity observer shipped (release note); issue itself lists durable recovery, native evidence navigation and real worker journeys as incomplete
Type: implementation
Baseline: master `5711a6fa0116731ab61cfa2f757ef2d2a5540703`

## CP-04a: bounded observer

The assistant had no unified read surface for native work. A job's latest project status is not proof of an older job's outcome. This first slice provides a read-only Activity surface beside Chat, using existing stores and native records. It does not introduce an unattended runner or grant new authority.

Implemented contract:

- Authenticated `GET /api/chat/activity` defaults to the session's active saved project. Explicit `project_id` accepts an authorized project ID/name without activation. Project ACLs are checked on every request; native handles, task checkpoints and queue records are restricted to the authenticated owner. Arguments, numerical results, provider history, raw errors and approval tokens are not projected.
- Version 1 snapshots have All, Running, Needs you, Scheduled and Completed filters, a default page of 20/max 50, stable source IDs and an opaque actor/project/filter-scoped cursor. At most 500 rows per filter are projected, after filtering; at most 500 task files are considered. Running/queued work is ordered before attention items and completed history. Truncation/source incompleteness is disclosed. Scheduling remains unavailable.
- Cursor snapshots retain at most four revisions in each of 32 scopes for one hour. Unknown cursors require resnapshot. Pagination is valid only against the current snapshot and page size; changed/foreign/expired pages refuse with `activity_resnapshot_required`. Older concurrent collectors cannot overwrite a newer observation. These process-local projections do not become a mutable numerical authority.
- Foreground, study and report handles bind to the captured context and native worker/start identity. Activity considers at most 512 recent bindings. Replacement, eviction or unestablished worker identity returns `unknown`/`unavailable`, never the latest job's success. The legacy `wait_for_job` adapters use the same atomic identity-bound read, including any returned numerical fields; a replaced source cannot complete an old wait. Registered handles are not recreated by observation. Existing mocked foreground/multi-family fixtures now publish native identity fields as production does; their completion and timing assertions are retained.
- Queue/GridSpine rows reuse native live-over-persisted precedence. Persisted queue projection now retains `kind`, so a restarted GridSpine job stays identifiable. Interrupted jobs need review and are never re-admitted by reading.
- Task rows expose bounded owned checkpoint metadata. Inconsistent completed checkpoints and malformed/oversized/symlinked files do not become completed work. Source warnings reveal no checkpoint contents. Cancelling future task advancement remains distinct from an actual running job.
- Needs you includes uncertain/unavailable work and unexpired live native confirmations with a matching owner, replica and server-recorded project origin. Origin is captured at issuance, rather than inferred from the reader or the dormant Phase-0 context session slot. Legacy unattested origins remain absent; manual project navigation does not rebind a pending decision. It links to the originating chat; Activity cannot approve, deny, retry, cancel or execute. Native decision expiry/single-use checks are unchanged. Native completion does not certify engineering results; evidence is explicitly not checked in this view.
- Browser polling replaces authority after reconnect, deduplicates snapshot pages, hides old-project/filter responses and marks previously displayed data stale on read failure. Closing Activity stops its observer; Chat remains mounted and native jobs/decisions remain untouched. Compose reopens Chat after Activity is closed.

Validation evidence is recorded with the published source in `/workspace/scratch/github-actions/chat-companion-cp04.json`. Public HTTP tests cover authentication, owner/project isolation, each registered family, replacement sources, native failure, replaced-worker wait refusals, filtering before caps, concurrent collector ordering, cursors/pagination, persisted queue/GridSpine, task corruption/privacy, confirmation expiry, the actual HTTP stream → pending card → Activity → native denial lifecycle, project-switch origin isolation and a real native foreground thread with only the expensive solver replaced. Frontend tests exercise disconnect/project switches, decision navigation, cleanup and Chat mount identity. `scripts/smoke-assistant-activity.mjs` drives production components in Chromium at desktop, 320-pixel touch and reduced-motion widths, with HTTP mocked and no mutations. This is not a live Provider, actual domain-solver or hosted-media probe.

## CP-04 remains incomplete

This is a bounded observer slice, not completion of CT-11–14 in every family/lifecycle:

- Durable transient-job origin/history indexing and recovery across application-worker restarts; jobs started outside the existing Harness registrations; retained terminal outcomes after their native slot is replaced. Current transient history is explicitly bounded/worker-local.
- Persistent questions/source-gap/uncertain-effect decision records, prior/recreated conversation inbox navigation and independently tested stale revision/action-digest decisions. Current ordinary confirmation cards remain the decision authority; arbitrary history does not mint approval.
- Real native study/report/queue/GridSpine worker journeys, broader prolonged/reordered/recreated-episode recovery and all family evidence. The new foreground worker test does not certify these engines.
- Native evidence/result navigation from Activity, further policy/control UI in CP-06 and durable advancement in CP-10. No cancel-job or task-resume authority is added here.

Paid live verification is pending separate fresh authorization. No paid model/media call, reviewer contact, deployment or activation occurred. Independent engineering/domain review and representative real-use calibration remain pending by the owner's choice. All required exact-head CI and current-master premerge gates must pass before merge; local success alone is not merge evidence.


Review correction: the initial CP-04a draft head `880e97f70be60fac8c0533d98fcbcd710c4c2905` is not merge-ready. A production-path review found its confirmation lookup depended on an unpopulated Phase-0 context field. A new authenticated HTTP regression reproduced the missing live approval; native issuance now records a project origin and Activity reads the owned live session registry. Approval, expiry, denial, replay and preview rules remain native. The replacement source requires its own complete CI, offline gate and fresh current-master premerge; previous-head results do not certify it.

CI repair: old-head Test `38753156292`, backend shard 2/4 job `116484635942`, exposed `test_no_project_authority_does_not_break_the_solve`. Its fake evidence-publication worker was started without publishing a native thread identity. The failure reproduced locally; the fixture now publishes its actual worker alongside `status=running`, matching production. Its completion/evidence-authority assertions remain unchanged. The whole evidence-publication file and Activity tests pass together. This is batched with the native-confirmation correction; old-head CI is never substituted for replacement-source validation.
