<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Assistant memory: design plan

Issue: `issues/01-design-plan.md`. Spec: `spec.md`. Owner decisions: MEMORY-1, MEMORY-DREAM-1, COPILOT-1,
DASHBOARD-1, PR-SIZE-1. Reviewer: GK B. No code in this package.

The plan answers the seven bullets of issue 01 in order (§1–§7), then names the three build slices (§8). Every rule
below is a contract for slices 02–04; a slice that needs to break one comes back to GK B first.

Words used here: an **item** is one memory entry; its **owner scope** says whose it is (a user or an org); its
**applicability** says where it may be recalled (a project kind and concept keys); **recall** puts an item into a
turn's prompt; **apply** makes an item's value take effect (a chip accepted, a view default used).

## 1. Data model

Memory spans projects, so it lives in the app database, not in a project directory (unlike `assistant_tasks` and
`durable_conversation`, which are per project). One new Alembic migration (the next free number when slice 02 opens;
0013 on today's master) adds two tables, modelled on `LibraryItem` in `db/models.py` (an immutable row per version).

### `memory_items`: one row per item

| Column | Type | Meaning |
|---|---|---|
| `id` | uuid | Stable id. The public handle is `mem_<id hex>`. Never reused. |
| `kind` | str(32) | Closed set: `preference`, `chart_spec`, `parameter`, `lesson`. Validated in `services/memory_store.py`; a plain string column, as `Project.project_kind`, so a new kind is not a migration. |
| `owner_scope` | str(8) | `user` or `org`. |
| `user_id` | uuid, null | Set when `owner_scope = user` (FK `users.id`, cascade delete). |
| `org_id` | uuid | Always set: the user's org at creation, or the org that owns the item (FK `organizations.id`, cascade). Lets "separable per organization" be a query, not a migration. |
| `project_kind` | str(32), null | Applicability: `capacity_expansion`, `planning_dynamics`, `connection` (`Project.project_kind`, NULL read as `capacity_expansion` there too), or NULL for "any kind". |
| `concept_keys` | text (JSON list) | 1–8 keys from §1.2. An item with no key is refused. |
| `status` | str(16) | `proposed`, `confirmed`, `retired`, `dismissed`. See §1.3. |
| `current_version` | int | Points at the row in `memory_item_versions` that is live. |
| `origin` | str(16) | How it was captured: `button`, `chat`, `repeat`, `run_end`, `dream`, `promoted`. |
| `promoted_from` | uuid, null | The user item an org item was copied from (§5). |
| `recall_count` | int | Times the item entered a turn's prompt block (§3). |
| `apply_count` | int | Times the user applied it: a chip accepted, a view opened with its default, a chart drawn from its spec. |
| `last_used_at` | datetime, null | Last apply (recall does not move it). |
| `created_by`, `created_at`, `updated_at` | | As elsewhere in `models.py`. |
| `dismissed_hash` | str(64), null | sha256 of `(kind, sorted concept_keys, normalised body)` on a dismissed proposal, so the same proposal is not offered again for 30 days (§2.4). |

Indexes: `(user_id, status)`, `(org_id, owner_scope, status)`, `(org_id, project_kind)`.

### `memory_item_versions`: one immutable row per edit

| Column | Meaning |
|---|---|
| `item_id`, `version` | Unique pair. An edit writes version n+1 and moves `current_version`; old versions stay readable so a provenance stamp `memory:<id>@v<n>` (§7) always resolves. |
| `body_json` | The kind's body (§1.1), ≤ 4 KB serialised; larger is refused. |
| `summary` | One line, ≤ 160 chars, used in the prompt block and on the Memory page. Written by the service from the body, or by the user on edit. |
| `evidence_json` | 0–8 evidence links (§1.4). |
| `edited_by`, `edited_at` | Who wrote this version. |

### 1.1 Body per kind

Bodies are validated by one schema per kind in `services/memory_store.py`. Free text is only ever a `note` or a
lesson `text`, ≤ 280 chars, and is treated as untrusted data everywhere (§3.4).

- **`preference`**: `{key, value, note?}`. `key` is from a closed registry (`PREFERENCE_KEYS`): `units.power`
  (`kW`/`MW`/`GW`), `units.energy`, `currency` (ISO 4217, checked against the money module's list),
  `report.sections` (ids from the report builder), `sensitivities.default` (a list of `{parameter concept, ±%}`),
  `answer.style` (closed enum: `brief`, `detailed`). A key outside the registry is refused, so a preference cannot
  smuggle an instruction in as a value.
- **`chart_spec`**: `{spec: chart_spec@1}` (§7.2). The settings a result view or a chart is drawn with.
- **`parameter`**: `{component_class, field, value, unit, currency?, currency_year?, carrier?}`. `unit` must equal
  the stored unit `get_component_schema` gives for that field when the item is applied (slice 03 checks it then,
  not only at save).
- **`lesson`**: `{text, situation: {step?, asset_class?, carrier?, tool?}}`.

### 1.2 Concept keys

A concept key says what an item is about, so recall can match it to what is on screen. Grammar
`<namespace>:<id>`, one closed list of namespaces, ids checked against an existing registry where one exists:

| Namespace | Id from | Example |
|---|---|---|
| `view` | `services/result_views` view ids | `view:capacity_mix` |
| `card` | `result_views.CARDS` | `card:commercial` |
| `param` | `<component_class>.<field>` from the component schema | `param:Generator.capital_cost` |
| `carrier` | the network's carrier names | `carrier:solar` |
| `step` | Guided workflow step ids (`harness/workflows/`) | `step:economics` |
| `report` | report section ids | `report:executive_summary` |
| `topic` | a small fixed list (`units`, `currency`, `style`, `sensitivity`) | `topic:currency` |

`concepts_for(ui_context, live_meta)` in the memory service maps the panel, results tab, results card, the open
asset and the Guided step (the same fields `_format_ui_context` already reads) to a set of keys. It never reads
free text.

### 1.3 Status

```
proposed ──confirm──▶ confirmed ──retire──▶ retired
    │                     ▲   │                │
 dismiss                edit  └──── restore ◀──┘
    ▼                (new version)
 dismissed  (body dropped after 30 days, dismissed_hash kept)
```

- Only `confirmed` items are recalled or offered. `proposed` items are shown only on the Memory page's
  Proposals tab and on the card that proposed them.
- `retired` keeps every version and its evidence (provenance may point at it). Restore makes it `confirmed`.
- Delete is allowed for `proposed` and `dismissed` items only, and for the user's own items when the account is
  deleted (cascade). A confirmed or retired item is retired, never deleted, so a `memory:` stamp never dangles.

### 1.4 Evidence links

`{kind: project | run | chat | answer, id, label, at}` where `id` is a project uuid, a solve-job id or run
fingerprint, a chat session id plus event seq, or a published answer id. Links are pointers only; the Memory page
re-checks project access when it renders one (a link into a project the viewer cannot open shows "source project
(no access)" and no name). Evidence never grants access.

## 2. Capture paths and their cards

Nothing is learned silently (MEMORY-1). Every path ends in a user action: a click on a button, or a decision on a
card.

### 2.1 "Save as default" (button, slice 02 for views, slice 03 for fields)

- On a result view's settings menu (view mode, metrics, unit, chart toggle; the same state
  `ui_open_asset_detail` pre-sets) and, from slice 03, on a field's menu ("Save for reuse").
- The click is the confirmation: the item is written `confirmed`, `origin: button`. A toast says
  "Saved as your default for <view>, all <project kind> projects · Edit · Undo". Undo retires it.
- A second save on the same `(owner, kind, concept key, project_kind)` writes a new version of the same item, not a
  second item.

### 2.2 "Remember this" in chat (slice 02)

- New tool `propose_memory(kind, summary, body, concept_keys, project_kind?)`, safety tier `write`, which never
  auto-approves: slice 02 adds an `ALWAYS_CARD_TOOLS` set to `confirm.py` that `AUTO_APPROVE_TIERS` cannot exempt. It
  writes a `proposed` item and returns a **memory card**.
- The memory card (a Choice-card sibling in the chat panel, same layout Guided and Expert): kind chip, summary
  (editable), scope ("Just me" / "All my <project kind> projects" / "Any project"), concept chips, source line
  ("from this chat"). Buttons: **Remember**, **Edit first**, **Not now**. Remember confirms; Not now dismisses.
- The card's decision goes through `approvals.py` like any card, so it is in the session event log.

### 2.3 Repeated correction (slice 02)

- The prompt block (§3) carries one sentence: when the user corrects the same thing a second time in a chat, call
  `propose_memory` once with that correction. This is guidance, not a detector; the cap below bounds it.
- Caps per session: at most one memory card per turn and three per session (`budget.py` gains the counter);
  past the cap the tool returns `memory_proposal_capped` (a new error kind in
  `pypsa-gui/tool-error-kinds.d/<branch>.json`).

### 2.4 Run end (slice 04)

- When a run the user watched finishes (`continuation.py`'s once-per-job hook), the assistant may propose one
  lesson with the run as evidence, under the same caps and card.
- A proposal whose `dismissed_hash` matches a dismissal younger than 30 days is not written
  (`memory_proposal_dismissed`).

### 2.5 Dream proposals (slice 04)

Written `proposed`, `origin: dream`, shown on the Memory page's Proposals tab with a badge in the nav. No chat card:
the job runs with no chat open.

## 3. Recall: budget, filter and placement

### 3.1 Two blocks, split by cache

`_format_ui_context` already explains why anything that changes on navigation must not enter the system prompt
(its `cache_control: ephemeral` breakpoint). Recall therefore has two parts:

1. **Standing block** in the system prompt, as a new part right after `_skills_block()` in
   `compose._build_system_prompt`: the user's and org's `preference` and `chart_spec` items for the bound
   project's kind, plus `lesson` items with no concept filter. It depends only on `(owner, org, project_kind,
   memory revision)`, so it is byte-stable across turns and invalidates the cache only when memory changes (the
   store keeps a per-owner revision counter, bumped on every write). Built by `_memory_block(session, ctx)`, which,
   like `_skills_block`, never raises: on any error it logs and returns "".
2. **Turn block** in the user turn, next to the ui-context block (`loop.py` where `_format_ui_context` is
   appended): items whose concept keys meet `concepts_for(ui_context)` and that are not already in the standing
   block (lessons tagged to the open step or asset, a `parameter` item for the open field).

Both blocks are FACTS: no tool name in the item text. The one hint that names `recall_memory` and
`propose_memory` is appended only when `include_tools` is true, so the tools-off prompt still names no tool
(Task 8 invariant).

### 3.2 Budget

| | Standing block | Turn block |
|---|---|---|
| Items | ≤ 8 | ≤ 4 |
| Characters | ≤ 1 500 (about 400 tokens) | ≤ 800 |
| Per item | its `summary` (≤ 160 chars) plus `mem_<id>`; a chart spec as a one-line summary | same |
| Overflow | counted: "+N more items: recall_memory" | same |

Constants live in `budget.py` next to the other caps. A test pins the block length against a store of 50 items.

### 3.3 Filter and ranking

Filter: `status = confirmed`; owner scope is the user's own items or the user's org's org items; `project_kind` is
the bound project's kind or NULL. Ranking, first difference wins:

1. a concept-key match with the screen (turn block only);
2. user over org (a user's own choice beats the org default for the same concept key, and the org item is then
   dropped, not shown twice);
3. a specific `project_kind` over NULL;
4. `apply_count`, then `last_used_at`, then `updated_at`.

`recall_count` is bumped once per item per session (not per turn), so it measures reach, not chat length.

### 3.4 Safety

- Each block is wrapped by `fence.fenced(...)`, so an item's text is untrusted data under
  `untrusted_data_clause.md`. A remembered "always auto-approve" is a sentence in a fence, not an instruction.
- Memory never bypasses validation: a chart spec is validated against `chart_spec@1` when drawn; a parameter goes
  through the field's own edit path and card (§7.1); a preference is a registry value.
- A turn with no bound project gets the standing block for NULL-kind items only.

### 3.5 Tools

- `propose_memory` (§2.2).
- `recall_memory(concept_keys?, kind?, query?)`, tier `read`: up to 10 confirmed items with summary, body and
  evidence, filtered as §3.3; `query` is a case-folded substring match on the summary, no embeddings.
- Both get route-parity declarations in `pypsa-gui/backend/smoke/assistant_route_parity_declarations.d/` and the
  inventory check, per the steward rules.

## 4. The Memory page

A page under the account menu ("Memory"), not inside a project, since memory spans projects. Slice 02 builds it;
slices 03 and 04 add a filter value and a tab.

- **Items tab**: one table, density as the rest of the app (DENSITY-1). Columns: kind, summary, applies to
  (project kind or "any"), scope (Me / <org name>), concepts, source (first evidence link), used (`apply_count`,
  `last_used_at`), status. Filters: kind, status (default `confirmed`), scope.
- **Row actions**: Edit (opens the body form for that kind; saves a new version), Retire / Restore, History (the
  versions with editor and date), and for the user's own items **Suggest to org** (§5). Org admins see **Promote**
  and **Retire** on nominated and org items.
- **Proposals tab** (count badge): proposed items from chat and the dream job, each with Confirm, Edit and confirm,
  Dismiss, and its evidence.
- Empty state says how items get there ("Use Save as default on a chart, or say 'remember this' in chat").
- Routes: `GET/POST /api/memory/items`, `PATCH /api/memory/items/{id}` (new version), `POST
  /api/memory/items/{id}/{retire|restore|confirm|dismiss|nominate|promote}`, `GET
  /api/memory/items/{id}/versions`. Every route checks owner scope server-side; a user cannot read another user's
  items by id (404, not 403, so ids are not probed).
- UX review: the page is outside the project workflow, so `review-ux.md`'s workflow-section checks apply only to
  the in-project Save-as-default control and the chips; the slice tells the UX reviewer where each sits.

## 5. Org promotion

- A user nominates one of their confirmed items ("Suggest to org"). Nothing else reaches an admin: an org admin
  never browses members' private items.
- An org admin (`OrgMembership.role == "admin"`, `tenancy_service.VALID_ORG_ROLES`) sees nominations on the Memory
  page and can **Promote** (edit first is allowed) or **Decline**.
- Promote copies the item to a new `owner_scope = org` item with `promoted_from` set and `origin: promoted`; the
  user's item stays theirs. Evidence links are copied, and each still renders only for viewers with project
  access (§1.4).
- Org items are retired or edited by org admins only. A member who wants something else saves their own item,
  which wins over the org item for that concept (§3.3 rule 2).
- With a personal workspace (one member, the local user is admin), Suggest to org and Promote are hidden: there is
  no one to share with. This is a UI condition only; the data model already carries `org_id`.

## 6. The dream job (slice 04) and the durable task queue

The dream job is per user and reads across projects, while the harness runner (`harness/runner.py`) advances
**project-bound** task files whose steps are tool calls through `dispatch_tool_call`. The dream job makes no tool
calls and has no project binding, so it does not become an `assistant_tasks` step. It reuses the runner's
durability rules instead:

- **Store.** A third table `memory_dream_jobs` (in slice 04's migration): `id, user_id, trigger (nightly |
  sessions | manual), status (queued | running | done | failed | capped | cancelled), cursor_json, budget_json,
  spent_json, lease_until, restarts, created_at, finished_at, error`.
- **Worker.** `harness/dream.py`, started from the `main.py` lifespan after `harness_runner.start_default()`, off
  under pytest like the runner. One worker thread, one job at a time per process. Lease `lease_until` renewed every
  30 s; another instance takes over only after it lapses; 3 restarts in an hour mark the job `failed` (the runner's
  rule). Woken by the manual route, the 60 s scan, and the triggers below.
- **Triggers.** Manual ("Review my recent work" on the Memory page); after 10 new chat sessions since the last
  job; nightly at 03:00 Europe/Berlin while the app runs. Nightly and session triggers are off until the user turns
  them on in Memory settings (the job sends past transcripts to the configured provider, so it is opt-in).
- **Inputs.** The user's own session event logs (`eventlog.read_events`, read-only) and run records
  (`solve_jobs`, run evidence) newer than the cursor, for projects the user can still open (access re-checked per
  project). Never another user's sessions.
- **Analyze.** Batches of up to 20 transcripts. One analyzer call per transcript on the provider profile marked
  `memory_analyzer` (a new optional profile role; falls back to the active profile). Input: a trimmed transcript
  (`history.trim_session_messages`), fenced. Output: strict JSON, at most 3 candidate lessons or preferences each,
  validated against the §1.1 schemas; anything invalid is dropped and counted.
- **Organize.** One organizer call per job: the candidates plus the user's confirmed items' summaries. It merges
  duplicates, drops candidates that restate a confirmed item, and links each remaining one to its evidence. Output
  validated the same way.
- **Write.** Proposals only (`status proposed`, `origin dream`), never `confirmed`; dismissed hashes are skipped.
  At most 10 proposals per job.
- **Budget.** Per job: token and cost caps from `budget.py` (defaults 200k input tokens and the daily cap's
  remaining share, whichever is lower). The job stops at the cap with `status capped`, keeps its cursor, and the
  next job continues there. Spend counts against the user's daily cap like chat.
- **Provider-agnostic and local.** It goes through `providers/wiring` like any turn, so any configured profile
  works; nothing uses the Managed Agents Dreams API (MEMORY-DREAM-1). Transcripts leave the machine only to the
  provider the user already chats with, and only after the opt-in above.

## 7. Overlaps

### 7.1 Provenance graph (`.scratch/provenance-graph/`, issue 07 there)

The provenance spec reserves a `memory` node kind and a `recalled_from` edge (input → memory), built from typed
records the app stores. Memory provides those records; it does not edit the graph's files.

- **Node identity**: `mem_<id>@v<version>`. Versions are immutable and items are never deleted once confirmed
  (§1.3), so the node always resolves.
- **Record the edge reads**: when the user accepts a parameter chip (slice 03), the value is written through the
  existing edit path and stamped by `services/asset_schema/provenance.record(...)` with `status="customised"`,
  `source="memory:<id>@v<n>"`, `approved_by`, `approved_at`, and `replaces` (the prior source), the same fields a
  Library reference apply uses today. No new provenance field. Provenance slice 07 maps a `memory:` source to the
  edge.
- **Staleness**: retiring or editing the item does not make the accepted value stale (the value keeps its stamp,
  as provenance issue 07 says). The memory service exposes `memory_store.node(id, version)` (summary, kind, owner
  scope, evidence) as the read the graph calls.
- **Who builds what**: slice 03 writes the stamp and `node()`; provenance 07 builds the node and edge. To be agreed
  with the provenance-graph session through GK B; this plan does not touch `.scratch/provenance-graph/`.

### 7.2 DASHBOARD-1 chart specs

DASHBOARD-1 says added charts are saved specs drawn for the base and every sensitivity case on shared axes. No
dashboard code or spec schema exists on master yet, so one schema must be first.

- Slice 02 adds `chart_spec@1` in its own module (`services/chart_spec.py`, no memory import): `{schema:
  "chart_spec@1", view_id? (result_views id), series: [{component_class?, carrier?, metric, unit}], chart:
  {type (line | bar | stacked_area | stacked_bar | duration | heatmap), x?, group_by?}, units: {power?, energy?},
  style: {stacked?, log_y?, sort?}}`, validated against the component schema and the result views. Nothing in it
  is a formula or code.
- The memory `chart_spec` kind stores that object. The dashboard package reuses the module for its added charts
  and owns any `@2`; memory follows. Drawing for each sensitivity case is the dashboard's, not memory's.
- If the dashboard package lands a spec schema first, slice 02 uses it instead and drops its own.

### 7.3 Other neighbours

- `create_chart` (PNG export) is left alone in these slices.
- Guided step ids and report section ids are read, not changed.

## 8. Slices (PR-SIZE-1: one PR each, about 1 500 changed lines or fewer)

Each slice is one PR, reviewer GK B; slices 02 and 03 also need UX's OK for their UI (the issue files say so).
Line estimates exclude shots and fixtures.

| Slice | Issue | Builds | Est. lines | Tests |
|---|---|---|---|---|
| **02** Preferences and chart specs | `issues/02-preferences-and-chart-specs.md` | Migration (`memory_items`, `memory_item_versions`); `services/memory_store.py` (schemas, CRUD, recall, revision, nominate/promote); `services/chart_spec.py`; compose standing block and turn block; `propose_memory`, `recall_memory`, memory card; caps and error kinds; `/api/memory/*`; Memory page (Items, Proposals, nominate/promote); Save as default on result views; view opens with the saved default | ~1 400 | store unit tests (scope, ranking, status machine, version immutability, cross-user 404); prompt tests (block pinned under budget, byte-stable across navigation, fenced, tools-off names no tool); card approval through `approvals.py`; route parity; Vitest for page and card; shots Guided/Expert |
| **03** Parameter suggestions | `issues/03-parameter-suggestions.md` | `parameter` kind; Save for reuse on a field; `GET /api/memory/suggestions?class&field&project_kind`; chips next to the field ("Used before: 7 % · project A ›" with date and currency stamp); accept writes through the field's edit path with the `memory:` provenance stamp (§7.1); `memory_store.node()`; unit mismatch refuses the chip, currency mismatch shows the stamp and never converts silently | ~900 | unit check on apply; stamp written exactly as §7.1; never applied without the click (COPILOT-1); chip hidden when no item; shots |
| **04** Run lessons via the dream job | `issues/04-dream-run-lessons.md` | `lesson` capture at run end (§2.4); `memory_dream_jobs` table; `harness/dream.py` worker (lease, triggers, opt-in); analyzer and organizer prompts under `harness/prompts/`; budget caps; Proposals tab hookup and Memory settings toggle | ~1 200 | fake-provider job end to end (analyze → organize → proposals only); budget stop keeps cursor; lease takeover; invalid analyzer JSON dropped; access re-check skips a lost project; injection text in a transcript lands only as a proposal |

If slice 02 measures over the cap when built, it splits before review into 02a (backend: store, prompt, tools,
routes) and 02b (UI: page, card, button), with GK B's GO.

Order: 02, then 03 and 04 in either order (both depend only on 02). Provenance issue 07 waits on 03.

## Open points for GK B

1. The two-block split (§3.1) departs from the spec's "next to `_skills_block`" for the screen-filtered half, to
   keep the system prompt cache stable. Agree?
2. `chart_spec@1` lives in memory slice 02 because no dashboard schema exists (§7.2). Agree, or wait for a
   DASHBOARD-1 package?
3. The dream job uses its own table and worker with the runner's lease rules rather than an `assistant_tasks` run
   (§6). Agree?
4. The provenance stamp reuses the Library-apply fields with `source = "memory:<id>@v<n>"` (§7.1): to be confirmed
   with the provenance-graph session.
