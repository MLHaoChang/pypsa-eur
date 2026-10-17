# CP-06c — saved personal project restrictions

Status: ready-for-human
Baseline: master `3f45c0e7d9ad66ee74e6bd918d655419152f3e8f` (#357)

Add personal, saved-project restrictions through Access and approvals. The three
closed modes are `standard`, `confirm_changes` (every non-read tool needs the
ordinary audited user card), and `read_only` (every non-read tool is refused).
Standard retains all native/Guided/server and mandatory external-write floors.
Rules cannot grant tools, relax checked profiles, bypass ACLs/locks or authorize
spending. Documents, bundles, preferences and model arguments cannot edit rules.
Read-only describes tool classification, not read-only inference or free usage.

An authenticated PUT `/api/chat/restrictions` accepts only the expected current
project UUID, current revision and one mode. Current authorized project binding
is required. The actor comes from authentication, never a body owner field.
Access snapshots show the mode/revision and the effective confirmation rules.
Rules belong to the current user and project UUID: another member has separate
rules; rename preserves scope, a new/scenario project does not inherit them.
Unsaved drafts cannot save rules. Clearing is a revisioned save of standard,
not deletion. The database stores only the current rule, not policy history.

Persist with an additive, cascade-deleted owner/project table and atomic
compare-and-swap updates. A stale save returns 409 without changing policy.
Missing rows mean standard; malformed or unavailable storage never becomes a
default grant. Reads and worker admission use fresh database sessions. Changes
are checked before confirmation, after an approved wait and at worker pickup.
Any changed revision, including change-and-restore, invalidates waiting work.
Already started handlers are not cancelled or rolled back by a later save.
Native handler admission/effect checks remain authoritative.

UI saves require an explicit user action. Show saving/saved/error and stale-save
recovery; do not optimistically claim enforcement. Hide old or late responses
after project/chat changes. Preserve pending Confirmation/Choice controls and
show that a save does not cancel started work. No tool, frame or provider API is
added. Wider effect previews, durable policy history and external readiness stay
separate. Independent domain review, real-use calibration and paid live/device
probes remain pending; no production or full-coverage claim.

Authorized test seams: authenticated HTTP with real isolated DB/project/ACL,
ordinary native/external/checked dispatch and owned confirmation, rendered UI
with mocked transport, and native persistence/migration fault injection. Use
vertical RED/GREEN cases for saved read-only blocking real writes, extra cards,
stale/foreign saves, pending/queued revocation and safe UI saves. Regress native
floors, mandatory external-write cards, scope isolation, six steward semantic
suites and the expanded offline pilot. Verify current-head actual remote shards,
pilot and QA; ordinary expected-head squash merge only after all gates pass.

## Local implementation evidence

- 29 new authenticated API/ordinary dispatch cases pass without skips, including
  real numerical writes, owned HTTP approval, change-and-restore revocation,
  actual queued worker pickup, concurrent saves on the real file-backed SQLite
  store, new connections/chats, owner/project isolation and unavailable/corrupt
  storage. A valid checked update is refused before its durable reservation.
  Native exemptions retain their existing floor; extra cards and mandatory
  external-write confirmations use the ordinary gate and real stdio stub.
- A broader backend selection passes 558 cases with two existing deliberate
  skips (frame recorder and empty KNOWN_GAPS), including all six steward semantic
  suites and seven SQLite migration tests. The migration preserves seeded prior
  rows including Library data. The final 29-case restriction selection also
  covers the added invalid initial-revision guard. Counts overlap, never sum.
- 1,017 frontend cases pass across 54 suites. A final 271-case selection covers
  all 12 access UI cases plus error-copy/manifest/profile regressions, including
  stale/failed saves, project/chat changes, unavailable drafts and old/mismatched
  acknowledgements. Forced TypeScript and production build pass (existing large
  bundle warning). These are simulated browser tests, not live device evidence.
- New Python files pass Ruff; changed files retain 22 baseline findings and no
  new findings. Inventory remains 255 tools / 15 result tabs / 19 panels; matrix
  remains 170 controls / 14 families, full_coverage not established. Diff checks
  pass. Clean-source offline pilot and exact-head remote evidence are recorded
  in the PR/handoff after confirmation; no paid live probe ran.
