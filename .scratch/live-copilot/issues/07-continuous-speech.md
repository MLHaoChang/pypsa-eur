# LC-07 — continuous speech on the shared harness

Status: done
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

GK B plan GO for PR2 with additions (recorded as given):
(g) budget before sound: a claim whose characters exceed the remaining TTS
reservation refuses with a typed refusal and nothing is spoken; never
truncated; charging at claim accepted, a claimed-then-cancelled answer stays
charged. (h) bounded per-session queue (e.g. 8), overflow drops the oldest
unclaimed answer with a counted warning; outputs carry the generation and a
claim after rebind or rotation refuses. (i) any suppression of code, tables
or tool logs yields exact in-order substrings of the approved text, shown as
the caption; without it the whole text is spoken; byte-equal test with
"7.5 MW" and "1 200 kWh" against the sha256. (j) barge-in is session-scoped:
stale generation or closed session refuses and cancels nothing; the
untouched-turn test spies on abort_event and the job/solver entry points.
(k) PR1's rules at the trial merge (policy suites, inventory, route-parity,
control-matrix `--check`, manifest test), byte-equal refusals for unknown,
non-owner and other-project threads, `speech_disabled` on every route while
the flag is off.

PR2 against them: (g) `speech_output_over_budget` (409, own fragment
`tool-error-kinds.d/claude-lc07-speech-output.json`), no usage row, the
worker's synthesize step is never reached. (h) `OUTPUTS_MAX = 8` bounds unclaimed
answers only (GK B (18) NOT OK @eac89f21: played answers had taken slots);
only the latest claimed answer is kept, for barge-in; when 8 are waiting the
oldest unclaimed one is dropped, counted in `_OUTPUT_DROPS` and logged; a
long session of one-at-a-time answers drops nothing (tested); a closed session (rebind, rotation, any exit)
drops its queue and refuses claims. (i) no suppression is built: the whole
approved text is the caption and the spoken text; test passes "7.5 MW" and
"1 200 kWh" through `HttpTtsAdapter` and checks the sha256. (j) `barge_in`
takes the thread generation (`stale_generation` 409) and refuses a closed
session (`speech_session_closed`); the test spies on `abort_event`,
`chat_tools.abort_*`, `assistant_tasks.cancel_task`, `runner.stop_default`,
`experiment_execution.cancel_experiment` and `cancel_if_queued`. (k) both new
routes are covered by the byte-equal refusal and flag-off tests.

2026-11-03: PR3 (branch `claude/lc07-speech-frontend`), frontend only.
`utils/liveSpeechSession.ts` (`LiveSpeechSession`) owns the mic stream, the
`/speech/start|end` session and a `SpeechMediaLink` seam
(`configureSpeechMediaLink`; none ships, no LiveKit SDK). Order: mic, then
start (a denied mic reserves nothing), then `link.connect(grant, track)`. The
grant is passed straight to the link and never kept in state. Every exit runs
one `stop()`: tracks stop first, synchronously, then the link closes and `end`
is sent (best effort; the server reaps an unended session). A stop racing a
pending mic prompt or a pending start stops the late track or ends the
late session. `hooks/useLiveSpeech.ts` offers the control only while Live
is open, the thread is bound, a link is configured and `GET /speech`
answers (flag on). Exits wired: Stop, error (mic denied, start refused, link
connect failure, link drop), Live closed, unmount, `visibilitychange`
hidden, `pagehide`, project switch, rebind (thread or generation change),
track `ended` (revoked/unplugged), session `expires_at`. The typed composer
stays on the same durable thread; each stop leaves a caption with the reason.
UX (7) rulings: option (b), "Start voice"/"Starting…"/"Stop voice" beside
Send while the server flag is on (disabled with the reason in the composer
caption when no media link is set up), the voice note hidden then; flag off
leaves master's note as it is. Captions: "Listening: speak, then pause." and
"Voice stopped: <reason>. Type here to continue in the same conversation.",
one map per exit reason and a fixed sentence per error code (no error or
server text shown). Expert clipping of the voice note: follow-up `07b`.
Tests: `hooks/useLiveSpeech.test.tsx` (22, a test per exit; mutants that drop
the closed/hidden/project wiring fail), `CompanionLauncher.test.tsx`
(+4). Shots: `scripts/live-speech-shots.mjs` (route-mocked, fake mic) →
`.scratch/live-copilot/evidence/07/` (Guided and Expert: off, listening,
stopped-hidden, flag off, flag on without a link).
Remaining: a concrete media link (LiveKit client) arrives with the transport
gate; `end` on `pagehide` is a normal request that may not finish, so the
server deadline reaper settles that session.

