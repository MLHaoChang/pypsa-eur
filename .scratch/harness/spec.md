# Chat harness — spec

Status: ready-for-agent (phases 0–2 implemented 2026-10-05; owner decisions Q1–Q14 in §8; open: issue 08, issue 11, the live OpenAI parity run)
Date: 2026-10-05
Plan: `docs/superpowers/plans/2026-10-05-chat-harness.md`
Context: `pypsa-gui` (vocabulary in `pypsa-gui/CONTEXT.md`; ADR-0002 and ADR-0004 apply)

## Problem statement

The in-app assistant runs on any provider already (Anthropic, OpenAI-compatible,
local endpoints), but everything that makes the assistant *behave the same* on
every provider is spread across five places:

- the system-prompt text, the agent loop, sessions, confirmation gating,
  budgets, persistence and the Guided-mode addendum all live in one 5,300-line
  module (`services/chat_service.py`);
- the tool catalogue (182 tools, three parallel structures: schemas, dispatchers,
  routes) lives in two more modules;
- the chat-start menu ("Try asking" chips) is a hardcoded array in the React
  panel, and the Guided-mode step requests ("Let the assistant do this") are
  hardcoded strings in the frontend;
- the model has no way to ask the user a structured question with options and a
  recommendation, so every clarifying question is free prose;
- there are no reusable procedures ("skills") for the in-app assistant: the
  grilling procedure the repo's developer agents use has no counterpart inside
  the app.

Adding a new guided workflow today touches the frontend, the prompt constants
and the tool catalogue, and nothing in the codebase says what a "workflow" is.
When the owner switches the assistant from Claude to an OpenAI model, Kimi or a
local model, the behaviour that is data (tools, prompts) carries over, but the
behaviour that is scattered code does not travel, and there is no place to add
behaviour that is guaranteed to travel.

## Solution

One package, `pypsa-gui/backend/harness/`, that holds everything a
provider-agnostic assistant needs and nothing provider-specific:

| Part | What it is | Authoring format |
|---|---|---|
| protocol | the provider seam (`LLMProvider`, `LLMRequest`, `LLMEvent`, error kinds) | Python |
| catalogue | the tool declarations, safety tiers and route map | Python (data) |
| events | the closed vocabulary of turn-loop frames the UI consumes | Python (data) |
| prompts | the system-prompt fragments | Markdown |
| workflows | the start menu and the step-by-step flows the assistant leads | Markdown with front matter |
| skills | reusable procedures the model can load on demand (grill, …) | Markdown with front matter (Agent Skills layout) |
| loop (later phases) | session, confirmation, budgets, persistence, the turn loop | Python |

The frontend reads the start menu from the harness, renders a **Choice card**
when the model calls `ask_user`, and sends the pick as the next message. A
provider plugs in below the protocol. Switching providers changes the wire, not
the experience.

## User stories

1. As a user, I want the chat to open with a menu of things I can do here (build a network, import data, run a study, explain results, improve the design, decide an investment), so that I do not have to know the tool to start.
2. As a user, I want that menu to depend on where I am (no project, Expert, Guided, project kind), so that it only offers what makes sense.
3. As a user, I want the assistant to ask me a structured question with options and a recommended answer when it needs a decision, so that I can click instead of typing, and see what the assistant suggests.
4. As a user, I want to be able to answer such a question in my own words too, so that the options never trap me.
5. As a user, I want a workflow to lead me step by step (what this step is, what it needs from me, what will change), with every write or run still behind the confirmation card, so that a guided flow never applies something I did not ask for.
6. As a user, I want to leave a workflow at any time and ask anything, so that the flow is help and not a cage.
7. As a user, I want to see which step of which workflow I am on, so that a reloaded page does not lose my place.
8. As a user, I want the same menus, questions, cards and step texts whether the active profile is Claude, an OpenAI model, Kimi, Qwen or a local model, so that switching the profile feels like changing the engine, not the car.
9. As a user, I want a tools-less profile to still show the start menu and still answer, and to say plainly that it cannot act, so that a weak model degrades honestly.
10. As a user, I want the assistant to run a "grill me" style interview (rounds of numbered questions, each with a recommendation) when I ask it to stress-test a plan or a design, so that the in-app assistant has the same discipline as the developer agents.
11. As a product owner, I want to add a workflow by adding one Markdown file, so that a new guided flow is a content change, not a four-file code change.
12. As a product owner, I want to add a skill by adding one `SKILL.md`, in the same layout the developer agents use, so that one procedure can serve both the app and the agents.
13. As a product owner, I want the tool catalogue, prompts, workflows and skills in one folder with one README, so that a newcomer (human or agent) finds the whole assistant contract in one place.
14. As a developer, I want the system prompt to stay byte-identical when its text moves into Markdown files, so that the prompt cache and the pinned hashes keep working.
15. As a developer, I want a tripwire test that every frame the loop yields is in the harness's event vocabulary, so that a new frame cannot reach the UI unnamed.
16. As a developer, I want the old import paths (`services.llm_provider`, `services.chat_tools_schema`) to keep working while the moves land, so that the 979 chat tests and the smoke scripts do not churn.
17. As a developer, I want a parity probe: the same scripted workflow against the fake provider and, opt-in, against each live wire, so that "feels the same" is measured per ADR-0002 and not asserted.
18. As a developer, I want provider adapters under the harness (`harness/providers/`), so that the dependency arrow (domain ← harness ← providers) is visible in the tree.
19. As a developer, I want the Guided-mode addendum and step texts expressed as a workflow definition, so that Guided mode is the first consumer of the workflow registry and not a parallel mechanism.
20. As a developer, I want the report generator (the second consumer of `LLMRequest`) to import the protocol from the harness, so that there is one seam.
21. As a developer, I want a workflow or skill file with a bad front matter to fail a test, not fail at runtime in front of a user.
22. As an external-agent user (Claude Code, Codex CLI, a Kimi agent), I want to drive the same catalogue over MCP later, so that the harness is the one contract for in-app and out-of-app agents. (Deferred; see §7.)
23. As a packager, I want the harness's Markdown files listed in the PyInstaller `datas` allowlist, so that the desktop app ships them.
24. As a security reviewer, I want workflow and skill bodies to carry no secrets and to be loaded from the package only (never from a project directory or an upload), so that a project bundle cannot inject assistant instructions.

