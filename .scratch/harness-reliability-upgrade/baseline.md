<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Baseline, shipped capabilities and ownership

Planning baseline: actual fetched master60d8332d64b2edef1b204fb5980311e6102229a8. Original research pinned06df80d420677f06f63b9a71b13d028227bc03bd; initial foundations drafted002a3fe212b0f11e07a726c72a44177ff919130f. The research notes retain their original dates/SHAs. New specs use60d8332d6; implementation must compare fresh master and reviewed files before work.

## Delta inspected on resume

Since002a3fe21, master changed native evidence/assurance read and compare integrity, workbook export/picker/chat tool, formula-injection escaping, per-turn check marks, family/owner journeys and UI wording. These are shipped code, not proposed new features. Do not add another workbook exporter or treat an old unsafe-export finding as still unpatched. These changes do not establish a full generic artifact revision controller, native L2 agent loop or generic child-worker factory.

## Existing seams

- Provider-neutral protocol/catalogue/shared dispatch: harness/protocol.py, catalogue.py, loop.py and providers/. Tools are finite JSON schemas; SDK names stay inside providers.
- Native mutable context: services/project_context.py, dirty_state.py, workflow_tools._network_input_fingerprint(exact=True), pypsa_service.py. Revision and fingerprint are different observations; HH-01adds residency identity.
- Existing cards: harness/edit_card.py and edit_card_records.py; session/confirm/approvals/restrictions. Chat input edits always have old→new cards in both Expert/Guided; other writes follow declared mode policy.
- Durable parents: services/durable_conversation.py and routers/durable_conversation.py; generation/CAS and old constraints/refs/budgets exist. Typed requirement authority and strict provider reservation integration are proposed deltas, not absence of storage.
- Study execution: harness/runner.py; services/run_profiles.py, run_gates.py, run_envelope.py, run_monitor.py, assistant_tasks.py. Current profile/adapter support is limited; generic stages skip explore/report. Existing study-harness04/05/09are the home of correction/research work.
- Native queue: services/solve_queue.py plus its store/job paths. Legacy best-effort queue writes/resident context access are not immutable input proof; HH-10defines a distinct strict admission path while preserving ordinary compatibility.
- Evidence/effects: run_evidence.py, assurance_records.py, harness/effects.py and bounded experiment receipts/reconciliation. Authentic record integrity is not physical correctness; uncertain effects never retry automatically.
- Reports: services/reports/{generator,store,report_job,roundtrip_service,docx_reader,docx_writer,number_audit,evidence}.py and routers/reports.py (verify each allowlist against source). Existing versions/section regeneration/roundtrip/rendering are reused.
- Workbook: services/results_workbook/ and routers/io.py, export_results_workbook native chat tool and ResultsWorkbookPicker. Shipped formatting/charts/export are not a general arbitrary Excel editor or recalculation engine.
- Evaluation: harness/replay.py, eventlog/history/pilot_evaluation.py and tests/test_workflow_provider_parity.py. Replay forks history, not a complete native world checkpoint. Finite suite is not production calibration.

## Existing parallel owners

K#851owns native services/results/plausibility.py, not checked publication. At this baseline that proposed module is absent; HH-06 cannot silently implement a duplicate. The exact checked finding adapter remains Assurance issue10 with its own admission/MAC/rederive/extra-claim gates. Native study engines own their algorithms/physical margins. New plan acceptance contracts live in HH-09, while HH-02enforces a small input-predicate set; neither replaces the Assurance publication schema.

G-b#853and outsourced register entries have their existing reviewed scopes. This package does not modify them. Old Status/progress prose can lag source; future builders read current owner decisions, actual paths and open PR metadata before assigning changes.

## Validated boundaries

No generic runtime worker factory was found in the current harness; current reviewer is a bounded no-tool call, and the server runner is a native task/stage controller. No existing report/Excel export implies numerical auditing of arbitrary user prose, cell formulas, external links or macros. No current profile can claim every template/module runs unattended. Named first-slice adapters and unsupported prerequisites are required in the detailed tickets.

This is a source/spec audit. Application tests, live model calls and domain reviews were not executed by the plan-writing task.

## Additional source delta: 30 October 2026

Fresh fetched master: `30c79cf750c42314eedd0597d06b1780b92a81fe` (after pinned planning base). Source diff inspected, no merge/rewrite of this specification branch’s native application tree:

- `models/design_intent.py` and `services/design_intent.py` implement asset-sizing intents/presets (AS-01). They represent physical sizing preferences, not user-request/deliverable acceptance. HH-09 references their exact values/version when selected rather than copying their semantics into another sizing model. Future AS-02..10 engine loops retain their native ownership; no generic outer correction loop may silently wrap them.
- `harness/loop.py` and its README now keep `_assurance` first in checked model projections, then `_edit_receipt`. HH-01/05 must preserve both; no receipt-first refactor may displace that checked guard.
- `services/assurance_records.py` now admits bounded hourly-year input series with row masks/index/value digests (`series-input/2`). Existing native readers reconstruct/revalidate facts. HH-04 references those native evidence APIs and cannot assume raw inline stored series facts or use stale `/1` shape.
- `services/chat_tools.py:export_to_excel` now uses the same `_escape_formula` as native I/O exports. HH-08 reuses that hardening; it must not restore earlier unsafe text/formula behavior.
- Adequacy campaign/margin/frontier paths and LC journey coverage now explicitly reject stale/incomplete/swap scope. HH-06 future reliability adapters consume these guards rather than bypassing them; the initial release tests their unsupported unattended routing, not positive new reliability loops.
- Owner decisions AS-BUILD-1/AS-AUTONOMY-1/AS-NOUPS-1 specify native asset-sizing work and defaults. These specs add no alternative autonomy default or sizing loop.

Reassessment outcome: these changes do not remove the ten proposed harness deltas, but narrow their integration contracts as listed. No application tests on the combined tree were run or claimed. Each implementation rechecks actual then-current master and current official PRs; a source-delta assessment is not runtime compatibility evidence.
