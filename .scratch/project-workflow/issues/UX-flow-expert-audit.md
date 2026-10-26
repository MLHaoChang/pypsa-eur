# UX-flow audit: one entry point, owning section, visible next step

Status: ready-for-agent
Progress: Audit document (26 findings) merged; the fix tickets it routes are not shown done.
Type: audit, which routes into fix tickets
Owner of the audit: UX reviewer. The coordinator routes each fix to its package.
Source: owner decision **UX-flow**, 2026-10-21 16:2x CEST (`docs/agents/owner-decisions.md`).
Audited code: `master` @ 66e5f44c, frontend `pypsa-gui/frontend/src/`. File paths below are relative to that directory.

## The three rules (the gate for every UI PR from now on)

1. **One entry point per feature.** Expert works like Guided: no two general buttons for the same feature. Contextual deep links are fine, for example an issue opening its field, or "Turn on in Solver settings" from an empty result.
2. **Grouped under the owning card or section.** A feature lives in the section or card that owns it (Capacity, Load flow / Grid, Reliability, Commercial, …). It is shared only where one thing must be set up before another (assets before time series and studies).
3. **A visible next step.** When a step is done, a visible action leads on: for example "give it a time series" after adding an asset, or "Next: <section>" at the end of a section.

"Planned" means the move is already in workspace-reconciliation §6.2 or design §5 B2. "New" means neither lists it.

## Findings, by user impact

