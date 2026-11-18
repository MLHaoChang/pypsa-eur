<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Expert-mode template journeys (E2E-TEMPLATES-1)

Walk of all 10 templates in Expert mode, template → setup → readiness → run → wait → results → decide/compare → report. App at master `0ab07339`, API :8000, Vite :5173, no assistant key (KEY-LATER-1). Studies ran with "Budget (LP solves)" = 1, so frontier, levers and FMEA were "not run"; default-budget times are unknown. Guided is covered by the journeys session (PR #1629); gaps 01–08 are #1613's, 09 is #1629's. Issues from this walk: [10](issues/10-study-overrides-connection.md) – [21](issues/21-open-project-slow-and-session-noise.md). Guided was not re-walked here; the Expert-vs-Guided column rests on #1613's notes.

## How Expert runs

Two runs exist. Header "Run LOPF" is a plain optimisation (cost, capacities, no verdict). The verdict study is at Results > Reliability > "Energy hub reference design" > Pack settings > "Run study". Expert readiness always reads "0 errors, 0 warnings, ready to run"; advisories never block. LOPF 3–9 s (168 h), 14 s (IPP), 35 s (ammonia); study 9–15 s (45 s ammonia).

## Journey table

| Template | Setup | Readiness | Run / wait | Results | Decide / compare | Report |
|---|---|---|---|---|---|---|
| eh_datacenter | form OK, no location | no block; 4 red flags (01) | study 12 s, only "Running… / Abort" (17) | "Certified (pass vs 3 h/yr)" on unset goal (13); raw names (19) | Branch + Compare work (unchanged copy) | JSON disabled (16); report needs key |
| eh_datacenter_staged | form OK | same | 15 s | toast €382.67M vs summary €576M (18) | not tested | JSON/CSV OK |
| eh_site hospital | Guided-only form | same | 9 s | Certified, LOLE 0.32 (13) | not tested | JSON/CSV OK |
| eh_site industrial | form OK | same | 12 s | Not certified, LOLE 4.58 h/yr; "Achieved 0.00‱" beside it (18); next step "Ask the assistant" dead end (19) | OK: allowed shortfall 6 → rerun → Certified | JSON/CSV OK |
| eh_microgrid | no form | "not in scope" notes | 9 s | "grid 100 MW" on an island (18) | not tested | JSON disabled (16) |
| eh_microgrid_staged | no form | same | 33 s | "No cost breakdown / capacities", stuck "Loading…" (18) | not tested | JSON/CSV OK |
| eh_h2_hub | no form | same | 9 s | cost and LCOH, no verdict (11) | not tested | JSON disabled (16) |
| eh_h2_hub_staged | no form | same | 12 s | toast €120.29M vs €195M (18); no verdict (11) | not tested | JSON/CSV OK |
| eh_ammonia_staged | no form | same | 45 s | no verdict (11); delivered > made (18) | not tested | JSON/CSV OK |
| eh_concentrator_staged | no form; 26,280 snapshots | same | not finished after >15 min; blocks backend (12) | not reached; Abort leaves all stages "not established" | not reached | not reached |
| eh_ipp_hybrid | no form | 1 warning with raw id (14) | LOPF 14 s; no study | cost −€4.55M, plants retired (14) | not tested | none found (11) |

## Decisions: where made, Expert vs Guided (EXPERT-GUIDED-1)

| Decision | Expert | Guided | Agree? | Marked assumed? |
|---|---|---|---|---|
| IT load, PUE, backup, architecture (DC) | creation form only; read-only after | creation form only | yes, both fixed after create (20) | no |
| Site type, stage peaks, must-stay-on, connection limit (eh_site) | cannot create; form works over Expert | form | no (20) | "default" |
| Allowed shortfall (h/yr) | Solver & mode > Network, Save settings | Section 8 | wording differs (20); both silently apply 3 h/yr (13) | no |
| Monte Carlo draws | Network tab "500 (default)" | "Simulation runs per check" | study reports 2000 (13) | no |
| Connection limit | Link property panel | read-only (02) | no | study rewrites it (10) |
| Study budget, levers, control mode | Expert only | absent | no | no |
| Location | Model Horizon panel | section 1 | yes | no (01) |

## Caveats

Concentrator results, verdict and export were not obtained. The ammonia and IPP first runs were contaminated by stale state (see 15; the walk's out-of-band API deletes may have caused it) and re-run clean. Branch/Compare tested on the DC only; change-and-rerun only on the industrial site.
