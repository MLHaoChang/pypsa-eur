# LC-07 — continuous speech on the shared harness

Status: ready-for-agent
Type: task
Baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`
Dependencies: Stable typed paths and existing parent/utterance/usage primitives

Read the [specification](../spec.md), [assessment](../gap-assessment.md),
[acceptance matrix](../test-plan.md) and
[implementation plan](../../../docs/superpowers/plans/2026-10-18-whole-application-live-copilot.md).
Applicable contracts/cases: LC-C7/C8/C9; LC-T13/14/15.

Complete mocked lifecycle and resource wiring, then provider-isolated LiveKit/STT/TTS integration under the approved hosted architecture. Finalized speech alone may request admission; preserve exact quantities, captions, interruption/fallback, scoped credentials, real cleanup and native confirmation. Vendor/device/readiness needs separate authorized evidence.

Expected ownership: speech adapters, scoped bridge/media routes, parent budget integration and browser media lifecycle; no second tool agent.

Rebaseline current master and incoming interfaces before implementation. Write
one meaningful public-seam RED case, then implement that slice and regress its
neighbors. Record actual source/commands/results and remaining gates here.
No new capability is enabled by this issue. Paid calls, reviewer contact,
deployment and full-coverage/domain-approval claims remain outside this package.


## Comments

2026-11-03: Rebaselined on master `8616af577`. Present: generic HTTP STT/TTS
adapters (`harness/providers/live_media.py`), utterance admission with stream
gate and auto-drain (`services/utterance_admission.py`, `harness/loop.py`),
durable thread with generation CAS, rebind-minted bridge tokens, replica pin,
one-worker store lock and `reserve_budget`/`settle_budget` (LV-09). Missing: a
speech session, streaming STT and media-transport seams, a TTS consumer, usage
wiring. No LiveKit/Deepgram SDK is installed. Companion Live still uses browser
Web Speech.

Plan (GK B (17) GO, three PRs, offline and mocked, nothing enabled):
PR1 backend speech session and resources (LC-T13, LC-T15); PR2 speech output
(LC-T14); PR3 frontend media lifecycle (UX (7) reviews UI).

GK B conditions:
- Seams are Protocols in `harness/providers/live_media.py`, provider-isolated,
  no SDK import at module load; mocks live in tests only.
- No separate replica check in reserve/settle: every speech route goes through
  `durable_conversation._read` (store lock fail closed, replica pin). A second
  process holding the store lock gets 503 `multi_worker_unsupported` on speech
  start and writes nothing.
- PR1 (a) policy suites at the trial merge (route_chat_disposition,
  project_route_acl_policy, write_surface_lock_policy, inventory, route-parity,
  control-matrix `--check`); each new error kind in its own
  `tool-error-kinds.d` fragment; unknown/non-owner/other-project threads refuse
  byte-equal; stale generation 409 writes nothing. (b) media credential scoped,
  TTL bounded, returned only by start, never stored, logged or echoed (grep test
  over the saved thread and captured log); rebind rotates it and closes the
  session. (c) partials never reach `admit_utterance`; each final admitted at
  most once per segment id; out-of-order or stale-generation finals refused; a
  "yes"/"confirm"/"approve" final leaves a pending card pending; ambiguous
  quantity rule narrow (one segment, two different numbers joined by a
  correction word) with a false-positive test ("7.5 MW"). (d) reservation before
  the transport starts; settle exactly once on end, error, timeout, rebind,
  stale generation and restart reconcile, a test each; duplicate or late usage
  is an idempotent no-op; usage finite, non-negative, in the reservation's unit;
  usage over the reservation settles at the cap and is flagged. (e) the start
  response carries `thread['reconcile']` items, cleared only in the same save
  that records the started session; a refused or failed start leaves them.
  (f) a default-off flag refuses every speech route with a typed refusal.
- PR2: spoken text byte-equal to caption, numbers/units verbatim against the
  admitted answer; unapproved, incomplete or checked-but-pending text never
  spoken; barge-in cancels playback only (running turn and solver untouched);
  TTS char usage settles once through PR1's ledger.
- PR3: device tracks stop on every exit (unmount, error, rebind, project
  switch, tab hidden), a test each; typed fallback in the same thread; no
  LiveKit SDK dependency.

2026-11-03: PR1 (branch `claude/lc07-speech-session`). `harness/providers/live_media.py`
adds `MediaTransport`/`MediaGrant` and `StreamingSttAdapter`/`TranscriptEvent`
Protocols (no SDK import). `services/live_speech.py` holds one speech session per
durable thread; routes `/api/durable-conversations/{id}/speech` (GET),
`…/speech/start|end` (owner) and `…/speech/segment|usage` (owner context plus
`X-Bridge-Token`). Off unless `PYPSA_GUI_LIVE_SPEECH=1`; no transport is
configured in the shipped app. A final segment yields a one-time
`speech:<segment_id>` ticket that `run_turn` redeems against the exact
recognised text before utterance admission (`speech_final_unknown` /
`speech_final_mismatch` otherwise). Rebind and episode rotation close the
session (`rebind`, `stale_generation`, `restart` on takeover). `end` closes the
room and waits up to `USAGE_GRACE_S` for the worker's `final` usage report,
then settles reported usage; a start that fails settles reported usage (0);
every other exit, a lapsed grace or a crash while `starting` settles the
reservation cap flagged `usage_unverified` (never under-count). Transport open
and close run outside the durable-thread lock; a start superseded by a rebind
while the room opens is refused and the room closed. Tickets carry their
admission generation.
GK B NOT OK @3c4dfe69 (d): timeout and end-without-report settled only on a
later speech call. Fixed: each open/closing session has a timer at its
deadline that re-reads the thread file (path captured by the ACL'd start/end)
under the thread lock and store lock and settles it; a boot sweep
(`live_speech.sweep_at_boot`, started from `main.py` startup off the boot path)
settles every live session a dead process left, locking only stores that hold
one. Tests settle with no later call; a later `state()` writes nothing; the
no-timer and no-sweep mutants fail.
Ledger refactor: `_reserve_in`/`_settle_in`
(public `reserve_budget`/`settle_budget` unchanged). Tests:
`tests/test_live_speech_session_lc07.py` (61 passed); durable/admission/
transport/manifest neighbours 163 passed; route_chat_disposition,
project_route_acl_policy, write_surface_lock_policy pass; inventory,
route-parity and control-matrix `--check` ok.

Remaining gates (recorded, not built):
- Worker-only bridge auth: the segment/usage routes still need the owner's
  authenticated context (project/actor resolution); a bridge-token-only path
  arrives with the audio-worker hosting gate.
- LiveKit Cloud credentials, server-SDK grant minting and agent dispatch
  (a concrete `MediaTransport`); agent-worker hosting and resources.
- Deepgram streaming contract and model id (a concrete `StreamingSttAdapter`);
  OpenAI TTS model id.
- Device, language, latency and accuracy measurements; paid calls; deployment
  smoke; multi-worker leases (reserve/settle still have no replica check of
  their own; speech routes inherit the store lock and pin through `_read`).

2026-11-03: PR2 speech output (LC-T14, branch `claude/lc07-speech-output`, on
master `cbfd6cbcd`). `run_turn` hands every client frame of a turn whose
utterance is a redeemed `speech:<segment>` ticket to
`live_speech.AnswerCapture`. At `turn_done` naming the `answer_approved`
answer, the turn's `token` frames (all after the approval) must equal the
assurance-approved text byte for byte; then the answer is queued for that
speech session if the turn was not aborted, no newer turn has started on
the conversation (typed, spoken or a drained correction; LC-T10) and the
ticket's admission generation is still current. A newer turn also drops
answers already queued but not yet claimed. Never queued: legacy (unvalidated) text, prose
before approval, an error frame, an approval whose turn never reached
`turn_done` (checked but publication pending), a `turn_done` naming another
answer, typed turns, superseded generations, text over
`live_media.MAX_TTS_TEXT_CHARS`. Bridge routes (owner context plus
`X-Bridge-Token`): `…/speech/output/claim` hands the worker the next answer
once (text, sha256, chars; stale-generation items are dropped) and charges
`len(text)` characters to the session's TTS usage through `report_usage`'s own
record (usage id `tts-<output_id>`; the worker's report under that id is a
duplicate), refusing (`over_budget`, caption only) an answer the reservation
cannot cover; `…/speech/barge-in` cancels queued and claimed answers only (no
abort, lease, admission-generation or job change; no refund). Every session
exit drops its queue. `live_speech.synthesize_output` passes the claimed text
to a `TtsAdapter` verbatim after its sha256 check. Tests:
`tests/test_live_speech_output_lc07.py` (21; ten mutants each fail a test).
mp-code-review (standards + spec) findings fixed: newer-turn supersede, and
`redeem_final` records the spoken ticket in the same lock block as the pop.
Kept: characters are charged at claim, not refunded on barge-in or failed
synthesis (never under-count); a worker TTS report under another usage id
double-counts (over, never under); `len(text)` counts code points, vendor
billing units are a vendor gate; `synthesize_output` has no production caller
until the audio worker exists.

Remaining gates (recorded, not built), in addition to PR1's:
- Pronunciation of engineering numbers and units (LC-T14 "pronounced
  correctly") needs the vendor voice and listening evidence; text is passed
  verbatim, never rewritten.
- Answers longer than one TTS request are not spoken (no chunking until the
  vendor's request limit is known).
- Worker-side playback stop and audio-frame flushing live in the audio worker
  (agent hosting gate); the backend refuses any further claim of a cancelled
  answer.
