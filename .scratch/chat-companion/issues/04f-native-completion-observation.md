# CP-04f — Bounded native completion observation

Status: ready-for-agent
Type: implementation
Baseline: master `4eb4b5ef619b7cef549de95b5bea66f17df44d62`, includes #333.

## Contract

Retain terminal observations for registered foreground/study/report jobs even
when Activity and native waits are closed. Extend CP-04b's owned metadata store;
do not introduce execution, advancement, result, approval or retry authority.

- Registration may watch an actual started native Thread bound to the exact
  context/slot/worker origin. One process-local daemon observes at most 512 globally
  recent origins for at most 24 hours. It holds weak context references, never
  consults the active request's project, and exits when no watches remain.
- Reads acquire lifecycle locks without waiting, copy only native status and
  release those locks before journal I/O. Worker completion and unrelated reads
  never wait for this observer. Persist owner/project/family/tool/ID/time/status
  only, to the owned destination captured at registration.
- Replacement, eviction, project rebinding, lost context, expiry, unsupported
  native identity or process loss cannot infer completion, cancellation,
  quiescence or no effect. A real exited worker still marked running is unknown,
  not failed/successful. Unknown observations never erase retained terminal facts.
- Terminal writes use existing bounded atomic transactions. Cap/expiry/source or
  storage failure is best effort and exposes a bounded scope-local warning;
  exception bytes stay private. No retry of native work is admitted.
  Incomplete-capture warnings cover at most 32 owner/project scopes in this
  process; they are not durable proof of missed capture after process loss.
- Observation is not a guaranteed completion callback. A terminal native slot
  replaced before observation, or process death before persistence, can still
  leave Needs review. Queue/GridSpine retain their own durable authority.
- Existing ACL rechecks, 512-origin/1MB/30-day history bounds and evidence labels
  remain. A terminal observation is not a checked engineering result.

## Verification

RED: authenticated native foreground thread finishes with Activity closed, then
registry/source loss still loses its terminal state on baseline.

Cover real native foreground startup (only expensive solver mocked), controlled
native study/report threads, success/failure/abort, no observer/model/user send,
replacement and wrong-project identity, busy lifecycle locks, dead worker with
no terminal status, cap/expiry/weak context cleanup, storage failure/corruption,
captured store scope, concurrent registrations and shutdown/reset. Public API
reads after registry loss must expose exact retained status without values or
restored waits. Add frontend incomplete-observation warning coverage. Run
neighboring Activity/job/wait/assurance/manifest checks, expanded offline pilot,
inventory/matrix, all current-head CI and fresh-master premerge before merge.

No paid calls, reviewer contact, domain engine changes, unattended advancement,
case execution or production activation. Independent domain review, real-use
calibration and live-provider/device verification remain pending.
