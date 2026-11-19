# Slice 02a: GK B plan GO and carried notes

These sections move into issues/02-why-this-number.md and issues/04-figure-names-parity.md once #1611 merges (those files land with it).


### Slice 02a plan GO, GK B (63), 2026-11-17

The plan: a provenance-graph target for the EH study's figures (`expected_shortfall`, with `lole_ci` as a step; `design_cost`; `verdict`). Producer `services/provenance_graph/eh_study.py`, `explain_study_figure` in `explain.py`, route `GET /api/results/eh_study/why/{figure}`. No chat tool in this slice: a KNOWN_GAPS / `_pending` row names the slice that adds it. 02b is the Guided disclosure; 02c is the same component on Expert's Results headline, bringing Expert up to Guided (EXPERT-GUIDED-1, walkthrough finding 01/J6).

Conditions:
- The goal pin compares the value, the basis (per year vs per horizon) and the unit exactly, each with its own killing case (#1257 F1a).
- W1. Each figure lists all its inputs. The revision counter does not cover PUT /finance, so anything design_cost reads outside the network (the pack or its prices, finance or currency settings) gets its own pin or reads uncompared. It is never fresh on the revision alone. The PR body gives the input list per figure.
- W2. The revision and the network/record are read under one lock (#1384 N1). The lock is a bounded try: a busy lock reads "network busy", uncompared and never stale, and never blocks on the mutation lock (PG-09 Y1, #1464 F2). Test a busy lock.
- W3. Pins compare exactly (#1386 A3), with a test at a value that is equal only after rounding.
- W4. One reader normalises the record. A wrong version or shape, a non-dict, or a field of the wrong type reads uncompared or unreadable, never a 500 or a silent default (#1255, #1390 F2). Test each case.
- W5. The read is project-scoped: project_id equals the active project, with 404/409 parity with the existing results reads, and nothing is written (solver_state included). The refusal kinds (`study_not_run`, the TargetUnknown kind) go in a `tool-error-kinds.d/` fragment. At the trial merge, run route_chat_disposition, ACL, write-surface, golden_coverage, route_parity/inventory/control_matrix --check and the error-kind manifest.
- W6. The outcome block comes first, and complete/truncated follow the existing explain rule. A walk that compared nothing never reads fresh (#1170). Test it.
- W7. Hand-mutate each pin compare and the None → uncompared mapping, and run mutants.py. List the survivors in the PR body.

Assumptions rows (template-e2e 08, session_01G7qCuo15kNsNqjAaBLjvmL): the eh_study record gains `assumptions: [{key, label, value, shown, source, reason, section, card, component, entity, field}]`. `section` is the flow section id; map it with `SECTION_STEP` in `hubDesignStore.ts`. An older record has no key; `[]` means none assumed.

## For issue 04

### LCOE after an edit (UX 24, #1611, 2026-11-17)

After an edit, `/results/headline_kpis` answers 204 whenever `_dispatch_ready(network)` is false (`routers/results.py:3313`), so Guided and Expert show "Not run yet · To get it, solve…" for a project that was solved but is now out of date. The server's 204 should say which case it is (never solved, or solved but out of date) so that LCOE can read "Out of date" in that case. This belongs to this issue's LCOE work, together with the money year.
