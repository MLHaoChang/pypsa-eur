# Gate — U2 final (WP10, the merge with master, and U2 as a whole) before the PR to master

**Verdict: GO WITH BINDING CONDITIONS**

- **Assessed:**
  - WP10 `0c31b1b6` against its base `58ae8809`: `git show 0c31b1b6` in full (81 files, +4881 / −4720).
  - The merge `61e09fae` (master `fceb886e`): `git show --cc`, the two conflict files (`routers/projects.py`, `AppHeader.tsx`) and the merge's own edits (two test pin fixes, the `check_bundle.ROOTED` manifest line).
  - U2 as a whole: `git diff 361682db^..61e09fae -- pypsa-gui`, read for the shared hot files; and `git diff fceb886e 61e09fae` on those files, for the true net change against master.
- **Read:**
  - the sub-plan's WP10 section and status, §5.1–§5.3, the pre-PR checklist and §8;
  - the parent plan's §4, §5 (U2), §6 (facade), §7 (protocol), §8 (owner decisions 1–10) and §10;
  - every U2 gate note (stage 1, WP6, WP7, WP8a, WP8b, WP9);
  - the findings note `docs/superpowers/findings/2026-10-05-guided-study-u2.md`;
  - `tests/fixtures/u2_deltas.json`, `u2_port_map.json`, `u2_pre_numbers.json`, `legacy_study_library.json`, `u2_gs_bound_npvs.json` and `pre_u2_fork/`.
- **Method:**
  - Everything ran on `git archive 61e09fae` copies under `scratchpad/g10`:
    - `a`, a **clean** archive, for the suite;
    - `p`, for the probes, the three QA drivers, vitest and `tsc`;
    - `mut`, for the mutations. After each run the file was restored from `p` and compared byte for byte ("restored identical: True" every time). The runner is `g10/tools/mut.py`, and the specs are in `g10/tools/specs.json`.
  - `p` and `mut` received the two `pre_u2_fork/*/network.nc` files copied from the main tree. They are **not in the commit** (S1). `a` was left as git has it.
  - A `git archive 14d0281f` copy (`old`) was the reference for GS's own dispatch and legacy loader.
  - **Not modified by me:** the main tree and `/tmp/wt-s5`. No `pkill`, no `&`. The long runs used the tool's background mode.

WP10 does what it says on every path I could reach, with one exception: the evidence for its headline safety property is not in the commit.

**What holds:**
- **W4** refuses a pre-U2 network through the real `/api/simulation/run` path. The refusal survives Save-As, Save-a-Copy, a branched scenario and a bundle round-trip.
- **The deletions are complete.** No code path names the deleted modules, fields or functions.
- **Owner decision 10's port** covers both cycling codes, including networks without a commercial config.
- **The shared files' net change against master** is the W4 hook plus two planned GS additions.
- **The judging rule** cannot pass an unrecorded move.
- **`lp_degenerate_dispatch_ties` is genuine.** GS's and the engine's golden dispatches have bit-identical objectives, MWh totals and monthly peaks.
- **The merge, packaging and every earlier condition** hold, or are carried with an owner.
- **Suites, drivers and the frontend** are green, apart from S1's 8 tests.

**Two findings need conditions before the PR:**
- **S1 (blocking).** The stored pre-U2 fixture's two netCDFs are gitignored (`*.nc`) and were never committed. On a clean checkout, 8 of the 10 W4 tests fail with `FileNotFoundError`. The full suite in `/tmp/wt-s5` will show the same 8. The fixture cannot be regenerated either: its recorder needs the deleted code.
- **S2.** W4 has a false positive. A study network written **after** U2, which a user later prices by hand, is refused as "built by an earlier version", with a remedy that does not apply. "Re-run the study" also does not clear a pre-U2 study's **base** project.

---

## Evidence summary

**Backend suite** (on the clean copy `a`). The file set is:
- `tests/test_u2_*.py tests/test_engine_adapter_*.py tests/test_study_*.py tests/test_studies_routes.py tests/test_decision_vocabulary_parity.py tests/test_golden_*.py tests/test_packaging_requirements.py tests/test_xlsx_formula_eval.py`;
- plus `test_solver_* test_validation_* test_preflight_* test_objective_* test_commercial_objective_reconciliation`.

