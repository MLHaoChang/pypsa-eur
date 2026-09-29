# Guided mode — QA gate P24-BE (review route, readiness additions, catalogue, hook lifts)

**Date:** 2026-09-27
**Reviewer:** independent QA-gate agent (did not write the code)
**Branch / range:** `claude/epic-allen-k2t1c4`, `git diff 407b709..HEAD` (fcf88d8 "WIP P24-BE")
**Contract:** spec §4, §5.1 (literal test-id rule), §5.9, §8, §10 + addenda; plan P24-BE section; spec review B8, B11
**Scratch evidence:** `/tmp/claude-0/-home-user-pypsa-eur/93e65137-63f8-5e19-b114-b55788c10af8/scratchpad/qa24be/`

## Verdict: **GO**

There are zero blockers and no system-level bugs. Rows 2–5 are green in my own runs. All 17 valid mutants were killed except one, which is noted below. The route is deep-equal to the chat tool on the running, ok, and stale paths, including a stale report caused by a real foreground solve. The pre-existing readiness keys are byte-identical to the base commit on 24 template/archetype/VOLL combinations. `outage_units.count` matches an independent count on all three EH templates. The lifted hooks are line-for-line the old inline code. The Expert snapshots match and the P23 smoke passes.

Row 1 (the full backend suite) is run by the orchestrator and is not part of this verdict. The GO is conditional on that row being green against the baseline.

---

## Blockers

None.

---

## Gate rows (my runs)

