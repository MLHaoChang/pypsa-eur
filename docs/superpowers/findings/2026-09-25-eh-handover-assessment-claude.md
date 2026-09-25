# EH reference design — handover assessment + e2e bugfixes (Claude)

**Date:** 2026-09-25
**Input:** Cursor handover `docs/superpowers/handovers/2026-09-25-eh-reference-design-claude-handover.md` (on PR [#52](https://github.com/MLHaoChang/pypsa-eur/pull/52), branch `cursor/eh-stack-e2e-seal-ef60`). The `/opt/cursor/artifacts/…` mirror is not reachable from this environment; the handover says it has the same content.
**Base:** `master` @ `ec233027` (post #51)
**Branch:** `claude/epic-allen-k2t1c4`

## 1. Handover claims: reproduced

| Claim | Result here |
|---|---|
| Backend `test_energy_hub_*` + sweep + chat + campaign = 247 | **247 passed** (Python 3.12, pinned `gui-requirements.txt` stack) |
| `test_energy_hub_study_http.py` = 10 | **10 passed** |
| Frontend EH panels (4 files) = 108 | **108 passed** |

Caveat: `test_energy_hub_study_http.py` **stubs `run_eh_study`**. Before this change no test drove the real pipeline over HTTP, so the seal's "257 green" never exercised the path a user takes (default packs, session config, foreground state). Driving it unstubbed found the bugs below.

Environment note: the repo needs **Python ≥ 3.12**. `gridspine/drivers/year_study.py:200` uses PEP 701 nested f-string quotes, so the whole backend fails to import on 3.11.

## 2. Bugs found by e2e (fixed on this branch)

| # | Bug | Impact | Fix |
|---|---|---|---|
| 1 | `run_eh_study` wrote the pack's ENS cap onto the **session** `SolverConfig` and never restored it | The user's next ordinary solve silently ran ENS-capped at 10‱ / 5‱ | The study works on a private `copy.copy(cfg)` |
| 2 | The pack patch applied only when the session cap was `None`. A session cap that was already set won for `ens_solve`, but the report header, redundancy/levers stages, and the achieved-‱ inversion used the pack's | The report said "10‱" for a solve run at another cap, and `achieved_ens_permyriad` was off by the ratio of the caps (e.g. 800‱ reported for a real 4000‱) | The pack target is authoritative for every stage; achieved ‱ is inverted with the cap actually solved at |
| 3 | `ens_solve` solved the **shared network** under the pack overlay and published `adequacy_report` / `last_lost_load` into the foreground result state | After an `off_grid` study, `/results/adequacy` and the network's dispatch described an islanded solve the user never ran, while the Links had been "undone". This broke the frontier `_restore_base` rule | The pipeline runs on a private `network.copy()` with a private sink; foreground state and the shared network are untouched |
| 4 | `budget_solves` was recorded but **never enforced** (budget 2 → 4 solves) | The chat campaign charges `eh_study` its `budget_solves` as an LP ceiling, so campaign accounting was unsound | `max_solves` on the redundancy / levers / DtC stress / DtC planning loops; the orchestrator passes the remaining budget; a stage with no budget left is `skipped` and its section is `not_established` with a note |
| 5 | Unknown stage names were accepted (`["bogus"]` gave a no-op study with status `done`), and lists without `apply_pack`/`ens_solve` produced a report labelled with an archetype whose pack never ran | Misleading reports; chat could pass anything | `validate_stages` (unknown → 422; `apply_pack` + `ens_solve` required per decision 18); `EH_STAGE_ENUM` in the chat schema; `budget_solves` bounds in the schema |
| 6 | An infeasible / failed `ens_solve` was reported as a **user abort** (`pipeline.aborted=true`, study `aborted`, panel says "Stopped"). Unreached stages stayed `pending`, and target/cost carried no reason | With the default `weak_flexible` pack (10‱, 50 MW import) on the MVP-B fixture, the study looked user-stopped instead of "target not achievable" | New stage status `failed`; the study record is `failed` with `error` and keeps the partial report; target/cost/sizing/tea/multi_energy are `not_established` with the reason; unreached stages are `skipped` with a reason (never `pending`) |
| 7 | Sibling tables (`eh_redundancy_comparison`, `eh_lever_comparison`, `eh_dtc_stress`, `eh_dtc_planning`) and the previous report survived into the next study | The panel showed the last archetype's lever/DtC tables beside a new report | Cleared at study start |
| 8 | FE: while a new study ran, the previous report and tables stayed on screen under "Studying…". Section notes were never shown, so every `not_established` had no stated reason | Stale, unexplained panel state | Report/tables hidden while running; chips carry the note as `title`; a "why not established" list; solves `consumed / budget` readout |

Tests: `tests/test_energy_hub_study_isolation.py` (14, incl. **live HTTP** runs without stubs), 3 more cases in `test_chat_adequacy_tools.py`, and 3 more panel tests. All were shown red before the fix.

## 3. Requirements vs implementation (spec `2026-09-14-eh-reference-design.md`)

| Requirement | Status | Evidence |
|---|---|---|
| D1/D2: plan on ENS, **certify on MC LOLE**; LOLE failure fails certification | **Not met** | The `mc_certify` stage is never executed (`IMPLEMENTED` excludes it); `mc_lole_h` is never populated. MVP-B tests pass only by overriding `mc_certify_required=False` (every case in `test_energy_hub_mvp_b.py`) |
| §3: MC LOLE certify **required for MVP-B** (weak_flexible, off_grid) | **Not met** (see above) | For `weak_flexible` the missing certification appears only in a pipeline-stage note: the `gates` section is taken by SCR |
| D18 default stages incl. `frontier`, `fmea_top` | **Stubs** | Both always `skipped` ("not implemented in P1.5 sync driver"). The frontier engine exists (`services/adequacy/frontier.py`) but is not wired |
| §3 strong_grid "primary deliverable: frontier + least-cost plan" | **Partial** | Least-cost plan at target yes; frontier no |
| §9 "every point on an EH frontier gets its own FMEA ranking" | **Not met** | Follows from `fmea_top` / `frontier` being stubs |
| D15 DSR opt-in with double-count preflight | **Not wired** | `solver_config_patch_with_preflight` exists but nothing calls it. `weak_flexible.dsr_opt_in=True` has no effect and no warning reaches the report |
| §6 / P3c energy import cap (`import_energy_mwh_per_year`) | **Not met** | Still reserved (warn-only); lever kinds are `import_cap` (MW) and `storage_duration` only |
| D13 configurable outputs (section inclusion / export columns) | **Backend only** | `stages` + `EXPORT_KEYS` exist; the panel has per-table CSV but no stage selection and no whole-report export |
| D17 budget default 30 / max 120 | **Met (now)** | Enforced as of this branch |
| D16 single assembler, §4 completeness enum, §6 import overlays, D8/§10 DtC stress + planning, P3a/b/c, P5 TEA, P6a/b, P7, P8a, P9 | **Met** | Suites green; e2e HTTP runs behave |

## 4. Additional TODOs (derived)

Priority is my recommendation; the product owner decides.

1. **Wire `mc_certify`** into `run_eh_study` (reuse `mc.py` / MC loop runner, count draws against the budget as the campaign does), populate `mc_lole_h`, and apply decision 2 (LOLE fail → certification fail). Until then, say plainly in the plan and handover that MVP-B certification is not delivered, and give the missing-MC fact its own report field for `weak_flexible`.
2. **Wire `frontier`** (budget-capped ε-sweep via `frontier.py`) and **`fmea_top`** (Link-primary Class-B sweep top-N), or drop them from the default stages so the report stops advertising them.
3. **Pack parameters over HTTP / UI / chat.** Today only `archetype`, `stages` and `budget_solves` are accepted: no ENS target, import MW, `dtc_config`, or DSR buses. The default `weak_flexible` pack (10‱ with a 50 MW import cap) is infeasible on the repo's own MVP-B fixture. Every live test overrides the pack in Python, so the user-reachable path had no coverage until now.
4. **Network tagging UI** for `eh_role`, `eh_poc`, `eh_critical`, `eh_sk_mva` / `eh_ibr_mva`. Without it, import selection falls back to carrier matching, and DtC / SCR can't be established from the GUI. This is a larger authoring hole than Class-C alone and should sit next to the Class-C authoring UI (handover priority 1).
5. **DSR preflight**: call `solver_config_patch_with_preflight` for `dsr_opt_in` packs and put its warnings on the `apply_pack` stage note / report.
6. **Energy import cap** GlobalConstraint overlay (spec §6 → P3c), or formally re-scope it in the spec.
7. **Revisit DtC attribution after P6(b).** Per-Load VOLL slacks now exist, so decision 8 / §10's "one slack per bus" premise no longer holds. DtC could report per-critical-Load unmet energy and drop the different-bus requirement.
8. **Pipeline UI**: render per-stage status/notes (the panel now shows solves and not-established reasons, but not the stage table), plus a whole-report JSON/CSV export (`export_reference_design`).
9. **Found in plan review, not yet fixed:**
   - DtC "fixed-plan" stress re-solves with extendables still free (`dtc.py:227-245`, no `freeze_capacities`), so it is not decision 8's stress-on-fixed-plan.
   - The MC engine is copper-plate: `off_grid` certification must exclude generation beyond the islanded import Links.
   - The `fmea_sweep` campaign estimate undercounts base + restore solves.
   Plan: [`plans/2026-09-25-eh-post-seal-implementation.md`](../plans/2026-09-25-eh-post-seal-implementation.md) (P10b, P11, P10c).
10. Keep the handover's own deferrals: Class-C authoring UI, climate P8(b), spare-lead modifier, planned-outage MC.
11. CI: make sure the GUI CI runs on Python ≥ 3.12, and add at least one **unstubbed** HTTP EH study test to the seal set (now in `test_energy_hub_study_isolation.py`).

## 5. Verification (this branch)

| Surface | Result |
|---|---|
| EH seal set (`test_energy_hub_*` incl. new isolation file, sweep, chat, campaign) | **261 passed** (247 + 14 new) |
| `test_energy_hub_study_http.py` | **10 passed** |
| Full backend, `-m "not slow"` | **5754 passed**. 15 failed only because the pip venv lacked `pywebview` / `python-magic` / `psycopg` / `ruff`; all 15 pass once those are installed. The pixi `test` env ships them |
| Full frontend (`vitest --run`) | **1943 passed** (177 files); `tsc --noEmit` clean |

```bash
cd pypsa-gui/backend
PYTHONPATH=<repo>:<backend> python -m pytest \
  tests/test_energy_hub_*.py tests/test_adequacy_sweep.py \
  tests/test_chat_adequacy_tools.py tests/test_adequacy_campaign.py -q
cd ../frontend && npx vitest --run src/pages/results/EhReferenceDesignPanel.test.tsx \
  src/pages/results/adequacy.test.tsx src/pages/results/McPanel.test.tsx \
  src/pages/results/AdequacyTab.test.tsx && npx tsc --noEmit -p .
```