**1155 passed, 9 failed, 1 skipped**, counted from the progress characters. The log is `g10/suite.log`.
- **8 failures** are `test_u2_wp10_pre_u2_resolve.py`: `FileNotFoundError` on `pre_u2_fork/*/network.nc`, which is S1.
- **1 failure** is `test_packaging_requirements.py::test_every_unguarded_third_party_import_is_pinned_for_the_build`. Master's `tools/pilot_evaluation_plugin.py` (#137 / #145) imports `pytest` unguarded.
  - It **fails identically on a `git archive fceb886e`** (master), so it is not U2's.
  - It will still redden the PR's CI. Someone must fix it on master or in the PR, and the owner should name who.
- **The skip** is the archive-only `test_the_recording_commit_predates_wp1` (no git).
- **The other U2 tests are green on the clean archive:** every other WP2 target, the golden matrix, the packaging pins, the port map and the one-engine acceptance.

**`test_u2_wp10_pre_u2_resolve.py` on `p`**, with the two netCDFs copied in from the main tree: **10 passed**.
- The main tree's files are `base/network.nc` (46,630 B, sha256 `6116c2a3…`) and `fork/network.nc` (163,502 B, sha256 `dbdf588a…`).
- `git ls-tree 61e09fae` lists only the two `solver_config.json` files and the recorder script.
- `git check-ignore` names `.gitignore:62 *.nc`.
- No `.nc` file is tracked anywhere in the repository.

**QA drivers** (on `p`, once each)
- **`qa_decision_study.py`: 67 passed, 0 failed** (172.5 s).
  - Verdict `marginal`, best `bess_1h` (0.796 MW), drivers `["demand_charge_price"]`; `bess_2h` and `bess_4h` skipped (0 MW).
  - Every fork carries `commercial` and `finance` and no `demand_charge`.
  - Engines: `tariff_engine` / `finance_engine`, bill refs `tariff_engine:counterfactual` and `tariff_engine:bess_1h`.
  - **§4.6 identity on `bess_1h`:** NPV 227,029.49276 against LP saving × AF 227,029.49280, **2.14e-10 relative**.
  - The judging rule reports 0 unrecorded figures and 6 recorded driver rows, each with a cause.
- **`qa_investment_case.py`: 259 / 259.**
- **`qa_value_flows.py`: 235 / 235.**

**Frontend** (on `p`)
- Vitest `src/pages/decision src/utils`: **50 files, 569 passed**. The merge added utils tests; the implementer's pre-merge count was 536.
- `npx tsc -b`: **exit 0**.

**Ownership**
- No IC-owned file is changed by WP10.
- **Net change against master** (`git diff fceb886e 61e09fae`) on the shared hot files:
  - `validation_service.py`: the W4 hook (8 lines) and the `eh_role = grid_supply` exemption that plan §5.3 keeps;
  - `schemas.py`: GS's `ProjectInfo.study_owned` / `owner_study_*` (F3);
  - `objective.py` and `objective_decomposition.py`: comments only;
  - `solver_service.py`: **no net change**;
  - packaging: the study's datas and checks.
- Nothing surprising.
- The exemption reads the `eh_role` column. Master's EH templates name their generator `grid_supply` but set no `eh_role`, and they price it per snapshot, so no master network loses the `gen_zero_costs` warning.

---

## Findings

### 1. W4: a pre-U2 network is refused (claim 1)

**[S1] The fixture's netCDFs are not in the commit (BLOCKING).**
- `tests/fixtures/pre_u2_fork/{base,fork}/network.nc` exist only as untracked files in the main tree.
- **The consequences on a clean checkout of 61e09fae:**
  - 8 of the 10 W4 tests fail. Only the two tests that do not open the fixture pass: the post-U2 network test and the names test.
  - MW2 (the refusal removed) is therefore **invisible** on the committed tree: those tests are red either way. WP10's headline guard ships effectively unpinned.
  - The implementer's 1262-pass suite and my `p` probes ran on trees that held the untracked files. So will no CI.
  - `make_pre_u2_fork.py` imports `services.study.tariff`, which is deleted, so the files cannot be rebuilt from the repository. The main tree's copies are the only ones.
