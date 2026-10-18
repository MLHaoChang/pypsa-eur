# CP-06d — access summary in in-page chat

Status: ready-for-human
Baseline: master `8723578028bcb1952a36948dede20ebd09eb2b91` (#358)

Show a concise, server-observed access summary while the user opens “Chat here”.
Use the existing authenticated access snapshot and the exact next-turn checked
selection from the mounted ChatPanel; do not copy that selection into a second
store or infer it from a past answer. Show current project/mode, personal rule,
checking selection and blockers. Explain that actions are rechecked and that an
already running turn retains its admitted selection. No summary grants access,
approves an action or claims the next answer has already been checked.

An explicit keyboard-accessible disclosure opens the existing full access and
restriction controls in the same surface. Keep ordinary Confirmation/Choice
cards and sending independent. Use the same scoped query, fresh refresh/focus/
reconnect behavior and save invalidation as docked chat. Hide old observations
during a fetch, failed refresh, project/chat/session/mode/checking change or
mismatched response. Stop observation and remove the summary when in-page chat
closes or the dock opens. Do not remount ChatPanel or interrupt its stream.

Authorized seams: rendered React UI with API transport/time mocked, plus the
existing ordinary chat send transport to verify the summary and request share
the actual checked selection. Add vertical RED/GREEN cases for automatic scoped
observation, scope/refetch failures, explicit review/save, selection changes and
surface lifecycle. No new backend routes, models, tools, frames or authority.

Durable policy history, wider effect adapters, external readiness, continuous
media, background work and domain adapters stay separate. Live provider/device
verification, independent domain review and real-use calibration remain pending;
no paid calls, deployment or full-coverage claim. Source-pinned CI and the fresh
master premerge gate remain required for the owner's ordinary squash merge.

## Local implementation evidence

- The summary uses a React portal from the continuously mounted ChatPanel into
  the in-page companion. Its transient checked selection remains in that panel,
  and opening/closing either surface does not replace the chat or its stream.
- Public rendered-UI RED cases observed the missing summary and the incorrect
  unavailable-checks label before their implementations. The final 59-case
  access/checked-selection selection passes, including all 12 new cases,
  keyboard review, explicit revisioned saves, failed/in-flight refreshes, late
  project responses, mismatched mode/session/checking, and actual pilot/strict
  in-page send transport. Requested-but-unavailable checks are not labelled off.
- A broader surrounding frontend selection passes 842 cases across 38 suites.
  The final 59-case selection covers subsequent test/copy additions; the counts
  overlap and must not be added. Forced TypeScript and production build pass
  (existing large-chunk warning); these are simulated browser tests, not live
  provider/device evidence.
- Existing backend policy/API regressions pass 300 cases with one existing
  empty-KNOWN_GAPS parameter skip, including all six steward semantic suites and
  authenticated access/restriction tests. No backend behavior changed.
- Remote exact-source CI and fresh-master premerge evidence are recorded in the
  PR and workspace handoff after they complete; local tests are not remote CI.
