# Handover — Energy Hub reference-design gap closure (for Claude)

**Audience:** Next Claude / Cursor agent continuing EH work for hao (PM).  
**Authoring agent:** Cursor cloud run [Energy hub reference design](https://cursor.com/agents/bc-877e9041-107d-465a-8e97-04a2c77aef60) (`bc-877e9041-107d-465a-8e97-04a2c77aef60`).  
**Date:** 2026-09-25  
**Repo:** `MLHaoChang/pypsa-eur`  
**Primary product surface:** `pypsa-gui/` (FastAPI + React). Do not touch `gui_streamlit/` unless asked.

---

## 1. One-paragraph status

EH gap-plan **v1 is sealed on `master`**. Complete stack (#48), P6(a) dedicated-bus multi-energy (#49), P6(b) per-Load VOLL slacks (#50), and chat tools (#51) are **merged**. Local e2e on `master@ec233027`: **257** backend + **108** frontend tests green ([seal finding](../findings/2026-09-25-eh-stack-e2e-seal.md)). Docs seal PR [#52](https://github.com/MLHaoChang/pypsa-eur/pull/52) may still be open. **Next product slice:** Class-C authoring UI. Still deferred: climate P8(b), spare-lead modifier, planned-outage MC.

---

## 2. Canonical documents (read these first)

| Doc | Role |
|---|---|
| [`docs/superpowers/specs/2026-09-14-eh-reference-design.md`](../specs/2026-09-14-eh-reference-design.md) | Product/architecture decisions (binding) |
| [`docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md`](../plans/2026-09-14-eh-reference-design-gaps.md) | Phase checklist + integration banner |
| [`docs/superpowers/specs/2026-09-15-eh-reference-design-panel.md`](../specs/2026-09-15-eh-reference-design-panel.md) | FE Adequacy panel (chat tools no longer a non-goal) |
| [`pypsa-gui/backend/models/energy_hub.py`](../../../pypsa-gui/backend/models/energy_hub.py) | Contracts / archetypes / pipeline stages |
| [`.cursor/skills/gui-backend-change/SKILL.md`](../../../.cursor/skills/gui-backend-change/SKILL.md) | Where EH runners / chat tools live |

**Working rules that shaped all slices**

- TDD + independent QA **GO / GO WITH BINDING / NO-GO** before treating a phase closed.
- **No Class-B rebuild** (already shipped). Dynamics = **gate not co-opt**. EMT = **flag only**.
- Prefer thin adequacy services; extend `services/adequacy/*`; chat tools call route handlers with `_h` alias.
- Branches: `cursor/<name>-ef60`; PRs via ManagePullRequest; preferred base recently `master`.

---

## 3. Merged PR map (chronological)

### Substrate (pre–EH packaging)

| PR | What |
|---|---|
| [#5](https://github.com/MLHaoChang/pypsa-eur/pull/5) | Solution FMEA stack (targets, MC/ELCC, frontier, loops, worksheet) |
| [#21](https://github.com/MLHaoChang/pypsa-eur/pull/21) | Chat reliability tools + campaign budget + study write-up |

### EH MVP-A / MVP-B and phase PRs (landed via #48)

| PR | Phase | What |
|---|---|---|
| [#39](https://github.com/MLHaoChang/pypsa-eur/pull/39) | P0–P5 core | Archetypes, runner, redundancy/levers/DtC, report |
| [#41](https://github.com/MLHaoChang/pypsa-eur/pull/41) | P2 | Class-B Link residuals only |
| [#42](https://github.com/MLHaoChang/pypsa-eur/pull/42) | P9 | Thin SCR warn gate (`weak_flexible`) |
| [#43](https://github.com/MLHaoChang/pypsa-eur/pull/43) | P7 BE | RAM v1 `rate_source` / `library_citation` |
| [#44](https://github.com/MLHaoChang/pypsa-eur/pull/44) | P8a | Class-C `profiles` runner + synthetic fixtures |
| [#46](https://github.com/MLHaoChang/pypsa-eur/pull/46) | P3a docs | QA gate cleared |
| [#47](https://github.com/MLHaoChang/pypsa-eur/pull/47) | P7 FE | COPT/MC provenance chips |
| [#48](https://github.com/MLHaoChang/pypsa-eur/pull/48) | Stack e2e | Merge train → master |

Also on that train: FE EH panel + SCR strip (see plan assessor links / AdequacyTab).

### Post–#48 slices (this agent lineage)

| PR | Branch | What | Status |
|---|---|---|---|
| [#49](https://github.com/MLHaoChang/pypsa-eur/pull/49) | `cursor/eh-p6-multi-energy-ef60` | **P6(a)** dedicated-bus multi-energy ENS | **Merged** |
| [#50](https://github.com/MLHaoChang/pypsa-eur/pull/50) | `cursor/eh-p6b-multislack-spike-ef60` | **P6(b)** per-Load VOLL slacks | **Merged** |
| [#51](https://github.com/MLHaoChang/pypsa-eur/pull/51) | `cursor/eh-chat-tools-ef60` | **Chat tools** EH start/status/report/abort | **Merged** `ec233027` |
| [#52](https://github.com/MLHaoChang/pypsa-eur/pull/52) | `cursor/eh-stack-e2e-seal-ef60` | Docs: stack e2e seal + this handover | Open / docs |

---

## 4. What each late slice changed (implementation detail)

### 4.1 P6(a) — dedicated-bus multi-energy (#49)

**Honesty rule:** never invent per-Load ENS from bus-level shed on shared-bus models.

- New: `services/adequacy/multi_energy.py`
- Wired into `eh_study` / report section `multi_energy`
- Section honesty: `dedicated_bus_by_carrier`; shared-bus → `not_established`
- Electrical ENS / FMEA default path **unchanged**
- CI hygiene on same PR: DtC uses `VOLL_SLACK_PREFIX` / `involuntary_slack_mask`; golden coverage EH routes; `scr_gate` ImportError guard for frozen apps
- Tests: `tests/test_energy_hub_multi_energy.py`
- Spike: [`findings/2026-09-19-eh-p6-multi-energy-spike.md`](../findings/2026-09-19-eh-p6-multi-energy-spike.md)
- Fixture pitfall: starved H₂ gen must be **omitted** (not `p_nom=0` — `generator_p_nom_invalid`)

### 4.2 P6(b) — per-Load VOLL slacks (#50)

**Product chose option C** (per-Load slack), not D (per bus×carrier).

- Slack naming: `voll_slack_name(load_id)` → `__voll_<load_id>` (sole owner: `services/adequacy/slack.py`)
- Creation: `services/solver/assumptions.py` — one involuntary slack **per Load**, sized from that Load’s `p_set`
- Capture: `lost_load_t` / `lost_load_load_period_mwh` keyed by **Load**; `lost_load_bus_period_mwh` retained as roll-up for DtC / electrical consumers
- `multi_energy`: prefers `per_load_slack` when Load-period capture present
- `metrics.electrical_columns`: Load-aware (and bus columns)
- Tests: `tests/test_energy_hub_multi_energy_p6b.py`; ENS-cap / DSR tests updated for Load-id columns
- Shared-bus tight ENS cap can be infeasible — tests use looser/no cap where needed
- Spike + ship note: [`findings/2026-09-24-eh-p6b-multislack-spike.md`](../findings/2026-09-24-eh-p6b-multislack-spike.md)

### 4.3 Chat tools (#51)

Extend the **existing adequacy chat suite** (do not invent a parallel stack).

| Tool / kind | Behaviour |
|---|---|
| `run_eh_study(archetype, stages?, budget_solves?)` | execution; `_campaign_gated("eh_study", …)` |
| `get_adequacy_results('eh_study')` | status / running / done record |
| `get_adequacy_results('eh_reference_design')` | assembled `ReferenceDesignReport` |
| `abort_adequacy_study('eh_study')` | via `ADEQUACY_STUDY_ENUM` |

**Files touched**

- `services/chat_tools_schema.py` — `EH_ARCHETYPE_ENUM`; kinds; `run_eh_study` schema; `TOOL_ROUTES`
- `services/chat_tools.py` — handlers, hints, abort map, `run_eh_study`, `DISPATCHERS`
- `services/adequacy/campaign.py` — `eh_study` in `CHARGEABLE`; estimate = `budget_solves` (default `DEFAULT_EH_BUDGET_SOLVES`); pack undo is **not** an extra LP charge
- `services/chat_service.py` — `_ADEQUACY_GUIDE_CHAINING` names `run_eh_study`
- `tests/fixtures/route_inventory_phase0.txt` — EH HTTP rows
- `tests/test_chat_adequacy_tools.py`, `test_adequacy_campaign.py`
- `CHATBOT.md`

**Guard paths covered:** unknown archetype, empty `stages`, out-of-range `budget_solves` leave surfaces idle (no worker publish).

---

## 5. Code map (where to look)

```
pypsa-gui/backend/
  models/energy_hub.py              # contracts, packs, budget ceilings
  services/adequacy/
    eh_study.py                     # sync pipeline driver
    eh_study_runner.py              # HTTP worker + EhStudyRequest
    eh_report.py                    # ReferenceDesignReport assemble / GET payload
    multi_energy.py                 # P6a/b section fill
    scr_gate.py                     # P9 warn-only SCR
    slack.py                        # VOLL prefix / masks / naming (source of truth)
    campaign.py                     # chat campaign budget (incl. eh_study)
  services/solver/assumptions.py    # per-Load slack creation (P6b)
  services/chat_tools.py            # DISPATCHERS + run_eh_study
  services/chat_tools_schema.py     # TOOLS + ADEQUACY_* enums + TOOL_ROUTES
  routers/results.py                # thin GET/POST /eh_study(+abort), /eh_reference_design
pypsa-gui/frontend/
  src/pages/results/EhReferenceDesignPanel.tsx
  AdequacyTab.tsx / McPanel / adequacy chips (RAM, SCR)
```

**Env for tests:** `PYTHONPATH=/workspace:/workspace/pypsa-gui/backend` (gridspine import + backend).

**E2e commands (seal):**

```bash
cd pypsa-gui/backend
PYTHONPATH=<repo>:<backend> python -m pytest \
  tests/test_energy_hub_*.py tests/test_adequacy_sweep.py \
  tests/test_chat_adequacy_tools.py tests/test_adequacy_campaign.py \
  tests/test_energy_hub_study_http.py -q

cd pypsa-gui/frontend
npm test -- --run \
  src/pages/results/EhReferenceDesignPanel.test.tsx \
  src/pages/results/adequacy.test.tsx \
  src/pages/results/McPanel.test.tsx \
  src/pages/results/AdequacyTab.test.tsx
```

---

## 6. Product / modelling invariants (do not break)

1. **Honesty over completeness** for multi-energy: no invented per-Load ENS from shared-bus bus-level capture without Load-scoped slacks (P6b now provides them).
2. **SCR is a feasibility gate**, not a co-opt lever; thin slice is **warn-only**; `emt_recommended` flag only.
3. **Electrical-only default** for ENS-cap / FMEA ΔEUE must stay bit-stable unless a phase explicitly owns a change.
4. Study mesh: at most one study in flight; EH registers under `STUDY_KEYS` as `eh_study`.
5. Chat: study starters are **execution** (confirmation-gated); abort is **destructive**; GETs map 204 → `{status:'no_data', message}`.
6. Campaign: check-then-record; failed start must not burn budget; MC charged 0; EH charged `budget_solves`.

---

## 7. Session narrative (what “we” did after #48)

Rough agent sequence (hao-driven):

1. Continue EH gap closure under TDD + QA gates.  
2. Ship / merge complete-stack e2e (#48).  
3. **P6(a)** option B → #49 merge + CI hygiene.  
4. Confirm **P6(b)** option C → implement + #50 merge.  
5. Suggest remaining tasks → **chat tools** chosen → #51 implement, CI green, **squash-merged**.  
6. Full stack local e2e → findings + plan banner → docs PR #52.  
7. This handover.

Independent QA assessors were used on earlier phases (P7/P8/P9/FE/complete-stack); late P6/chat slices relied on focused pytest + GUI CI. Re-introduce independent GO gate if hao asks.

---

## 8. Remaining work (priority order)

| Priority | Item | Notes |
|---|---|---|
| 0 | Merge [#52](https://github.com/MLHaoChang/pypsa-eur/pull/52) if still open | Docs-only seal + this handover |
| 1 | **Class-C authoring UI** | Backend parametric + profiles shipped; worksheet UI write/edit is the product hole |
| 2 | Spare-lead severity modifier | Optional P7; **documented** only — no silent severity scale |
| 3 | Climate P8(b) | Data procurement gate |
| 4 | Planned-outage MC | Large semantics change — explicitly deferred |

**Suggested first follow-up prompt for Claude:**  
“Implement Class-C authoring UI for EH stress scenarios (TDD + FE), branch `cursor/eh-class-c-authoring-ef60`, base `master`.”

---

## 9. Findings index

| Finding | Topic |
|---|---|
| [`2026-09-16-eh-p2-class-b-residuals-inventory.md`](../findings/2026-09-16-eh-p2-class-b-residuals-inventory.md) | P2 inventory |
| [`2026-09-18-eh-p7-ram-v1-inventory.md`](../findings/2026-09-18-eh-p7-ram-v1-inventory.md) | P7 RAM |
| [`2026-09-19-eh-complete-stack-e2e.md`](../findings/2026-09-19-eh-complete-stack-e2e.md) | #48 e2e |
| [`2026-09-19-eh-p6-multi-energy-spike.md`](../findings/2026-09-19-eh-p6-multi-energy-spike.md) | P6 spike |
| [`2026-09-23-eh-p6a-complete-stack-e2e.md`](../findings/2026-09-23-eh-p6a-complete-stack-e2e.md) | P6a + CI hygiene |
| [`2026-09-24-eh-p6b-multislack-spike.md`](../findings/2026-09-24-eh-p6b-multislack-spike.md) | P6b option C |
| [`2026-09-25-eh-stack-e2e-seal.md`](../findings/2026-09-25-eh-stack-e2e-seal.md) | Post-#51 seal |
| **This file** | Claude handover |

---

## 10. Cloud access copies

| Location | Purpose |
|---|---|
| Repo path above (pushed on `cursor/eh-stack-e2e-seal-ef60` / PR #52) | Durable GitHub access for any agent |
| Cursor agent run | https://cursor.com/agents/bc-877e9041-107d-465a-8e97-04a2c77aef60 |
| Artifact mirror | `/opt/cursor/artifacts/handovers/2026-09-25-eh-reference-design-claude-handover.md` (same content; cloud-agent artifact store) |

Notion upload was attempted; MCP auth timed out in this environment — treat **GitHub + agent URL + artifact** as the cloud sources of truth unless hao pastes into Notion later.