- See **U1**.

**[N1] The refusal itself is right, through the real paths (probes `g10/tools/w4_routes.py` and `w4_inproc.py`; qa sandbox, real routes).**

| Path | Pack marker kept | Result |
|---|---|---|
| Project saved, the stored pre-U2 `solver_config.json` (with `demand_charge`) put back, `GET /api/projects/{name}` | yes | loads; `demand_charge` dropped |
| `POST /api/simulation/preflight` | — | error `study_network_tariff_in_links` |
| `POST /api/simulation/run`, then `/status` and `/log_history` | — | `failed` / `validation_failed`; the `[VALIDATION] ERROR … Re-run the study … [study_network_tariff_in_links]` line names both Links |
| Save-As (`rebind=True`), reloaded | yes | refused |
| Save-a-Copy (`rebind=False`) | yes | (refused on load, same network) |
| `POST /{base}/scenarios`, reloaded | yes | refused |
| `GET /{name}/bundle` → `POST /import_bundle`, reloaded | yes | refused |
| `grid_import` renamed | — | refused (through `grid_export`) |
| **both** meter Links renamed | — | **not refused** (a deliberate Expert edit of both names; accepted, recorded) |
| static price only; priced in one hour only | — | refused |
| all prices NaN | — | not refused (correct: an unpriced Link is not the old tariff) |

- **The marker survives every save path** because it lives in `n.meta`, inside `network.nc`, not in `metadata.json`. `_saves_its_own_directory` strips only the fork owner keys, which is the right split.
- **Every solve path** goes through `run_simulation` → `validate_for_run`: the queue, the adequacy frontier / sweep / levers / DTC / redundancy / coupling / margin loops, the EH study, `/run` and myopic. A grep for `.optimize(` outside `solver_service` / `myopic` finds nothing. So "every solve refuses" holds.
- **A post-U2 fork, solved** through `run_simulation` with the study's compiled config, carries no Link price after the solve or after a netCDF round trip. It is not refused.

**[S2] A false positive with no way out, and a remedy that does not reach the base.**
- **(a) The detector cannot tell post-U2 from pre-U2.**
  - It keys on "pack marker + priced meter Link". A post-U2 study network has the same marker: the pack meta has no version or engine field.
  - A user who Save-As's a post-U2 fork (the marker is kept, shown above) and then types a price on `grid_import` is refused with "built by an earlier version … Re-run the study". Probe (b): REFUSED.
  - Neither part is true, and the UI cannot clear `n.meta`.
- **(b) The pre-U2 base is never rewritten.**
  - Before U2 the study wrote its **base** project's network with the Link prices (`10f8abc` `packs.py:609`, at study creation).
  - After U2 the runner rebuilds only the forks (`runner.py:662`, `packs.build_site_network`). Nothing rewrites the base's `network.nc` (only `forks._write_network` writes networks).
  - So "Re-run the study" never clears the refusal on a pre-U2 base.
- **Impact today is small.** GS was never on master (`10f8abc` is not an ancestor of `origin/master`), so pre-U2 studies exist only on installs that ran the feature branch. A post-U2 copy priced by hand is an edge case.
- The message is still the product's answer in both cases. See **U2**.

### 2. Deletions (claim 2)

**[N2] Nothing references the deleted engine.**
- I searched the `.py`, `.ts`, `.tsx`, `.json`, `.spec`, `.toml` and `.sh` files under `pypsa-gui`, plus `pixi.toml`, for the deleted names:
  - `services.study.tariff` / `proforma` / `proforma_xlsx`;
  - `BillCalculator`, `_wrap_with_demand_charge`, `DemandChargeRefused`, `write_tariff_prices`, `demand_charge_config`;
  - `record_solved_demand_charge`, `solved_demand_charge_config`, `demand_charge_eur_from_network`;
  - `_check_export_cycling`, `export_cycling_flags`, `EXPORT_CYCLING_CODES`, `_cross_hour_cycling`, `_site_storage`;
  - `load_library`, `UnknownLibraryVersion`, `LIBRARY_DIR`.
