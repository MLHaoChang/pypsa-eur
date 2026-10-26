# Gatekeeper: standing rules

What the engine gatekeeper applies to every review, so a successor reads this file instead of a hand-off note's rule list. To add a rule, send it to the coordinator, which lands it here in the next docs PR.

Post each verdict as a PR comment, with the attribution footer: `GK OK @<full sha>: <what was checked>` or `GK NOT OK @<sha>: <what to fix>` (owner decision VERDICT-PR). Plan reviews go to the coordinator, because there is no PR yet. The verdict must be the first line of the comment; the attribution footer goes last.

## Review setup

- Fetch `refs/pull/N/head:refs/gk/N`, `git worktree add --detach` under the scratchpad, and remove the worktree afterwards.
- Backend tests: `PYTHONPATH=<wt>:<wt>/pypsa-gui/backend /root/.venv-pypsa-gui/bin/python -m pytest -p no:warnings -q -rfE <files>`, no xdist. Check the exit code. Keep each run under 590 s and split files (`test_pilot_evaluation` and broad finance sweeps time out).
- Frontend: symlink `node_modules` from `/home/user/pypsa-eur/pypsa-gui/frontend`, put `/home/user/pypsa-eur/.pixi/envs/default/bin` on `PATH`, then `npx vitest run <paths>` and `npx tsc --noEmit -p .`.

## Delta reviews

- List the files both sides changed since the old merge-base (`comm -12`) and test those. For hand resolutions, read `git diff <merge-tree> <head>`.
- `tool-error-kinds.json`: its kinds must be the union of both parents.
- Inventory: `smoke/assistant_inventory.py --check`; regenerate with `--write`, never hand-merge.

- A PR that adds an import of gridspine or another third-party module to `pypsa-gui/backend`: run `tests/test_packaging_requirements.py` at the trial merge (the frozen-app pin test; #707 missed it).

## Known container-only failures

`test_harness_provider_parity`, `test_workflow_provider_parity[*-anthropic]`, `test_profile_gen_atlite_source` (atlite is not installed) and `test_openpyxl_parses_uploads_with_defusedxml_in_this_environment`.

## Test quality

Probe tests that monkeypatch `_probe` can hide dispatcher-signature bugs: ask for a test against the real signature.

## Rulings

- `profile_location_missing` skips uploaded and network_native profiles (`readiness.profile_kind`).
- A project-scoped result-view read carries `project_id` = the active project, and 404/409 count as `no_data`.
