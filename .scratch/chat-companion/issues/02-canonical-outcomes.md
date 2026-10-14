<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# CP-02 — canonical companion answers and checked evidence

Status: ready-for-human
Type: task
Implementation baseline: master `d2b9f57b24aa755c71486512ab106c6543dc67c5`
Branch: `claude/chat-companion-outcomes`
Product dependency: [CP-01 / PR #235](https://github.com/MLHaoChang/pypsa-eur/pull/235), head `856df016955e219b85ddeabb62936a4eb60366f3`; do not rewrite that green source or repeat its implementation.

The owner accepted the [companion specification](https://github.com/MLHaoChang/pypsa-eur/blob/856df016955e219b85ddeabb62936a4eb60366f3/.scratch/chat-companion/spec.md) and [implementation plan](https://github.com/MLHaoChang/pypsa-eur/blob/856df016955e219b85ddeabb62936a4eb60366f3/docs/superpowers/plans/2026-10-14-chat-companion-implementation.md). This independent master-based slice covers CC-02 and applicable CC-03/11/16, CT-04–07. Its diff does not copy CP-01. Combined-source integration must be reviewed before release and after CP-01 lands; the shared dock tests and CSS need normal overlap reassessment.

## Implemented behavior

- The most recent completed response is readable beside the collapsed companion, including when in-page chat is closed. Canonical text is retained through expansion/scrolling with no model rewrite or timed removal. A new unanswered request hides older results; streaming text is not presented as a completed outcome.
- Ordinary output says Answer not checked and labels recorded conversation context without inferring numerical provenance. Partial/interrupted output remains explicitly incomplete. Oversized ordinary text uses the transcript route rather than an unbounded card.
- Checked publication uses two existing authenticated reads: the owned answer record and active project context. Bypass HTTP caches, then require exact answer/project/available live turn identity, matching receipt, supported profile/coherent requested-slot disposition and native UTF-8 SHA-256. A receipt or checked-policy hint alone cannot authorize a paraphrase, raw artifact or ordinary downgrade.
- Project/session/profile/new-chat/generation changes invalidate in-flight view state. Late reads cannot update another request. Focus and explicit refresh revalidate evidence; missing/pruned/corrupt/foreign or revoked sources withhold the card body and evidence actions.
- Reuse the existing checked values/evidence ZIP and authenticated record/evidence routes. Those handlers recheck ownership, source validity and artifact bytes at use; no new route/tool/stream frame or artifact admission is introduced. Clarifications declare that no result is checked and offer no numerical export. Scope/window/limitations and the declared EUR price year/fixed-cost fraction remain visible without guessed defaults. Snapshot text is labelled Recorded evidence rather than implying a continuously refreshed result.
- Ordinary downloads still require the native project upload-blob shape; checked turns suppress raw tool downloads. Open transcript expands the existing dock and sends no work. Source-bound navigation to native results remains CP-03: this slice offers a view of the exact checked answer record and evidence, not a fabricated native run link.
- Scroll bounds keep the entire companion reachable on short screens. Coarse-pointer controls exceeded the default composer height and covered Check answers after returning to the dock; a layout minimum preserves their touch targets and the user's saved resize preference.
- The same explicit JSON fixture file-list fix as CP-01 is present for an independently buildable master-based branch; it is identical, not a relaxed type check.

## Validation and limits

Initial RED: 11 new closed-publication cases failed against the download-only surface. The final boundary selection covers ordinary/checked outcomes, unknown scopes, receipt/hash/turn/project mismatch, missing approval/evidence, clarification, generation/failure invalidation, no-timeout canonical expansion, opt-ins/history and cost-basis validation. Exact results are recorded with the publication evidence; selections overlap and are not summed.

Existing authenticated backend suites `test_answer_assurance.py` and public P1/P2/P4/P6 assurance acceptance passed **60 cases**, with no non-pass progress symbols and exit 0. They exercise real auth/project/record/source/export guards with fake inference. The initial restricted execution stalled in TestClient startup; the same diagnostic passed in 2.92 seconds after granting the networking capability required by the in-process async client. The selected suite then passed with that runtime setting; no application guard or assertion was changed for the environment issue.

Native Chromium smoke exercises the real dock/ChatPanel, HTTP/SSE/digest handling, closed checked cards, withheld tampered/unavailable records, refresh/recovery, 320px touch/600px screen bounds, reduced motion and transcript reopening. External APIs are mocked and remote HTTP is blocked; no paid inference/media calls occur. This is browser/public-contract evidence, not hosted API or engineering certification.

Local full-suite/build, combined CP-01 integration, exact-head remote CI and current-master premerge results are recorded in the publication description and durable validation record. Applicable [ADR-0002 live verification](../../../pypsa-gui/docs/adr/0002-chat-changes-need-a-live-api-probe.md) remains pending without a fresh capped paid-probe authorization. Independent domain review and real-use calibration remain pending by owner choice. No deployment, continuous media, unattended work, new case execution or full-application correctness claim is made.

## Confirmed local publication evidence

Integrated application source `bbf89d7cb88093e924382b4abebe52402346a4a4`, including master `bcb803c52640436e46cbc3a9d93932a2bf2b64c8`: full frontend **5,167 passed / 445 files**, 487.28 seconds, no skips reported. Production build passed in 33.09 seconds; inherited bundle warnings remain. Native Chromium checked-answer smoke passed with actual emitted revalidation headers; no remote HTTP/model calls are permitted by the fixture. Browser artifacts: `/tmp/checked-browser-VxSy43`.

On that integrated source, the authenticated answer/evidence selection and all three required semantic suites passed **265 tests**, with **one empty-parameter skip** in `test_every_known_gap_is_still_a_gap` because `KNOWN_GAPS` is empty. This is not a waiver or an unexecuted application case. These selections overlap the earlier results and are not summed.

Combined CP-01/CP-02 trial `50c82beefb23962107faab96ba1656cd622f4424`, tree `8ec10cbfab5ef63323cf0f910ad4f63546097777`: **147 focused checks passed**, and both native Chromium workflows passed (closed checked outcomes plus keyboard/touch/reduced-motion companion state). The handoff insertion and dock expectations require normal conflict reconciliation when the two PRs meet; the trial preserved CP-01's media-off expectations and CP-02's canonical-answer expectations. Neither feature branch was rewritten. Combined counts overlap other selections and are not summed. Artifacts: `/tmp/checked-browser-QE4wcB`, `/tmp/companion-browser-zXx871`.

PR #241's first published head `72e13b3e03c16736bd42ab60fdd2a1e91a121d4e` finished all applicable checks successfully or with expected skips. Fresh premerge then identified a code overlap with master's external-agent strip in `ChatPanel.tsx`. After every prior-head run finished, master was integrated through an ordinary merge; its strip import and placement are preserved. Replacement-head remote CI and fresh premerge must independently pass; prior-head CI cannot certify this replacement. Exact remote evidence is retained in the PR description and `/workspace/scratch/github-actions/chat-companion-cp02.json`. Recheck actual master before reporting green or any later merge.

## Next scope

CP-03: one source-bound walkthrough controller with displayed-data acknowledgements, historical source refusal and shared current load-flow UI/API/chat agreement. Activity/native job recovery remains CP-04; real speech and background responsibilities remain later slices.