- What remains is comments, recorded strings in fixtures, the recorder script, IC's own ported `preflight._site_storage`, and `runner` / `tornado_runner` importing `engine_adapter.cycling_flags` **as** `export_cycling_flags` (an alias, not the deleted function).
- **Stored configs with `demand_charge`:**
  - `_solver_config_from_dict` drops the key: project load, bundle import and the queue snapshot. The route probe shows the load succeeds and the key is gone.
  - `SolverConfigSchema` (pydantic default `extra="ignore"`) ignores it on `PUT /solver_config`.
- **`demand_charge_eur`:**
  - No frontend file reads `objective_decomposition`'s old key. The frontend's only `demand_charge` is the guided **tariff form's** own field (`decisionStudies.ts:118`).
  - The backend's remaining `demand_charge_eur` is the runner's detail field, filled by `engine_adapter.demand_charge_eur`.

**[N3] Owner decision 10: IC's preflight covers what GS's did.**
- `commercial/preflight.py` emits both `tariff_export_exceeds_import` and `tariff_export_exceeds_import_via_storage`.
- `_check_commercial` runs `network_findings` (the raw `marginal_cost` pair check) **when there is no commercial config**, and `commercial_findings` with one. That is decision 10's "also for networks without a commercial setup", which closes R7 / Q16.
- `test_preflight_cycling` holds GS's three functions verbatim as the parity oracle (`test_the_vectorised_check_matches_the_gs_reference`), plus the BC-S3-1 performance guard.
- The study keeps disclosing the codes through `engine_adapter.cycling_flags` (BC-F1-1).

### 3. Shared files (claim 3)

**[N4] Minimal and correct.** See the ownership paragraph above.
- WP10's own diff on the shared files is pure removal (GS's additions), plus the 8-line W4 call and its comment.
- The removal in `run_simulation` takes the F1-B4 record out from under the `status in ("ok", "optimal")` guard and leaves master's commercial price-frame persistence intact.
- **Nit:** the `grid_supply` exemption's docstring still says "the tariff is priced on the import/export Links". That is stale since WP6, when the engine started pricing at solve. One line.

### 4. `test_study_one_engine.py` (claim 4)

**[N5] Sound, and each exception has a real ask.**
- **The ten `ALLOWED_EXCEPTIONS`** map to the four asks the findings note lists under "Engine asks still open":
  - Q15 ×5;
  - delete_series / pinned_by ×3;
  - the WP7 freeze ×2 (`wacc_gate`, `META_LINKS`);
  - N15 ×2 (`derive.apply_parts`, `has_parts`).
- The list cannot go stale: an unused entry fails.
- **The scan** reads `from M import x`, `import M [as y]` with attribute reads, lazy imports inside functions, and `*`. A dynamic `importlib.import_module` is not read. That is acceptable: the deleted modules would fail at runtime anyway.
- **Mutations (all red, 1 test each):**
  - MW1 (a `BillCalculator` import in `findings.py`);
  - MW6 (a non-facade name in `engine_adapter`);
  - **G1 (own):** `findings.py` lazily imports `services.finance.metrics`, a **facade** name, outside the seams → the seam test is red;
  - **G2 (own):** `routers/studies.py` uses plain `import services.commercial.tariff_engine as te` → red;
  - **G8 (own):** a used exception removed from the list → red;
  - **G9 (own):** `SolverConfig.demand_charge` re-added → red.

### 5. The QA driver and the judging rule (claim 5)