| # | Command | Result | Evidence |
|---|---|---|---|
| 1 | full backend | orchestrator (background) | — |
| 2 | targeted EH set (brief's 12 files, incl. `test_eh_review_route.py`, `test_energy_hub_tagging.py`, `test_golden_coverage.py`, `test_results_range.py`) | **536 passed, 17 skipped, 0 failed** (455.95 s). `test_eh_review_route.py` / `test_energy_hub_tagging.py` / `test_guides.py` alone: 67 passed, **0 skipped** (the `live_solve` route test runs) | `qa24be/row2.txt` |
| 3 | `npx tsc --noEmit -p .` | exit 0 | `qa24be/row3.txt` |
| 4 | `npx vitest run` | **215 files / 2234 tests passed**, exit 0 | `qa24be/row4.txt` |
| 4b | the 3 `*.expertUnchanged.*` + `FmeaTab.test` + `NewProjectWizard.templates.test` + 3 new tests | 8 files / 40 passed | `qa24be/row4b.txt` |
| 5 | `smoke-guided.mjs --phase P24-BE` | **PASS**, 10 screenshots. The eh_review curl sequence returned 204 (empty body), then 200 `running`, then 200 `ok` with `stale:false` and verdict `fail` | `qa24be/smoke-P24-BE.log`, `qa24be/s-P24-BE/eh_review-curl.txt` |
| 5 | `--phase P23` | **PASS**, 13 screenshots | `qa24be/smoke-P23.log` |
| 5 | `--phase P22.9` | **PASS**, 10 screenshots | `qa24be/smoke-P22.9.log` |
| 7 | Expert-unchanged | signed (see below). `git diff 407b709..HEAD -- 'pypsa-gui/frontend/src/**' \| grep -c uiMode` = 0 | — |

After the smoke runs, no uvicorn, vite, stub or chrome processes were left running (`pgrep` came back empty), and the working tree is clean.

The implementer's transcript (`smoke24be/eh_review-curl.txt`) matches my own run byte for byte on the 204 and running entries. The ok entry has the same verdict, `mc_lole_h_per_year` 12.38 and `cost_at_target_eur`.

---

## Contract checks

**Route == tool (§4.1).** `routers/results.py:1517` and `chat_tools.review_eh_study` both call `eh_review.review_latest` with the same `(_state, get_eh_study() if dict)`. The only difference is `no_data_message`, and that never reaches the wire because the route returns 204 there. I probed this on a scratch copy of the backend; the probe test was deleted afterwards (`qa24be/probes.txt`):

| Probe | Route | Tool |
|---|---|---|
| done → **real** `POST /api/simulation/run` (not a store pop) | `/eh_reference_design` 204; review 200 `ok`, `stale:true`, source "study record (…)" | deep-equal |
| running | 200 `{status:'running', message}` | deep-equal (the committed test covers this) |
| study **aborted** | 200 `ok`, `stale:false`, `summary.verdict: null` (the aborted run still stores its partial report) | deep-equal |
| study **failed** by exception (record `failed`, `report: None`, store cleared at study start) | **204** | `no_data` |
| no network / no study | 204 | `no_data` |

**204 semantics.** They are honest with respect to "nothing to review". There is no project parameter; the state is per-session, as §4.1 says ("404 never"). See note N2 for the failed-study wording.

**`stale`.** It is true exactly when the stored report is absent and the study record's copy was reviewed. That matches §4.1 and §10 "Stale detection". The value is a boolean in both cases. The first study start clears the store (`eh_study.py:962`), so a running study never shows the old report.

**Endpoint map / B8.** `TOOL_ROUTES["review_eh_study"] == [("GET", "/api/results/eh_review")]`. I regenerated the inventory into scratch with `tools/openapi_diff.py --out` and it is **identical** to the committed fixture (283 lines). The committed diff is exactly one row.

**Readiness (§4.2, B11).**
- **Pre-existing keys are byte-identical.** I compared the old `eh_readiness.py` (from `407b709`) with the new one on 3 templates plus the `_feeder_hub` network, × 3 archetypes × VOLL ∈ {None, 3000}. That is 24 cases, all IDENTICAL, including key order. Only the three new keys are added (`qa24be/readiness_bytes.txt`).
- **`outage_units`.** I recounted it without using `resolve_outage_params`, from the raw asset columns plus `CARRIER_DEFAULTS`:

  | template | expected (by class) | route |
  |---|---|---|
  | eh_datacenter / weak_flexible | G5 L2 S2 = 9 | 9, `missing: []` |
  | eh_h2_hub / strong_grid | G0 L3 S2 = 5 | 5, `missing: []` |
  | eh_microgrid / off_grid | G4 L1 S2 = 7 | 7, `missing: [Link subsea_tie]` |

- **Pack-applied copy.** `pack_defaults` follows the overrides: a mutant that ignores them is killed. `import_p_nom_mw` reads the pack-applied `net`, so the data-center override of 25 MW reads 25: a mutant that reads the raw network is killed. For the data center: `pack_defaults` = {3.0, 10.0, `mc_lole`} and `import_p_nom_mw` = 40.

**Catalogue (§4.3).** All 20 keys are present. None contains the §4.3 jargon list, and each is at most 30 words. The tour is correctly deferred to P24-FE. Frozen bundle: `smoke/check_bundle.py:176` already roots `data/guides/eh_fmea_guide.json`. No new backend module was added and no `.spec` or `smoke/` file changed, so packaging is unaffected. Wording notes are in N5.

**FE client.** `resultsApi.getEhReview` maps 204 to `null`. The `EhReview` union matches `review_report`'s return shape plus `source`, `stale` and `status`. The `EhReadiness` fields are optional.

---

## Vacuity (mutation checks, all on scratch copies under `qa24be/mut/`)

Backend (`qa24be/mutbe.txt`) — **10/10 killed**:
- M1: the route drops `next_steps`. Killed by the done/stale equality test.
- M1b: the route changes the running message. Killed by the running equality test.
- M2: running → 204. Killed.
- M2b: no_data → 200. Killed.
- M3: `stale` is always False. Killed by the route test and the pure-helper test.
- M4: count ignores MTTR. Killed.
- M4b: count drops StorageUnit. Killed.
- M5: `missing` lists every unrated unit. Killed.
- M6: `pack_defaults` ignores overrides. Killed.
- M7: `import_p_nom_mw` read from the raw network. Killed.
- Catalogue: `P19` added to `verdict`, and `voll_plain` emptied. Both killed.

Frontend (`qa24be/mutfe.txt`) — **6/7 killed**:
- F1: `onCreated` moved after `openInWorkbench`. Killed.
- F2: G4 note moved after `setCurrentProject`. Killed.
- F4: navigate removed. Killed.
- F5: a registry `error` no longer refuses the sweep. Killed by the hook test **and** the existing `FmeaTab.test`.
- F6: an unreadable registry is swallowed. Killed.
- F7: `eh_review` dropped from `invalidateAll`. Killed.
- **F3: `navigate` before `addTab` — SURVIVED** (see N1).

---

## Expert unchanged (row 7)

I diffed the old inline code against the lifted hooks after stripping whitespace (`qa24be/old_*.txt` vs `new_*.txt`):
- `useCreateFromTemplate`: the only changes are `export`, `useMutation` returned instead of assigned, and `onClose()` → `onCreated?.(res.imported)`. The wizard passes `() => onClose()`. The order is unchanged: note → invalidate → set current/name → log → toast → onCreated → addTab → navigate.
- `useStartFmeaSweep`: the `mutationFn` and `onError` are identical. `onSuccess` calls `onStarted` when given, and FmeaTab passes `() => { void refetchModes() }`, which is the old behaviour. The `else` branch (invalidate `fmea_modes`) runs only for callers that pass no `onStarted`, and none exist yet.
- `FmeaTab.test.tsx` and `NewProjectWizard.templates.test.tsx` were not edited and still pass. The three `*.expertUnchanged.*` snapshots pass. In the smoke, the P22.9 and P24-BE Expert paths used the real wizard Template tab and the real "Run B/C sweep" button, and both passed. The P23 Guided smoke also passes.

Signed: the Expert behaviour is unchanged.

---

## Non-blocking notes

- **N1 — the order test does not pin addTab → navigate.** `useCreateFromTemplate.test.tsx:81` has "…add tab, navigate" in its title, but it only asserts the navigation **eventually** happens. Mutant F3 (navigate first) survives. The hook is byte-identical to the pre-lift code, so there is no behaviour risk today. A one-line fix is to push `nav:${where}` from `Where` into `calls` and assert it comes last.
- **N2 — the "204 = no study record" wording is loose.** Both the route docstring (`routers/results.py:1525`) and spec §4.1 say 204 means "neither a study record nor a stored report". A study that **failed with an exception** has a record and still returns 204. An **aborted** study returns 200 `ok` with `stale:false` and `summary.verdict: null`. The P24-FE state machine must therefore take failed and aborted from `getEhStudy`, not from the review. Suggested fix: correct the docstring.
- **N3 — the running message is chat-tool prose.** `eh_review.py:433` reads "poll get_adequacy_results('eh_study') first". It is fine for the model. P24-FE must not render it on a card.
- **N4 — microgrid Site-card data.** Under `off_grid`, `outage_units.missing` lists `subsea_tie` (the import Link that the pack islands). `import_p_nom_mw` is 20 for an island whose tie is normally open (`p_max_pu = 0`). Both follow §4.2 literally (import Links count as thermal-like), which is the accepted `outage_units` scope. P24-FE's wording for `off_grid` should not tell the user to "add outage data for subsea_tie" or present a 20 MW grid connection. Also, `_gen_category` counts `slack`, `sink` and `dump` carriers as conventional, so a VOLL slack generator without outage data would show as "missing". None of the three templates has one.
- **N5 — catalogue plainness.** `grid_strength` (`eh_fmea_guide.json:204`) says "short-circuit power in MVA" and "inverter equipment". `voll_plain` (`:215`) says "MWh". Both are understandable but technical for a first-time user. `verdict` (`:208`) names certified, not certified and not decided, but not "no target set", which is the H₂ hub's outcome (§5.5). The `hub_*` entries end in a question ("What is missing?"). That reads as an Ask prompt rather than a hover. Check this against the cards in P24-FE.
- **N6 — layering.** `hooks/useStartFmeaSweep.ts:11` imports `blockerMessage` from `pages/results/McPanel`, so a hook depends on a page. It works, but it may be worth moving `blockerMessage` to `utils/` later.
- **N7 — theoretical non-finite value.** `import_p_nom_mw` (`eh_readiness.py:282`) is not scrubbed. An import Link with `p_nom = inf` would make `/eh_readiness` return 500 under Starlette's `allow_nan=False`. No template or realistic network has one.
- **N8 — spec inconsistency for P24-FE.** Spec §5.10 (`HubDesignPanel.flow.test.tsx` row) says "`stale` banner from `source`", which contradicts §4.1 and §10 (read the boolean, never the prose). P24-FE should follow §4.1.

The accepted deviations were verified as described: the plain-text check covers only the new keys; there is one line each in `test_golden_coverage.py` and `test_results_range.py`; the `outage_units` scope is as noted in N4; and `TEMPLATES` is exported in P24-FE.
