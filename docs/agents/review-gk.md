# Gatekeeper: standing rules

What the engine gatekeeper applies to every review, so a successor reads this file instead of a hand-off note's rule list. To add a rule, send it to the coordinator, which lands it here in the next docs PR.

Post each verdict as a PR comment, with the attribution footer: `GK OK @<full sha>: <what was checked>` or `GK NOT OK @<sha>: <what to fix>` (owner decision VERDICT-PR). Plan verdicts go straight to the builder (its session id is in the plan message), because there is no PR yet. The verdict must be the first line of the comment; the attribution footer goes last. The PR comment is the verdict. Also `send_message` each verdict (plan GO, NOT OK with its asks, OK) to the builder named on the PR body's `Session: <id>` line; a missing line goes to the coordinator. The builder sends plans and re-review requests to you directly. Message the coordinator only for a NO-GO or NOT OK the builder disputes and you hold, your hand-off at 300k, a standing ruling to record, or a PERMISSION request; never an informational OK, since premerge reads verdicts from the PR. A clean master merge (its tree equals `git merge-tree --write-tree <old-head> <master>`) carries the OK: reviewers skip it, and builders don't ask for it.

## Review setup

- Read a PR with `git diff --stat` first, then one hunk or file at a time, each capped with `| head -c 6000`. Whole-file or cross-file reads go to an Explore subagent that returns file:line findings only.
- Fetch `refs/pull/N/head:refs/gk/N`, `git worktree add --detach` under the scratchpad, and remove the worktree afterwards.
- Backend tests: `PYTHONPATH=<wt>:<wt>/pypsa-gui/backend /root/.venv-pypsa-gui/bin/python -m pytest -p no:warnings -q -rfE <files>`, no xdist. Check the exit code. Keep each run under 590 s and split files (`test_pilot_evaluation` and broad finance sweeps time out).
- Frontend: symlink `node_modules` from `/home/user/pypsa-eur/pypsa-gui/frontend`, put `/home/user/pypsa-eur/.pixi/envs/default/bin` on `PATH`, then `npx vitest run <paths>` and `npx tsc --noEmit -p .`.

## Delta reviews

- List the files both sides changed since the old merge-base (`comm -12`) and test those. For hand resolutions, read `git diff <merge-tree> <head>`.
- Error kinds: a PR adds new kinds only in its own `pypsa-gui/tool-error-kinds.d/<name>.json` fragment, and edits the base `tool-error-kinds.json` only to change an existing kind. Two fragments never conflict as files, so a kind defined twice is caught only by `smoke/json_fragments.py`, which refuses it. Run `tests/test_tool_error_kind_manifest.py` and `ChatPanel.manifest.test.tsx` at the trial merge, not only on the PR head.
- Inventory, route parity, control matrix: their `--check`s build in memory and nothing generated is committed, so there is nothing to regenerate or hand-merge. At the trial merge, run `smoke/assistant_inventory.py --check`, plus `assistant_route_parity.py --check` and `assistant_control_matrix.py --check` when routes, chat tools or the frontend changed. A `stale (sweep prunes)` line is fine; a `FAIL` line is not.

