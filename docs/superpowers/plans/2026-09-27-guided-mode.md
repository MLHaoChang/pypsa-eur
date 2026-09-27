# Guided mode — a simple, assistant-driven path for first-time users (P22.9, P23–P26)

**Status:** planned; spec written 2026-09-27. Nothing implemented yet.
**Spec (contract-level, read before any task):** [`docs/superpowers/specs/2026-09-27-guided-mode.md`](../specs/2026-09-27-guided-mode.md). Section numbers below (§) refer to it.
**QA input:** [`docs/superpowers/qa/2026-09-27-realapp-clickthrough-expert.md`](../qa/2026-09-27-realapp-clickthrough-expert.md) — the Expert click-through on the data-center template, commit f934560.
**Base commit for baselines:** `2b78c83` on `claude/epic-allen-k2t1c4`.
**Requested:** 2026-09-27. The UI should be easy for a non-consultant. It should hide what an expert does not need up front, guide a first-time user, offer hover help everywhere, and let the assistant do the engineering steps and answer questions.

**Decided (product owner, 2026-09-27; each is the recommended option):**

| # | Question | Decision |
|---|---|---|
| G1 | Shape | A **Guided / Expert toggle** in one app. Guided hides advanced panels and shows a step-card flow; Expert is today's UI. |
| G2 | Scope v1 | **Hub design end to end:** template or own network → site → study → results in plain language → FMEA risks → improve with the assistant. Everything else stays Expert. |
| G3 | Assistant | **It does the steps and you confirm.** "Let the assistant do this" sends a specific request. Every change goes through the normal confirmation card, and questions can be asked at any time. |
| G4 | Default | **New users and new projects start Guided.** One click switches, and an explicit choice is remembered and never overridden. |

**Rules:** the same as the parent plans: TDD, a QA gate, honesty over completeness, and build on existing engines. Guided mode adds **no new engine**. It is a presentation over the P10–P22 surfaces (readiness, EH study, review, FMEA sweep, guide catalogue, assistant tools). Expert mode stays behaviourally unchanged except for the P22.9 fixes (spec §1 "Expert unchanged").

**Model tiering.** Implementation: Opus-class or lower, one phase per agent. Plan and spec changes, phase-plan review and the QA-gate verdict: Fable. An implementer who finds the spec wrong stops and reports.

**Order and gating.** P22.9 → P23 → P24 → P25 → P26. **After every phase the integration gate (spec §8) runs end to end across the whole system, and the next phase starts only on a GO.** The gate checklist is repeated under each phase; tick it in this file.

---

## Baseline (once, before P22.9)

- [ ] Full backend suite on `2b78c83` (spec §8.1 command, cwd `pypsa-gui/backend`, ~40 min), output kept in the scratchpad.
- [ ] `npx vitest run` and `npx tsc --noEmit -p .` on `2b78c83` (cwd `pypsa-gui/frontend`).
- [ ] `docs/superpowers/qa/2026-09-27-guided-mode-baseline.md` lists every failing id with the summary line as evidence. Only ids in that file count as pre-existing.

---

## P22.9 — Expert-flow fixes (before P23; these are system-level bugs)

Source: the click-through report. Spec §2.

