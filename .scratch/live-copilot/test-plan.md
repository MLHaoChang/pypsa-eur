<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Copilot acceptance matrix

Status: ready-for-agent
Execution: these are acceptance requirements, not assertions of completed tests.
Baseline: master `c75ca5584b873418f10f1bca42f868255d445b7b`

Use public authenticated API/SSE, actual native services/isolated database,
real local workers/solver fixtures and rendered browser interaction. Model
inference may use a loopback wire fixture; vendor/device validation remains
separate. Keep the numerical oracle independent of adapter implementation.
Never use forced clicks, route-fulfilled successes or mutable result state as
proof that a user action, native effect or job actually succeeded.

The owner-requested independent source review found these 19 generic cases
insufficient as a domain acceptance specification. The
[24 mandatory domain suites and 14-family mapping](domain-coverage.md) add
capacity expansion/multi-period investment, static load flow, distinct adequacy
engines, control strategies and actual aggregate time-domain frequency tests,
as well as the other application families. LC-D01–24 are requirements, not
newly passing tests. One representative journey cannot certify every engine
inside a family; use the annex's operation/variant traceability and negative
cases as the release denominator.

| ID | Journey / failure case | Acceptance evidence |
| --- | --- | --- |
| LC-T01 | Discover all currently eligible tools under provider offer limits | Schema/dispatcher/route/toolset parity, both provider wires, on-demand discovery then ordinary execution; inaccessible capabilities never offered as authorized |
| LC-T02 | Inspect complete model parameters across actual schema classes | Paged public schema/value reads, entity IDs, units/enums/bounds/editability; generated inventory detects new/missing fields; no secrets or unbounded prompt export |
| LC-T03 | Storage X/Y/Z cost edit | Real Store and StorageUnit fixtures; explicit cost-basis clarification, correct field mapping, exact old/new/unit preview, owned approval, native read-back; invalid/unit/NaN/computed-field writes refuse |
| LC-T04 | Update several line parameters then simulate | Native write receipts and revised input fingerprint precede one actual solver job; a changed target/input/policy, denied approval or one failed/uncertain edit prevents the dependent run |
| LC-T05 | Duplicate/restart/late write or solve | Browser disconnect, process death and delayed workers preserve reservation/effect state; reconcile unknown before new explicit retry; no second write/solve from restored history |
| LC-T06 | Exact sensitivity/project/run selection | Same-name cases, multiple projects/runs and ambiguous “X” require exact selection; current results never replace an unavailable requested historical run |
| LC-T07 | Native result adapter oracle | API/table/chart/chat/export agree on facts, units, windows and source; for a branch fixture use flows 45/65, rating 100, s_max_pu 1/0.5, weights 1/3: peak utilisation 130%, peak nameplate loading 65%, over-limit weighted hours 3; do not label active-power proxy AC thermal compliance |
| LC-T08 | Retained-result proof | Complete immutable manifest/section/hash; owner/ACL rechecked at read, navigation, answer and download; missing/pruned/corrupt/unfinished sections refuse with exact source ID |
| LC-T09 | Generic results walkthrough | Every released adapter has current and supported historical cases, matching rendered-source ACK, filter/entity/time selection; unrelated/legacy mount ACK cannot certify source; Guided mode preserved |
| LC-T10 | Manual navigation / rebind races | User navigation/new turn/project/run/generation supersedes pending ACK, answer, highlight and speech; no stale caption or cross-project view mutation |
| LC-T11 | In-page decisions and composer layout | Pointer/keyboard selection of Check answers with expanded and collapsed Access, default/resized composer, desktop/touch/short screens; exact Choice/Confirmation controls reachable; no forced-click substitute |
| LC-T12 | Long native work with follow-up | Real slow workers permit ordinary reads and follow-ups; native progress/completion bound to job/run; stopping playback/observation does not cancel work; cancelling a job has its own receipt |
| LC-T13 | Finalized speech and corrections | Partials never execute; duplicated/out-of-order final utterances, unit ambiguity, overlap and echo cannot duplicate actions or approve a card |
| LC-T14 | Playback/caption identity | Same admitted answer/facts, engineering numbers/units pronounced and displayed correctly; barge-in flushes late audio; incomplete/unapproved checked text never spoken |
| LC-T15 | Media ownership and resource bounds | Mock lifecycle first; real scoped credentials, expiry/rebind/revoke, all cleanup exits, cached/unknown/late usage settlement exactly once and caps across restart |
| LC-T16 | Per-capability and domain release | All applicable LC-D01–24 suites and distinct engine/control variants mapped to source-pinned read, permitted edit/action, result/explanation/navigation/export and refusal/recovery evidence; all eligible tools/schema fields/audited controls mapped. One representative family journey or inventory counts cannot certify the rest |
| LC-T17 | Incoming feature compatibility | After actual merge, profile generation/presets/apply and process-unit fields use native provenance/unit/admission contracts; pending PRs never presented as installed capabilities |
| LC-T18 | Independent live readiness | Separately authorized capped vendor probes and real browser/device/network/language measurements, including prolonged use; domain review and real-use calibration retain separate evidence |
| LC-T19 | Unsupported or invented quantitative narrative | Inject extra numbers, wrong units/source, altered paraphrases and unsupported result claims from the model; publication withholds those claims or refuses. Every released engineering numerical claim maps to admitted native facts; no unchecked fallback for a checked request |

Added 2026-10-19 from the [vision review](vision-review-2026-10-19.md). The
end-to-end [owner journeys](owner-journeys.md) OJ-01–12 are the acceptance
denominator for what the owner will try; each Codex layer closes on its journeys
(owner decision Q1).

| ID | Journey / failure case | Acceptance evidence |
| --- | --- | --- |
| LC-T20 | Owner journeys end to end (Tier A) | OJ-01–12 as they land: exact tool trace, approvals, native read-back, one job per approved run, UI target shown in a real browser, every answer number traced to a tool fact of the turn. CI, loopback wire, real backend |
| LC-T21 | Focus/recency resolution | Selection beats earlier talk; class mismatch asks; foreign/stale selection ignored; single candidate used; otherwise one Choice; “latest” skips failed/running runs and names the one used |
| LC-T22 | Continuation after a long job | Solve longer than 25 s finishes the plan without a new prompt; restart during the job gives one continuation and no second run; cancel or project switch gives none |
| LC-T23 | Live-model journeys (Tier B) | Owner decision Q2: manual trigger only. Same journeys with a real hosted model under a hard cost cap; pass counts with denominators per journey; never a merge gate |

Current browser evidence for the existing permission implementation is recorded
separately in the permission closeout. Its pointer-accessibility failure remains
open; this matrix creates no placeholder tests and does not waive that failure.
