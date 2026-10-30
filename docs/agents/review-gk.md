# Gatekeeper: standing rules

What the engine gatekeeper applies to every review, so a successor reads this file instead of a hand-off note's rule list. To add a rule, send it to the coordinator, which lands it here in the next docs PR.

Post each verdict as a PR comment, with the attribution footer: `GK OK @<full sha>: <what was checked>` or `GK NOT OK @<sha>: <what to fix>` (owner decision VERDICT-PR). Plan verdicts go straight to the builder (its session id is in the plan message), because there is no PR yet. The verdict must be the first line of the comment; the attribution footer goes last. The PR comment is the verdict. Message the coordinator only for a NO-GO the builder disputes, a NOT OK that blocks a merge, or a standing ruling to record. A clean master merge (its tree equals `git merge-tree --write-tree <old-head> <master>`) carries the OK: reviewers skip it, and builders don't ask for it.

## Review setup

- Read a PR with `git diff --stat` first, then one hunk or file at a time, each capped with `| head -c 6000`. Whole-file or cross-file reads go to an Explore subagent that returns file:line findings only.
- Fetch `refs/pull/N/head:refs/gk/N`, `git worktree add --detach` under the scratchpad, and remove the worktree afterwards.
- Backend tests: `PYTHONPATH=<wt>:<wt>/pypsa-gui/backend /root/.venv-pypsa-gui/bin/python -m pytest -p no:warnings -q -rfE <files>`, no xdist. Check the exit code. Keep each run under 590 s and split files (`test_pilot_evaluation` and broad finance sweeps time out).
- Frontend: symlink `node_modules` from `/home/user/pypsa-eur/pypsa-gui/frontend`, put `/home/user/pypsa-eur/.pixi/envs/default/bin` on `PATH`, then `npx vitest run <paths>` and `npx tsc --noEmit -p .`.

## Delta reviews

- List the files both sides changed since the old merge-base (`comm -12`) and test those. For hand resolutions, read `git diff <merge-tree> <head>`.
- Error kinds: a PR adds new kinds only in its own `pypsa-gui/tool-error-kinds.d/<name>.json` fragment, and edits the base `tool-error-kinds.json` only to change an existing kind. Two fragments never conflict as files, so a kind defined twice is caught only by `smoke/json_fragments.py`, which refuses it. Run `tests/test_tool_error_kind_manifest.py` and `ChatPanel.manifest.test.tsx` at the trial merge, not only on the PR head.
- Inventory, route parity, control matrix: their `--check`s build in memory and nothing generated is committed, so there is nothing to regenerate or hand-merge. At the trial merge, run `smoke/assistant_inventory.py --check`, plus `assistant_route_parity.py --check` and `assistant_control_matrix.py --check` when routes, chat tools or the frontend changed. A `stale (sweep prunes)` line is fine; a `FAIL` line is not.

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

`test_harness_provider_parity`, `test_workflow_provider_parity[*-anthropic]`, `test_profile_gen_atlite_source` (atlite is not installed) and `test_openpyxl_parses_uploads_with_defusedxml_in_this_environment`.

## Test quality

Probe tests that monkeypatch `_probe` can hide dispatcher-signature bugs: ask for a test against the real signature.

When a PR changes a validation guard, confirm by mutation that its test fails for the guard, not for a downstream check (#807's decoy case).

`test_json_fragments`' real-file assertion must not depend on any declaration list being non-empty.

A test never asserts a value a known defect produces as if it were correct. Assert the parts that are right, leave the wrong field to a `_pending` manifest row that names the ticket, and keep any identity that holds before and after the fix. No xfail, no skip, and no fixture chosen to hide the defect (#890, ticket 30).

A stacked or full-file mutant run can hang when the mutant lets a real solve or restudy run. Run the single targeted test with -x and a timeout.

A family-manifest PR flips a variant to `_covered` only for what its tests check. Each untested or defective annex item gets its own `_pending` row, with a ticket when it is a defect: a family whose only variant is covered reads as `evidenced` (#918, #920).

A `pytest.raises(Exception)` on a refusal is checked for what actually raises. An incidental library error (#918's pandas `InvalidIndexError`, a 500) is a defect, not a guard.

## Rulings

- `profile_location_missing` skips uploaded and network_native profiles (`readiness.profile_kind`).
- A project-scoped result-view read carries `project_id` = the active project, and 404/409 count as `no_data`.
- A run profile whose approval envelope lists `run_sensitivity_sweep` or `run_eh_study` needs its own GK review. A sweep's cases[].changes create child projects with arbitrary edits that no edit bound limits; admit such a run only with per-case bounds or a card per case. An envelope admission must also refuse any `*project*` argument that is not the run's own project (#875).
- Builders record a GK plan GO's conditions in the issue file's `## Comments`, so a rotation does not drop them.
- A PR that changes a stored format (e.g. the run manifest MAC): existing readers see byte-equal output (strip the new field on read), and a pin test shows the other MAC domains are unchanged.
- A "covered" manifest row that says something is refused needs a test of each tool it names (#922 claimed "unknown asset moves nothing", and two tools emitted frames).
- Measure a cap raise downstream: #927 found `_assurance` cut from long checked results; it now goes first, like `_edit_receipt`.
- A chat tool that wraps a route handler through `_route` and turns a non-error status (204) into a refusal needs a test of that refusal: #939's mutant dropping the 204 guard saved a 0-byte .xlsx and passed every test.
- A PR that adds custom (non-PyPSA) component columns the LP reads adds them to `_network_input_fingerprint`, appended only when present so existing fingerprints stay byte-identical, with a pin test (AS-02).
- A new defaults-pack version becomes `load_defaults_pack()`'s default for every unpinned caller: the PR lists those callers and tests that the previous version's rows are byte-equal (AS-02).
- An exception subclass that narrows a refusal kind (#971's ProjectDeleted under AccessRevoked) is checked against every caller of the raising function: only the intended handler tells the two apart, so no existence oracle opens.

## Engineering rules

- Grid source impedance derived from an entered S_k'' carries the IEC 60909 voltage factor c (Z = c·Un²/S_k''; S_sc2 = S_k''/c), in every screen: unbalance, the flicker note, the impedance scan.
- TDD and other ratio screens (Isc/IL) use the entered S_k'' without the c factor; `td.STRICTER` is only for a band computed from S_sc,min.
- A shipped demo limit that is an IEC planning level says so in its clause: "planning level, not an emission limit allocated to this installation".
- A new scorecard input read from a component is registered as an editable field end to end (FieldSpec, model, schemas, attrs_doc, fieldLabels, fixture) in the same PR.
- E1 growth placeholders: setting `nh3_delivery_tpd` converts t/d to MWh_NH3/h (× 5.166/24), with a test.
- A handler that `pd.concat()`s user-uploaded series survives members that cannot be aligned (a tz-aware index next to a naive one, a repeated timestamp) and reports a gap, never a 500 (#868). Test both.
