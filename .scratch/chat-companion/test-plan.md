<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Companion acceptance matrix

Status: ready-for-agent
Execution: this matrix defines the contract; per-slice evidence and remaining gates are recorded in the linked issues below. Unrecorded cases remain pending.
Source baseline: `4ac218cc98a1437950dd20c3f46233efac6cc159`
Specification: [CC-01–CC-16](spec.md)
Sequence: [implementation plan](../../docs/superpowers/plans/2026-10-14-chat-companion-implementation.md)

Execution records: [CP-01](issues/01-truthful-state.md), [CP-02](issues/02-canonical-outcomes.md), and [bounded CP-03](issues/03-source-navigation.md). CP-03 exercises CT-08–10 for retained native evidence and current Expert branch loading; it does not establish all-tab adapters, historical harness completion ACKs, live model verification, domain review or real-use calibration.

Use authenticated public chat/API/SSE and browser interaction. External transport/inference, time and interrupted I/O may be mocked; real ownership, catalogue/admission, ordinary confirmation, stores, job services and exported bytes remain exercised. Do not infer completion from a model sentence, HTTP success, animation or permission grant.

## Required observable cases

| ID | Requirements | Scenario and pass condition |
| --- | --- | --- |
| CT-01 | CC-01 | Open in-page text with no media capability: Chat here is usable; no mic/listening or speaking claim; unavailable voice explains readiness |
| CT-02 | CC-01,09 | Capture and playback start/end determine state; starting, denied, muted and reconnecting are represented; no timer fabricates audio |
| CT-03 | CC-01,06 | Microphone off while a solve runs: work progress remains visible; toggling media does not cancel the job |
| CT-04 | CC-02 | Closed companion renders the actual answer and an open-transcript route, plus authorized result/evidence/download actions |
| CT-05 | CC-02,11 | Checked original/projection identity matches receipt; tampered text, mismatched answer/actor/project/generation and unapproved shorter paraphrase never publish or speak |
| CT-06 | CC-02,16 | Ordinary, supported checked and unavailable/unsupported output are visibly distinct; no whole-domain verified badge |
| CT-07 | CC-02,03 | Downloaded exact selected artifact matches ID/hash/source; ACL revoked, missing/pruned or corrupt artifacts refuse without replacement |
| CT-08 | CC-03 | Historical follow-up retains selected run/window/baseline through navigation/rebind; live inputs stay unchanged; missing history is unavailable, not current results |
| CT-09 | CC-03 | Walkthrough opens eligible matching views and waits for displayed data/source ACK; stale/unknown ACKs cannot mark success; newer manual navigation wins |
| CT-10 | CC-03 | Shared current load-flow oracle covers both terminals, weights, window, configured/optimized rating and limits; proxy/AC distinction, missing/zero ratings and nonconvergence remain explicit |
| CT-11 | CC-05 | Activity lists only authorized facts, defaults to active project and obeys page/result bounds; wrong-owner/nested project references reveal no private work |
| CT-12 | CC-05 | Foreground/study/report/solve-queue/GridSpine fixtures expose real native states; an old success does not complete a newer queued/running/failed job |
| CT-13 | CC-05,06 | Cursor gap/duplicate/reorder/reconnect/recreated episode resnapshot correctly; no lost/duplicated work; unavailable jobs stay unknown rather than unexecuted |
| CT-14 | CC-05,06 | Ordinary read completes while slow real work runs; stop observer/voice, pause task and actual job cancellation retain distinct authorities/receipts |
| CT-15 | CC-06,07 | Interrupted/detached effect worker cannot be reconciled for replay while possibly active; missing ledger/index does not recreate admission |
| CT-16 | CC-06,07 | Verified completed effect reconciles without replay; verified no-effect retry needs fresh ordinary approval/reservation; unresolved state remains Needs review |
| CT-17 | CC-07 | Two isolated numerical cases match independent oracles and exact candidate identities; real baseline hash unchanged; one deliberate failed case remains visible under stop/continue policy |
| CT-18 | CC-07 | Comparison/export matches mode/window/weights/units/accounting basis; planned input differences are disclosed; unrelated drift/missing evidence/mixed basis refuses |
| CT-19 | CC-04,06 | Exact old/new/unit review; denial/expiry/changed preview/ACL/lock/policy revision prevents an effect; user can find the pending decision on either surface |
| CT-20 | CC-04,15 | User restrictions cannot lower native safety or external-write confirmation floors; documents/preferences/MCP instructions cannot grant authority |
| CT-21 | CC-09 | Gesture/permission/readiness gates capture; actor/thread/project/profile/generation-scoped credentials expire/rebind; browser has no main key or raw tool handler access |
| CT-22 | CC-10 | Partial, duplicate, fragmented or reordered transcript events create at most one admitted finalized utterance; one turn lease and bounded correction queue preserved |
| CT-23 | CC-10 | Technical names and ambiguous decimal/unit/negation corrections trigger appropriate clarification; partial or garbled text never approves an input mutation |
| CT-24 | CC-11 | Barge-in flushes playback, retains approved text and heard cursor and leaves completed effects/jobs intact; stale audio/captions cannot enter a new generation |
| CT-25 | CC-09–11 | Open dock, project/provider change, end, denial/autoplay failure and teardown clean tracks/playback/sockets/subscriptions; fallback preserves checked policy and requires fresh affected approvals |
| CT-26 | CC-12 | Concurrent/duplicate/late resource events settle once; unknown usage remains reserved; budget/startup refusal prevents new work |
| CT-27 | CC-12 | Restart/reconnect/profile rotation preserves parent media/model/job caps; child sessions and scheduled occurrences cannot multiply a parent allowance |
| CT-28 | CC-01,16 | Keyboard, coarse pointer, narrow viewport, reduced motion, readable contrast/captions and restrained announcements; existing Compose/Dictate/dock/focus/upload behavior retained |
| CT-29 | CC-09–12,16 | Separately authorized hosted/device probe measures actual supported language/number correction, noise/pauses, interruption/reconnect, stage latency, usage and shutdown; mocks cannot pass this case |
| CT-30 | CC-08 | Explicit work proceeds through ordinary admitted steps without an open browser stream, then pauses at an input/confirmation/unsupported checked operation |
| CT-31 | CC-08 | Crash before/after reservation, duplicate claim, late worker, checkpoint corruption, stale generation/ACL/revocation cannot duplicate effects or invent completion |
| CT-32 | CC-08 | Only authorized isolated project contexts are used; native task project-rebinding protection retained; ordinary reads and existing jobs remain usable |
| CT-33 | CC-13 | Scoped finite schedules persist, expire and respect IANA zone/DST policy; duplicated/late/missed occurrences are recorded without unexpected backfill |
| CT-34 | CC-13 | Read-only watcher gives a relevant source-bound change once; unchanged states stay quiet; pause/revoke/expiry/budget blocks new runs |
| CT-35 | CC-13 | Notification destination/disclosure is explicitly authorized; recurring read approval cannot authorize an external send, paid call or solve |
| CT-36 | CC-14 | Inspect/correct/delete owned preferences and derived caches; engineering assumption source/approval and retention behavior remain explicit; removed preferences stop future use; required audit records are distinguished from deletable context |
| CT-37 | CC-14 | Explicit bounded context handoff only; cross-project/tenant values, deleted context and revoked references cannot leak into another conversation |
| CT-38 | CC-15 | Safe external-tool readiness/reconnect/revoke; no secret disclosure or arbitrary endpoint; session catalogue freeze, limits and non-read confirmations preserved |
| CT-39 | CC-15,16 | New checked domain/effect/chart/report artifact admits only its proven source/units/basis/bytes; unknown tools and incomplete/malformed values remain refused/unavailable |
| CT-40 | CC-16 | Real representative journeys for every required family/control/parameter/view in released scope; fixture IDs, numerical tolerances, accepted errors/refusal classifications and denominators recorded; incorrect accepted claims or unauthorized effects block release; catalogue counts alone cannot pass |
| CT-41 | CC-16 | Prolonged project/Provider conversation with compaction/restart/rebind, concurrent monitoring, cumulative budgets and actual artifact contents; no replay or source substitution |
| CT-42 | CC-16 | Independently disable media/runner/scheduling/new adapters; safe typed chat, receipts, existing jobs and checked publication preserved; deployment/domain review statuses remain distinct |

