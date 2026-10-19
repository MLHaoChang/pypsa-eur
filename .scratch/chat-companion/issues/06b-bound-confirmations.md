# CP-06b — bound confirmations and complete engineering previews

Status: ready-for-human
Baseline: master `e7777aa2e2c086789f1c802d387410a5280dc0cc` (#356)

Deliver a bounded part of CT-19/20 through the existing shared dispatch and
Confirmation card. Snapshot finite JSON call arguments in ordinary as well as
checked turns. Caller-owned inputs and emitted frames cannot rewrite the action
being approved. A changed confirmation policy or safety classification while
waiting invalidates the decision, even when the replacement is less restrictive.
Recheck at worker pickup before the handler; preserve native policy exemptions
and mandatory external non-read confirmation. A revision is server-derived,
never a preference, document or model-provided grant.

For `preview_project_changes` / `apply_project_changes`, admit only a complete,
bounded old/new/unit review (at most 20 values and a 3800-character review).
Refuse larger reviews with an instruction to split them; do not authorize hidden
rows. Bind the confirmed preview's full persisted content digest to the handler
through server-only execution context. Re-read and validate the record and
current candidate under the normal network lock before any effect. Changed
records, displayed rows, fingerprints, owner/project bindings or incomplete legacy
previews require a new preview. Existing applied/uncertain no-replay semantics,
ACL, foreign edit lock, study gates and unsaved/undo behavior remain authoritative.

Native candidate assignment must preserve each requested scalar exactly. Reject
unrepresentable integers (including overflow) before persisting a preview;
read back Python scalars so NumPy's implicit integer-to-float comparison cannot
hide rounding. Exactly representable large values remain supported.

The shared Confirmation card renders an accessible old/new/unit table on dock
and companion. Incomplete/malformed preview cards cannot enable Approve; Deny
remains available. No new event vocabulary, tools, lower confirmation floor,
policy-edit API or authority-bearing client field is introduced.

Authorized verification seams (standing owner authorization): ordinary public
harness dispatch with real project handlers, authenticated confirm/API, isolated
DB/project/locks, native filesystem fault injection, and rendered UI interaction.
Mock inference/transport/time/configuration only. Use vertical RED/GREEN cases
for argument mutation, policy change and persisted-preview replacement, then
regress denial/expiry/revocation, ACL/lock changes, undo, checked/native/external
dispatch, six steward semantic suites and the expanded offline pilot.

Remaining CP-06 includes exact previews for other supported effect families,
saved restrictive rules and inline access summaries. External-tool configuration
readiness/reconnect belongs to CP-13. Independent domain review, real-use
calibration and paid live-provider/device probes stay pending. No full-coverage,
domain approval or production activation claim.

## Local implementation evidence

- Three public REDs reproduced the original argument replacement (20 reviewed,
  99 applied), changed-rule approval and persisted-preview replacement paths.
  The final real-dispatch/HTTP regression selection passes 458 cases, including
  all 18 new binding cases and all six steward semantic suites. Two deliberate
  existing skips are the frame recorder and empty KNOWN_GAPS parameter.
- Owned HTTP approval applies the exact reviewed values; replay is refused.
  Caller/request/card mutation, changed confirmation exemptions/tool tier before
  admission or worker pickup, stale source, foreign edit-lock acquisition,
  missing legacy seals and a consistent replacement record all retain the old
  unapproved value. Oversized reviews cannot hide a twenty-first value.
- Additional public REDs reproduced native float64 rounding of
  `9007199254740993` to `9007199254740992` and overflow during numeric admission.
  Candidate preparation now refuses both before saving a preview; an exactly
  representable large value still passes ordinary review and applies correctly.
- 855 frontend cases pass across 40 suites, including ten new rendered review
  cases on both surfaces/modes, malformed/truncated review refusal and preserved
  zero/boolean values. TypeScript and production build pass (existing bundle
  size warning). These are simulated browser checks, not live device evidence.
- Inventory remains 255 tools / 15 tabs / 19 panels. The control matrix remains
  170 controls / 14 families, `full_coverage=not_established`. New tests pass
  Ruff; four changed Python modules retain their 25 baseline findings with no
  new findings. Expanded offline pilot and exact-head remote CI results are
  recorded in the PR and handoff after confirmation; no paid live probe ran.