**[N6] The judging rule cannot pass a real unrecorded move** (`g10/tools/judge_probe.py`, on the driver's own evidence dump).
- `_judge` builds the WP0 section with each recorded row applied. A row's `pre` must equal the frozen value within tolerance, and its `post` replaces it. It then diffs the run against that **both ways**: a missing figure fails, and so does an extra key.
- The tolerances are 1e-9 relative + 1e-9 absolute for money, 1e-9 for rates and years, 1e-4 MW, and 1e-6 on objectives.
- **On the real run:** `[]`.
- **On perturbed copies, each flagged:**
  - NPV +1e-6 relative;
  - IRR +2e-9;
  - terminal value +1 EUR;
  - the recorded `upfront_cost_series_eur_per_mw` moving again (730,000 → 730,001);
  - a new KPI key;
  - a dropped KPI;
  - battery MW +2e-4;
  - a row whose `pre` is 1 % off (refused at the row).
- **`u2_record_pre_numbers.py`** re-derives on the engine (`golden_s5`, `seed_bills`, `toy_3month` through `u2_targets`), and `main` refuses: exit 2, "is final since U2 WP10".
- **`u2_pre_numbers.json`** is byte-identical to WP0 `361682db`; `git log` shows that commit only.
- **Mutations:**
  - **G6 (own):** a recorded delta's `post` moved by 1 EUR → `test_both_seed_bills_on_the_golden_dispatch_reproduce` red;
  - **G7 (own):** that row deleted → red.

### 6. `u2_deltas.json` (claim 6)

**[N7] Every row leads with a stable snake_case code.**
- 79 rows, 21 codes.
- `none` is the code for "no change" (rows 6 and 15). The WP6 rows carry `lp_dispatch_on_ic_chain (WP6 judges)`, which is the code plus a remark.
- The same-LP rows' largest |rel| is **2.88e-16**, as the note says.
- **Nit:** nothing pins the shape. A three-line test (`re.match(r"^[a-z0-9_]+(:| \(|$)", cause)` over every row) would keep it.

**[N8] `lp_degenerate_dispatch_ties` is genuine degeneracy, not a hidden behaviour change** (independent re-solve: `g10/tools/dump_dispatch.py` on `old` = 14d0281f with GS's wrapper and Link prices, and on `p` = the engine's `golden_dispatch`; compared by `cmp_dispatch.py`).

| | bess_2h | bess_pv_2h |
|---|---|---|
| objective GS / engine | 829,729.809424455 / **identical** | 727,163.144684475 / 727,163.1446844751 (1.6e-16) |
| battery, PV sizes | Δ 2.2e-16 / 0 | 0 / 0 |
| `grid_import`: hours shifted, max | **34**, 0.0182 MW | **22**, 0.3687 MW |
| `grid_export`: hours shifted, max | 0 | **38**, 0.5487 MW |
| storage hours shifted | 34 | 60 |
| import / export MWh | equal (Δ 0) | equal (Δ 0) |
| monthly import / export peaks | equal (≤ 3e-16) | equal (0) |
| DE energy cost (flat 130) / export credit (flat −40) | equal | equal |

- Both dispatches are optimal under the DE seed they were solved on, with identical LP objectives, so the differences are ties by definition.
- The TOU seed then rates the tied hours at its band prices: +0.51 EUR and +6.53 EUR. Since the energy line and the total move by the same amount, the export credit does not move.
- The row's numbers (34 / 22 hours, ≤ 0.018 / 0.369 MW) match my measurement.
- **Nit:** the `bess_pv_2h` rows' text names only the import shifts. The export Link also shifts in 38 hours (storage in 60). Add it.
- The other 12 WP10 rows were each checked against their test:
  - the golden table's `fom_rounded_by_pypsa_statistics` (2);
  - `lcos_is_the_engines_storage_metric` (2), under the X5 (b) identity test;
  - `c4_ledger_seeded_from_the_pinned_pack` (2);
  - `vocabulary_rename_5_4` (2);
  - the driver's `c4` and `vocabulary` rows (2, already in the driver evidence above);
  - the TOU totals (2, the same tie).

### 7. C8 and the earlier gates (claim 7)

**[N9] C8.**
- **(a) Met, and verified independently.** On `old`, the deleted loader's `seed_ledger(BESS_AT_SITE, …, load_library())` for the DE and TOU seeds equals `legacy_study_library.json` **row for row** (20 / 20, every field), and both seed tariffs are equal. The fixture records `recorded_at_commit 14d0281f`.
- **(b) Met** at WP9.
- **(c) Met:** `test_no_target_carries_pending_any_more`, G12 (own: a `pending('WP11')` in a target) → red.

**[N10] Carry-forward from earlier gates.**

| Condition | Status at 61e09fae |
|---|---|
| Stage 1 C1, C2, C3 a–d, C4, C5, C6, C7 | met (earlier gates) |
| Stage 1 **C3e** (owner accepts the IC internals the series delete reads) | **open, owner**: listed in the findings note and the plan's "Left before the PR" |
| WP6 W1, W2, W3, W5 | met (`c1e032f7`, `243cb6e7`, WP8) |
| WP6 **W4** (pre-U2 re-solve refuses) | met in code (N1); **its pinning is S1** |
| WP7 X1–X5 | met at `48d023c9`; re-based by WP10 where their oracle was the pro forma: X2 now a closed form (BC-7) plus `<` centre, the cost and rate bounds pinned to `u2_gs_bound_npvs.json`; X3 now "not engine-ready → 409"; X5 (b) on the WP0 record. All pass. |
| WP7 **X6** (the parent's C5 amendment) | **open, coordinating session through the owner** |
| WP7 N12 (rename the S4 test) | moot: the old test is deleted; the port's name matches its assertion |
| WP8a Y1–Y4, N7 | met (WP8b; N7 at WP10, `run_hashes.not_engine_solved`, pinned by `test_study_case_routes`) |
| WP8b Z1 → WP9 V1 | met: **G10 (own, the WP9 gate's G4a form):** a keyword hand-off of the notes in `tariff_engine.py` → 3 red in the parity file |
| WP8b Z2, Z3 → WP9 V2 | met: V2 (a) typed `findings_unreadable` / 409 in `tornado_runner`, (b) `TornadoContext.centre_cases` reused (code read; the implementer's MW9 / MW10) |
| WP9 V3 | met per the implementer's MW11 / 11b / 12 / 14 (not re-run by me) |
| WP8b nits (PROFORMA_ENGINE / ENGINES dropped, the N11 docstring, the non-bundle branches) | met: no hit for `PROFORMA_ENGINE` / `ENGINES`; `_capex_bound` / `_rate_bound` go through `_centre_bundle` |
| Pre-PR checklist | open: full suite (`/tmp/wt-s5` will show S1's 8 failures), `pixi run gui-qa-drivers` (I ran all three drivers green on `p`), the plan's progress, the owner's merge |

### 8. The merge (claim 8)

**[N11] Correct.**
- **`_BUNDLE_DIRS = ("uploads", "reports", "studies", "sites")`**: §8's rule plus master's `sites/`. `_STUDY_FORK_META_KEYS` and master's `_SAVE_WRITTEN_FILES` are both kept.
- **Master's new sidecar loop in `_carry_sidecars_on_move`** copies every `_BUNDLE_FILES` entry the save does not write itself (`layout.json`, the adequacy sidecars, `sites.json`, `island_config.json`, `library_refs.json`, …). On a study fork's Save-As or move, it copies **nothing study-owned**:
  - the fork owner keys live in `metadata.json`, which is save-written and excluded from the loop. They are round-tripped only by `_saves_its_own_directory`, so a copy never gains them;
  - forks never hold `studies/` (`forks.SKIPPED`);
  - the pack marker travels inside `network.nc`, as it did before the merge (N1).
- **`library_refs.json`** travelling with a fork's copy only re-pins the study's export series for the copy that references it. That is W1's "kept while used", the intended behaviour.
- **Save-As of a study's base** carries `studies/`, as GS has done since BC-4; that is not new.
- **`AppHeader.tsx`** keeps both imports.
- **The merge's own edits:**
  - `test_study_ledger_from_pack` and `test_u2_wp1_spike` read `load_defaults_pack(PACK_VERSION)` (the study's pin) instead of the newest pack. That is correct: those tests are about the pinned pack;
  - `check_bundle.ROOTED` gains the 2026-10-07 manifest.
- **The newer-pack tripwire** (`test_a_newer_vendored_pack_is_noticed_before_the_pin_is_bumped`, `test_the_bump_tripwire_guards_only_what_the_study_reads`) **passes** with 2026-10-07 vendored and the pin at 2026-10-05.

### 9. Packaging (claim 9)

**[N12] Consistent.**
- `pypsa-gui.spec` ships `study_library/load_profiles` only, plus IC's `defaults_pack/versions`.
- `check_bundle.EXPECTED` names `study_library`, `load_profiles`, `commercial_office.csv` and `industrial_two_shift.csv`; none of the deleted files.
- `test_packaging_requirements` asserts `"study_library" not in datas`, the shipped dir is `{load_profiles}`, and rebuilds the frozen layout.
- **The disk** holds `study_library/load_profiles/{commercial_office,industrial_two_shift,index}.csv` only.
- **At runtime** `study_library` appears only as the **module alias** of `services.study.library`. The one directory reader is `packs._PROFILE_DIR`.

### 10. The findings note (claim 10)

**[N13] Accurate against the code and the deltas, with stale status lines.**
- **Accurate:**
  - 79 rows;
  - the per-cause counts (my classification puts 8 / 11 in the last two buckets where the note has 9 / 10; same total);
  - ≤ 2.9e-16 for the same-LP rows;
  - LCOS +58 % on golden `bess_2h`;
  - the W4 table's three objectives, its design, and the deletions table;
  - the port map (82 → 80 targets + 2 dropped);
  - the asks.
- **Stale:**
  - the Status line says WP10 is "uncommitted" and the master merge "not yet done";
  - the package table says "uncommitted";
  - the counts (1264, vitest 536) are pre-merge.
- **Not stated:** S1 and S2. The W4 section says "tested on a stored pre-U2 fork" without saying the fork's networks are outside git.

---

## Mutations

All ran on `g10/mut`. After each, the file was restored from `p` and compared byte for byte ("restored identical: True" every time). "Red" counts failing tests in the files run.

| # | Mutation | Tests run | Result |
|---|---|---|---|
| MW1 (plan, re-run) | `from services.study.tariff import BillCalculator` in `findings.py` | one-engine | **1 red** (`test_nothing_imports_the_deleted_gs_engine`) |
| MW2 (plan, re-run) | the W4 refusal removed from `validate_for_run` | W4 file (with the netCDFs) | **6 red** (every refusal test) |
| MW3 (impl, re-run) | W4 reads only the static Link price | W4 file | **6 red** |
| MW6 (impl, re-run) | `engine_adapter` imports `lp_bindings.ENERGY_PRICE_ATTR` | one-engine | **1 red** |
| G1 (own) | a non-seam study module (`findings.py`) lazily imports a facade name | one-engine | **1 red** (seam test) |
| G2 (own) | `routers/studies.py` uses `import services.commercial.tariff_engine as te` | one-engine | **1 red** |
| G3 (own) | W4 skipped when a commercial config is present | W4 file | **1 red** (`commercial_added`) |
| G4 (own) | W4 a warning, not an error | W4 file | **6 red** |
| G5 (own) | W4 detects only positive prices (misses the export credit) | W4 file | **1 red** (names both Links) |
| G6 (own) | a recorded delta's `post` +1 EUR (TOU `bess_2h` energy) | seed-bills reproduce | **1 red** |
| G7 (own) | that delta row deleted | seed-bills reproduce | **1 red** |
| G8 (own) | a used `ALLOWED_EXCEPTIONS` entry removed | one-engine | **1 red** |
| G9 (own) | `SolverConfig.demand_charge` re-added | one-engine | **1 red** |
| G10 (own, WP9 V1) | the notes handed to a helper by keyword in `tariff_engine.py` | parity | **3 red** |
| G11 (own) | `pre_u2.METER_LINKS` loses `grid_export` | W4 file | **2 red** (names test, pack-names pin) |
| G12 (own, C8 c) | a target calls `pending('WP11')` | port-map complete | **1 red** |

On the **committed** tree, MW2–MW3, G3–G5 and G11 cannot be seen: the W4 file is already red there (S1).

### Probes (`g10/tools`)

| # | Probe | Result |
|---|---|---|
| P1 | `w4_routes.py`: the fixture fork through save, load, preflight, `/run`, Save-As, Save-a-Copy, scenario, bundle, renamed Links | refused on every path; the marker kept; only both-renamed bypasses (N1) |
| P2 | `w4_inproc.py`: a post-U2 solve, its round trip, a user price on it, static / one-hour / NaN / string meta | clean after the solve; **false positive** with a user price (S2); the rest as expected |
| P3 | `dump_dispatch.py` + `cmp_dispatch.py`: GS (14d0281f) vs the engine golden dispatch | genuine ties (N8) |
| P4 | `judge_probe.py`: the `_judge` rule on the run's evidence and 8 perturbations | `[]`, then 8 / 8 flagged (N6) |
| P5 | `legacy_check.py`: the 14d0281f loader vs `legacy_study_library.json` | equal row for row; tariffs equal (N9) |
| P6 | the recorder's `main` | refuses, exit 2 |
| P7 | the tripwire tests with 2026-10-07 vendored | pass |

---

## Carry-forward

**Before the PR (the plan's pre-PR checklist):**
- U1 and U2 below.
- The full backend suite on a **clean** checkout. It must be green including `test_u2_wp10_pre_u2_resolve.py`.
- Master's own red packaging test (`pilot_evaluation_plugin.py` imports `pytest` unguarded; it fails on `fceb886e` too). It is not U2's, but it will fail the PR's CI, so the owner names who fixes it.
- `pixi run gui-qa-drivers`.
- Tick the plan's checklist.

**With the owner:**
- C3e;
- X6 (the coordinating session's C5 amendment);
- the engine asks: Q15, delete_series / pinned_by, the WP7 freeze, N15, `scale_capex(asset=, part=)`, unrounded statistics, the dispatch-record helper, `put_flat_export_series(label=)`, the cash-identity structure.

**U3 and later:**
- the U3 outline;
- "defaults updated" for legacy ledgers;
- row 30 non-zero and row 31 `eu_de`;
- the pre-WP9 CSV header for the MVP-2 importer;
- W2's reading of the parent's report-workbook row.

---

## Binding conditions

- **U1 — Commit the stored pre-U2 fixture (before the PR; blocking).**
  - Add `tests/fixtures/pre_u2_fork/base/network.nc` and `fork/network.nc` to git. Either `git add -f` them, or add `!pypsa-gui/backend/tests/fixtures/pre_u2_fork/**/network.nc` to `.gitignore`.
  - Use the main tree's copies (sha256 `6116c2a3…` and `dbdf588a…`): they cannot be regenerated, because the recorder needs the deleted code. Keep a backup until the commit is pushed.
  - Then re-run `test_u2_wp10_pre_u2_resolve.py` on a fresh `git archive` of the new head: 10 passed, and MW2 → 6 red on that archive.
  - Make the test fail with a clear message when the fixture is absent. Today a bare `FileNotFoundError` from netCDF reads like an environment problem.
  - Record the fixture's sha256 in `make_pre_u2_fork.py`'s docstring.

- **U2 — The W4 refusal says something true on every network it refuses (before the PR, or the owner accepts it as recorded).**
  - (a) Mark the networks the study writes since U2: for example, `n.meta[PACK_META_KEY]["tariff_on"] = "engine"` in `packs.build_site_network`. Refuse only pack networks **without** that mark. Add a test: a post-U2 fork with a user-typed `grid_import` price is not refused as pre-U2.
  - (b) Give the message a way out that works for any copy and for a pre-U2 **base**: "remove the prices from 'grid_import' and 'grid_export' and set the tariff in the commercial settings, or, for a study's option, re-run the study". Today the re-run does not rewrite a pre-U2 study's base network (S2 b).
  - The study's own HELP / `ERROR_COPY` entry for the code, if it gains one, follows in both mirrors.

- **U3 — Record (not blocking; one line each).**
  - The `bess_pv_2h` tie rows: say the export Link shifts in 38 hours (storage 60) as well.
  - Update the findings note's and the plan's status lines: committed, merged at `61e09fae`, post-merge counts. Add S1's fix and the W4 limits: both Links renamed bypasses; S2.
  - The `grid_supply` exemption's stale docstring (N4).
  - Optionally, a test that pins the `u2_deltas.json` cause shape (N7).
