# Guided mode — a simple, assistant-driven path for first-time users (P23–P26)

**Requested:** 2026-09-27. The UI should be easy for a non-consultant. It should hide what an expert does not need up front, guide a first-time user, offer hover help everywhere, and let the assistant do the engineering steps and answer questions.

**Decided (product owner, 2026-09-27; each is the recommended option):**

| # | Question | Decision |
|---|---|---|
| G1 | Shape | A **Guided / Expert toggle** in one app. Guided hides advanced panels and shows a step-card flow; Expert is today's UI. |
| G2 | Scope v1 | **Hub design end to end:** template or own network → site → study → results in plain language → FMEA risks → improve with the assistant. Everything else stays Expert. |
| G3 | Assistant | **It does the steps and you confirm.** "Let the assistant do this" sends a specific request. Every change goes through the normal confirmation card, and questions can be asked at any time. |
| G4 | Default | **New users and new projects start Guided.** One click switches, and an explicit choice is remembered and never overridden. |

**Rules:** the same as the parent plans: TDD, a QA gate, honesty over completeness, and build on existing engines. Guided mode adds **no new engine**. It is a presentation over the P10–P22 surfaces (readiness, EH study, review, FMEA sweep, guide catalogue, assistant tools).

---

## P23 — Guided / Expert mode

- **State.**
  - `uiStore.uiMode: 'guided' | 'expert'` is persisted under `network-diagram:ui-mode`, following the density pattern, together with `uiModeExplicit`.
  - **Default rule:**
    - A stored explicit choice always wins.
    - Otherwise, a first-time user (no prior `network-diagram:*` keys) starts `guided`.
    - An existing user starts `expert`, so today's users are not surprised.
    - Creating a project from an EH template switches to `guided`, unless the user has chosen a mode explicitly.
- **Switch.** A "Guided | Expert" segmented control in the `AppHeader`, with hover text explaining both. It is also available as a command-palette entry.
- **Hiding in Guided.**
  - The Sidebar shows only the Assistant, a **Hub design** entry that opens the guided flow, and Project basics (open, save, projects home).
  - DATA / SIMULATION sections, the canvas tool switcher and the Solve Queue are hidden.
  - Results show only the tabs the flow links to: Energy Hub (adequacy) and FMEA.
  - Nothing is deleted or disabled for Expert; switching back restores everything. Hidden features stay reachable through the assistant.
- **Tests.**
  - The default rules, including a first-time user, an existing user, a template project and an explicit choice.
  - The switch persists.
  - Guided hides the listed sidebar sections and results tabs; Expert shows all.

## P24 — The hub-design step cards

A full-width panel `hubDesign`, opened by default in Guided mode when a project is open. Five steps sit in a progress rail, each a **card** with at most three decisions, plain-language text, a "?" hover on every term (guide catalogue), **Ask about this** (prefills a question) and **Let the assistant do this** (sends a request, P25).

1. **Start.** Pick a template (the three EH templates, with one-line purposes) or "use my network". It shows the template's provenance ("synthetic example data").
2. **Site.** Readiness in words:
   - "Grid connection: *grid_import* (40 MW)";
   - "Critical load: *it_bus*";
   - "Grid strength data: present / missing";
   - "Outage data: 6 units".

   Each gap has a fix button that calls the assistant ("tag the critical buses"). The one editable choice is the site type (archetype), in plain words: *strong grid / limited grid / island*.
3. **Goal.** A reliability goal in plain words: an allowed shortfall per year (LOLE, default from the template/pack) and "how strict on energy" (ENS, shown as advanced). Budget and stages are hidden, and a sensible default is used (the template's recommended settings). Then **Run study**.
4. **Results.** Plain-language cards generated from the same review the assistant uses (`GET /api/results/eh_review`):
   - a headline verdict ("Not certified: about 12 h/yr of shortfall vs a 3 h/yr goal — driven by grid-import outages");
   - cost;
   - the top risks (fmea_top) with a one-line meaning each;
   - "what this study could not establish" (the not_established notes, in words).

   A link opens the full expert report.
5. **Improve.** The findings with high and medium severity as cards. Each has "Why" (evidence) and **Let the assistant do this** (the finding's action, sent to the assistant and confirmed there) or **Ask** (for findings without an action). Also **Check risks (FMEA)**, which runs the B/C sweep with the stress registry, and "Add a stress scenario" via the assistant.

- **Backend.** `GET /api/results/eh_review` serves `review_report` (the same function as the chat tool, one source). A 204 means there is no study.
- **Tests.**
  - The card flow navigates and reflects state (study absent / running / done).
  - The Results / Improve cards render review findings.
  - Every term hover resolves in the catalogue, pinned.
  - The route matches the chat tool.

## P25 — The assistant does the steps

- **Send from a card.** `chatStore.sendRequest(text)` opens the dock and dispatches the message through ChatPanel's own send path (the same one used for typed messages). It is queued while a turn streams. A card button is an explicit user click, so sending is the user's act. Writes still show confirmation cards.
- **Guided prompt awareness.** The chat request carries `ui_mode`. In Guided mode a prompt addendum asks for plain language and short answers. The assistant does the engineering steps the user delegates, stating what it will change before the confirmation card. It points to the card the user is on.
- **`suggest_eh_setup` (read).** For a user's own network it proposes tags with a reason for each: import Link candidates (Links between a bus with large cheap supply and the load side), a PoC bus, critical buses (the largest or named loads), and missing outage data. It returns ready `update_component` / `bulk_update_components` actions. It never applies anything itself.
- **Tests.**
  - `sendRequest` dispatches once and queues while streaming.
  - The prompt addendum appears only in guided mode.
  - `suggest_eh_setup` on the three templates, with tags removed, recovers the template tags, and its actions validate against their tool schemas.

## P26 — Verification

- A real-app click-through of the guided flow on all three templates: first-time user → Guided → template → site → goal → run → results → improve via the assistant (confirmation shown) → FMEA check.
- The findings of the 2026-09-27 real-app click-through of the Expert flow are folded in.
- An independent QA gate, and full backend and frontend suites.
