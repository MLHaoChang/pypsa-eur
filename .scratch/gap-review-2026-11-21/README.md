<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Gap review of PyPSA Studio, 2026-11-21

Commissioned by the owner through the coordinator: walk the current implementation against the design and the planned backlog, find what is missing, and file it so builders can start.

**Base.** master `76707f13`, app run locally (API :8000, Vite :5173, SQLite, dev admin), Playwright at 1440×900. The assistant is **off**: no `ANTHROPIC_API_KEY`, so no chat behaviour was walked. Only its off state was checked.

**Method.** Four walks in the running app: W1 projects/templates/Guided/Expert; W2 run/results/reports/investment; W3 grid study/compliance/imports; W4 assistant off state/roles/sharing/account. Three read-only maps alongside them: design promises vs backlog, the open backlog by journey (147 issues), and end-to-end test coverage. Findings already filed by the template walks of 20 and 21 Nov were not re-filed.

**Limits.** All walkers shared one backend and, for W1–W3, one admin account, so slow or intermittent behaviour (open glitches, the 33–65 s evidence report) may partly be load. Where a finding depends on that, its issue says so. Screenshots stayed in the session scratchpad and are not committed.

## 1. Feature map

"% built" is the share of a feature's issues that are done (won't-do excluded). "Journey" is the walk's verdict on the user journey, which the issue count can hide.

| Journey / feature | % built (issues) | In flight (PRs) | Planned (open issues) | Journey on master | Missing (new here) |
|---|---|---|---|---|---|
| Projects & templates (`template-e2e`, `project-workflow`) | te 7 % (2/29), pw B1/B9/E1 done | #1626, #1645, #1757, #1773/#1778, #1786/#1787 | te 01–28 (21 ready) | works: 14 templates in 3 families, create 2–3 s, rename, duplicate | blank project inherits the last project's config (te/32); deep link sometimes never activates (te/30); delete only via Expert › Scenarios (te/34) |
| Guided sections 1–10 | B1 done; te/ux open | #1627/#1630/#1716 (run progress) | te 01, 03, 06, 08, 09; ux 01–07 | works: every section opens with state; run gate per §3.3 | the location fix never clears (te/01 extended); rep-week called chronological (te/33) |
| Expert panels (`expert-catalogue`) | 0 % (0/5) | #1634 (catalogue core) | ec 01–05 | works: all panels open, edits persist both ways | none new |
| Run & results (`guided-results`, `provenance-graph`) | gr 0 % (0/6); pg done | #1700, #1729 | gr 01–06 | partly: run fine, but an edit drops the run to not_run | edit discards run (pg/13); Hand-off group empty (gr/07); no Commercial tabs (gr/08); no Products tab (gr/09); Load flow dead end (sh/61) |
| Reports & export (`project-io`, `results-workbook`) | pio 0 % (0/4); rw done | #1661, #1689, #1709 | pio 01, 02, 06, 07 | partly: bundle/NetCDF/CSV/MATPOWER fine; Excel export fails at 30 s | Excel export timeout (pio/09); report variants (bc/19) |
| Investment & business case (`business-case`, `homer-parity`) | bc 82 % (14/17); hp 83 % | #1711, #1679 | bc 12, 16, 18 | partly: investment case works; business case cannot reach NPV from the IPP preset | IPP preset unrunnable (bc/21); busy reads as stale (bc/22); indexation % vs fraction (bc/23); no sensitivities (bc/20) |
| Commercial section 7, library, market (`site-commercial-ux`) | 2 issues, both done | — | none | partly: tariff inputs in section 7; participants still edited under Results | K1 shell (scu/03); party board (scu/04); library save/update/live lookup (scu/05); market data (scu/06); Sankey process node (scu/07); sensitivity case banner (pw/S5) |
| Grid study & compliance (`grid-compliance`) | 24 % (7/29) | #1577, #1578, #1584, #1656, #1743, #1765, #1767 | gc 01b–22 | works on DC: campus study 35 s, compliance table; UI tracker missing (gc/09) | PSS/E bundle download (gc/23); SC PASS with no limit (gc/24); Study project panel (pio/08) |
| Imports: Excel, PSS/E, DGS, PLEXOS | xl 20 %; psse 67 %; dgs 0 %; PLEXOS slices 1–3a merged, no issue file | #1798 (Excel S2), #1745 (PLEXOS 3b), #1765 (DGS) | xl 02–06; psse 02; dgs 01 | **missing for users**: the three model importers have no route or screen; only the old PyPSA .xlsx/.nc/.m import works | one import pipeline (pio/04), preview suggestions (pio/05) |
| Assistant (`live-copilot`, `assistant-assurance`, `live-voice`, `harness`) | lc 99 %; aa 45 %; lv 53 %; harness done | #1556, #1655, #1704, #1707 | aa 04–23, lv 01–21 | off (no key): honest for admins; a member's panel hangs on "Loading models…" | non-admin off state (lc/49) |
| Roles & sharing (`project-viewer-role`), accounts | pvr 0 % (0/4) | #1718 (viewer 2a) | pvr 01–04 | partly: sharing works but only in Expert; invites without SMTP fail silently; set-password links serve the login page | invite and password links (acct/01); link without email (acct/02); account page (acct/03); viewer and sharing UI (pvr/05) |
| End-to-end CI coverage | — | — | — | CI browser: template journeys (to the .docx download) and 5 owner journeys | export and Expert↔Guided steps (te/29); Expert/investment/grid/import journeys (te/31) |