## Implementation decisions

### D1. Name and place

The package is `harness`, importable as `harness` from the backend root, at
`pypsa-gui/backend/harness/`. The name is not new: the seam spec of 2026-08-05
already calls the provider-neutral agent layer "the harness" and the code
comments use the word the same way. This spec makes the tree match the word.
The harness belongs to the `pypsa-gui` context; it is not a repo-root folder,
because it must be importable by the backend and must not be mistaken for the
developer-agent tooling in `.claude/`.

### D2. Layering (unchanged from the seam spec, now visible in the tree)

```
domain      services/* (PyPSA, projects, studies)      ← harness may call
harness     protocol · catalogue · events · prompts · workflows · skills · loop
providers   harness/providers/{anthropic, openai_compat, fake}   ← import harness.protocol only
```

No provider name, SDK import or wire word appears in `harness/` outside
`harness/providers/`. A test greps for it.

### D3. Markdown is the authoring format for behaviour

Workflows, skills and prompt fragments are Markdown files with YAML front
matter, loaded once at import and validated by a test. Rationale: the owner's
requirement is that a Claude, an OpenAI model or a Kimi model read the same
instructions; Markdown is what every one of them reads best, it is diffable,
and it is the format the developer agents' skills already use. JSON and YAML
were rejected for the bodies (unreadable prose); front matter carries the
machine fields.

### D4. Workflow model

A workflow is a Markdown file under `harness/workflows/` with front matter:

- `id` (slug), `title` (menu label), `intent` (one sentence shown as the chip
  tooltip and to the model),
- `when`: which contexts offer it: `unbound`, `expert`, `guided`, and an
  optional `project_kind` list,
- `order` (menu position),
- `steps`: a list of `{id, title, done_when}`; the body has one `## Step:
  <id>` section per step with the instructions for the assistant.

Runtime state is `session.workflow = {id, step}`; it is set by the
`start_workflow` tool (read tier) and advanced by `advance_workflow`; the
frontend may also send `ui_context.workflow` so a reload rebinds it. The
instructions for the current step travel as a per-turn user-content addendum
(the same slot the Guided addendum uses today), never in the system prompt,
so the prompt cache is untouched and Expert turns stay byte-identical.

The Guided-mode addendum and the five hub-design steps become the first
workflow (`hub-design`), so Guided mode consumes the registry rather than
running beside it. Its per-turn rules keep their current wording.

### D5. The start menu is data from the harness

`GET /api/chat/workflows?context=…` returns the menu for the caller's context.
The React panel renders the chips from it; the three hardcoded prompt arrays
are deleted once the endpoint ships. A chip sends the workflow's opening
request through the existing `sendRequest` queue (so a reload and a denial
behave as the hub-design delegation already does) rather than seeding the
composer.

### D6. Structured questions: the `ask_user` tool and the Choice card