## Numerical fixture and prior art

Use a real tiny network with 5 MW load over two one-hour snapshots and one feasible generator. With marginal cost 10 EUR/MWh the baseline objective is 100 EUR; isolated costs 20 and 30 produce 200 and 300 EUR. The declared same-basis difference between those cases is 100 EUR. A capacity of 1 MW yields a deliberate infeasible case and no published objective. Verify actual input/dispatch, owner/job/run identities, recorded sections and downloaded evidence, not just returned totals. Different weights/window/accounting units need independent expected outcomes or an incomparable result.

Existing prior art includes the companion/dock and checked-opt-in browser suites, public answer-assurance acceptance harness, experiment-case advancement, durable parent/rebind/worker-pin/prolonged conversation, nonblocking multi-family monitoring, approvals and event-log truth. Extend those seams rather than duplicating private service-only happy paths.

## Evidence record for each slice

Record exact source/head and applicable acceptance IDs; RED cause; GREEN/regression commands and actual counts; skips/deselections/path filters; independent fixture expectations; inventory/frame/schema changes; current-head CI and premerge results; and live-probe/device/latency/resource identities when actually executed. Counts from overlapping selections are never summed as unique cases.

For media cases, verify no default audio recording and no credentials/raw audio/private transcript content in ordinary diagnostics. Any optional recording feature requires separately tested consent, access, retention and deletion contracts before admission. Correctness evidence reports supported answers and refusal classes against explicit independent expectations; finite zero-error runs never become a zero real-use error claim.

All CT cases started pending. Add tests per implementation slice and record executed boundaries in its issue; future cases do not become permanent skipped placeholders. Preserve the existing 133-case offline gate and include new supported adapters before broadening checked admission. Report applicable ADR-0002 live verification, hosted/device proof, independent domain review and representative real-use calibration separately; no new paid run or reviewer contact is authorized by this matrix.