| Item | Report ref | Contract | Files | Tests |
|---|---|---|---|---|
| Live network mutated after the study / sweep | Bug 3 | §2.1 — preserve `control`/`sub_network` and `n.sub_networks` around every live-network solve (`sweep.py` helper shared by the studies); find and fix the study-path touch; invariant: `GET /api/network/buses` (and `/links`) equal before and after, study and sweep, incl. abort | `BE/services/adequacy/sweep.py`, whichever study/preflight touches the live object | `BE/tests/test_live_network_untouched.py` (3 tests) |
| Contradictory solved state | Bug 2 | §2.2 — greeting says "Solved" only with `condition` + `solve_time`; otherwise the study-re-solve sentence | `FE/components/ChatLaunchGreeting.tsx` | `ChatLaunchGreeting.solvedState.test.tsx`; one backend test documenting the post-sweep status |
| Loaded bus shows 0 MW | Bug 4 | §2.3 — additive `p_set_peak` on Load rows; "Peak load" row | `BE/routers/network.py` (`_get_component` for Load), `FE/layout/PropertiesPanel.tsx`, `FE/api/types.ts` | `test_network_loads_peak.py`, `PropertiesPanel.peakLoad.test.tsx` |
| Raw numbers | Bug 1 | §2.4 — `fmtEnergy`/`fmtCurrency` in the DtC tables and FMEA € columns | `EhReferenceDesignPanel.tsx`, `FmeaTab.tsx` | `…formatting.test.tsx` ×2 |
| `/api/projects/unclaimed` console noise | Bug 7 | §2.5 — `enabled: authEnabled` | `FE/pages/ProjectsHomePage.tsx` | `ProjectsHomePage.unclaimed.test.tsx` |
| Template does not open the workbench | Obstacle 2 | §2.6 — `addTab` + navigate to `/app?project=` from `/projects` (template, file and clone tabs) | `FE/layout/NewProjectWizard.tsx` | `NewProjectWizard.templates.test.tsx` (+2) |
| Tagging tour dead-ends | Obstacle 3 | §2.7 — `GuideButton.prepare`, select a bus, open Properties, `propertiesEditRequest` → Edit; `reveal` ids `props-edit-bus/link`; Guide button on the Bus card; Link step optional | `GuidedTour.tsx`, `EhReferenceDesignPanel.tsx`, `prepareTaggingTour.ts`, `uiStore.ts`, `PropertiesPanel.tsx`, `cardKit.tsx`, catalogue | `GuidedTour.prepare.test.tsx`, `EhReferenceDesignPanel.taggingTour.test.tsx`, `PropertiesPanel.editRequest.test.tsx`, `test_guides.py` |
| Send enabled without an API key | Obstacle 9 | §2.8 — disable on `chat_ready === false`, fail-open on unknown, inline `ApiKeySetup` | `FE/components/ChatPanel.tsx`, `ApiKeySetup.tsx` (invalidate health) | `ChatPanel.sendGate.test.tsx` |
| Failed verdict has no next step; "ok" chips red | Obstacle 5 (part) | §2.9 — `certificationHeadline.next` per verdict, `eh-certification-next`; `statusTone('ok')` → success token | `EhReferenceDesignPanel.tsx`, `index.css`, catalogue `enter` line | `EhReferenceDesignPanel.verdictNext.test.tsx` |
| Quick win: name + finished cue | Obstacles 1, 4 | §2.10 — header "Energy Hub reference design"; `eh-study-finished-cue` scrolls to `eh-report`; banner stays while running | `EhReferenceDesignPanel.tsx` | `…finishedCue.test.tsx`, `…banner.test.tsx` |
| Smoke script skeleton | — | §8.4 — `scripts/smoke-guided.mjs --phase P22.9` | `FE/scripts/smoke-guided.mjs` | runs green |

**Not in P22.9 (deferred, recorded):** bug 5 (FMEA severity-0 rows without explanation), bug 6 (one-off 409 on resume), obstacles 10–12 (popover placement, `gen_zero_costs` warning, misleading save path). Obstacles 1, 4, 6, 7, 8 are answered by the Guided flow (mapping in spec §1) and, for 1/4, partly by the quick win above.

**P22.9 integration gate (spec §8):**
- [ ] 1 full backend suite — zero new failures vs. baseline
- [ ] 2 targeted EH e2e set green (`test_energy_hub_templates_e2e.py`, `test_energy_hub_review.py`, `test_guides.py`, `test_live_network_untouched.py`, `test_chat_tools_endpoint_map.py`, `test_chat_tools_dispatch.py`)
- [ ] 3 `npx tsc --noEmit -p .` clean
- [ ] 4 `npx vitest run` — zero new failures
- [ ] 5 browser smoke `--phase P22.9` (spec §2.11 path) green, screenshots kept
- [ ] 6 independent QA-gate review: GO (`docs/superpowers/qa/2026-09-27-guided-mode-gate-P22.9.md`)
- [ ] 7 Expert-unchanged review signed (only the fixes above differ)