- Never run two mutant batches in one worktree at once: the edits cross-contaminate (#1246).
- Run root `tests/` and `pypsa-gui/backend` tests in separate pytest invocations: mixing them fails on the `tests._shard` plugin.
- A PR that adds an import of gridspine or another third-party module to `pypsa-gui/backend`: run `tests/test_packaging_requirements.py` at the trial merge (the frozen-app pin test; #707 missed it).

- A PR that adds a route: run `test_route_chat_disposition`, `test_project_route_acl_policy` and `test_write_surface_lock_policy` at the trial merge (#725 and #741 both missed it).

- A PR that touches project templates: check native byte-identity. Load `eh_templates` by path, `write_sidecars` into a temp dir per `TEMPLATE_META` id, and `filecmp` each file against `project_templates/<id>/`.

- A plan or PR that hooks "user writes to `_user_ts`": list the writers with `git grep -nE "user_ts\[[^]]*\]\s*=[^=]"` and check each is hooked.

- A plan that adds fields to `SolverConfig`: check whether they feed `workflow_tools._network_input_fingerprint` and the investment-case digest. Inert fields (goal settings that change no LP input) must be excluded, with a test that changing them leaves both unchanged.

- A PR that widens which fields a bulk or preview path can write: check that it runs the same validators as the single-edit path (`update_component`'s `_check_investment_parts` was the #738 case).

- Byte-identity against master's tests: copy master's test file into the PR worktree and run it there (a recording pytest plugin can capture rows). Running it from the master worktree imports master's code even with PYTHONPATH on the PR, because the rootdir wins.
- A merge commit M has no hand resolution when `git merge-tree --write-tree M^1 M^2` equals `M^{tree}`.

## Known container-only failures

`test_harness_provider_parity`, `test_workflow_provider_parity[*-anthropic]`, `test_profile_gen_atlite_source` (atlite is not installed), tests that need `tsam` (not installed) and `test_openpyxl_parses_uploads_with_defusedxml_in_this_environment`.

## Test quality

Probe tests that monkeypatch `_probe` can hide dispatcher-signature bugs: ask for a test against the real signature.

When a PR changes a validation guard, confirm by mutation that its test fails for the guard, not for a downstream check (#807's decoy case); the same for any test covering a guard: mutate that guard, not a neighbour (#807, #1091).

`test_json_fragments`' real-file assertion must not depend on any declaration list being non-empty.

A test never asserts a value a known defect produces as if it were correct. Assert the parts that are right, leave the wrong field to a `_pending` manifest row that names the ticket, and keep any identity that holds before and after the fix. No xfail, no skip, and no fixture chosen to hide the defect (#890, ticket 30).

A stacked or full-file mutant run can hang when the mutant lets a real solve or restudy run. Run the single targeted test with -x and a timeout.

You may start from the PR body's survivor list and `scripts/coord/mutants.py <src> <test>`. It does not replace mutating the guards the PR changes and checking which test kills each (it misses multi-line statements, decorators and non-`.py` guards).

A family-manifest PR flips a variant to `_covered` only for what its tests check. Each untested or defective annex item gets its own `_pending` row, with a ticket when it is a defect: a family whose only variant is covered reads as `evidenced` (#918, #920).

A `pytest.raises(Exception)` on a refusal is checked for what actually raises. An incidental library error (#918's pandas `InvalidIndexError`, a 500) is a defect, not a guard.

A plan keyed by a version that readers check for equality needs a test that every reader accepts each contract version (#1132 F1).

An autouse conftest fixture patches through its own `pytest.MonkeyPatch.context()`, never the test's shared `monkeypatch` (#1132).

A redundant guard layer is mutated together with its sibling when the sibling also enforces the rule; a mutant of one layer alone survives and proves nothing (#1127 M10).

A read-back that compares frames after an IO round trip normalises dtype, index names and NaN/None. It is tested on an edited network and an in-memory one, never only on a fixture loaded by the same reader (#1175 E1).

A guard whose failure is also caught downstream with a different reason still needs its own test asserting its own reason (#1225 P7).

## Rulings

- `profile_location_missing` skips uploaded and network_native profiles (`readiness.profile_kind`).
- A project-scoped result-view read carries `project_id` = the active project, and 404/409 count as `no_data`.
- A run profile whose approval envelope lists `run_sensitivity_sweep` or `run_eh_study` needs its own GK review. A sweep's cases[].changes create child projects with arbitrary edits that no edit bound limits; admit such a run only with per-case bounds or a card per case. An envelope admission must also refuse any `*project*` argument that is not the run's own project (#875).
- Builders record a GK plan GO's conditions in the issue file's `## Comments`, so a rotation does not drop them.
- A plan GO for work over one reviewer area or about 1,500 changed lines (excluding generated files, shots and fixtures) names the slices, one PR each (PR-SIZE-1).
- A PR that changes a stored format (e.g. the run manifest MAC): existing readers see byte-equal output (strip the new field on read), and a pin test shows the other MAC domains are unchanged.
- A "covered" manifest row that says something is refused needs a test of each tool it names (#922 claimed "unknown asset moves nothing", and two tools emitted frames).
- Measure a cap raise downstream: #927 found `_assurance` cut from long checked results; it now goes first, like `_edit_receipt`.
- A chat tool that wraps a route handler through `_route` and turns a non-error status (204) into a refusal needs a test of that refusal: #939's mutant dropping the 204 guard saved a 0-byte .xlsx and passed every test.
- A PR that adds custom (non-PyPSA) component columns the LP reads adds them to `_network_input_fingerprint`, appended only when present so existing fingerprints stay byte-identical, with a pin test (AS-02).
- A new defaults-pack version becomes `load_defaults_pack()`'s default for every unpinned caller: the PR lists those callers and tests that the previous version's rows are byte-equal (AS-02).
- An exception subclass that narrows a refusal kind (#971's ProjectDeleted under AccessRevoked) is checked against every caller of the raising function: only the intended handler tells the two apart, so no existence oracle opens.
- A runner that solves a private network copy binds `PyPSAService.solving_context(<private ProjectContext>)` around the transforms, the solve and the restore; otherwise `mark_transient` and `clear_transient` hit the project ctx (#992).
- A test that skips without tsam uses `pytest.importorskip("tsam")`; cloud containers have no tsam.
- A browser journey that flips a manifest row to covered waits on a locator specific to each screen effect it claims, not on text that may already be visible (#998).
- A route that sizes or solves a named project's network reads its solver config through `routers/projects.py::_solver_config_from_dict` (pack-only import-cap strip, lpf coercion, invalid-goal drop) and never forks it. An unbuildable saved config is a 422, never a silent default (#1009).
- An X/R-dependent verdict never passes on an assumed X/R alone; while X/R is assumed, a pass also needs the X/R-free bound (AS11B-RVC-1).
- Voltage-form direction depends on the P/Q mix: hand-check with both P and Q > 0, not only P.
- A typed refusal needs a fail-closed test at the catching site, with a base-class mutant that the test kills.
- An inflow-fed unit keeps its charge side at 1.0 even with a pump.
- The split-storage reverse mapping is exact only for the migrate layout; a hand-built or add_split_storage layout is refused, not approximated (SPLIT-COST-1).
- A gate that infers deployment shape from argv or env fails closed behind a process-exclusive lock: `python -m uvicorn`, `gunicorn -c` and `GUNICORN_CMD_ARGS` are invisible to argv (#1051).
- A deadline exit (timeout, grace period) settles with its own timer and a boot sweep, never only on the next touch, and the boot sweep honours the replica pin and lease (#1058).
- A capture that reads a shared live slot (`_state[key]`) checks the identity of the record it publishes, under the slot's lock (#1038).
- "Fail closed" names a cross-process mechanism (`durable.file_lock`), never an in-process `threading` lock (#1070).
- A requirement or bound check counts "cannot read" and an incomplete or truncated card as a violation, never as no constraint (#1079).
- A fill or admission whose value could come from the model is bound to a source fixed at the grant; a set of several candidates (tariffs) is the user's choice, on a card (#1072).
- A callback under a process-global store lock never takes a project lock; the order is mutation, then solver_state, then store, with a two-thread timed test shown red (#1079).
- A capture verifies that unlisted inputs are ABSENT, not only that listed ones match; a "not applied" flag counts only when it is exactly True (#1083).
- A compare-then-write against files whose writers share no lock re-checks after the write and settles not-applied on change (#1083).
- A cross-process ledger write also checks lease ownership; a file lock alone does not stop a stale process whose peer saves without it (#1088).
- A `Thread.start` check does not pin "before the registry insert": pin the publish point itself, by wrapping `solver_state` and recording at the insert (#1077, #1085).
- A unit-precedence rule (explicit unit vs catalogue) needs a case where the two disagree, e.g. a tonne-catalogue carrier with an explicit MW (#1057).
- The frontend types a server field from the server's own return shape; a fixture of the wrong type can pin a defect (#1060 `unavailable_member`).
- An engine loop's solve count is `solves_used` + `probe_solves` + any restore-final re-solve. The h/yr LOLE goal goes through `reliability_goal.loop_goal` with `metrics.horizon_years(n)`, never `eh_study`'s nyears (SH-05).
- A combined pytest run of the tornado route suites takes ~630 s: run them per file.
- A registry that changes what an unlocked reader sees registers only when the caller owns the mutation lock, counted re-entrantly by depth (#1133).
- A name derived from a user-capped name is never truncated: cap the input, or refuse the collision (#1127).
- Provider stop reasons map to "truncated" through an allow-list, so an unknown reason reads truncated. An echo bound keeps a spoken answer from its start, and a verified-outcome block goes first in a tool result (GK B (25)).
- A revalidation or outcome stamp that compared nothing never reads current or completed; it gets its own state (#1170, unanchored).
- A check compares only the fields its independent read supplies, and names the rest as uncompared (#1170, revision).
- A "done" count includes only rows the call changed, never rows already present or left as they were (#1175 E2, the S2 W1 analogue).
- A reader of the island sidecar states whether it takes the raw or the validated config, and its caller passes that one (#1172).
- An adequacy proof and its cost leg describe the same fleet. An extendable candidate with no size reads `not_shown`, never `fail` (#1172).

## Engineering rules

- Grid source impedance derived from an entered S_k'' carries the IEC 60909 voltage factor c (Z = c·Un²/S_k''; S_sc2 = S_k''/c), in every screen: unbalance, the flicker note, the impedance scan.
- TDD and other ratio screens (Isc/IL) use the entered S_k'' without the c factor; `td.STRICTER` is only for a band computed from S_sc,min.
- A shipped demo limit that is an IEC planning level says so in its clause: "planning level, not an emission limit allocated to this installation".
- A new scorecard input read from a component is registered as an editable field end to end (FieldSpec, model, schemas, attrs_doc, fieldLabels, fixture) in the same PR.
- E1 growth placeholders: setting `nh3_delivery_tpd` converts t/d to MWh_NH3/h (× 5.166/24), with a test.
- A handler that `pd.concat()`s user-uploaded series survives members that cannot be aligned (a tz-aware index next to a naive one, a repeated timestamp) and reports a gap, never a 500 (#868). Test both.
- A smoothing tier's latency has one source, `spectrum.store_latency`: a given override, then the store's `control` column, then the technology default. AS-07's sheet and AS-09's tier split both read it (AS-15).
- A trace-sourced latency band uses max(1/T, f_c), never f_c alone: a trace's f_c can fall below 1/T and loosen the check (#988).
- A solve-like runner that bypasses `run_simulation` solves the project's own LP through the shared wrap-chain helper (`solver_service.build_lp_chain`, from AS-06 #992), and names per setting any pre-solve step it does not reproduce (island apply, commercial PoC writer, user code); a blanket note is not enough.
- A runner that defers transform restores restores per round; it never holds every solved copy at once (#992).
- A guard that refuses on "not a DatetimeIndex" is probed with an integer-index (RangeIndex) upload; RangeIndex-snapshot networks are valid (#997).
- Every solver-config write (routes, loop holds) bumps the network revision; a loop's own hold re-captures its record's revision, and the hold path has its own test (#1001, #1008).
- `goal_free_config` hashes finance and commercial: they belong in `_network_input_fingerprint` (#1001).
- A storage availability rule treats inflow-fed hydro separately: an inflow-fed StorageUnit keeps dispatch-only availability and is named in `storage_notes` (#1010).
- A PR that rewords an exception message runs `git grep "<old text>"` for substring matches on it; a change to `adequacy/levers.py` also runs the EH suites (#1010).
- A split-storage reader handles a split whose charger and discharger sit on different buses; `storage_inventory.results_splits` keeps such splits out (#1012).
- A report job is tied to a harness run (strict mode, run_binding) only by the harness's own explicit launch. The route and the chat tool never infer a run from state, so a human request is never strict and never charged to a run's budget, even while a run sits at its report stage (SH-07).
- Pin the publish point, not `Thread.start` (#1077, #1085).
- A unit-precedence rule needs a case where the units disagree (#1057).
- Frontend types follow the server's return shape (#1060).
- An engine loop reports its solve count; LOLE goes through `loop_goal(horizon_years(n))`. Run the tornado suites per file.
- An engine loop's solves = solves_used + probe_solves + the closing re-solve whenever it ran, on every path (`_restore_closing` runs on all of them, not only restore "final") (#1123 Q1).
- A `runs` entry that lists a tool for the harness's own step must not admit the same tool on a model turn; probe `_refusal` with the grant (#1123: run_margin_loop admitted, its solves uncounted).
- A record identity check (started_at) needs a test with the activity bindings cleared (after takeover); otherwise the binding's origin check masks the mutant (#1123).
- A flag borrowed to reach an existing code path (eh_process on a gas boiler) also enrols the component in every other reader of that flag; list the readers before accepting it (#1142).
- For a wording-only or ruff-only delta, compare ASTs with strings blanked rather than re-running mutants (#1142).
- A multi-term credit cap needs a test per term: each term alone binds the cap while the others do not.
- An energy or tonne sum needs a test with non-unit `snapshot_weightings`; unit weights hide a missing multiplication.
- `flag_column` agrees with `process_chain._truthy`: "true", "1" and "1.0" are truthy, each string pinned by its own test.
- Removing a duplicated caveat needs a positive test that the caveat now appears exactly once, not only that the copy is gone.
- A transient LP component removed after the solve: check the post-solve readers (energy flows, load shape, CFE) for served-load accounting, and require its series persisted with axis_hash (#1225).
- A solve record in n.meta is cleared at solve start, whatever the strategy, so a failed run never keeps the previous record (#1225, DC-07 S3 C25).
- A floating-point total that a PR re-expresses through new sub-totals needs a committed test with order-sensitive values (e.g. [1e16, 1.0] + [1.0, 1.0]); an ad-hoc compare outside the suite does not count (#1249 M5).
- A message that states a numeric limit the user can enter rounds toward the feasible side, and each case has a test that builds at the stated value (#1252).
- A lookback or window search over weighted snapshots is mutated with a fractional duration; a whole-hour duration on unit weights hides boundary bugs (#1243 Mback).
- A reader of a record persisted in n.meta checks its version and shape and reads a mismatch as stale, never a KeyError 500 (#1255).
- A KPI that could not be evaluated is never a user-accepted pass. A capture is evidence, never an admission (SH-07 B3b).
- An absent intake digest refuses; it never reads as "no drift" (SH-07 B3b).
- A goal from template data counts only when the card shows its value, unit and source. A code default never counts (SH-07 B3b).
- Before accepting a rotation notice, verify the new id against docs/agents/coordinator.md or the outgoing coordinator (a wrong id named UX (12), 2026-11-07).