2026-11-05: Rebaselined on master `e98226bd1`. Merged: PR1 #1058 (`cbfd6cbc`),
PR2 #1059 (`e9127fae`), PR3 #1063 (`45e06fe9`), 07b #1075 (`570e2398`).
PR4 echo guard (LC-T13 "echo", branch `claude/lc07-echo-guard`), backend only.
`claim_output` records the claimed answer's words, its claim time and its
character count per session (`_RECENT_SPOKEN`, bound 4, process-local, kept
after barge-in, dropped on every session exit). `submit_segment` returns
`echo_ignored` (no ticket, segment marked seen) for a final of at least
`ECHO_MIN_WORDS` (3) words that is a contiguous word run of such an answer
within `ECHO_BASE_S + chars / ECHO_CHARS_PER_S` (5 s + 1 s per 12 characters)
of its claim. Shorter finals are never echo; the bare-approval rule still
guards "yes". No new error kind and no route change. Tests:
`tests/test_live_speech_echo_lc07.py` (11; 28 after E1–E7, the code review and GK B's NOT OK). They cover an echo during
playback, the same words after the window, user speech over playback, echo
after barge-in, short overlaps such as "7.5 MW", reordered words, the session
exit, and overlap: three finals during a running turn give one admitted turn
plus one pending correction (the latest), and a replayed final is a
duplicate. The no-guard, no-window and no-exit-cleanup mutants each fail a
test. Neighbours (speech session/output, admission, drain, rebind,
one-worker, error-kind manifest): 148 passed.
Kept: a user who repeats three or more words of the answer verbatim while it
is still playing is ignored once; the reply says to type it or say it again
after playback. Word matching is exact; STT spelling drift (e.g. "7,5")
leaves an echo unmatched, so it falls back to the admission rules
(generation, single pending correction, no spoken approval).

GK B (25) GO with changes E1–E7 on PR4 (recorded as given, with how each is met):
E1 approval path: an answer that asks "Say yes apply it"; with a card
pending, the echoed "yes" is refused by the bare-approval rule
(`not_an_approval`, as `test_spoken_yes_never_approves_a_pending_card`), and
the echoed "yes apply it" is `echo_ignored`; the card stays pending.
E2 min words: a "yes" or "apply it" heard during that answer is an ordinary
final; the 1- and 2-word threshold mutants fail.
E3 bounded state: each claim keeps its words from the start, up to
`ECHO_WORDS_MAX` = `MAX_TTS_TEXT_CHARS // 2` (1000), which never cuts a
speakable answer (GK B (25) NOT OK @5029b0d7: the first cut kept the last 256
words, so an echo of a long answer's opening was admitted; a test with a
2000-character, 401-word answer echoing "w0 w1 w2 w3" failed first, then
passed; a test with the bound at 8 shows the opening is kept); `_RECENT_SPOKEN` is read and written only under
`_TICKETS_LOCK`; a rebind clears it (test).
E4 (option 1 of three put to GK B): a final that gets no ticket
(`echo_ignored`, `clarify`, `not_an_approval`) leaves a notice
{status, fixed message, seq, generation} in a per-session list (at most
`NOTICES_MAX` 4, oldest dropped, under `_TICKETS_LOCK`, cleared on every exit
and on rebind). `GET /speech` returns `notices` for the current generation
only. Tests: one per status with no transcript substring; the bound; rebind
and exit clear the list; older generations are hidden; a foreign owner gets
today's byte-equal `thread_not_found`. The echo reply carries no `text`
field and nothing is logged. The Live caption rendering is
[07c](07c-speech-notices-caption.md) (UX gate); until it lands the drop is
silent in the browser (audio-worker gate).
E5 window: `time.monotonic()`, `ECHO_BASE_S + chars / ECHO_CHARS_PER_S`,
inclusive; tests 0.5 s inside and outside; the `>=` and no-chars/12 mutants
fail.
E6: after an ignored echo (card pending), the same words as a new segment
after the window are `final_accepted`, not a duplicate.
E7: risk listed in the PR body; speech neighbours, the three route policy
suites, inventory, route-parity and control-matrix `--check` run at the
trial merge (master `e98226bd1`): 189 passed, 1 skipped; checks ok.

Acceptance (LC-T13/14/15) within this package: met offline and mocked.
The remaining gates listed under PR1, PR2 and PR3 above need vendor,
device or deployment evidence the issue keeps outside this package
(vendor transport, STT/TTS models, pronunciation listening evidence,
worker-side audio flush, real scoped credentials, paid calls). They are
recorded, not built.
