# CP-04b — Retained job observations

Status: ready-for-human
Type: implementation
Baseline: master `07f90f2316ebdc1f9e42e1fffc17ea22d3b30e6a` (#309)

## Contract

CP-04a's registered native job handles disappeared when the application worker
restarted. This slice retains factual origin/status metadata for the existing
Harness foreground, study and report registrations. It adds no work admission,
runner, numerical evidence store or approval authority.

- Registration best-effort writes a canonical owner/project-scoped origin even
  without an Activity observer. Activity and terminal native `wait_for_job`
  reads retain only status observed against the exact native worker identity.
  The ledger contains family, job ID, closed tool name, registration time and
  last recorded status/time. It contains no arguments, raw errors, numbers,
  report contents, worker objects, session history or approval tokens.
- Authenticated Activity reads still recheck the project's current ACL. Scoped
  app-data records are separate from project snapshots: Save As, importing a
  project and navigating cannot rebind an origin. A fresh process can read the
  metadata without recreating native jobs, waits, contexts or sessions.
- Exact live sources take precedence. A retained terminal observation remains
  visible after its native slot/registry is replaced. Previously running or
  unknown work without a matching source is Needs review (`interrupted` in the
  projection), never successful, never proof of cancellation/quiescence and
  never proof no effect occurred. Late running collectors cannot erase a saved
  terminal observation. Retained metadata cannot restore `wait_for_job` or
  return numerical results.
- At most the newest 512 origins are stored per owner/project, in a file capped
  at 1 MB. Visible history covers 30 days from its last recorded state change;
  expiry is pruned on the next access, not a background deletion guarantee.
  Truncation remains disclosed across restarts. Unchanged reads do not refresh
  timestamps or churn snapshot cursors. The existing 500-row filtered Activity
  cap and 512 live binding cap remain independent.
- Storage uses the existing bounded cross-process transaction, private atomic
  replacement and fsync. Invalid versions/shapes, owner/project mismatch,
  duplicate/invalid IDs, extra fields, nonregular/oversized files and store links
  are unavailable, not imported or silently rewritten. Read/save failure keeps
  live observations usable and warns that missing history does not prove work
  never ran. No native network mutation lock is held for history I/O.
- UI labels live versus retained observations, shows a last-recorded timestamp
  and explains uncertain outcomes and limits. There are no recovered job action
  buttons. Native completion remains `evidence_available=false` / `not_checked`.

## Validation

The initial authenticated HTTP RED lost an origin immediately after registration
when process-local adapter registries were cleared. Regression cases cover all
three registered families, terminal success/failure/cancellation, native waits,
registry loss, replacement sources, separate-process reads/writes, stale running
collectors, scope navigation/ACLs, TTL, stable origin caps, corruption, links,
privacy and write failures. The real native foreground worker journey retains
its observed failed outcome after registry loss; only its expensive solver is
replaced. Frontend tests/build and mocked Chromium desktop/touch/reduced-motion
journeys exercise retained labels and zero job/approval mutations.

Local regression: **365 passed, 2 existing skips** (the deliberate frame recorder and empty known-gap parameter). Frontend focused tests: **32 passed**; production build passed. Inventory: **249 tools / 15 tabs / 19 panels**; matrix: **170 controls / 14 families**, full coverage not established. Counts overlap with the required evaluation and remote checks and must not be summed.

Exact-source evaluation and CI/premerge evidence belong in the PR and durable handoff;
local passing tests are not a claim that remote checks or merge are complete.

## Still pending

- Completion capture without an Activity/native-wait observation. A job that
  finishes and loses its native source before any terminal observation remains
  Needs review. No background completion watcher is introduced here.
- Jobs launched outside the existing Harness registrations, wider real native
  study/report/queue/GridSpine worker journeys, and retained native evidence
  actions. Queue/GridSpine keep their existing authoritative persisted stores.
- **CP-04c:** persistent questions/source gaps/uncertain-effect decisions and
  exact prior/recreated-conversation navigation. Recovery must not mint a new
  token or bypass expiry, revision, action-digest or ordinary confirmation gates.
- CP-05 reconciliation and CP-10 durable advancement are separate scopes.

No paid model/media calls, reviewer contact, deployment or activation. Live
Provider verification, independent domain review and real-use calibration remain
pending; finite tests and metadata do not establish engineering correctness.