---

## P23 — Guided / Expert mode

Spec §3.

- **State.** `uiStore.uiMode: 'guided' | 'expert'` under `network-diagram:ui-mode`, `uiModeExplicit` under `network-diagram:ui-mode-explicit`, density pattern (§3.1).
- **Default rule** (§3.2 truth table). A stored explicit choice always wins; otherwise a first-time user (no `network-diagram:*` key other than `theme-schema`, no `pypsa-guide-seen:*`, no `results:active-tab`) starts `guided` and that is persisted once; an existing user starts `expert`. `FIRST_RUN` is a module-level constant evaluated **before** `create()` because `storedTheme()` writes `theme-schema` during store creation. Creating a project from an EH template switches to `guided` unless the choice was explicit (`noteTemplateProjectCreated`, hooked into the wizard's template mutation).
- **Switch.** Segmented control in `AppHeader` (`ui-mode-switch`), palette entry `act-ui-mode` (§3.3).
- **Hiding in Guided** (§3.5 table): sidebar keeps Assistant, **Hub design**, and Save / Recent / Projects home; DATA and SIMULATION sections, the `ModeSwitcher` and (through SIMULATION) the Solve Queue row are hidden; Results shows `adequacy` and `fmea` (`expertOnly` on the rest, filtered like `multiOnly`). Hidden panels stay reachable through the palette and `ui_open_panel`. Switching to Guided prunes a hidden slide panel to `hubDesign`/null and coerces a hidden results tab to `adequacy` without writing storage (§3.7).
- **`hubDesign` panel slot** (§3.6): `SlidePanel` member, `PANEL_META`, `FULL_SCREEN_TABS`, placeholder component, `_normalizePanelId` alias, `SAFETY_PANEL_ENUM`; auto-opens once per project in Guided.

| Task | Files | Tests |
|---|---|---|
| Store fields, keys, `FIRST_RUN`, actions, switch-time pruning | `FE/store/uiStore.ts` | `uiStore.uiMode.test.ts`, `uiStore.firstRunOrder.test.ts` |
| Header switch + palette entry | `AppHeader.tsx`, `CommandPalette.tsx` | `AppHeader.uiMode.test.tsx`, `CommandPalette.uiMode.test.tsx` |
| Sidebar test ids, Guided render conditions, Hub design row | `layout/Sidebar.tsx` | `Sidebar.uiMode.test.tsx`, `Sidebar.expertUnchanged.test.tsx` (snapshot from base) |
| Results `expertOnly`, `effectiveTab`, tab ids | `pages/Results.tsx` | `Results.uiMode.test.tsx` |
| Panel slot, auto-open | `App.tsx`, `pages/hubDesign/HubDesignPanel.tsx` (placeholder), `ChatPanel.tsx`, `BE/services/chat_tools_schema.py` | `App.hubDesignAutoOpen.test.tsx`, `BE/tests/test_chat_tools_schema_panels.py` |
| Template rule hook | `layout/NewProjectWizard.tsx` | covered in `uiStore.uiMode.test.ts` + wizard test |

**P23 integration gate (spec §8):**
- [ ] 1 full backend suite — zero new failures
- [ ] 2 targeted EH e2e set green
- [ ] 3 `tsc` clean
- [ ] 4 `vitest` — zero new failures
- [ ] 5 browser smoke `--phase P23`: fresh context → Guided on; sidebar shows Assistant / Hub design / Project basics only; Results shows two tabs; switch to Expert → everything back; reload keeps Expert; existing-user context (seeded `network-diagram:current-project`) → Expert; template creation with implicit Expert → Guided
- [ ] 6 QA-gate review GO (`…-gate-P23.md`)
- [ ] 7 Expert-unchanged review signed

---

## P24 — The hub-design step cards

Spec §4 (backend) and §5 (frontend). A full-width panel `hubDesign`, opened by default in Guided mode when a project is open. Five steps sit in a progress rail, each a **card** with at most three decisions, plain-language text, a "?" hover on every term (guide catalogue), **Ask about this** (prefills a question) and **Let the assistant do this** (sends a request, P25).

1. **Start.** Pick a template (the three EH templates, with one-line purposes) or "use my network". It shows the template's provenance ("synthetic example data").
2. **Site.** Readiness in words: grid connection (`import.links`, `import_p_nom_mw`), critical load, grid-strength data present/missing, outage data (`outage_units.count`). Each gap has a fix button that calls the assistant. The one editable choice is the site type (archetype) in plain words.
3. **Goal.** Allowed shortfall per year (LOLE, default from the template / `pack_defaults`), "how strict on energy" (ENS, advanced), VOLL read-only with an assistant fix. Budget and stages hidden (template / pack defaults). Then **Run study** — the same `buildEhStudyBody` as the Expert panel.
4. **Results.** Plain-language cards from `GET /api/results/eh_review` (the same `review_latest` the chat tool uses): headline verdict (§5.5), cost, top risks, "what this study could not establish". A link opens the full Expert report.
5. **Improve.** High/medium findings as cards with **Why**, **Let the assistant do this** (the finding's action) or **Ask**. **Check risks (FMEA)** runs the B/C sweep with the stress registry (lifted `useStartFmeaSweep`), "Add a stress scenario" via the assistant.

State machine `no_project / no_study / running / done / stale` (§5.4), auto-advance on `running → done` (§5.6), delegation texts (§5.7), text sourcing rules (§5.8).

| Task | Files | Tests |
|---|---|---|
| `review_latest` lift; `GET /api/results/eh_review` (200 / 204); endpoint map → the route; regenerate inventory | `BE/services/adequacy/eh_review.py`, `chat_tools.py`, `routers/results.py`, `chat_tools_schema.py`, `tests/fixtures/route_inventory_phase0.txt` (`PY tools/openapi_diff.py --phase0-fixture`) | `BE/tests/test_eh_review_route.py` (route == tool output; 204; running; `TOOL_ROUTES` pin) |
| Readiness additive keys `pack_defaults`, `outage_units`, `import_p_nom_mw` | `BE/services/adequacy/eh_readiness.py` | readiness test module (+3, existing keys pinned) |
| Catalogue: 20 `fields` keys, `hub_design` tour | `BE/data/guides/eh_fmea_guide.json` | `test_guides.py` (+`test_hub_fields_present`, `test_field_text_is_plain`; target pin already exists) |
| FE client | `FE/api/simulation.ts` | type-checked |
| Panel, rail, five cards, `Term`, `CardShell`, store, `delegate.ts` | `FE/pages/hubDesign/**` | `HubDesignPanel.flow`, `StartCard`, `SiteCard`, `GoalCard`, `ResultsCard`, `ImproveCard`, `Term` tests (§5.10) |
| Lifted hooks | `FE/hooks/useCreateFromTemplate.ts`, `FE/hooks/useStartFmeaSweep.ts`; `NewProjectWizard.tsx`, `FmeaTab.tsx` consume | hook tests; existing wizard / FMEA tests stay green |
| Expert panel exports + `eh_review` invalidation | `EhReferenceDesignPanel.tsx` | existing panel tests |

**P24 integration gate (spec §8):**
- [ ] 1 full backend suite — zero new failures
- [ ] 2 targeted EH e2e set green (incl. `test_chat_tools_endpoint_map` with the regenerated fixture)
- [ ] 3 `tsc` clean
- [ ] 4 `vitest` — zero new failures
- [ ] 5 browser smoke `--phase P24` on the data-center template: Start (template banner) → Site rows populated → Goal default 3 h/yr → Run → running → done → rail at Results, headline "Not certified…" → Improve lists findings → Check risks → FMEA tab; Open full report scrolls `eh-report` into view; buses table equal before/after
- [ ] 6 QA-gate review GO (`…-gate-P24.md`)
- [ ] 7 Expert-unchanged review signed (Expert panel and FMEA tab behave as before the hook lifts)

---

## P25 — The assistant does the steps

Spec §6.

- **Send from a card.** `chatStore.sendRequest(text, {source})` queues; ChatPanel's effect dispatches through `dispatchSend` (the typed-message path) when not streaming and no confirmation card is pending; dedupe within 2 s; attachments never included; write confirmation cards untouched (§6.1).
- **Guided prompt awareness.** `ui_context.ui_mode` and `guided_step` from `buildUiContext`; backend allow-lists them, omits them in Expert (byte-identical block), and in Guided appends `_GUIDED_MODE_ADDENDUM` to the per-turn user content — the system prompt is untouched (§6.2).
- **`suggest_eh_setup` (read).** `services/adequacy/eh_setup.py`; heuristics for import Link, PoC bus, critical buses, missing outage data; ready `update_component` / `bulk_update_components` actions that validate against their schemas; never applies anything (§6.3). One sentence added to `_EH_GUIDE_CHAINING`.

| Task | Files | Tests |
|---|---|---|
| Queue + `sendRequest` | `FE/store/chatStore.ts`, `FE/components/ChatPanel.tsx`, `FE/pages/hubDesign/delegate.ts` | `chatStore.sendRequest.test.ts`, `ChatPanel.sendRequest.test.tsx`, `delegate.test.ts` |
| `ui_mode` / `guided_step` | `FE/utils/uiContext.ts`, `BE/services/chat_service.py` | `uiContext.uiMode.test.ts`, `BE/tests/test_guided_mode_prompt.py` (Expert block byte-equal; system prompt pinned) |
| `suggest_eh_setup` | `BE/services/adequacy/eh_setup.py`, `chat_tools.py`, `chat_tools_schema.py`, `BE/tests/_tool_actions.py` (shared `_validate_action`) | `BE/tests/test_eh_setup_suggest.py` (three templates with tags stripped recover the builder's tags; actions validate; network untouched; bulk collapse) |
| Stub provider scripted tool call (smoke prerequisite) | `BE/smoke/stub_openai_endpoint.py` if needed | smoke |

**P25 integration gate (spec §8):**
- [ ] 1 full backend suite — zero new failures
- [ ] 2 targeted EH e2e set green (+ `test_eh_setup_suggest.py`, `test_guided_mode_prompt.py`)
- [ ] 3 `tsc` clean
- [ ] 4 `vitest` — zero new failures
- [ ] 5 browser smoke `--phase P25`: Improve → "Let the assistant do this" → request appears as a user message, `ui_mode: guided` in the recorded request body (stub records it), a **confirmation card** renders, decline; a second click while streaming is queued and sent after; Expert request bodies carry `ui_mode: expert` and no addendum
- [ ] 6 QA-gate review GO (`…-gate-P25.md`)
- [ ] 7 Expert-unchanged review signed (only the `_EH_GUIDE_CHAINING` sentence differs)

---

## P26 — Verification

Spec §7.

- [ ] Real-app click-through of the Guided flow on all three templates (`smoke-guided.mjs --phase P26`): first-time user → Guided → template → site → goal → run → results → improve via the assistant (confirmation shown) → FMEA check; mode persistence both ways.
- [ ] Any new finding fixed test-first in the owning phase's files; recorded here.
- [ ] Verdicts observed per template recorded (expected from P20: data center fail, H₂ hub no target, microgrid inconclusive; differences are recorded, not forced).
- [ ] Independent QA gate GO (`…-gate-P26.md`), full backend and frontend suites green vs. baseline, `tsc` clean.
- [ ] Close-out note: what Guided still does not cover (spec §1 non-goals) and the deferred P22.9 items.
