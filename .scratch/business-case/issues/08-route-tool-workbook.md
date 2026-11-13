<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 — Route, chat tools and workbook sheet

Status: ready-for-agent (blocked by 05; Opus; GK B)
Type: task
Blocked by: 05
Gated: yes (GK B)
Model tier: opus
Size: M

Read [the spec](../spec.md) §7, `harness/README.md`, the DC
[05](../../dc-business-case/issues/05-route-tool-workbook.md) it replaces and GK B
BC10 first.

## What to build

1. `POST /api/results/business_case` (after an IC exists; else "run the
   investment case first"), `GET` the stored result; the IC route's job lifecycle
   and abort; `PUT` the inputs (lines) with 02's validation.
2. Chat tools `run_business_case`, `get_business_case`,
   `set_business_case_lines` (fills rows from the user's words; never invents a
   price); catalogue rows, route-parity declarations
   (`assistant_route_parity_declarations.d/`), error kinds (`tool-error-kinds.d/`).
3. Workbook "Business case" sheet from the stored result, else `not_established`
   with "run the business case" (RW-KPI-1).

## Tests

BC10: route ACL, `route_chat_disposition`, write-surface lock, the error-kind
fragment, inventory, route-parity and control-matrix `--check`s; solver config
through `_solver_config_from_dict` (#1009).

## Comments

### 2026-11-11: triage

Checked on master: `services/finance/business_case.py` and `project_templates/business_case_presets/` do not exist; 01 and 02 are done (#1322, #1320); business-case 04 is in progress in another session and was left alone. Verdict: ready-for-agent, held by its Blocked by line. Likely files, tests and acceptance are in the body.

### 2026-11-13: GK B (53) plan GO, two slices (08a route/job/workbook, then 08b chat tools)

- Q1 lease-up volume factor: out of BC 08, follow-up issue. Until then #1430 F2 holds: a year-varying linked_share is a commercial sold share only; nothing in 08a/08b offers or words it as physical lease-up; the 08b card labels it "sold share".
- Q2 `ebitda_margin`: no change. `business_case.py:789` is the per-year P&L row (None only when ebitda is); the KPIs `ebitda_margin_gross`/`_net` are gross/net of pass-through. No alias.
- Q3: two slices.
- 08a C1 fingerprint: `business_case` changes leave `_network_input_fingerprint` (exact and non-exact), `goal_free_config`, the IC digest and the stored IC hash unchanged (BC1 pin). The PR body states how a BC-input write invalidates the stored BC result (staleness on its own inputs hash, tested) and whether it bumps the network revision.
- C2 one config reader: PUT and POST read through `_solver_config_from_dict` (#1009); legacy dict without `business_case` loads; an unbuildable one is 422.
- C3 IC freshness: `investment_case_missing` for no report, a stale report, and a hash/assumptions mismatch with the rebuilt case, through the IC's own staleness reader; a test each, red under a mutant.
- C4 same solve: linked outputs and lost production from the IC report's own solve; shortfalls through `process_products.shortfalls` with that solve's `last_lost_load` (#1429); network edited after the IC → refuse or stale, never mixed.
- C5 job: mirror `start_investment_case` (`_refuse_if_mesh_busy`, abort); start check and job creation under one cross-process lock or unique key (#1401 T1), two concurrent POSTs tested; the 409 while a solve runs checked under the lock (#1378 K1b).
- C6 refusals: typed 422/409, kinds in the fragment; `DefaultUnitMismatch` fail-closed at its catching site with a base-class mutant killed; break_even_* and `kw_per_gpu_missing` read not_established (KPI, workbook, API), tested.
- C7 workbook: nothing stored → `not_established` with reason, export never runs it; stale stored result → says stale.
- C8 PUT runs exactly the `BusinessCaseInputs` validators; no bulk path skips one (#738).
- C9 at the trial merge: the four route-add tests, `test_packaging_requirements` on new imports, the error-kind manifest tests, inventory / route-parity / control-matrix `--check`.
- 08b (plan delta before building): the card shows every digest-bound value (price, unit, linked_share, each year of linked_share_by_year, default_units, default_keys, kw_per_gpu; #1379 F2); a grant freezes only what the card showed; prices only from the user's words, bound at the grant (#1072), model-supplied price not on the card refuses; no `*project*` argument but the session's own (BOUND_CONTEXT_TOOLS + #875 test if a run profile lists them); a runs entry for the harness's own step never admits `run_business_case` on a model turn (#1123 / SH-14 B2); solves counted, plan delta states whether it solves.

### 2026-11-13: GK B (54) 08b plan GO, D1–D9

- Q1 (a), refined: a price needs a citation only when it is new or changed against the stored config; an unchanged price keeps its stored source. The citation is anything `run_intake` accepts (the owner's own typed text, an upload's sheet/cell, a PDF page/quote), checked through its verifier, never a fork. Anything else refuses `price_not_from_user`. This enforces "never invents a price" without an owner question.
- Q2 (a): `abort_business_case`, mirroring `abort_investment_case`. Q3 (a): the preset stays out of chat.
- D1 card in both modes: a diff against the stored config (each line added, changed old → new, or removed; a removal is never silent); each price's source (cited text, upload ref, "preset default (assumed)", "unchanged"); the digest binds exactly the rows shown; an oversize card refuses `business_case_card_too_large`.
- D2 edit-card tests: one per digest-bound value (changed after preview → `edit_preview_stale`); one per citation path; each refusal (`price_not_from_user`, a quote not in the turn, another author's turn, a number absent from the quote) red under its mutant.
- D3 `if_match` required; empty or `*` refused tool-side, as `_finance_request`; `business_case_changed` maps to its kind; tested through the tool.
- D4 `get_business_case`: stale reads stale with what changed; not_established stays not_established; tested. A result that can exceed the tool-result cap says it is truncated and lists what was cut (#927, #1079).
- D5 no project argument; `run_business_case` and `abort_business_case` in `run_envelope.NEVER_LISTED`, pinned; BOUND_CONTEXT ∩ NEVER_LISTED stays `{run_eh_study}`; `campaign.CHARGEABLE` at 0 solves, with a test that the run performs no solve.
- D6 job: job_adapters monitor and wait-to-done, job_holds INPROC count 29 → 30, activity_history; tested.
- D7 error kinds only in the `.d` fragment; at the trial merge `test_tool_error_kind_manifest`, `ChatPanel.manifest.test.tsx` and `smoke/json_fragments.py`; a route-parity `.d` only if the check asks.
- D8 not_via_chat narrowed to the preset; "show the business case for …" reaches `get_business_case`; the BAU non-hits re-pinned.
- D9 C9 again at the trial merge (route_chat_disposition, write_surface_lock_policy, project_route_acl_policy, golden_coverage, schema match, endpoint map, inventory / route_parity / control_matrix `--check`); the body lists the mutants killed and survived.
- Lease-up stays out; `linked_share` reads "sold share" everywhere.