| # | Rule | Finding | Evidence | Fix | Owner |
|---|---|---|---|---|---|
| 1 | 2, 1 | **Reliability goal typed in up to five places, unlinked.** LOLE in the Goal card, the EH reference design's pack settings (local state), the Loop panel and the Margin-loop panel. ENS in Solver settings, the Goal card and the EH pack. Archetype in the Site card and the EH panel. MC draws in four panels. A target set in one doesn't reach the others. | `pages/hubDesign/cards/GoalCard.tsx:96,120`; `pages/results/EhReferenceDesignPanel.tsx:919,922,1211,1274-1275`; `pages/results/LoopPanel.tsx:401-407`; `pages/results/MarginLoopPanel.tsx:252-257`; `pages/SolverSettings.tsx:1477`; `cards/SiteCard.tsx:182` | One editor in section 8, "Reliability goal" (LOLE, ENS, draws), shown in Guided and Expert. Archetype in section 3. The Results study panels show the goal read-only with "Change in Scenarios & optimisation ›", and keep only their Run. | **W3b** (Goal card move is planned) + **new slice "W3b-R: one reliability goal"** for the EH pack, Loop, Margin-loop and MC inputs. GK checks that each study reads the one stored goal. |
| 2 | 2, 1 | Investment-case inputs are edited in Results and duplicated in Economics. | `pages/results/InvestmentTab.tsx:168-218`; `pages/economics/EconomicsPanel.tsx:62-77` | Remove them from Results and keep the read-only views with a link to 7. | **W3b, in flight as #533.** Blocked on the Guided section-7 card, so Guided keeps an editor. |
| 3 | 1 | Discount, inflation and default lifetime have two editors on the same fields, so unsaved drafts overwrite each other. | `SolverSettings.tsx:1269-1304`; `pages/economics/RatesForm.tsx:46-52` | RatesForm (7) is the only editor. Solver settings shows the value read-only with a link. Lifetime goes to 5, CO₂ price to 7c. | **W3b, rates in #533.** Lifetime and CO₂ follow in W3b. |
| 4 | 1 | **A second Run inside Solver settings, "Run AC Power Flow on current solution", bypasses the run gate.** "Apply clustering" also changes the network from an inputs panel. | `SolverSettings.tsx:1899-1936, 2012-2018, 2373` | Remove the button. Re-running AC PF becomes an action on Results › Load flow that goes through `requestRunSolve` and the gate. Clustering becomes an explicit section-8 action with a confirmation. | **New small slice "R1-single-run"**: UX + GK (run path). |
| 5 | 3 | **Section 9 "Readiness → run" has no Run.** Issues' all-clear says "The model is ready to run" with no button. The Guided readiness card says "Ready to run." with no button, and the Guided header hides the idle Run. | `layout/sidebar/SectionRows.tsx:120-170`; `pages/IssuesPanel.tsx:261-268`; `pages/hubDesign/SectionView.tsx:85-125`; `layout/AppHeader.tsx:1062` | Add a "Run" button to Issues' all-clear and to the Guided readiness card. It calls `requestRunSolve()`, the same action as the header, not a second Run. | **W3c** (or a W3a follow-up) |
| 6 | 3 | **Expert has no "next section" anywhere.** The sidebar section header is a label, the full-page panel chrome has only Close, and no panel links forward. | `layout/sidebar/FlowSectionBlock.tsx:7-30`; `App.tsx:177-200` (`FullPageTab`); none in `TimeSeriesManager.tsx`, `CapacityBoundsEditor.tsx`, `economics/*`, `ProfileStudioSection.tsx` | A "Next: <section n+1> →" button in the `FullPageTab` header (`openSectionInExpert(next)`), from `SECTION_META` and `PANEL_SECTION`. A "Next ›" row in `FlowSectionBlock` once the section is complete. | **W3c** |
| 7 | 3 | **Guided sections have no Next either.** SectionView and the generic card offer only "Open in Expert". The Site card has no "Continue". | `SectionView.tsx:43-155`; `HubDesignPanel.tsx:141-171`; `cards/SiteCard.tsx` | A footer under SectionView: "Next: <section> →" through `pickSection`, reading "Continue anyway" while items are open. | **W3c** |
| 8 | 1 | **"Projects home" has 5+ entry points:** header breadcrumb, sidebar menu, Workspace panel ×2, user menu "Back to projects", palette. | `AppHeader.tsx:896-903`; `layout/Sidebar.tsx:979-984`; `pages/WorkspacePanel.tsx:128,289-290`; `layout/UserMenu.tsx:87-91`; `components/CommandPalette.tsx:433-439` | Keep the breadcrumb and the palette. Remove the sidebar row, both Workspace buttons and the user-menu item. | **W3c** (chrome clean-up) |
| 9 | 1 | Save has three buttons plus the palette: header, sidebar menu, Overview. | `AppHeader.tsx:1030-1046`; `Sidebar.tsx:932-938`; `pages/OverviewPanel.tsx:200-202` | Keep the header Save (Ctrl+S, palette). Drop the other two. | **W3c** |
| 10 | 1 | Two "Export bundle" buttons do different things: the sidebar opens a five-format chooser, Overview downloads `.pypsaproj.zip` directly. | `Sidebar.tsx:46-50,945-950`; `OverviewPanel.tsx:153-199` | One Export, in 10 › Hand-off: the chooser. Remove Overview's. | **W3c** (with W10 hand-off) |
| 11 | 1 | Project rename has three editors: header field, sidebar card (focuses the header), Overview's own `ProjectNameEditor`. | `AppHeader.tsx:920-933`; `Sidebar.tsx:354-368`; `OverviewPanel.tsx:145-148` | Keep the header field, with section 1 mounting the same component (§6.2). The sidebar card and Overview title become plain text. | **W3c** |
| 12 | 1 | **The palette's "New project" resets the open network immediately, with no confirmation.** Every other "New project" opens the wizard. | `CommandPalette.tsx:449-466` | Point it at `navigate('/projects')` (the wizard), or remove it. | **New small fix "palette-new"**. Do it first: it can discard work. |
| 13 | 2 | **Section 3 has no connection editor.** Its only row is Campus electrical, a post-solve study. The PoC / site connection is in Economics › Basis and Results › Investment. | `SectionRows.tsx:56-63`; `layout/flowSections.ts:75`; `EconomicsPanel.tsx:29-37,64`; `InvestmentTab.tsx:168` | Section 3 gets a "Connection" row (one `SiteConnectionSection` mount, the energization table, the grid-code profile). Campus electrical leaves section 3's rows. | **W3b** (J7, the planned next PR) |
| 14 | 2 | Campus electrical stacks five sections in one panel: file (5), grid codes (3), library (5), study settings (8), run (9), results (10 › Grid), island (8/5). Its finish toast points back to section 3. | `pages/CampusElectricalPanel.tsx:182-193,284-349,363-364,556-690`; `AppHeader.tsx:661-664` | The §6.2 split. The toast points to 10 › Grid. | **W3b** (planned) |
| 15 | 2 | The stress-scenario editor is inside Results › FMEA. | `pages/results/FmeaTab.tsx:437` | Move it to 8 "Stress conditions", with a read-only list in FMEA. | **W3b, in flight as #535.** Blocked on the Guided editor and scope. |
| 16 | 1 | "Reports" opens from three general buttons, two on the same Adequacy tab. | `SectionRows.tsx:182-185`; `pages/results/AdequacyTab.tsx:77-85`; `EhReferenceDesignPanel.tsx:1434-1442` | Keep the sidebar row and the Adequacy one (contextual). Remove the EH panel's. | **W3c** |
| 17 | 1 | "Library" names two different features (commercial and campus equipment), and the commercial one is mounted twice. | `EconomicsPanel.tsx:77`; `InvestmentTab.tsx:216`; `pages/CampusLibrarySection.tsx` | One Library page with inline pickers (§6.2, planned). Rename the campus one "Equipment library". | **W3b** (planned) + **L1** (library page) |
| 18 | 1 | The Workspace panel duplicates the Scenario tree (also row 8) and Recent projects (also the sidebar menu). | `WorkspacePanel.tsx:203-232,266-286`; `SectionRows.tsx:104-106`; `Sidebar.tsx:954-972` | Workspace keeps ownership, lock and People. The tree is only under Scenarios, Recent only in the menu. | **W3c** |
| 19 | 1 | "Assign members" has two entry points: user menu and Workspace › People. | `UserMenu.tsx:79-86`; `WorkspacePanel.tsx:238-244` | Keep it in Workspace › People (project-scoped). Remove it from the user menu. | **W3c** |
| 20 | 1 | Canvas tools exist in both the sidebar and the canvas: Connect, Zoom, and Add bus, where the canvas "+" auto-creates but the palette opens a form. | `Sidebar.tsx:1160-1239`; `pages/TopologyCanvas.tsx:3078,3133-3165`; `layout/paletteData.ts:15` | Canvas toolbar only (§6.2, planned). The canvas "+" opens the same creation form. | **W3b** (planned) |
| 21 | 2, 1 | The "Decision study" row appears in section 10 for every project. §6.2: its own project kind, reached from Home, "not a flow destination". | `SectionRows.tsx:172-175`; `ProjectsHomePage.tsx:615`; `NewProjectWizard.tsx:126` | Show it only for decision-kind projects, gated like Planning → dynamics, or remove it once Home lists decision studies. | **W3c** (or W2 with Home) |
| 22 | 2 | The Assets palette (section 5) also holds demand (section 4) and Bus / Line / Transformer (section 3). | `paletteData.ts:13-17,53-57`; `SectionRows.tsx:65-80`; `flowSections.ts:63-67` | "Add demand" in 4 (planned). "Add bus / line / transformer" in 3 (new). The canvas toolbar stays. | **W3b** (demand) + **P1** (network items in 3, with the AC-PF palette work) |
| 23 | 3 | Model Horizon says "Step N of M" but has no Back / Next, and the last step doesn't lead to section 2. | `pages/ModelHorizon.tsx:799-822`; `pages/modelHorizon/StepShell.tsx:104-150` | Back / Next in `StepShell`. On the last step: "Next: Carriers & processes →". | **W3c** (or E1 if it owns Horizon) |
| 24 | 3 | **Adding an asset, or applying a profile, ends with only a toast.** No "give it a time series" step, and no link between Time Series and Profile Studio. | `layout/CreationForm.tsx:663-665`; `pages/ProfileStudioSection.tsx:65-105` | After create: "Next: give it a time series →", opening Time Series on the right tab, filtered to the asset. Profile Studio success: "View in Time Series →". Renewables tab: "Generate with Profile Studio". | **W3c** (the owner's own example) |
| 25 | 3, 1 | A finished solve lands Expert on Results › **Economics**, not Summary. If a panel is open, only a 6 s toast appears. Summary has no hand-off step. "Economics" names both the section-7 panel and a Results tab. | `AppHeader.tsx:48-53,694-704`; `pages/Results.tsx:90,394`; `results/SummaryTab.tsx:138-189`; `SectionRows.tsx:89-95` | Land on Summary. The header status pill becomes a lasting "Open results" link. Summary gets "Next: hand off (Reports, Export) →". Rename the Results tab "Asset economics". | **W3c** + **W10** (results by card) |
| 26 | 3 | Creating from a template in Expert opens no section. Guided gets its initial step. | `hooks/useCreateFromTemplate.ts:98-114` | After create, open the first section from `/flow` that isn't complete (`openSectionInExpert`). | **W2** (Builder) or **W3c** |

## Already compliant (checked)

- **Contextual deep links:**
  - "Turn on in Solver settings" (`results/LoadFlow.tsx:829`);
  - "Set VOLL in Solver settings" (`results/LostLoadTab.tsx:218`);
  - the run-gate blocker (`AppHeader.tsx:1068-1075`);
  - Issues item → field (`layout/flowNavigation.ts`).
- **Run CTAs reuse the header Run through `requestRunSolve`:** `results/NotSolved.tsx:26`, `hubDesign/ChaptersSection.tsx:84`.
- **Guided hides the idle header Run,** leaving one study Run in the Goal card.
- **Solver settings' "Pre-run validation → Issues"** is a visible next step.
- **A failed solve opens Issues.**
- **Guided advances to Results** when a study finishes.
- **The expanded sidebar and the icon strip share one `STEPS` list.**
- **The Carriers and Loads rows open the existing tables.** That's navigation, not a duplicate.
- **Per-asset cost in Properties plus "All asset costs"** is the dual view §6.2 keeps deliberately: the same field editors.
- **The per-asset discount-rate override** is an override, with the global value as placeholder.
- **Theme and density:** the sidebar control plus a palette mirror is acceptable.
- **Settings:** one header button.
- **Planning → dynamics is gated** to study projects.

## Proposed routing

- **Do first:** 12 (palette "New project" discards work without asking).
- **W3b (moves, already running):** 2 and 3 (#533), 15 (#535), 13, 14, 17, 20, 22 (demand), and the Goal-card half of 1.
- **New slice "W3b-R: one reliability goal":** the rest of 1. It needs the GK, because each study must read the one stored goal.
- **New slice "R1-single-run":** 4. UX + GK.
- **W3c, a new package "flow chrome and next steps":** 5, 6, 7, 8, 9, 10, 11, 16, 18, 19, 21, 23, 24, 25. 26 goes to W3c or W2.
- **P1:** network items into section 3 (the other half of 22).
- **W10:** the results-by-card half of 25.

Each fix PR is gated on the three rules above, with screenshots of the old place and the new one.

## Comments

- 2026-10-26, R1-single-run (finding 4), done in #719: the AC PF Run had already left Solver settings (W4b, 67d42bb); #719 removes its unused frontend client, makes "Apply clustering…" ask first (ConfirmDialog) and adds a "Run the solve ›" next step through `requestRunSolve`. Open: the assistant's `run_ac_pf_stage` path, `.scratch/harness/issues/24` (#715).
