<!-- Research thread produced on 2026-09-28 by a delegated research agent for the assessment
docs/superpowers/assessments/2026-09-28-investment-study-gap-analysis.md. Kept verbatim so URLs, matrices and
evidence tags survive. Market facts are search-extract based: the session's network policy blocked direct fetches
of most vendor sites. Tags: [V] verified against the cited page's extract, [V-code]/[V-fetched] read in source,
[BK] background knowledge, [I]/[INF] inference. Verify a claim at its URL before quoting it externally. -->

# Agent C: roadmap and plan synthesis for pypsa-gui, gridspine and docs/superpowers

**As of:** 2026-09-28. Repo HEAD is `67c4c77` on master; the latest merges are #55 (EH zonal MC), #54 (FOM) and #57.
**Method:** I read these in full: the EH reference-design spec and panel spec, the solution-FMEA spec v4 (§§1–12), the gridspine design, the agent-layer v5 spec (§§1–6), the model-horizon guided-steps spec, the trustworthy-numbers spec, the desktop-app spec (§§1–3), the cloud/SaaS plan (non-goals and sequencing), the EH gaps plan, the plans and findings for 2026-09-26/27/28, the FOM finding, the gridspine inc-3 handoff, notes/next-steps, OPEN-ITEMS.md, CHATBOT.md (study-report, campaign, explain, FMEA-tools, limits), CHATBOT_FEATURE_RESEARCH.md and pitfalls-myopic-and-cost-reporting.md. For every other plan and finding I read the title plus about the first 30 lines, or grepped its status line.
**Checking status against code:** where a status mattered, I checked what the code actually contains (for example `pypsa-gui/backend/services/adequacy/*`, `gridspine/drivers/*`, `chat_tools.py`) rather than trusting the checkboxes. The repo itself warns that checkboxes go stale (`plans/2026-08-13-remaining-implementation.md`: "The `- [ ]` boxes below are STALE").

---

## (i) Product thesis, in the docs' own terms (5 sentences)

1. **User.** The owner is a power-systems consultant at **Hitachi Energy Power Consulting**. The gridspine design says "Owner: Hao (Hitachi Energy Power Consulting)" and calls the tool an "internal consulting accelerator", and the EH plan brands the target deliverable a "PGGI **Energy Hub reference design**". The users are consultants who build PyPSA models *without Python*. The README's pitch is "build, edit, solve and analyse energy-system networks entirely in your browser, no Python scripting required", aimed at "quick exploration, teaching, or stakeholder demos".
2. **Core job: size a system to a reliability target at least cost, with honest numbers.** The FMEA spec states it as: "state a reliability target, get a least-cost plan that meets it, and see a ranked, model-computed account of which failure modes drive the residual risk". The EH wrapper packages this per site archetype (`strong_grid` / `weak_flexible` / `off_grid`) into one `ReferenceDesignReport` "linking availability and cost".
3. **Second job: hand planning results downstream to dynamics.** The gridspine planning→dynamics pipeline runs PyPSA nodal UC → pandapower AC/N-1/N-2 → snapshot ranking → a PSS/E `.raw` + `.dyr` + contingencies + assumptions-ledger bundle for **PowerFactory / PSS/E**. Own IP is the "canonical schema, snapshot ranking, handoff contract, dynamic parameter templates, assumptions ledger, reporting, compliance rules (later)".
4. **Differentiator: trustworthiness, not breadth.** ADR-0001 says unresolvable figures ship as `null`, never `0`. Every number carries engine and fidelity provenance. Each report section is `ok | not_established | skipped`. Payloads carry "honesty notes". The docs repeatedly refuse any claim the model cannot back ("No number produced by Phases 0–4 may be compared to a statutory standard").
5. **Delivery: three front doors, one engine.** A browser workbench, an unsigned internal **desktop app** (Windows / macOS arm64), and a multi-tenant **org server** (SaaS migration only partly done). An LLM **copilot** gets "parity across all study types via a shared action layer" (112 tools, provider-agnostic). The copilot is meant to narrate engine payloads, never invent numbers ("It writes no prose. The caller narrates, and can only narrate what is in the payload.").

**Implied from the sources, not stated as a thesis in any doc:** the concrete client use cases are energy hubs with critical loads (datacentre-type), hydrogen (LCOH), and weak-grid connections that need a dynamics screen. **Investment decision support in the financial sense (NPV/IRR/payback/revenue/tariffs) is not a stated goal anywhere.** The docs' "cost" means least-cost system planning cost, and "bankable" appears only in the reliability sense ("A design without a certified LOLE is not bankable").

---

## (ii) Programme timeline and status

Legend: **shipped** = on master with tests/evidence · **partial** = some deliverables landed, some not · **planned** = spec/plan exists, not built · **deferred** = explicitly pushed out by a spec.

### A. Chat / agent layer