A new read-tier tool `ask_user(title, question, options[{label, description,
recommended}], allow_free_text)` yields a new frame `choice_request` and
returns `{"status": "presented"}`; the model is told to end its turn after
presenting. The panel renders a Choice card; a click sends the chosen label
as the next user message (labelled in the transcript like a delegated
request); free text is always possible. Non-blocking by design: it works on
every wire, survives a reload (the pending question is in history), and does
not hold a thread open the way the confirmation gate does. The confirmation
card is unchanged: it is a safety gate and stays blocking.

### D7. Skills

A skill is `harness/skills/<name>/SKILL.md` with `name` and `description`
front matter (the Agent Skills layout, so one file can be copied between
`.claude/skills/` and the harness). The system prompt carries only the
catalogue (name + description per skill, stable → cached); the model calls
`use_skill(name)` (read tier) and receives the body as the tool result.
The first skill is `grill`: rounds of numbered questions with a recommended
answer each, asked through `ask_user`, ending when the frontier is empty.

### D8. Prompts move to Markdown, byte-identical

Each prompt constant becomes a file under `harness/prompts/`; the loader
reassembles the same bytes. The pinned-hash test is the gate: the hashes do
not change. The FACTS/CHAINING split stays as two sections of one file.

### D9. History format stays

The persisted history stays in the block format the seam spec documents; the
OpenAI adapter keeps translating; a cross-wire switch keeps starting a new
chat. A harness-native history that allows mid-chat wire switches is a later
decision (§8, Q7), not part of this spec.

### D10. Moves are re-export moves with shims

`services/llm_provider.py` and `services/chat_tools_schema.py` move into the
harness and leave shims that re-export the same objects. Identity is asserted
by a test. The agent loop itself (`chat_service.py`) moves in later phases,
continuing the turn-loop decomposition plan, never as a line-range cut.

### D11. Packaging

The PyInstaller `datas` allowlist gains `harness/prompts`, `harness/workflows`
and `harness/skills`; `smoke/check_bundle.py` checks they are present.

### D12. Security

Workflow and skill files are read from the package directory only. Their
bodies are instructions and are placed outside the `<untrusted_data>` fence;
the fence rules are unchanged. `ask_user` option labels sent back by the
client are user text and go through the same path as any typed message.

## Testing decisions

- Seam for the registries: unit tests load every Markdown file and assert the
  front matter schema, unique ids, and that every step named in front matter
  has a body section.
- Seam for the moves: identity tests (`services.llm_provider.LLMRequest is
  harness.protocol.LLMRequest`, same for every public name of the schema
  module); the existing 979 chat tests run unchanged.
- Seam for events: an AST scan of the loop collects every yielded frame name
  and asserts it is in `harness.events.FRAMES` (red first with a planted
  frame).
- Seam for prompts: the pinned-hash test, unchanged.
- Seam for `ask_user` and workflows: `run_turn` with `FakeProvider`, asserting
  the frame sequence (the contract the decomposition plan established).
- Parity: `smoke/run_chat_smoke.py` gains a `--workflow` mode that drives the
  start menu, one workflow and one `ask_user` round; per ADR-0002 the live
  probes (Anthropic wire, one OpenAI-compatible profile) must be run and
  named in the report of any phase that changes the loop.
- Frontend: vitest for the Choice card and the menu fetch; the manifest test
  learns the new frame.

## Out of scope

- Changing any tool's semantics, tier or route.
- A new engine or study (ADR-0004 stands; the investment workflow compiles onto the existing engine when its tools land under plan v1.4 U3).
- Mid-chat wire switching (Q7).
- MCP exposure (Q8) beyond reserving the place.
- The vision sub-call's direct Anthropic SDK use (recorded as a known second path; it moves when the loop moves).

## §8. Owner decisions (grill rounds 1–2, taken 2026-10-05)

Asked in the grilling format through the app's question UI. The owner chose
the recommended answer on every question except Q11.

