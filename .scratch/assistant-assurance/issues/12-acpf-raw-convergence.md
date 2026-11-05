<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A1.7: AC power flow records unknown convergence as converged

Status: done (#1125)
Type: task
Blocked by: none in code. Code starts after GK A (12) plan GO, UX (8) GO and the coordinator's go (FILL-11-5).

Split out of [02](02-evidence-identity-and-access.md) (GK B (21) plan condition A5; owner decision AA02-SCOPE-1).

## Problem

`services/ac_pf_service.py` builds the per-snapshot convergence map from PyPSA's `converged` frame. Two branches mark every snapshot as converged without evidence:

- `pf_result` has no `converged` frame (the "No info — assume all converged" branch);
- reading the frame raises (the bare `except Exception` branch).

The run then records `ac_pf_converged_count == ac_pf_total_snapshots`. The convergence gate added by issue 02 (`run_evidence._convergence`) accepts that run as fully converged, so its numbers can be checked although nothing proved convergence. The UI convergence bar also shows "n of n converged".

Spec: capture raw validity before sanitization; missing is not converged (reliability contract, A1.7).

## Acceptance

- Missing or unreadable convergence records unknown, not converged. Each snapshot is `ok: null`, or the run carries an explicit flag, and the counts no longer read `n of n`.
- `run_evidence` publishes a run with unknown convergence so that `_convergence` returns `not_known_to_have_converged`.
- The load-flow UI shows unknown convergence as unknown, not as converged and not as failed. This is a UI change, so it needs the UX reviewer.
- Test first, with a fake `pf()` result that has no frame and one whose frame raises. No test asserts the current all-converged output as correct.
- A snapshot missing from the frame, a NaN cell or a frame with no sub-network columns is unknown too (pandas `all()` reads each of them as True).

## Comments

### 2026-11-05 — plan (triage + design; no code yet)

**What "unknown" is in the solver output.** PyPSA 1.1.2 `n.pf()` (`pypsa/network/power_flow.py`) returns `Dict(n_iter, error, converged)`, where `converged` is a bool DataFrame of snapshots × sub-networks, pre-built with `dtype=bool` and filled per sub-network (a single-bus sub-network is filled with `True`, which is real convergence). It returns `None` for `linear=True`. A snapshot's convergence is *unknown* when the service has no evidence for it:

1. `pf_result` is `None` or has no `converged` key (today: "No info — assume all converged");
2. the value is neither a Series nor a DataFrame (today: the `else` branch, all True);
3. reading or iterating it raises (today: bare `except`, all True);
4. the frame has no sub-network columns (`all(axis=1)` is vacuously True);
5. a cell is NaN or not a bool (`all()` skips NaN, `bool(nan)` is True);
6. a snapshot of `n.snapshots` is not in the frame's index (today it is dropped, and `n_total = len(converged_map)` shrinks, so the bar can read "k of k").

Not reachable from outside PyPSA and left as a residual risk: an unfilled cell of PyPSA's pre-built `dtype=bool` frame already reads `True`. PyPSA fills every sub-network in its loop, so 1.1.2 never leaves one.

**How it is recorded** (`services/ac_pf_service.py`, `run_ac_pf_stage`). One pure helper `_convergence_rows(pf_result, snapshots) -> list[{snapshot, period, ok}]` with `ok` in `True | False | None`:

- per snapshot, across sub-networks: any `False` → `False`; else any unknown cell → `None`; else `True`;
- every snapshot in `n.snapshots` gets a row, in order; frame rows not in `n.snapshots` are ignored;
- cases 1–3 make every row `None`.

State keys:

- `ac_pf_convergence_list`: rows as above (`ok` may now be `null`);
- `ac_pf_convergence` (legacy dict): same `True/False/None` per ISO;
- `ac_pf_total_snapshots = len(n.snapshots)` (always);
- `ac_pf_unknown_count` (new, int): rows with `ok is None`;
- `ac_pf_converged_count`: rows with `ok is True` **only when `ac_pf_unknown_count == 0`, else `None`**. A partial count beside unknown rows would read as "partly converged", which is a claim nobody proved.

The phase line reads `Stage 2: AC PF complete — convergence unknown for u of n snapshot(s).` when `u > 0`, and is unchanged otherwise.

**Gate.** `run_evidence` publishes `ac_pf_converged_count` only when it is an int, so an unknown run is sealed with `ac_pf_total_snapshots` alone, and `_convergence` already returns `not_known_to_have_converged` for that (one count, or `kind == 'ac_pf'`). `run_evidence.py` needs no logic change; it gets a test. Recorded refusal states, in precedence order: **unknown** (`not_known_to_have_converged`) > **partly converged** (`partly_converged (k of n …)`, AA02-SCOPE-1, unchanged) > converged. A run with some `False` and some unknown is unknown: its k is not known.

**Callers that change.**

| File | Change |
|---|---|
| `backend/services/ac_pf_service.py` | the helper and state keys above |
| `backend/services/project_context.py` | add `ac_pf_unknown_count` to the AC PF state key list (l. 310) and the dataclass (l. 594) |
| `backend/routers/simulation.py` | reset `ac_pf_unknown_count=None` with the other AC PF keys (l. 1606) |
| `backend/routers/results.py` `/ac_pf/status` | pass `converged_count` through as `null` (today `or 0` turns unknown into "0 of n"); add `unknown_count` |
| `backend/services/loadflow_summary.py` `_ac_convergence` | the mask already treats `ok is not True` as invalid (safe); add `unknown_in_window` beside `unconverged_in_window` and count only `ok is False` as unconverged, so the assistant does not call unknown snapshots failed |
| `frontend/src/api/simulation.ts`, `api/solveQueue.ts` | `ok: boolean \| null`, `converged_count: number \| null`, `unknown_count?: number` |
| `frontend/src/pages/results/LoadFlow.tsx` | KPI and section title (below); the non-converged list filters `ok === false`, not `!ok` |
| `frontend/src/components/SnapshotPicker.tsx` | badge for `ok === null` (below); today `null` hides the badge |
| `frontend/src/pages/TopologyCanvas.tsx` | `currentSnapConverged` must not fall back `?? true`; source-toggle tooltip (below) |

`solver_service.py` and `routers/projects.py` only move the keys through; `projects.py` passes `ac_pf_convergence_list` as is. None of these files is in open PR #1119's list.

**What users see** (needs UX (8)). The rule: unknown is its own state, neither ✓ nor ✗, and never counted as converged.

- *LoadFlow, "AC Power Flow status"* KPI "Snapshots converged": known → `k / n`, unchanged. Unknown → `Unknown / n`, with a muted line under the KPI grid: "PyPSA did not report convergence for u of n snapshots. These AC values are not checked; re-run Stage 2 or read LOPF." Section-title tooltip: `AC Power Flow results (convergence unknown for u/n)`.
- *Non-converged list*: only `ok === false`. When `u > 0`, "All snapshots converged." is not shown.
- *SnapshotPicker badge*: `?` in `text-muted`, title "AC PF convergence not reported for this snapshot". ✓/✗ unchanged.
- *Canvas*: on the AC PF view, an unknown snapshot shows the existing banner style in the neutral (not warn) tone: "AC PF convergence was not reported for this snapshot; values are not checked." Toggle tooltip as in the section title.
- *Assistant / checked reads*: a checked read of the run is refused as "not known to have converged" (existing wording from issue 02).

**Tests (written first, watched fail).**

- `tests/test_ac_pf_convergence_unknown.py` (new): drives `run_ac_pf_stage` with a fake `n.pf` returning (a) `None`, (b) `{}` with no `converged` key, (c) a frame whose `.all` raises, (d) a frame with no columns, (e) a frame with a NaN cell, (f) a frame missing one snapshot, (g) a mixed frame with one `False` plus one unknown. Each asserts `ok is None` where unknown, `ac_pf_converged_count is None`, `ac_pf_unknown_count == u`, `ac_pf_total_snapshots == len(n.snapshots)`. Plus a plain all-True frame (converged) and one `False` (partly converged, count k).
- `tests/assurance_acceptance/test_p1_run_convergence.py`: a published run with total only (from state with `converged_count None`) is refused `not_known_to_have_converged`, end to end through `run_evidence`.
- `tests/test_loadflow_summary.py`: an unknown row is excluded from the mask and counted in `unknown_in_window`, not `unconverged_in_window`.
- results router: `/ac_pf/status` returns `converged_count: null` and `unknown_count` for an unknown state.
- frontend: a vitest for the KPI text and the picker badge on `ok: null`.
- `tests/test_ac_pf_link_dispatch.py:78` asserts `converged_count == total` on a real PyPSA solve; that is a real frame, so it stays and still passes.

**Out of scope.** PyPSA's pre-filled `dtype=bool` frame (above); the LOPF "converged" notion; any change to `_convergence`'s precedence for runs sealed before this fix (they keep their recorded counts; a run sealed with the old all-True fallback cannot be told apart, so it stays "converged").

### 2026-11-05 — UX (8) plan GO @a58145eb, asks folded in

1. KPI: `k / n · u unknown` when 0 < u < n; `Unknown / n` only when u = n. `ac_pf_converged_count` stays `None` for the gate, so `/ac_pf/status` adds `known_converged_count` (rows with `ok is True`), which feeds this KPI. The vitest pins both strings.
2. Muted line: "PyPSA did not report convergence for u of n snapshots: their AC values are not checked. Re-run Stage 2, or use the LOPF results." (`1 of n snapshots` when u = 1.) "Re-run Stage 2" links to the Stage 2 control only if one sits on this page; otherwise it stays plain words.
3. Canvas banner: "AC PF convergence was not reported for this snapshot: its values are not checked." Keep "AC PF" only if the existing banner already uses it.
4. Picker `?` badge: `aria-label` equal to its title.
5. Screenshots, in Guided and Expert wherever Load flow renders: KPI at u = n, the mixed case with the muted line, the picker with ✓/✗/?, and the canvas banner on an unknown snapshot.

### 2026-11-05 — GK A (12) plan GO @a58145eb, conditions (binding on the code PR)

1. **Raise scope.** An exception from `n.pf()` itself is still re-raised, as it is today; only a failure while *reading* the result becomes unknown. The "raising" test is named for what it is: a result whose `converged` access or `.all` raises. Add a test that a raising `n.pf` still fails the run and records no counts.
2. **Strict truth.** A snapshot is `True` only when every sub-network cell is a real boolean `True` (`bool` or `np.bool_`). NaN, `None`, a non-bool value (`1`, `"True"`, object dtype) and an empty row are unknown. Test one non-bool value. This replaces pandas `all(axis=1)`.
3. **Index match.** Missing-snapshot detection keys on `n.snapshots`, a MultiIndex on multi-period networks, not on the frame's own index. Frame rows outside `n.snapshots` are ignored and never counted. Add a multi-period case.
4. **Truthiness sweep.** Before coding, list every reader of `ok`, of the legacy `ac_pf_convergence` values and of `ac_pf_converged_count`:
   - backend: `loadflow_summary` 184/210-212/392, `results` 648/661-664, `simulation` 1606-1611, `run_evidence` 923, `projects` 4292/4336;
   - frontend: `solveQueue.ts:80`, and the LoadFlow, SnapshotPicker and TopologyCanvas readers.

   Put the list in the PR body, and show that each one maps `None` to unknown. Look hardest at `count == total` comparisons and at `or 0`.
5. **Mutants**, each with its killing test named in the PR body:
   - (a) the helper maps unknown to `True`;
   - (b) the `u>0 → converged_count None` step is dropped;
   - (c) per snapshot, unknown wins over a known `False`;
   - (d) the parse `except` is restored to all-`True`;
   - (e) `/ac_pf/status` `or 0` is restored;
   - (f) `loadflow_summary` counts unknown as unconverged.
6. **Back-compat.** A state or pkl written before the fix, with no `unknown_count`, reads as it does today, and runs sealed before the fix are unchanged. Pin both in tests.
7. **Frontend.** A null `ok` renders as a distinct unknown state, never green or red. Add a vitest, and run `tsc` on the widened types.
8. **Residual risk** (open): in PyPSA 1.1.2, `_network_prepare_and_run_pf` pre-builds `converged` as `pd.DataFrame(..., dtype=bool)`, so a cell PyPSA never filled reads `True` and cannot be told apart from converged. It is not reachable today, because every sub-network is filled in the loop. Re-check on a PyPSA upgrade. The code PR adds a release-note fragment.

GK B (22) is needed only if the route or harness side grows beyond the `/ac_pf/status` field.
9. **Display-only count** (GK A, GO at plan head 77196d8f). `known_converged_count` appears only on the `/ac_pf/status` response. It is never a state key that `run_evidence` reads, never sealed into a run's metrics, and never a gate or plausibility fact. A test checks that a mixed run's sealed manifest carries `ac_pf_total_snapshots` only, with no converged count under any name. It must kill a mutant that seals `known_converged_count` as `ac_pf_converged_count`. The UI shows it only beside its "u unknown" suffix, never alone as "k / n".