| Item | Date | Status | Evidence |
|---|---|---|---|
| Chatbot v6 hardening (Track A: retries, eviction, truncation, confirmations, untrusted fence) | 07-25 → 09-12 | **shipped** | CHATBOT.md; security PR #18; `findings/2026-09-10-a-tool-result-can-close-the-untrusted-fence.md` FIXED |
| Adaptive orchestrated agent layer v5 (Track B: LangGraph + LiteLLM triage/coordinator/waves) | spec 07-25, plan CLEARED 07-25 | **planned, not built** | No `backend/agent_orchestrator/`; no langgraph/litellm anywhere in pypsa-gui |
| LLM provider seam + profiles (Anthropic/OpenAI/Moonshot/Qwen/Ollama/LM Studio) | 08-05 → 09-09 | **shipped** | `services/llm_provider.py`, `llm_openai_compat.py`; CHATBOT.md "Provider profiles" |
| Voice-to-text, assistant dock, presence/deixis, chat compare + navigate | 07-26 → 08-07 | **shipped** | Frontend has SpeechRecognition, AssistantDock; `compare_scenarios` + `ui_event navigate` in `chat_tools.py:2358,2646` |
| Chat turn-loop decomposition | 09-09 | **partial** | Plan status: "Phases 0, A, B, C, D done; Phase E's … result-shaping join still open" |
| Campaign budget, `build_study_report`, `explain_investment`, `diagnose_network`, adequacy + EH chat tools | 08-27 → 09-26 | **shipped** | CHATBOT.md; `run_eh_study` (#51) |
| Composite tools: `plan_what_if`, `generate_run_report`, `diagnose_results`, `solve_overview`, `sanity_check_results`, `submit_plan`, `undo_my_last_chat_action` | research 06-08 | **planned / never implemented** | `chat_tools.py:4495`: "NOT YET IMPLEMENTED. This block previously registered eight names … that were never defined". None of the 7 is in the schema. |

### B. Desktop app

| Item | Status | Evidence |
|---|---|---|
| Phase 1a: local mode, SQLite, SPA served by FastAPI | **shipped** | Plan execution log 2026-07-27; `backend/local_mode.py` |
| Phase 1b: human-readable storage + legacy importer | **shipped** | "phase 1b COMPLETE (2026-07-27)", 1459 tests |
| Phase 2a: pywebview shell, safe quit | **shipped** | `backend/desktop/{launcher,gui,single_instance,splash}.py` |
| Workstreams I–L: freeze, installers, key handling, CI | **partial** | `pypsa-gui.spec`, `build-macos.sh`; gridspine inc-6 froze a 674 MB Linux onedir; frozen-deps gap FIXED. The Windows runbook says: "Every Windows-specific decision … is currently *reasoned from documentation*, not measured." |
| Code signing / notarization; auto-update | **deferred** (spec non-goals) | D8 "Unsigned for v1. Internal audience."; D9 "implement later" |

### C. Tenancy / SaaS

| Item | Status | Evidence |
|---|---|---|
| Multi-user org tenancy (Postgres, orgs, ACL, locks, admin) | **shipped** (flag `PYPSA_GUI_AUTH_ENABLED`) | Tenancy plan; `routers/auth.py`, `admin.py`, alembic |
| SaaS Step 0a (authz sweep, CSRF, CORS) / 0b (session-bound active project) | **shipped** | "✅ **LANDED**" in the SaaS plan; 0b at `09bd7020` |
| Security hardening: prompt injection, per-route authz, chat session owner | **shipped** (PR #18) | `session_owner_allows` in chat.py; lock check in `adequacy_worksheet.py`; changelog super-admin predicate |
| SaaS Step 1 (storage seam, opaque keys), Step 2 (worker queue), Step 3 (stateless web tier), Step 4 (S3, quotas, metering, observability) | **planned, not started** | No `services/storage_backend.py`. The plan calls Step 2 the "Biggest risk". |
| SaaS "single-user mode is removed" | **superseded in practice** | Conflicts with the desktop spec (local mode is "an additive flag, never a fork"), and local mode shipped. |

### D. Trustworthy numbers (roadmap items 1–4 in `specs/2026-08-01-trustworthy-numbers-design.md`)

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Structurally trustworthy numbers: nine economic surfaces, golden fixture, independent oracle | **shipped** | `findings/2026-08-01-economic-surface-disagreements.md` CLOSED; follow-up **FOM fix #54 (2026-09-27)** |
| 2 | `/results/*` range + resolution | **shipped** | results-range and results-tabs-window plans; verification record CLOSED (16/16) |
| 3 | **"Study → one deliverable. Solved network to a single document: assumptions, results, figures, scenario comparison."** | **planned (one line only), never specified** | No spec or plan exists. Partial substitutes: `build_study_report` (adequacy only, JSON, no prose), `ReferenceDesignReport` (JSON + per-table CSV), Asset Detail `export.xlsx`. No PDF, Word or HTML report anywhere. |
| 4 | "Modelling depth, chosen from evidence after 1–3. Deliberately unspecified." | became the FMEA → EH programme | — |

### E. Editing and horizon UX

| Item | Status | Evidence |
|---|---|---|
| Asset editing A (editable grid, Excel clipboard), B (catalog-driven "+ Add parameter"), C (drop-on-bus) | **shipped** | Plans A/B/C; "Scope C has already landed" |
| Asset Detail results tab (eleventh tab: revenue, net profit, LCOE, xlsx export) | **shipped** | `routers/asset_results.py` |
| Model horizon defects (11), residuals, **guided steps** | **shipped** | `frontend/src/pages/modelHorizon/{HorizonSummary,StepMode,StepYears,StepPeriodEconomics,StepWindow,StepSampling,StepWeights}.tsx` |
| Compare-tab correctness, solve-queue full pass, project write safety, modal a11y, unplaced buses, carrier icons | **shipped** | Findings marked FIXED/CLOSED |

### F. Solution FMEA / adequacy (spec v4, §11 phases, extended to 12i)

| Phase | Content | Status |
|---|---|---|
| 0 | Outage-rate attrs (value + FOR/EFORd basis + MTTR), `SLACK_CARRIERS` over 30 sites, canonical lost-load, shed-hours, `AdequacyReport` contract | **shipped** |
| 1 | Two-tier slack (DSR vs load_shedding), system ENS cap + per-zone ceilings, target UI, achieved-vs-target | **shipped** |
| 2 | COPT screening + leave-one-out criticality | **shipped** |
| 3 | IEC 60812 worksheet (computed + persisted expert rows, mitigability, CSV) | **shipped** |
| 4 | Class B (Link outages) + Class C (parametric stress) | **shipped**. Class-C real climate years **deferred** (next-steps D1). |
| 5 | Shed-hours bisection + on-demand ε-constraint frontier | **shipped** (`frontier.py`, `frontier_loop_runner.py`) |
| 6 | Sequential Monte Carlo (LOLE/EUE) | **shipped** |
| 7 / 8 / 9 | Coupling loop (ENS lever) / reserve margin / margin loop (firm-capacity lever) | **shipped** |
| 10 / 11 | Study scoping / study swap guard (BINDING specs) | **shipped** |
| 12, 12a | ELCC derating v1 ("not as scoped"); 12a superseded by 12c-pre | **rejected / superseded** |
| 12b, 12c-pre, 12c-0, 12c, 12d | Net-load window; profiled outage units; one demand basis; portfolio ELCC; activity/vintages | **shipped** |
| 12e / 12f / 12g / 12h | Abort everywhere + cheaper `/copt`; NaN bound refused; NaN finite inputs refused; static CF + `p_max_pu_includes_outages` flag | **shipped** (12f took v6 after 16 blockers) |
| 12i | Constant-series fold | **closed unbuilt**: "do not build. The defect is real; every available remedy is a worse trade" |
| (spec phase 6 optional) | PRAS / Antares exporters | **deferred** (next-steps D2: "Large. A new integration") |
| Integration | Whole branch merged as PR #5 (`75e3a1a`) + PR #12; QA drivers `qa_adequacy_journey.py` (91 checks), `qa_adequacy_studies.py` (72) | **shipped** |

### G. gridspine (`specs/2026-08-27-gridspine-design.md`)

| Increment | Content | Status | Evidence / caveat |
|---|---|---|---|
| 1 | 39-bus vertical slice: PyPSA nodal UC → pandapower → `.raw` v33 → PowerFactory <1% harness | **shipped, but its validation gate is open** | Handoff: "Increment 1's <1 % gate has still never been closed against an independent oracle"; PowerFactory fixture test skipped |
| 2 | 8760 h ranking (min inertia, max IBR, peak, import), top-k selection | **shipped** | — |
| 3 | AC N-1 (lightsim2grid), LODF N-2, IEC 60909, SCR, `.dyr`, contingencies.csv, ledger README, bundle | **shipped** | 474 tests; v4 year run; ruling 30: DC proxy has rho −0.57 against AC severity (superseded by AC severity in F2) |
| Follow-ups F1–F7 | AC severity ranks, resume from dispatch, lightsim2grid in-service fix | **shipped** | `plans/2026-09-05-gridspine-follow-ups.md` |
| 4 | Action layer + solve-queue jobs + GUI panel + chat tools; `project_kind` column | **shipped** | "task 6 UNVERIFIED against a live model; task 7 verified by component tests only" |
| 5 | Editable package install (D5); a solved GUI project as dispatch source (D3) | **shipped** | "LANDED … 2026-09-08/09" |
| 6 | Frozen desktop app ships the pipeline; PowerFactory **read-back** by file upload | **shipped** | "LANDED … 2026-09-09" |
| Spec phase 4 / later | PowerFactory **API** exporter; full grid-strength screen (SCR/WSCR/ESCR, impedance, RoCoF → EMT); **compliance rule engine** (VDE-AR-N 411x/412x/413x, RfG); mitigation sizing; batch; **clustered producer** (needed for PyPSA-Eur scale); PowSyBl/CGMES | **planned / deferred** | Design "Deferred / revisit triggers" |
| Variant 2: **connection study** | — | **planned, not built** | UI shows "[3] connection is greyed with 'later'"; no `gridspine/drivers/connection.py` |

### H. Energy Hub reference design (`specs/2026-09-14`, `plans/2026-09-14-eh-reference-design-gaps.md`)

The final gate verdict was **GO WITH BINDING CONDITIONS** (2026-09-14). Every phase ran red → green → independent assessor.

| Phase | Content | Status | Gate |
|---|---|---|---|
| P0 | Contracts: `models/energy_hub.py`, completeness enum, decisions 1–18 | **shipped** | — |
| P1 | Archetype packs (`archetypes.py`), import overlays, DSR preflight | **shipped** | GO WBC → satisfied |
| P1.5 | `EHStudyRunner` / `run_eh_study` + HTTP (`/results/eh_study`) | **shipped** | GO (after NO-GO fixes) |
| P2 | Class-B residuals | **shipped / closed N/A for the SCLOPF merge** | GO |
| P3a / P3b / P3c | Redundancy scenarios / outer-loop select / import-cap + storage-duration levers | **shipped** | GO |
| P4a / P4b | DtC stress (fixed plan, islanding) / DtC planning (islanded + retained critical demand, §10) | **shipped** | GO |
| P5 | `ReferenceDesignReport` assembler + TEA (LCOE) + FE panel + sibling CSV tables | **shipped** | GO (after NO-GO LCOE fix) |
| P6(a) / P6(b) | Dedicated-bus multi-energy ENS (#49) / per-Load VOLL slacks (#50) | **shipped** | GO |
| P7 | RAM v1 = rate library + provenance chips | **shipped**. Spare-lead-time modifier **deferred**; planned-outage MC **deferred**; detectability **dropped** | GO |
| P8a / P8b | Synthetic Class-C profiles / real climate-year bundles | P8a **shipped**; P8b **deferred** (procurement) | GO WBC |
| P9 | SCR gate (warn-only; `fail` reserved) + EMT flag | **shipped** | GO WBC |
| EH chat tools | `run_eh_study`, report kinds, abort | **shipped** (#51) | — |
| Wire skipped stages | `frontier`, `mc_certify` (plan on ENS, certify on MC LOLE), `fmea_top`, LCOH | **shipped** (#53, 2026-09-26) | e2e 39/39 |
| Zonal MC import outages | Link sampled as a unit (v1) + grid-side surplus (zonal v2) | **shipped** (#55) | 64/64 |
| Zonal MC open items | Grid storage dispatch, several grid areas, two-area COPT, common-mode events | **shipped** (#55, 2026-09-28) | 108/108; 5866 backend passed |
| Still deferred | Class-C authoring UI; real climate data; planned-outage MC; spare lead time; exact multi-state per-mode attribution under the grid distribution; common-mode event rate library | **deferred** | findings 09-28 "What is still deliberately not done" |

### I. Code-health lifts

| Item | Status |
|---|---|
| Backend god-file decomposition (`solver_service.py` 5,783 → ~1,370; PR #6) | **shipped** |
| Router lifts ×11 (2026-09-13: copt-modes, lost-load, mc-study, network bulk/bus/crud/global-constraints/line/profiles/undo, planning-loop) | **shipped** (`services/adequacy/*_runner.py`, `services/network_*.py`, `routers/network_profiles.py` exist) |
| Study polish (`_abort_study`, margin_loop QA section) | **shipped** (`routers/results.py:866`) |
| Next god files: `routers/projects.py` (2,964 lines, `_save_context` 454) | **planned** ("next, same shape") |

---

## (iii) Explicit non-goals and deferrals, across all specs

**EH reference design (§8, v1 non-goals)**
- "Joint MILP of unit commitment + redundancy integers in one solve"
- "In-tree EMT simulation / dynamics↔adequacy co-simulation" (decision 10: "Dynamics = **feasibility gate, not co-opt lever**")
- "Statutory PRAS/Antares replacement"
- "Full maintainability / spares logistics program"
- "Treating import caps as firm interconnection adequacy without outage modelling". This is now partly addressed by the zonal MC.
- "Per-load DtC attribution on the current single slack-per-bus geometry". P6(b) added per-Load slacks for multi-energy.
- "Replacing the existing FMEA worksheet UX"
- "Rebuilding shipped Class-B Link sweep"
- Decision 13: "'Configurable outputs' (v1) = fixed report schema + optional section inclusion / export columns — not arbitrary metrics."
- Decision 7: "Annual ENS/LOLE alone does not claim multi-day autonomy."
- EH panel non-goals: "New Adequacy IA or marketing layout"; "P2 / P6–P9 modelling".

**Solution FMEA (v4)**
- "Not modelled, each materially affecting the answer: planned/maintenance outages …, load-forecast uncertainty, and interconnector availability".
- Carrier scope is "Electricity only". "Non-electrical service failure is invisible." Partly reopened by EH P6.
- VoLL is "Single value now, schema shaped for segments later".
- "No RPN and no Action Priority."
- "Sweep configurator + progress" and "Frontier explorer" were deferred in §8.2 and later shipped as panels.
- PRAS/Antares exporters are "Optional".

**gridspine**
- "Target grid scale / dynamics fidelity deferred until a real client grid arrives".
- PowSyBl is deferred behind an MPL licence check; the clustered producer is deferred ("build when a study exceeds nodal-UC solve capacity").
- The grid-strength screen and compliance engine are "connection-study phase 2".

**Desktop app (§2)**
- "Rewriting the tenancy/ACL layer"; "Multi-project concurrency beyond what the backend does today"; "Auto-update"; "Code signing and notarization"; "Removing the web/Postgres deployment". Also D15: no size trim (~500–600 MB).

**Cloud / SaaS**
- "Blobs *inside* the database"; "SSO/SAML, per-node ACL, **billing implementation**, K8s manifests"; "Rewriting the solver". Also: "Do not redesign the workbench, Scenarios panel, or Compare UX".

**Multi-user tenancy (v1)**
- OAuth/SSO/magic link, public self-registration, multi-org membership. Also: "Do not redesign Scenarios panel / Compare UX beyond ID/ACL wiring."

**Trustworthy numbers**
- Staleness; "Distinguishing 'unset' from 'deliberate zero' for `capital_cost`, `marginal_cost`, `fom_cost`. **Measured as infeasible**"; extending `registry.py` to results/compare. ADR-0003: no generic `Resolved[T]` wrapper.

**Model horizon guided steps**
- "Any change to what a control does, to the API, or to the backend." It is "Deliberately NOT a staged wizard with one final commit" and has "No 'cancel the whole setup'".

**Agent layer v5 / continuation plan**
- "v1 agents PRODUCE artifacts; no side effects." `sensitivity=private` is refused until a local tier exists. The orchestrator is triggered only explicitly (Analyze button / `/analyze`). The continuation plan defers "Full guided what-if macro and click-to-navigate canvas (follow-up plan)". D10: "Defer `plan_what_if` / `submit_plan` / remaining composites until after Track B".

**RAM (EH P7)**
- Spare-lead-time modifier and planned-outage calendars in MC are deferred; detectability is dropped.

---

## (iv) Archetype / use-case coverage the plans target

| Use case | Where planned | Status | Notes / honesty limits |
|---|---|---|---|
| `strong_grid` (grid import is an economic resource; frontier + least-cost at stated ENS) | EH §3, MVP-A | **shipped** | "SCR gate optional / informational"; MC certify optional |
| `weak_flexible` (tight import `p_nom` overlay; DSR opt-in; DtC stress default; MC LOLE certify required; SCR warn) | EH §3, MVP-B | **shipped** | Import `p_nom` only: `import_energy_mwh_per_year` is "reserved" (warn, no constraint). The import is now outage-sampled (zonal v2 + common-mode). |
| `off_grid` (imports forced out via `p_max_pu`→0; storage + fuel primary; storage-duration scenarios required) | EH §3, P3c | **shipped** | "Report autonomy scenarios explicitly (do not equate annual ENS with multi-day sufficiency)" |
| **Data centres** | Not named as an archetype | **implicit only** | "datacentre waste-heat" appears only as a Link converter type in the README and dispatch code. There is no data-centre pack, no Tier/uptime metric, no PUE model. The "DtC critical loads" concept is the closest proxy. |
| **DtC critical loads** (critical bus/load tags, islanding contingencies) | EH decision 8, P4a/P4b, §10 | **shipped** | The docs never expand "DtC". Attribution is `bus_aggregate_not_per_load`; honesty notes are `no_per_load_attribution`, `retained_critical_demand`, `stress_on_fixed_plan`, `islanding_is_planning_contingency`. Critical and non-critical loads must be on different buses. |
| **Hydrogen / LCOH** | EH decision 9, 09-26 WP4; `services/results/lcoh.py` | **shipped** | LCOH per electrolyser Link; `lcoh_status` `ok/skipped/not_established`, "never `0`". e2e: €0.50/kg on the fixture. Unmet H₂ reported via P6 when buses are carrier-dedicated. |
| **RAM** (reliability, availability, maintainability) | EH P7 | **v1 only** | Rate library + `rate_source` provenance chips. The note says "No claim of full RAM/CMMS"; maintainability and planned outages are deferred. |
| Multi-energy (H₂/heat unmet) | EH P6 | **shipped (a+b)** | Electrical-only adequacy remains the default. |
| Redundancy (N-1 generation/conversion, parallel storage) | EH P3a/P3b | **shipped** | Discrete scenarios + outer-loop select; "Not continuous FOR derating" |
| Weak-grid dynamics (SCR → EMT) | EH P9, gridspine `static/strength.py` | **shipped (proxy)** | SCR proxy `eh_sk_mva / eh_ibr_mva` at `eh_poc` buses; warn-only; EMT is a flag only. |
| Planning → dynamics handoff (PowerFactory/PSS/E) | gridspine inc 1–6 | **shipped, IEEE 39-bus only** | Nodal only; the clustered producer (PyPSA-Eur scale) is not built; the PowerFactory oracle gate is not closed. |
| Grid connection study | gridspine variant 2 | **planned** | Greyed "later" in UI |
| National / system adequacy (COPT, MC, ELCC, reserve margin) | FMEA | **shipped** | Desktop-safe screening; not statutory-grade |

---

## (v) TEA and financial metrics in the plans (quotes)

**What the plans actually say:**
- EH decision 9: "**TEA** = post-process wrap (LCOE / optional LCOH) — no second cost engine."
- EH decision 3: "**Cost axis** = total system cost excluding shed (`excludes_shed_cost: true`), same as frontier. State period basis on every cost field."
- EH P5: "TEA: LCOE post-process from cost ÷ served energy — no second cost engine." (P5 re-gate was "GO (after NO-GO LCOE fix)", i.e. an "ENS-honest LCOE".)
- FMEA §5.2: "**Total system cost: CapEx + FOM + variable OpEx (fuel, VOM) + CO₂ cost — excluding load-shedding cost.**"
- FMEA §5.2 (the **only NPV mention in the entire docs tree**): "In a multi-period run `investment_period_weightings.objective` discounts costs, so the figure is an **NPV**, and the 'optimum' is an NPV optimum — not the annualised number a reliability standard implies. Label the axis accordingly and state the period basis."
- FMEA §5.5: "VoLL becomes a reporting parameter, not a design parameter".
- 09-26 plan: "A design without a certified LOLE is not bankable." The finding adds: "a report that *looks* adequate on ENS is now told, in its own words, that it is not bankable on LOLE." Here "bankable" means reliability certification, not finance.
- Asset Detail plan (07-31): `revenue_eur` "Σ p × bus price × weighting"; `net_profit_eur` "revenue − (fixed cost + VOM)"; `lcoe_eur_per_mwh` "(fixed cost + VOM) ÷ energy".
- CHATBOT.md `explain_investment`: "an extendable asset at an interior optimum earns ≈ zero net profit **by construction**, since the LP builds until the marginal MW breaks even. Without that note a near-zero `net_profit_eur` reads as a defect". The shadow-price revenue is an LP artefact, not a market forecast.
- FOM finding (09-27): "**Fixed cost reported to the user is annuitised investment plus FOM.** That is the number the objective paid, so it is the only one that reconciles."
- Trustworthy numbers: "`export.xlsx` is in the set deliberately. An export that disagrees with the screen it came from is the worst version of this bug, because it is the number that leaves the building."
- README: Economics shows "per-asset revenue, OPEX, annuitised CAPEX, net profit and **LCOE / LCOS / LCOH**". Solver settings include a discount rate and a CO₂ price/limit.

**What is absent (grep over docs/superpowers + pypsa-gui/*.md + pypsa-gui/docs):**
- **IRR: 0 hits. Payback: 0. Tariff: 0. PPA: 0.** NPV: 1 hit (above, as an objective-basis caveat).
- In code (services/routers/frontend/gridspine): 0 hits for NPV/IRR/payback/tariff/PPA.
- Also absent: WACC as a finance concept (only per-asset `discount_rate` for annuities), cash-flow schedules, CAPEX phasing, financing structure, revenue stacking (energy/capacity/ancillary/merchant), tariff or network-charge modelling, contract prices, sensitivity/tornado or Monte-Carlo-on-costs, and a CAPEX-vs-reliability business case beyond the ε-frontier.
- Note: "capture price" appears in code (10 files) as a derived per-asset metric (Asset Detail).

---

## (vi) Mentions of UX for novices, guided workflows, wizards, reports, narrative, PDF (quotes)

**Guided flows and wizards**
- Model horizon spec: "restructure the Model Horizon page from one 1,476-line scroll doing six jobs into a summary-first guided-step flow". Also: "Deliberately NOT a staged wizard with one final commit: that needs transactional support the backend does not have". It rejected the SolverSettings tab pattern because "six equal doors give a first-time user no ordering". **Shipped.**
- `NewProjectWizard.tsx` exists (modal a11y plan), and gridspine inc-4 added a kind picker "[1] capacity expansion unchanged, [2] planning → dynamics; [3] connection is greyed with 'later'".
- gridspine design: "From the user's perspective this is **one tool**: open the app, pick a study type, run the corresponding pipeline. Engine boundaries are invisible." and "User flow: open app → new study → pick type … → type-specific input form → run → per-stage progress → results, ranked snapshots, handoff bundle download, report."
- FMEA §8.1: "The app's model is **project → solve → results**. A sweep is **project → N solves → aggregate results**. That has no slot in the current IA: a sweep is not a result, it is a *study that produces* results." It recommended avoiding a new "Studies" area. EH later mounted its panel "**last** on `AdequacyTab` (after margin loop): packaging study over the stack the user already read".
- FMEA §5.1, novice guard: "The UI takes the energy target in **parts per ten thousand (‱)** … A user who types '99%' gets a cheap-looking, badly under-built plan."
- FMEA §10: "No number produced by Phases 0–4 may be compared to a statutory standard … The UI must say so at the point of display, not in a footnote." §8.2 lists "Provenance / fidelity badges — Cross-cutting, touches every number."
- FMEA §4.3: sector-coupled rows "must be rendered as 'out of scope for this metric', never as negative criticality — otherwise the worksheet recommends breaking the electrolyser."

**Reports and narrative**
- Trustworthy numbers roadmap item 3: "**Study → one deliverable.** Solved network to a single document: assumptions, results, figures, scenario comparison." This was **never specified or built.**
- CHATBOT.md `build_study_report`: "assembles the client-facing reliability write-up from everything the session established — **and everything it did not**" … "It writes no prose. The caller narrates, and can only narrate what is in the payload." It carries `required_disclosures`, `not_established` and `evidence_gaps`.
- EH decision 12: "**Report name** = `ReferenceDesignReport` only." The report is a JSON schema; the panel shows chips and tables plus per-table CSV. There is no document export.
- CHATBOT_FEATURE_RESEARCH (06-08) includes "#17 **Run-report markdown generation** — one-command shareable brief (objective, cost split, top carriers, emissions vs cap, sanity flags)"; "#7 **Guided what-if** — `clone → mutate → solve → compare` macro"; "#12 Scenario-comparison narration"; "#15 Suggested-prompt chips / slash commands — discoverability for a 70+ tool agent; empty state has no conversational starters"; "#25 Suggest-next-modeling-step advisor". Its root finding: "the system prompt is policy-only — zero PyPSA domain guidance". A domain guide (`_DOMAIN_GUIDE`, `_ADEQUACY_GUIDE`) now exists. However, `generate_run_report` and `plan_what_if` are **not implemented** (`chat_tools.py:4495`).
- CHATBOT_IMPROVEMENT_BACKLOG P2 #14: "Empty-state onboarding for the chat panel".
- README positioning: "hard for quick exploration, teaching, or stakeholder demos" → "keeps the full modelling power of PyPSA while removing the scripting barrier."

**PDF**
- PDF appears only as a **chat upload input** type (CHATBOT_UPLOADS_WORKFLOW, 100-page cap). **There is no PDF, DOCX or HTML report output anywhere in plans or code.** Exports are CSV, SVG/PNG charts and the Asset Detail XLSX; gridspine exports a `ledger.md` README inside the handoff bundle.

**Summary:** novice-friendliness appears only as local guards (units, disclosure chips, guided horizon steps, the kind picker). There is no plan for an end-to-end guided study workflow, an executive summary, a narrative report or a client-ready document. The house style consistently puts correctness ahead of polish; the EH panel explicitly lists "New Adequacy IA or marketing layout" as a non-goal.

---

## (vii) Open items and known defects that would block a client-facing investment study

### Security / tenancy (for a hosted or shared deployment)
1. **CRITICAL, open: the user-timeseries store is a process global shared across tenants.** `services/user_timeseries.py:39` still has `_user_ts` keyed `(component, attribute, column)`. The finding reproduced this: "org A uploads a profile for `L1`; org B activates its OWN project and reads A's values, then saves them into B's storage." "The desktop build is affected too, as a multi-project data-integrity bug." About 230 references across 13 modules are affected; the fix needs its own plan.
2. **OPEN-ITEMS.md is stale.** It was last verified 2026-09-12. Items 2 (worksheet lock), 3/5 (chat session owner) and 4 (changelog predicate) appear **fixed in code** by PR #18 (`adequacy_worksheet.py:89-104`, `chat_service.session_owner_allows`, `changelog.py:47-55`), but the index was not updated. Item 6 (`ProjectAccessDep` on 6 of 23 routers), item 8 (names not validated at the edge), item 9 (refused `/stream` switches model) and item 10 (cookie policy hardcoded to `.cursorusercontent.com`) were not re-verified.
3. SaaS Steps 1–4 are not started, so the web tier is single-process with in-memory state (one live network, results slots per project).

### Cost-reporting correctness (directly affects investment numbers)
4. **FOM was missing from fixed cost on every economic surface until 2026-09-27** (#54). Measured: gas LCOE 260 → 310 €/MWh. There was also a **365× FOM over-charge** on sub-annual models, and an older `capex_expansion` multi-period parse defect. Any numbers exported before #54 are suspect.
5. **Myopic pitfalls** (`pitfalls-myopic-and-cost-reporting.md`):
   - Summing per-period objectives is wrong by −42.9% / +22.2% depending on config. Rule: use `horizon_system_cost`.
   - Myopic freezes capacity at the first period silently. This is now only a **warning** (`myopic_capacity_locked_after_first_period`), and unserved energy absorbed growth (47 → 5,183 MWh).
   - A stale `_myopic_period_objectives` marker caused misreporting (fixed).
6. The myopic `assign_solution` TypeError appears on the pip stack (pypsa 1.3/pandas 3). pandas is now bounded below 3 (#24), and 14 of 43 baseline failures are myopic.
7. The trustworthy-numbers non-goal remains: `capital_cost=0` cannot be distinguished between "unset" and "deliberate zero".

### Model validity limits a client must be told about
8. Adequacy numbers are screening/proxy grade: "No number … may be compared to a statutory standard". Outages are independent (`MC_WARNING_V1`). Planned outages and load-forecast uncertainty are not modelled. **Real climate years are absent** (Class-C runs on synthetic/parametric data). The COPT class-A under the zonal grid carries a "±40 % caveat". The constant-series netting error can be +192.5% (disclosed, closed unbuilt). VoLL is a single value. PRAS/Antares cross-check is not built.
9. EH import energy caps (`import_energy_mwh_per_year`) are reserved with no constraint. The SCR gate is a proxy and warn-only. EMT is a flag only. Autonomy (multi-day) is only scenario-enumerated.
10. **gridspine is not validated against its oracle.** "Increment 1's <1 % gate has still never been closed against an independent oracle." It is IEEE 39-bus only, nodal only (no clustered producer, so no PyPSA-Eur-scale dispatch). The connection study is not built. The compliance engine (grid codes) is not built. Open modelling questions include the interconnection equivalent as a committable unit, case39 base-case overloads, and SCR min vs max case.

### Delivery / verification
11. CI signals are untrustworthy (OPEN-ITEMS #11): path-filtered jobs "read green while running nothing"; `GUI backend` gets cancelled by follow-up pushes; `dev-env` has been red since `14eae4d`; the self-hosted `Run validation` has never run. There were 124 local pre-existing failures (`No module named 'gridspine'`); the "only sound gate is *the failing set is unchanged*".
12. ADR-0002: "Chat changes are not covered by the test suite and need a live-API probe" ("a fully green suite once shipped a total chat outage").
13. The desktop app is unsigned. Windows behaviour is "reasoned from documentation, not measured".
14. UX defect: node positions revert on the blank canvas. It was diagnosed on 2026-07-31 and never fixed (OPEN-ITEMS #7).

### Product gaps (absent, not defects)
15. **No financial layer** (IRR/NPV per project/payback/tariffs/PPA/revenue scenarios) and **no client document** (roadmap item 3 unspecified; no PDF). Two copilot features are not implemented: guided what-if (`plan_what_if`) and run-report generation (`generate_run_report`).

---

## 3. House conventions, for writing a new proposal in-style

**Artifact flow (`docs/superpowers/`)**
- `specs/YYYY-MM-DD-<slug>-design.md` (or `-spec.md`). Status line (`design, awaiting review` / `approved` / `BINDING`), Goal, **Decisions table (numbered, "pinned")**, Non-goals, "What already exists (reuse)" inventory, contracts, phasing, open questions. Revised specs keep a **revision history and a "What vN got wrong" section** (FMEA v4 §3, SaaS Appendix A) rather than silently overwriting.
- `plans/YYYY-MM-DD-<slug>.md`. Header block: "> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development … or superpowers:executing-plans" (appears in 40+ plans). **Goal / Architecture / Tech Stack / Spec** lines, Global Constraints, then tasks or work packages with `- [ ]` checkboxes, **Files**, **Steps**, **Acceptance**, **TDD evidence**, and a Definition of done. Superseded plans are **kept as -v1…-vN** with their reviews, and one can end as "`-do-not-build`".
- `findings/YYYY-MM-DD-<slug>.md`. Title is a plain-English claim ("Fixed cost left out FOM on every economic surface"). **Status:** line (FIXED / CLOSED / OPEN, NOT fixed / verification record), Date/Branch/Plan/Spec, "The gap, as verified on `master`", "What changed" table, measured before/after table, "Local verification" / "E2E evidence" with test counts, and **"Still open / deliberately not done"**.
- `notes/` (decision memos written "for a decision, not as a plan", with options tables: cost / done-when); `handoffs/` + `handovers/` (zero-context continuation docs: where the code is, status, environment, API, numbered **rulings and traps**, open modelling questions for the owner); `assessments/` (read-only audits with a "Calibration: what is already solid" section); `runbooks/`; **`OPEN-ITEMS.md`** (thin index by severity, used because GitHub Issues is disabled).
- `pypsa-gui/docs/adr/NNNN-*.md` (short ADRs: decision, considered options, consequences). `pypsa-gui/CONTEXT.md` is the glossary (terms checked against code, with an "_Avoid_:" list).

**Quality gates**
- **Independent assessor gate per phase**, with verdict `GO` | `GO WITH BINDING CONDITIONS` | `NO-GO`. The rules: "Do not start Phase N+1 until Phase N's gate is cleared". Binding conditions are satisfied in the same phase and re-gated. Verdicts are recorded inline as checkbox + link (`bc-…` IDs). The rubric: "acceptance criteria met; no rebuild of shipped work; no scope leak into later phases; TDD evidence (red→green) present; tests fail-closed without climate/EMT data."
- **Adversarial plan reviews before code** ("v1 was adversarially reviewed … 4 blockers, 13 should-fixes", tagged `[B*]/[S*]/[N*]`). Every reviewer finding is "reproduced before it was applied".
- **TDD evidence** is stated as the actual red error ("`ImportError: eh_stages` … (red) → … → 14 green"). For gridspine and FMEA, a **mutation** is "chosen to break the property the task exists for … shown to turn exactly the intended tests red". There are also "biteable" tests, and the principle "a passing negative-guard test proves nothing otherwise".
- **Measure, don't assume.** Examples: "Read the installed package, not a remembered API", "run, read the number, then pin the number", and "Verified, not assumed". Plans cite **function names, not line numbers** (desktop 2a lesson).
- **Live e2e QA drivers** (`tests/qa_*.py`, auto-discovered by `run_qa_drivers.py`), reported as N/N steps over HTTP. Integration is reported as before/after suite counts, and the gate is "failing set unchanged".
- **Path-limited commits**, one phase per PR, `pixi run gui-tests`, worktrees.

**Honesty conventions (content)**
- ADR-0001: unresolvable → `null` + flag, never `0`. The completeness enum is `ok | not_established | skipped`. Payloads carry `engine` / `fidelity` / `period_basis` / `excludes_shed_cost: true`. Honesty-note tuples appear in payloads (`no_per_load_attribution`, `retained_critical_demand`, `planning_limit_only`). The docs keep "Honest scope (v1)" sections, "Not claimed." lists, and "Pinned product rule". Reusing engines is mandatory: "no second cost engine", "No parallel adequacy stack", "one report builder".
- Voice: first person, candid about own mistakes ("I got this wrong myself … which is the point"). Tables everywhere. Numbers are measured with provenance.

**Template for a new proposal in house style:** a spec with Status, Goal, "What already exists (reuse)" table, numbered pinned Decisions, normative contract(s) with completeness/null semantics, MVP slices, Non-goals, and relationship to existing specs. Then a plan with an agentic-worker header, dependency graph, phase QA gate + TDD protocol, per-phase Files/Steps/Acceptance/TDD evidence/gate checkbox, "Review deltas", explicitly deferred items and Definition of done. Findings are written at the end with before/after counts and "Still open".