| # | Decision |
|---|---|
| Q1 | Backend package `pypsa-gui/backend/harness/`. |
| Q2 | Markdown with YAML front matter. |
| Q3 | The start menu is served by `GET /api/chat/workflows`; the hardcoded chip arrays go. |
| Q4 | `ask_user` ends the turn; the pick is the next user message. The confirmation card stays blocking. |
| Q5 | Guided mode is folded into the `hub-design` workflow, wording and write-tier confirmation kept. |
| Q6 | All six workflows are active in the first menu; `investment-decision` stays planned. |
| Q7 | History format and the new-chat-on-wire-switch rule stay. |
| Q8 | MCP exposure later, own spec. |
| Q9 | Adapters move to `harness/providers/` in phase 1; `llm_config` stays in `services/`. |
| Q10 | A start-menu chip sends its opening request immediately through the request queue, labelled with the chip title. |
| Q11 | **`hub-design` is offered in Expert mode too** (owner's choice over the recommendation). |
| Q12 | This session implements phases 1 and 2 now, on this branch. |
| Q13 | In Expert, `hub-design` brings its steps only: the plain-language rules and the write-tier confirmation stay bound to Guided mode (`ui_mode`), never to the workflow. The workflow's Guided preamble is therefore marked `preamble_when: [guided]`. Expert turns outside a workflow stay byte-identical. |
| Q14 | Phase 1's ADR-0002 gate: the live Anthropic probe runs here; the OpenAI-compatible wire runs against the repo's stub endpoint, and the report says so; the live OpenAI run stays owed until a key or endpoint is available. |

The questions as asked, with the recommendations:

❓ **Q1 — Place and name**: `pypsa-gui/backend/harness/` importable as `harness`, or a repo-root `harness/` folder?
➡️ Backend package. The seam spec already calls this layer the harness; the backend must import it; a root folder would read as developer tooling.

❓ **Q2 — Authoring format for workflows and skills**: Markdown with YAML front matter, or YAML/JSON?
➡️ Markdown with front matter. Models read prose best; one file serves both the app and `.claude/skills/`.

❓ **Q3 — Where the start menu lives**: backend endpoint read by the panel, or keep the chips in the frontend and only mirror them?
➡️ Backend endpoint (`GET /api/chat/workflows`). One source of truth; the same menu for every provider and for a future MCP client.

❓ **Q4 — `ask_user` blocking or non-blocking**: block the turn until the user clicks (like the confirmation card) or end the turn and send the pick as the next message?
➡️ Non-blocking. Works on every wire, survives reloads, no held thread; the confirmation card stays blocking because it is a safety gate.

❓ **Q5 — Guided mode becomes a workflow**: fold the five hub-design steps and the Guided addendum into the `hub-design` workflow, or leave Guided as is and add workflows beside it?
➡️ Fold it, keeping the addendum wording and the write-tier confirmation rule. One mechanism; Guided is its first consumer.

❓ **Q6 — Which workflows ship first** (beyond `hub-design`): build-network, import-data, run-study, explain-results, improve-design, investment-decision?
➡️ All six as definitions now (they are content); `investment-decision` stays marked `planned` until the U3 tools exist.

❓ **Q7 — History format**: keep the block format and the new-chat-on-wire-switch rule, or design a harness-native history now?
➡️ Keep. The switch rule is documented and tested; a native format is a separate spec.

❓ **Q8 — External agents over MCP**: in scope for this effort or a later one?
➡️ Later, after the loop has moved; the catalogue is already the data an MCP server would serve.

❓ **Q9 — Provider adapters**: move `llm_anthropic`, `llm_openai_compat`, `llm_fake`, `llm_config` under `harness/providers/` in phase 1, or leave them in `services/`?
➡️ Move in phase 1 with shims, so the layering is visible; `llm_config` (the profile store) stays in `services/` because it is settings, not a provider.

## §9. Open owner questions (2026-10-06, from the DeepSeek Harness assessment)

Raised by `docs/superpowers/assessments/2026-10-06-deepseek-harness-adoption-assessment.md`.
Not decisions yet: each gates one of issues 13–19. Ask in the grilling format
with these recommendations.

❓ **Q15 — The session event log (issue 13)**: adopt an append-only event log as
the source of truth, with `chat.jsonl` kept as a projection for one release,
or keep the turn-record format (decision Q7) and add replay some other way?
➡️ Adopt it, keeping `chat.jsonl` as a projection so `/history`, the lineage
rules and every test see no change. It is what makes "switching models
shouldn't feel different" measurable: fork a session at a point and replay
the next turn on another provider.

❓ **Q16 — User-authored skills (issue 14)**: allow one extra skill root under
the app data directory, or keep D12's "package only"?
➡️ Allow the app-data root only, never a project folder or bundle; same-name
shadowing refused; provenance shown in the panel, never in the prompt.

❓ **Q17 — Goals with a round budget (issue 17)**: may the assistant start
turns on its own toward a stated goal, within a round cap and the existing
token budgets, with every card still blocking?
➡️ Yes, default 5 rounds, ceiling 20, no tier changes; only a human resumes
a paused or blocked goal. Deferred until the event log exists (the goal is
log state).

❓ **Q18 — External tools over MCP (issue 19)**: let admins configure MCP
servers whose tools join the catalogue for every provider?
➡️ Later, own spec with issue 11: admin only, zero servers by default,
write tier unless the server marks a tool read-only, descriptions capped and
results fenced.
