# U3 — the guided face in Guided mode (progress record)

Parent: `2026-10-05-one-investment-engine-two-faces.md`, §5 U3 row, §10 S2, §11 "Guided shell" (M7, M8,
the question-first rail). Session: GS (U3), started 2026-10-13 from master `8776f4a6`.

Owner decision on provenance (2026-10-13): S1a owns `n.meta["asset_params"]` and its single write helper.
S2 reads `services/asset_schema/provenance.get` and the descriptors in `services/asset_schema/fields.py`;
study code that writes pack defaults calls `provenance.record(..., status="default", source=<pack id/hash>)`.
No second store, no direct writes to `n.meta["asset_params"]`.

## Slices (one PR each)

| # | Slice | Needs | Status |
|---|---|---|---|
| A | Drop `PYPSAGUI_DECISION_STUDIES`; the auth-mode refusal stays, reworded (it no longer cites OPEN-ITEMS 1) | — | PR #183 |
| — | W4 refusal fix (mark post-U2 networks, refuse only unmarked ones, the user-typed price test) and the gate's four U3 one-line records | — | already on master (`96b7b6ef`); re-run green on `8776f4a6` |
| B | Decision-study entry in Guided's Start card: "Is my site reliable?" beside "Is this investment worth it?" (owner decision 5) | A | branch `claude/u3-start-card` |
| C | The six study chat tools with Guided confirmation tiers and `delegate()` steps | B | branch `claude/u3-chat-tools` |
| D | The key-parameter list view | C | branch `claude/u3-key-params` |
| E | M7 (Guided shell) and M8 (Guided without an API key) | — | |
| F | "Open in Expert" lands on the study's project (today's cards; switch to the S1b cards when they merge) | S1b for the final form | |
| H | Open the study routes in multi-user mode, with a cross-tenant test of every study route, the study-created base project and its forks (owner, 2026-10-13: a later slice) | — | later |
| G | S2: ledger rows as a view of the provenance store; pack defaults written through `provenance.record`; Expert edits mark ledger rows | S1a | waits for S1a |

## A — the flag

The flag ENABLED the study routes in local mode only; auth mode refused regardless "until OPEN-ITEMS 1 is
closed" (review v2 BC-6). #71 closed OPEN-ITEMS 1, so the flag goes and the desktop app always serves the
routes. Auth mode still refuses: the study runner, its study-owned base projects and forks were never
reviewed for several tenants on one server, and that review is not OPEN-ITEMS 1's. The message says so
instead of citing the closed item. (Owner, 2026-10-13, option 1: keep the refusal, reworded.) `decision_studies_disabled` is gone from the backend, the client and the copy.

## B — the Start card

`pages/decision/useDecisionEntry.ts` holds the questions (`GUIDED_QUESTIONS`, today only `bess_at_site`),
the availability probe, the reason a card is disabled and what "Start" does; the New-project wizard's
Decision study tab and Guided's Start card both use it. The Start card shows "Is my site reliable?" (the
energy-hub examples, "Use my network") above "Is this investment worth it?" (the questions).

## C — the chat tools

| Tool | Route(s) | Tier | Guided card | Expert card |
|---|---|---|---|---|
| `start_decision_study` | `POST /studies/` | write | yes | no |
| `set_study_parameter` | `PUT /studies/{id}/ledger` (`apply_user_row`, unit required) | write | yes | no |
| `run_decision_study` (`stage` options / tornado) | `POST .../run`, `POST .../findings/tornado` | execution_long_running | yes | yes |
| `get_study_findings` | `GET .../run`, `.../findings/tornado`, `.../findings` | read | no | no |
| `explain_verdict` | `GET .../findings` | read | no | no |
| `generate_study_report` (`format` docx / xlsx / html) | `POST .../report`, `GET .../report.<fmt>` | write | yes | no |

- Every tool takes `project`: the study's own base project, which `start_decision_study` returns. For
  `start_decision_study` it is the project open now, which only authorises the call.
- The tools call the route handlers in process through `chat_tools._route`, so the routes' refusals pass
  through typed. That includes `decision_studies_unavailable` in multi-user mode.
- The two new kinds, `study_stage_invalid` and `study_report_format_invalid`, are `inline` in
  `tool-error-kinds.json`.
- `services/study/chat_view.py` cuts the payloads under the 4,000-character result cap. It renders the
  verdict sentence with its figures and turns every code into the report's sentence (`report.help_for`).
  A missing figure reads "not established".
- `start_decision_study` returns the key parameters (`sensitivity_flag` rows) with their defaults and
  sources.
- `get_study_findings` reports progress while the options or the tornado run.
- The `investment-decision` workflow is now `active`, with steps for the real tool shapes. The planned-workflow
  rule keeps its tests on a patched registry.
- The decision panel's ledger, run, verdict and report steps carry "Let the assistant do this" and "Ask
  about this" (`StudyAssist.tsx`, through the hub-design `DelegateButton` and `AskButton`). The copy lives
  in `decisionVocabulary.ts`, and a test checks that every tool the sent text names is in the catalogue.

## D — the key-parameter list

The assumptions step (`LedgerReview`) is the key-parameter list of owner decision 4:
1. The site's answers: price zone, grid connection, consumption, tariff and PV.
2. The key drivers (`sensitivity_flag` rows).
3. Any other row the user changed, or that needs a look.
4. Everything else, collapsed as "N more, from the generic defaults (<ledger version>)".

The labels live in `KEY_PARAMETER_LABELS`. S2 will later make these rows a view of the provenance store;
the list does not change shape for that.
