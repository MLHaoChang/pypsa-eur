# "Why this number" and the Results assumptions group

Status: ready-for-agent
Type: task
Spec: `.scratch/guided-results/spec.md` §1.4, §2 (Results group)
Blocked by: 01

Gated: GK B (62), UX (23). GK B conditions G2 and G3 in `01-headline-card.md` apply.

## Comments

### Inputs for the plan (2026-11-17)

- UX (23) condition 3: Expert never shows less than Guided (#1193). The plan says how Expert gets "Why this number":
  the same disclosure on Expert's HeadlineStrip, or a logged slice.
- UX (23) condition 4: the walk passes the plain-words test (no ledger keys, route names, engine ids; RAW-NAMES-1);
  "Assumed" is the word for unconfirmed defaults. Prose uses the tiles' notation (`kpiMoney`), not `money()`.
- Keyed walkthrough audit (J6, `origin/claude/ux-walkthrough-2026-11:.scratch/ux-walkthrough-2026-11/README.md`):
  for the same project Expert Results reads "not run yet" while Guided reads "Certified". Expert's Summary has no
  study verdict or expected-shortfall figure (`SummaryTab.tsx:262` only links to Adequacy). The parity answer for
  condition 3 should cover the verdict and the reliability figure too, not only "why".

### Owner decision WHY-FULL-1 (2026-11-17, via the coordinator)

Option 2, the full walk first: add a provenance-graph target for the Guided study's figures (backend). Send the plan to
GK B (63) before any code, and build it TDD. Then the disclosure on top: each input's source and whether it is current,
the run and its settings, and the unconfirmed Assumed defaults with Edit. The same component goes on Expert's Results
headline. Slice 03 may run in parallel where it does not overlap.

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

### Slice 02b plan verdicts (2026-11-17)

The plan: two PRs. 02b-1 (backend, GK B) fixes the two latent items from the 02a review in
`services/provenance_graph/eh_study.py`: a record with no seed drops its node, and the current draws and seed ignore
`req.mc`. 02b-2 (frontend, GK B + UX) is the "Why this number" disclosure (`cards/WhyThisNumber.tsx`), placed under the
expected shortfall and design cost tiles and under the verdict. 02c is its own issue (`05-expert-results-parity.md`).
02b-1 waits for #1675; 02b-2 waits for #1675 too, and goes on top of control-strategy PR C if that lands first.

GK B (66) GO, with these conditions:
- W1. The current draws and seed come down the runner's path: the effective request's mc (parsed with
  `McOptions.model_validate`), else the pack. The separate goal-or-pack branch for draws goes (the goal is already in
  `req.mc` through `eh_request_with_goal`). Tests, red first: an override equal to the record reads current; one that
  differs reads stale; a goal mc_draws with no request mc reads the goal; with neither, the pack. The same for the seed,
  apart from the goal case.
- W2. An mc the runner would refuse (not a dict, draws over MAX_DRAWS, McOptions failing) makes the current draws, seed
  and cov_target an error, which reads uncompared. It never falls back to the pack. Test each refusal.
- W3. A record with `seed: None` or no seed key keeps the mc/seed node as "not recorded", uncompared and never fresh,
  and the walk's outcome then does not read current. Test both.
- W4. A record the real writer produced (always an int seed) still reads fresh when nothing changed.
- W5. Run mutants.py on eh_study.py and list every survivor with its reason; kill any on the req.mc-then-pack order or
  the has_seed path. Run the semantic tests and the provenance/eh_study tests at a trial merge.
- W6. The state mapping never shows uncompared or "not recorded" as current. Name the test that kills the hand mutant
  (uncompared → current). The run settings show recorded vs current per field, "not recorded" included.
- W7. The fixture is captured from the real 02a route at the merged head, with a note on how. study_not_run, 409 and
  any other failed read each get their own words and test.
- W8. No new route or backend change in 02b-2. A new error kind gets its own `tool-error-kinds.d` fragment, with
  ChatPanel.manifest.test.tsx at a trial merge. Run tsc and the vitests for cards/, ResultsHeadline and ResultsCard.
- W9. Edit goes to `SECTION_STEP[section]` as a user move; test the mapped section and the fallback.

UX 2 GO, with these changes:
1. Assumed rows use IB-C's shared `AssumedChip` (#1687), with the source as visible text.
2. "Edit ›" (U+203A), one link per input row. An input only Expert edits reads "Edit in Expert ›" through `withReturn`.
   In Guided: `openFlowTargetInPlace`, then request-then-scroll to the input's own card. The Back pill names Results.
3. "Out of date" (ResultState's word) names what changed. "Not compared" is a new state: one visible line,
   "Not compared: <inputs> could not be checked", never styled as current.
4. Run settings in plain words ("Random seed", "Network version", "Draws", ISO dates), no ids. Recorded vs current as
   two labelled values, not colour alone.
5. `kpiMoney` writes the same strings as `money()` ("€ 3.5 m"); one test asserts it.
6. A button with `aria-expanded`, fetched on open, with the 200 ms skeleton in a `role="status"` `aria-busy` region of
   the same size. A failed read has words and Retry.
7. EXPERT-GUIDED-1: Guided-only is acceptable for 02b only while 02c is a filed issue named in the PR body; if 02c slips
   past this wave, tell the coordinator.
8. Shots: DC and microgrid, dark and light, the disclosure open, an assumed row; each of current, out of date, not
   compared, not run and failed read.

File boundaries with control-strategy PR C (session_01G7qCujvtPjkFYUMUxCAbyb), agreed: 02b adds
`cards/WhyThisNumber.tsx`, `getEhStudyWhy` in `api/results.ts`, and one mount line each inside the tile blocks of
`ResultsHeadline.tsx` and under `hub-results-verdict` in `ResultsCard.tsx`. PR C owns the source-run line, the Guided
headline after an Expert solve, ControlChapter, ChaptersSection, useDispatchRun and SummaryTab.
