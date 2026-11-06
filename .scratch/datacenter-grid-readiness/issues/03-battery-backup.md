# Battery-based backup in place of diesel

Status: ready-for-agent
Model: Opus
Type: task
Blocked by: 01
Gated: yes (adequacy, finance)
Model tier: opus
Size: M

## What to build

- A backup-mode setting on the DC load: `genset | bess | hybrid`.
- BESS sizing for ride-through plus N hours of autonomy for the critical share, honouring redundancy (N, N+1, 2N).
- The DtC stress re-dispatch proves critical load is served on loss of grid.
- Investment case: avoided genset capex/fuel/run-hours against BESS capex, augmentation and fade (`BessSpec` already has them).

## Done when

A DC project can switch to `bess` and see adequacy plus the cost delta against `genset`.

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Files: DC load model and template parameters, `services/adequacy/` (DtC stress re-dispatch), `BessSpec` sizing and finance. Blockers: 01 is done (#421). Reviewers: GK A (adequacy) and GK B (finance). Unblocks 05, 06, 07.

### 2026-11-06: plan verdicts (session_01G7qCunRZs4jhG7eAZytnEp)

GK A (13), adequacy: GO. Q1 (a) an optional outage window on `DtcConfig` (None means today's path, byte-identical). Q2 (a) a stored setting plus an assessment on a copy; the saved network is never mutated. Conditions:
1. The windowed zeroing keeps each link's own p_*_pu outside the window. Undo restores exactly: a created links_t column is dropped. Tested with and without a pre-existing series.
2. Window placement:
   - Length is in hours via snapshot weightings, never by index.
   - It starts at the max rolling critical-energy span; start and energy are reported.
   - A window longer than the horizon is refused (422); one that runs past the end wraps or shifts, never clipped.
   - Tested on a profile whose peak hour is outside the max-energy window.
3. The strings carry usable energy only (nameplate × DoD × retention), with η from the same RTE split as the sizing. Proof: exactly sized serves, 0.95× fails.
4. Backup holds its charge outside an outage: discharge is off in normal solves and allowed only in the stress window. SoC is pinned full at the window start, or the perfect-foresight note is kept. Tested: normal-solve discharge = 0.
5. Sizing:
   - S = max (P_c − g)⁺ with g from `site_peak.firm_genset_mw`.
   - Ride-through seconds are in energy only; the hourly LP cannot prove them.
   - The fade arm names its cycle count.
   - Tested: 2N differs from N+1 + 1.
6. Sidecar:
   - The bess sidecar passes `gridspine.schema.island` validation.
   - site_peak's peak check and outage reserve read the new fleet, tested in bess and hybrid mode.
   - Genset mode keeps byte-identity for every template.
7. Routes:
   - The route-policy tests pass at the trial merge.
   - The PUT validates like the create body.
   - Revision or solve-time reads are shown.
   - assess uses `_solver_config_from_dict`, `build_lp_chain` and `PyPSAService.solving_context`, and names any pre-solve step it does not reproduce.
   - network.nc and eh_template.json are byte-equal around assess.
8. An unreadable critical-unmet is "not shown", never "served".
9. If the window is persisted where fingerprints hash it, it is appended only when set, with a pin test; otherwise the PR says it is assess-only.

GK B (25), finance: GO WITH CHANGES, option (a). A standalone `backup_cost_delta` uses `finance.metrics.npv`; the IC engine is untouched; it folds in under DC-05.
- F1 The genset fleet uses the same N/N+1/2N function as the adequacy sizing. A test shows both read the same MW.
- F2 One horizon-end convention for both legs, named. Hand test with a repurchase in the final year.
- F3 The rate basis matches the IC engine's real/nominal handling. The 7 % fallback is assumed and real. Tested with inflation set.
- F4 Units on every row. A pack fuel row is used if present. Assumed values are labelled. Hand test: fuel = h × MW × heat rate × price.
- F5 Fail closed (typed refusal) on a missing pack key, blank fade, analysis_years < 1 or a non-finite input. One mutant goes RED.
- F6 Hybrid legs count only their own share; the parts sum to the whole.
- F7 `none` puts the up-sized MWh in capex and buys nothing later. restore and fixed_years are hand-tested on the same fade curve.
- F8 BackupSpec fields that are not LP inputs stay out of `_network_input_fingerprint`, with a test; agreed with GK A.

### 2026-11-06: GK A (13) rulings after the plan verdicts

1. F8 agreed, with a stronger test. Changing `dc_load.backup` through the PUT, valid or invalid, leaves all three byte-equal: `_network_input_fingerprint`, the investment-case digest and network.nc. A project created with backup=bess needs no extra pin.
2. Window hours (corrects condition 2): elapsed hours are counted with `snapshot_weightings.stores`, never `generators` (the annualising 52× in the DC templates). A DC-template test checks the window holds exactly ceil(autonomy_h / stores weight) snapshots.
3. site_peak shaving excludes the `backup_bess_*` strings, and every storage the sidecar lists as backup, from its shaving term; they count only toward the outage reserve. Test: the DC template in bess mode shaves the same as with the strings removed. This site_peak change is allowed for this purpose only.

### 2026-11-06: DC-03 (2) progress (session_01G7qCuia7CuUNr1UaxfxeSd)

- The 4 failing sidecar tests passed gridspine's validated form (values flattened into `requirements.values`) to `site_peak`, which reads the raw `{value, source}` sidecar as the screen does. The test now passes the raw sidecar; no code change.
- Routes: `PUT /api/campus-electrical/{name}/dc-backup` and `POST .../dc-backup/assess`, both lock-checked; 422 `{error_kind: dc_backup_invalid, field}`. Chat disposition `dc_backup` in `services/not_via_chat.py` (unbuilt).
- Ruling 1: route test over a valid and three invalid PUTs asserts fingerprint, IC digest (`_ic_current_assumptions`) and network.nc byte-equal.
- Ruling 2: `dc_backup.network_window` is the one place the window reads weightings (`stores`); tested on both DC templates.
- Ruling 3: `site_peak.without_backup` drops the strings from the shaving set only; the sidecar keeps them in `ride_through_storage`. The test tightens the import to 97 % of the peak so the check binds; the mutant (shaving on the raw sidecar) turns it RED.
- GK A 6: `write_sidecars` per `TEMPLATE_META` id against `project_templates/<id>/`: 9 templates, 40 files, 0 mismatches.

### 2026-11-06: UX (9) GO and review fixes (session_01G7qCuia7CuUNr1UaxfxeSd, PR #1172)

UX (9) GO, with A (save on click; a refusal leaves the stored mode selected) and B (assess the stored mode only). Rules:
1. "Assessing…" while it runs; a result for another mode reads "Out of date".
2. Sign words and no "+/−" ("saves € 1.2 m"); the redundancy tier in words; nbsp units; plurals; the not-shown reason after a colon.
3. Guided drops the lines table; "assumed" marks are visible in both modes.
4. Shots of each state. All are done; the shots are `.scratch/datacenter-grid-readiness/shots/dc03-*-{guided,expert}.png`.

mp-code-review fixes:
- GK A 7: the assess result has `basis {network_saved_at, network_sha256, not_reproduced}`, and the card shows it.
- "Usable" MWh now reads the usable per-string energy.
- No repr quotes and no ticket id in user text.
- `fixed_years` now sizes the nameplate for the longest gap between augmentations, not the whole horizon. Before, it paid the fade twice. Tested, and the mutant goes RED.

Open for the reviewers:
- GK A: a project *created* with backup=bess has the strings in network.nc, so assess refuses it ("assess a campus in genset form"). A PUT changes only the stored mode, and network.nc stays genset form; that is the path assess serves.
- GK A ruling 3: gridspine's island schema rejects a backup list (unknown field in requirements and in units), so the `backup_bess_` name prefix is the only backup marker. site_peak drops the strings from both the shaving set and its reserve figure, which matches the ruling's test (the same shaving as with the strings removed).
- GK B: F3: a missing `inflation_rate` is passed as 0, so the rate reads as real; the IC engine flags this case instead. F4: there is no pack fuel row today, and none is looked up. F6: the hybrid genset leg is the full fleet on the critical peak, so the delta is the strings alone. F7: retention compounds and augmentation is linear.