Features whose issues are all done: adequacy-loops, assistant-memory, campus-*, chat-companion, data-to-model, datacenter-grid-readiness, gridspine-library-eu, harness, ipp-renewables-bess, money-year, provenance-graph (until 13), results-workbook, retro-speed-1, site-commercial-ux (until 03), timeseries-generators.

## 2. New and extended issues, ranked by user value

Status R = `ready-for-agent`, T = `needs-triage` (the owner question is written in the file as options).

| # | Issue | St | Model | Why it ranks here |
|---|---|---|---|---|
| 1 | `.scratch/project-io/issues/04-model-import-pipeline.md` | R | opus | Three built importers (PSS/E, DGS, PLEXOS) are invisible to users. |
| 2 | `.scratch/template-e2e/issues/32-blank-project-inherits-solver-config.md` | R | sonnet | Silent wrong inputs, and one project's commercial data leaking into another. |
| 3 | `.scratch/provenance-graph/issues/13-component-edit-discards-run.md` | R | sonnet | Every edit hides the last results; Results and Investment disagree. |
| 4 | `.scratch/accounts-onboarding/issues/01-invite-and-password-links.md` | R | sonnet | No second user can onboard without SMTP. **Opened** (session_01G7qCumn3dwi5dmAg5fwr6u). |
| 5 | `.scratch/project-io/issues/09-excel-export-times-out.md` | R | sonnet | Excel export fails on every hourly template. |
| 6 | `.scratch/business-case/issues/21-ipp-preset-cannot-run.md` + `23-indexation-fraction-vs-percent.md` | R | sonnet | The business case cannot reach an NPV, and a typo gives 200 %/yr. |
| 7 | `.scratch/guided-results/issues/07-hand-off-results-group.md` | R | sonnet | The results journey ends at an empty card; reports and exports sit in 2–3 places. |
| 8 | `.scratch/study-harness/issues/61-load-flow-tab-dead-end.md` | R | sonnet | A dead end on every Energy Hub project. **Opened** (session_01G7qCukZDvwRFMjvgH4Gurc). |
| 9 | `.scratch/grid-compliance/issues/23-campus-pss-e-bundle-download.md` + `24-switchgear-pass-without-rating.md` | R | sonnet | The TSO hand-off cannot leave the app, and a PASS shows with no limit. |
| 10 | `.scratch/template-e2e/issues/30-project-deep-link-never-activates.md` | R | sonnet | An empty app with no message, about 1 open in 3 under load. |
| 11 | `.scratch/template-e2e/issues/29-journey-suite-export-and-expert-parity.md` | R | sonnet | CI guard for exports and Guided/Expert agreement. **Opened** (session_01G7qCuk2dRcskiyXzKdfgdo). |
| 12 | `.scratch/live-copilot/issues/49-non-admin-chat-off-state.md` | R | sonnet | Spinners that never end for every non-admin with no key. |
| 13 | `.scratch/business-case/issues/22-network-busy-reads-as-out-of-date.md` | R | sonnet | A wrong refusal reason sends users into a loop. |
| 14 | `.scratch/project-viewer-role/issues/05-viewer-and-sharing-ui.md` | R (blocked by 01/#1718) | sonnet | The viewer role is unusable without UI. |
| 15 | `.scratch/project-io/issues/08-study-project-panel.md` | R | sonnet | A Study project shows hub sections and no progress for 7 min. |
| 16 | `.scratch/guided-results/issues/09-products-results-tab.md` | R | sonnet | H2 and ammonia results lack their product and its cost. |
| 17 | `.scratch/site-commercial-ux/issues/07-sankey-process-node.md` | R | sonnet | A design follow-up (A1b) that was never filed. |
| 18 | `.scratch/template-e2e/issues/34-projects-home-rename-delete.md` | R | sonnet | Delete is hidden in Expert › Scenarios. |
| 19 | `.scratch/template-e2e/issues/33-representative-week-called-chronological.md` | R | sonnet | A small mislabel. |
| 20 | `.scratch/template-e2e/issues/01-location-required-not-asked.md` (**extended**) | R | (has builder) | The drawer it relies on writes the anchor, which the flow never reads. |
| — | `.scratch/site-commercial-ux/issues/03-section-7-commercial-shell.md` | T | opus | K1: unblocks all commercial slices; the participants designer still edits under Results. |
| — | `.scratch/template-e2e/issues/31-ci-browser-checks-for-uncovered-journeys.md` | T | sonnet | CI cost decision. |
| — | `.scratch/business-case/issues/20-investment-case-sensitivities.md` | T | opus | No sensitivities; phase codes in the chip. |
| — | `.scratch/accounts-onboarding/issues/02-invite-link-without-email.md` | T | sonnet | Security decision: show the link to the admin? |
| — | `.scratch/guided-results/issues/08-commercial-results-group.md` | T | sonnet | R5 Bill, Contracts & revenue. |
| — | `.scratch/project-workflow/issues/S5-sensitivity-case-banner.md` | T | sonnet | Waits on te/23 triage. |
| — | `.scratch/site-commercial-ux/issues/04-party-board.md` | T | opus | K4–K7. |
| — | `.scratch/site-commercial-ux/issues/05-library-scope-and-live-lookup.md` | T | sonnet/opus | L1–L5. |
| — | `.scratch/site-commercial-ux/issues/06-market-data-and-ppa-defaults.md` | T | opus | S1, S2, K8. |
| — | `.scratch/project-io/issues/05-import-preview-suggestions.md` | T | opus | AI-IMPORT-1 scope. |
| — | `.scratch/business-case/issues/19-report-variants.md` | T | sonnet | K9. |
| — | `.scratch/accounts-onboarding/issues/03-account-page.md` | T | sonnet | Is an account page wanted? |

Not filed, for the owner's roadmap (design promises with no issue, too early to spec): G3 external engines via an optional worker (modular-cards.md:182–190), R-N1 N-1 view (waits on SCLOPF, which `flow_scope` marks not yet available), assistant H1/H2 card walkthroughs (card-workspaces.md §6), DASHBOARD-1/VISUALS-1, LIFE-CO2's section-7c CO₂ editor, P2/P3 campus assets (SVC, OLTC). Seen but not filed, because they are unverified under shared load: the evidence report taking 33–65 s with only "Creating…", and the results workbook export taking 72 s (folded into pio/09).

Backlog hygiene seen on the way (not changed here):
- Duplicate issue numbers: grid-compliance 13, template-e2e 20, study-harness 50, assistant-assurance 12.
- Overlapping pairs: ux/01 = gr/05; ux/02 = te/27; te/05, te/19 and ux/06.
- Ready issues with no acceptance section: xl/02, xl/04, xl/05, bc/16, gc/06c, gc/06d, gc/18, gr/03, lv/16, lv/17, pio/01, pvr/01, psse/02.
- `project-io/03` has PRs (#1689, #1709) but its issue file exists only on those branches.

## 3. Suggested build order (lanes that share no files)

Each lane runs in order. Lanes can open in parallel; no two lanes touch the same file, except where noted.

| Lane | Packages, in order | Files the lane owns |
|---|---|---|
| A Imports and Hand-off | pio/09 → pio/04 (A backend, then B UI) → gr/07 → gr/09 | `routers/io.py` (after #1798), new `routers/model_import.py` + service, `main.py` (after #1798), `api/client.ts`, `api/io.ts`, `layout/NewProjectWizard.tsx` (after te/01's builder), `pages/ImportExport.tsx`, `layout/Sidebar.tsx`, `pages/results/groups.ts`, `pages/Results.tsx`, `pages/ReportsPanel.tsx` |
| B Project lifecycle | te/32 → te/30 → te/34 | `routers/network.py` (`reset_network`), `routers/projects.py`, `pages/ProjectsHomePage.tsx`, `utils/projectActions.ts`, `App.tsx`, `pages/ScenariosPanel.tsx` (read) |
| C Freshness | pg/13 | the component PUT routes in `routers/network.py` (other functions than lane B; trial merge expected clean), `services/provenance_graph/`, `flow_service.py` freshness, `useStaleRunKinds` |
| D Business case | bc/21 + bc/23 (one package) → bc/22 | `business_case_presets/*.json`, `services/business_case/`, `investment/BusinessCaseLines.tsx`, `BusinessCaseRun.tsx`, `routers/results.py` (`_bc_read_solve` only) |
| E Grid hand-off | gc/23 + gc/24 (one package) → pio/08 | `campus_electrical_service.py`, `routers/campus_electrical.py`, `CampusElectricalPanel.tsx`, `campus_study.py` (switchgear check); then `GridspinePanel.tsx`, `sidebar/SectionRows.tsx`, `gridspine/drivers/progress.py`, `routers/gridspine.py`. gr/07 (lane A) reads GridspinePanel's bundle list, so it opens after pio/08 or reuses it read-only. |
| F Small UI | scu/07, te/33 (independent) | `EnergyFlowsTab.tsx`; `ExpertOnlyLines.tsx` |
| G Chat | lc/49 after #1556 | `AssistantModelSettings.tsx`, `ChatPanel.tsx`, `AssistantSetupCard.tsx` |
| H Sharing | pvr/05 after #1718 | `AssignMembersDialog.tsx`, `WorkspacePanel.tsx`, the project header, a new `smoke-viewer-role.mjs` |
| Opened already | acct/01, sh/61, te/29 | as in their files |

The needs-triage issues open after the owner answers. scu/03 (K1) is the gate for scu/04, scu/05 (pickers), gr/08's Parties tab and bc/19's per-participant variant.

## 4. Owner questions (each is in its issue file, recommended option first)

1. **scu/03**: which commercial slice opens next? (a) K1 shell now [rec.]; (b) K4 party board first; (c) hold the K-slices.
2. **te/31**: CI browser checks for the Expert, investment, grid and import journeys: (a) schedule's full mode only [rec.]; (b) PR short mode, path-filtered (+5–8 min per PR); (c) not now.
3. **bc/20**: investment-case sensitivities: (a) finance-only tornado, dispatch fixed [rec.]; (b) re-solve per step; (c) plain-words chip only.
4. **acct/02**: onboarding without SMTP: (a) one-time copyable link to the super-admin [rec.]; (b) temporary password; (c) require SMTP.
5. **gr/08**: Commercial results: (a) Bill and Contracts now, Parties later [rec.]; (b) all with the party board; (c) defer.
6. **pio/05**: import suggestions: (a) importer suggestions, no model [rec.]; (b) plus the assistant; (c) defer.
7. **scu/06**: market data: (a) ENTSO-E prices first [rec.]; (b) S1+S2+K8 plan; (c) not now.
8. **scu/05**: library: (a) L1–L3 now, L4–L5 later [rec.]; (b) all; (c) not now.
9. **scu/04**: party board: (a) K4 then K5 after K1 [rec.]; (b) K4–K7 stack; (c) not now.
10. **S5**: sensitivity banner: (a) with te/23's fix [rec.]; (b) after 23; (c) assistant only.
11. **bc/19**: report variants: (a) NPV/IRR now [rec.]; (b) after the party board; (c) no.
12. **acct/03**: account page: (a) small Account page [rec.]; (b) change-password dialog only; (c) not now.

## 5. What this review skipped

- No chat behaviour (no key). The LLM "Generate report…" was not walked.
- Viewer refusals were not walked in the UI (no viewer can be made on master).
- Edit-lock contention with two users in one project, MATPOWER/NetCDF import, and the sidebar "Duplicate project" save-as path.
- Guided sections were walked in full on DC staged only; MG staged and Hospital were checked through `/flow` and section 9.
- Template walks of 20/21 Nov cover the per-template findings; they were not repeated.
