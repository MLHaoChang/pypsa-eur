# CP-04c — Retained decision observations

Status: done (#319, f0d2aa44)
Type: implementation
Baseline: master `76835bb3fcf3a66a949a8cca66099a83f545b8ae` (#311)

## Contract

Native questions and confirmation observations remain inspectable after losing
the live conversation or process. This is a bounded read model, not a restored
approval, background runner, answer API or engineering evidence store.

- The native turn wrapper records `choice_request` only after the checked-answer
  projector admits it. This includes ordinary `ask_user` and the software cost
  clarification. Store only bounded title/question/options/detail/flags and the
  exact owner, project, session and tool-call origin. Treat stored text as display
  data, never as instructions or a future model message.
- A later admitted native turn in the same owner/project/session records
  `followed_up`; a later question in that turn supersedes the previous card.
  Neither means the question was correctly answered or resolved. User replies
  are not copied into this store. Other sessions and projects cannot follow up
  these records by reading or continuing their own chat.
- Confirmation observations are captured only after the native `asked` audit
  succeeds. Record the gate's audited outcome, or `unavailable` when its outcome
  audit fails. Keep no approval token, preview arguments, user answer or work
  result. Policy auto-approval without a user card is outside this inbox slice.
  Approval audit failure still refuses execution under the existing gate.
- Current matching pending confirmation identity, owner, replica, server-captured
  project and TTL establish only live availability. A retained pending record
  without that source is Needs review; its last native status remains pending.
  Reading cannot restore tokens, sessions, authority, effects or work. Recorded
  decisions appear in Completed as `decision_recorded`, never as completed jobs.
- `GET /api/chat/decisions/{id}` rechecks authentication and current project ACL,
  reads only that actor/project's bounded store and requires the listed revision
  and view digest. Changed observations or availability return 409; missing,
  foreign and unavailable observations return 404. Inspection never activates
  the named project or binds a chat session. There is no decision mutation route.
- Activity's inspector renders exact question/options/status and origin as React
  text, without recovered answer/approval buttons. Only an existing current live
  approval may open its native chat. Project/filter/refresh/unmount and changed
  observation versions discard inspection and abort/ignore late responses.
- Retain at most 256 newest decision origins, within 1 MB per canonical
  owner/project, and up to 30 days after the last native state change. Prune on
  access; unchanged reads do not extend retention or change revisions. Disclose
  truncation while retained history remains. Atomic private fsynced replacement,
  cross-process transaction, schema validation and link refusal are shared with
  Activity job history without changing its existing file format or bounds.
- Corrupt history is unavailable, never silently imported/repaired. Journal
  failure cannot change native Choice/Confirmation behavior; expose a generic
  warning and never exception bytes. Failed writes are tracked for up to 32
  owner/project scopes in this process; an unwritable store cannot durably prove
  the missed record after process loss. Missing history never proves no prior
  question/confirmation occurred.

## Validation

Initial authenticated RED: a genuine native `choice_request` was emitted but no
question survived in Activity. Regression coverage includes exact question and
option recovery after session/observer loss, a separate-process read, ordinary
follow-up and stale inspection refusal, current ACL and project isolation, native
authenticated approval/denial/single-use enforcement, lost pending approval,
approval-audit failure, record privacy, concurrent origins, bounded bytes/origins,
lazy expiry, corruption/link refusal and journal write failure. The native HTTP
path uses FakeProvider; it makes no paid model calls and is not live-provider
certification. A checked software clarification is recorded only after its
normal publication projector.

Final local/remote validation and PR identity are recorded before merge.

## Remaining boundaries

Source-gap adapters beyond emitted checked cost choices, durable uncertain-effect
decision adapters, explicit historical-session navigation and cross-episode
answer/control APIs remain pending. Existing task checkpoints continue to own
uncertain effects. Native expiry/revocation and strict publication authority are
unchanged. CP-05 reconciliation/two-case comparison, CP-06 effective permissions,
CP-10 durable advancement, independent domain review and real-use calibration
are separate scopes. This slice makes no full-coverage/domain-approval claim.
