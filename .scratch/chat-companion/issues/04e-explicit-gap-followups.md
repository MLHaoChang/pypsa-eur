# CP-04e — Explicit checked source-gap follow-ups

Status: ready-for-agent
Type: implementation
Baseline: master `c06248d22`, includes CP-04d #327.

## Plan / contract

- The version/digest-bound source-gap inspector may offer a prepared checked
  request only when its stored descriptors can be validated as a supported
  RequestScope for that same project. Normalize legacy full-horizon generator
  scopes through the current schema; preserve entity, metric selection, window,
  cost basis and retained/baseline identifiers. Never copy numerical results,
  history, approval tokens or native session state. Unpreparable scopes and the
  eight-follow-up limit get a visible reason; ordinary chat stays available.
  A clarification-required scope cannot be prepared: the adapter must not choose
  an entity, cost quantity or basis on the user's behalf.
- Preparing places a separate, project/current-chat-bound request card in the
  existing chat; it does not send, overwrite the user's draft/attachments,
  enable the global check toggle, restore the old chat or activate a project.
  The user explicitly clicks Send checked follow-up. That click opts this one
  message into checking through the normal availability/profile/budget gates.
  The fixed message and exact scope are visibly separate from ordinary draft
  text, so free-form constraints cannot silently be discarded. Dismiss and
  project/new-chat reset clear the prepared card; it is never auto-queued.
- A bounded source_gap_ref in /stream is control metadata, not authority or
  model-visible history. Require strict checking, exact prepared message and
  checked_scope, no attachments, operation, script or voice/queued admission.
  Native worker admission rechecks current identity/project authorization and
  the exact owned gap revision/view digest before scope suspension or provider
  work. Stale, foreign, expired/unreadable and scope-mismatched references refuse;
  never fall back to an unchecked send or implicitly bind an old project.
- Only an actually admitted native answer_approved publication can append an
  owned link on the originating gap. Recheck the immutable originating gap and
  exact request scope; record only new answer/turn/session IDs, observed time
  and that receipt's request_complete boolean. No result values or current
  evidence claim. Links are idempotent and capped at eight per gap; concurrent
  admitted requests may append under the existing owner/project transaction.
  Admission refuses a full cap. A publication-time loss/cap/corruption produces
  the existing unavailable-history warning and cannot block publication.
- The original gap remains source_gap_recorded / Needs review. A complete or
  limited follow-up receipt describes only that publication, never resolution,
  freshness, engineering correctness or permission to execute/retry. Reads and
  unrelated later answers do not create links. Follow-up observations increment
  the parent revision, invalidate stale inspector references and share the
  existing 256-origin/1MB/30-day retention and corruption/link-refusal policy.
  Legacy records without links remain readable. No answer download/resurrection
  or gap-resolution API is introduced.

## Verification

Native authenticated HTTP/FakeProvider plus real HiGHS full/partial answer
fixtures: explicit complete/limited links, exact origins and descriptors,
foreign/current ACL, Save-As, stale digest/revision, wrong scope/message/mode,
no model call on refusal, no links on unpublished/aborted/ordinary answers,
session/process loss, idempotence, cap/concurrency and journal corruption/failure.
Frontend: prepare without sending, separate preserved draft/attachments,
per-message checked send only on click, no global opt-in or old session restore,
project/new-chat cancellation, safe text and no implicit retries. Run affected
regression, all six semantic suites, offline pilot, QA, build/browser, inventory/
matrix, full final-head CI and fresh-master premerge before ordinary squash.

## Boundaries

No automatic resolution, result certification, uncertain-effect reconciliation,
case execution, broader runner, paid model/media calls, reviewer contact or
production activation. Independent domain review and real-use calibration stay
pending by owner choice. No full-coverage claim.
