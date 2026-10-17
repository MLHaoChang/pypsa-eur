# CP-06a — current access and confirmation visibility

Status: ready-for-human
Baseline: master `fae95896500d6d74625e94e9f3a546d2d6b405af` (#354)

Deliver the read-only portion of CP-06 (CC-04/06/15, CT-19–20). An authenticated
`GET /api/chat/access` projects the caller's current project, native versus
external confirmation rules, current lock blocker, checked policy and configured
turn/session/daily limits. Optional expected project and owned session selectors
must refuse foreign, missing, revoked or changed bindings. Reading never creates
a chat, lists/connects external servers, calls a provider, grants an action,
consumes a confirmation or reserves work. Lock inspection must not prune rows.

The requested Guided/Expert and checked selections describe the next turn;
they are not claims about an already running turn. Reuse one confirmation-policy
function in the ordinary gate and the projection, including configured native
auto-approval and mandatory external non-read confirmation. No new policy floor,
saved rule, strict adapter or tool offer is introduced. A snapshot cannot certify
all action-specific prerequisites or future authority. Dispatch/commit rechecks
remain authoritative. Documents and preferences are never authorization inputs.

UI: a keyboard-accessible Access and approvals disclosure beside Check answers
in the dock's ChatPanel, reachable from the companion's existing Open assistant
route. Inline in-page permission summaries are not added in this slice.
Fetch only while the disclosure and dock are open, refresh
periodically/on reconnect/focus, and provide explicit refresh and unavailable
states. Key requests by project/chat/mode/check selection; hide previous or late
data after scope change or failed refresh. Show server text/typed fields, no
model interpretation, credentials, raw arguments, tokens or holder identities.
Existing Confirmation/Choice cards remain the only decision controls.

Authorized verification seams: authenticated HTTP/API, ordinary harness dispatch
and confirmation, real isolated DB/project/lock state and rendered browser
interaction. Mock external inference/transport/time only. Start with API RED for
auth/project binding and confirmation floors, then rendered visibility/rebind
and failure tests. Regress existing confirmation/ownership/ACL/lock/checked
contracts, six steward semantic suites and the offline expanded pilot.

## Local implementation evidence

- 18 authenticated access cases pass, including fresh locks/revocation, foreign
  sessions, non-consuming reads, actual configured confirmation floors and
  unavailable versus reported usage and an invalid server policy blocking every
  turn even without check opt-in. Included in 401 passing backend regressions
  across confirmation, native/external dispatch, ownership, budgets, Activity,
  layout and all six steward semantic suites. One pre-existing empty KNOWN_GAPS
  parameter is skipped; no new access cases are skipped.
- 798 frontend tests pass across 36 chat/dock/companion suites, including five
  rendered access cases for disclosure, keyboard, rebind, failed refresh and
  mismatched source. TypeScript and production build pass. These are simulated
  browser tests, not device or live-provider evidence.
- Inventory and navigation remain 255 tools / 15 tabs / 19 panels; matrix remains
  170 controls / 14 families, `full_coverage=not_established`. New Python module
  and tests pass Ruff; the existing confirmation/router modules retain their
  nine pre-existing docstring findings, with no new findings.
- Expanded offline pilot and exact-source remote CI evidence are recorded in
  the PR/handoff when confirmed. No live model/media probe was run (ADR-0002
  remains pending under the owner's no-paid-call constraint).

Remaining CP-06: revised exact old/new/unit previews across all supported effects,
policy-revision-bound decision invalidation and optional saved restrictive rules.
CP-13 owns external-tool readiness/reconnect controls. No claim that all CT-19/20
or milestone B is complete. Paid live probes, independent domain review and
real-use calibration remain pending. No live activation or reviewer contact.
