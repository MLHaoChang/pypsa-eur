# Assistant presence and deixis — design

**Status:** approved in brainstorming + grilling, 2026-08-05 · **Sequence:** step (c) of (a)→(b)→(c)

Turn the chatbot from an opt-in panel into an assistant that is present when
the tool launches, knows what you are looking at, and moves the app to support
what it says.

## The three problems, measured

### 1. The assistant evicts itself

`store/uiStore.ts:266` declares `activeSlidePanel: SlidePanel | null` — **one
value** — and `'chat'` is one of the union's fourteen members
(`uiStore.ts:30`). The assistant is therefore mutually exclusive with Results,
Compare, Scenarios, Time Series and everything else.

Follow that through `applyUiNavigate` (`ChatPanel.tsx:128`): it calls
`ui.setSlidePanel(panel)`. **When the assistant navigates you to Results, it
closes itself.** Ask it to show you curtailment and it removes itself from the
screen in order to comply.

This is very likely why the navigation capability reads as absent despite
being complete: every successful navigation ends with the assistant gone.

### 2. The channel is one-way

The agent→UI half is finished. Four tools emit the `_ui_event` marker —
`ui_select_component`, `ui_open_panel`, `ui_set_snapshot`,
`ui_open_asset_detail` — and `ui_open_panel` reaches every branch of
`applyUiNavigate`: twelve slide panels, canvas views, Results sub-tabs, bottom
asset tabs, the A|B compare rail. `compare_projects` even auto-opens the rail
on the matching tab.

Nothing travels the other way. The model never learns the active panel, the
selected component, the current results tab, or which projects are in the
compare rail. So the phrasing people actually use with an assistant does not
work:

* "Why is **this** so high?" — no referent.
* "Compare it to **the other one**" — no referent.
* "Explain **this**" — no referent.

The assistant can move your view but has its back to the screen.

### 3. The stance is reactive

`_build_system_prompt` (`chat_service.py:1706`) opens with *"answer questions
and make changes"* and instructs *"be terse."* Nothing asks it to orient the
user, open the view that supports its answer, or resolve deictic references.
A complete navigation tool surface plus a reactive prompt yields a chatbot
that can drive and never does.

## Architecture

Four additions and one removal. Nothing in the agent→UI half changes.

```
uiStore ──buildUiContext()──▶ POST /api/chat/stream {ui_context, input_mode}
                                        │
                              _format_ui_context() → user-turn block
                                        │
                                      model
                                        │
                              ui_open_panel() → {_ui_event: true}
                                        │
        uiStore ◀──applyUiNavigate()── SSE ui_event
```

### `frontend/src/utils/uiContext.ts` (new)

One exported pure function, `buildUiContext()`, reading `uiStore` and
returning a stable minimal object. No React, unit-testable directly. It gets
its own module because it is the contract; inside `ChatPanel.tsx` it would be
untestable and invisible.

**Identifiers only** — active panel, canvas view, results tab, bottom tab,
selected component (class + name), compare A/B/tab, snapshot. Roughly 80–150
tokens.

Deliberately **no values, no chart data, no screenshot.** The model already
reads live state through 139 tools that use the same code paths as the UI.
Pasting values into the prompt creates a second source for the same fact, and
the prompt copy is the stale one — captured at send time, blind to an edit
landing mid-turn and to changes the model itself just made. Nothing would make
the model prefer the tool over the text in front of it. Context says *what you
are looking at*; tools say *what is true*. That boundary also stops the payload
growing every time a panel is added.

### `ui_context` and `input_mode` on the request

New optional fields on `ChatStreamRequest` (frontend) and `StreamRequest`
(pydantic). Optional is load-bearing: `run_chat_smoke.py` and the test harness
must keep working with them absent.

`input_mode` is `"voice" | "text"`, carried from the composer. It is a field
rather than an inference because speech reciprocity depends on it and
reconstructing it later from timing or content is guesswork.

### `_format_ui_context()` (new, backend)

A pure dict → string renderer, appended to the **user turn**.

**Never the system prompt.** `chat_service.py:2086` marks the system block
`cache_control: ephemeral`, and the code documents the stakes: cache_read at
$0.30/MTOK against raw input at $3.00/MTOK. Volatile context in the system
block would invalidate that cache on every navigation and multiply input cost
roughly tenfold, with the bill as the only signal.

Failure rule, copied from the precedent at the `_build_system_prompt` call
site — *"Failure → omit; never abort the turn for meta."* Malformed or absent
context degrades to no block, never to a failed turn.

### `_ASSISTANT_STANCE` (new system-prompt block)

The smallest change with the largest effect. It instructs the model to open
the view that supports what it is saying rather than describing where to
click, and to resolve "this / that / here" against the supplied context rather
than guessing.

It belongs in the **system** prompt precisely because it is stable policy —
cache-safe, unlike the context itself.

### The dock, and removing `'chat'` from the union

The assistant moves out of `activeSlidePanel` into its own collapsible dock
with independent open/collapsed state, rendered **outside** the panel
container so it survives `FULL_SCREEN_TABS` (`results`, `timeseries`,
`capacityBounds`, per `App.tsx:120`) taking over the view.

Collapsed, it is a slim always-visible strip carrying the launcher button and
the microphone — the always-accessible affordance, without permanently
spending the width.

`'chat'` is **removed** from the `SlidePanel` union. That is what makes the
self-eviction bug structurally impossible rather than fixed by convention: a
future `setSlidePanel('chat')` stops compiling.

**Honest cost:** every full-screen tab and canvas view must lay out correctly
against a narrower main area. This is a broad frontend change touching files
the rest of this work would not.

## The launch orientation

**Hybrid — local first, model enriches.**

`schemas.py:643` already classifies results as `fresh` / `stale` / `none`, and
`OverviewPanel.tsx:36-40` already fetches `getMeta` and `getStatus` at launch.
So a useful orientation needs no backend work and no API call: project name,
network size, solve status, staleness.

That local summary renders **immediately** — no spinner, no key required, no
network. If an API key is configured and the setting is on, one model turn
follows and adds judgment: why the results are stale, what is worth doing next.

**Enrichment rule: the model may add, never re-assert.** If the local line says
results are stale and the model turn says otherwise, the user watches the app
disagree with itself. The model turn is instructed to extend, not restate.

**With no project open**, the greeting says so and offers the two useful next
actions — open a recent project, or create one. This makes the assistant the
natural entry point on a cold start rather than an empty canvas.

**With no API key**, the local greeting still renders and carries a quiet
one-line offer to add a key, which opens the U-1 field inline. It must **not**
produce the red `missing_api_key` error banner — a feature that throws an error
on every launch gets disabled permanently within a week.

**The model turn is opt-out**, in `LocalSettings`, default on. It costs an API
call per launch, so it needs a switch; defaulting it off would mean nobody ever
sees the feature. The local summary ignores the setting — it is free and always
renders.

## Speech

**Modal reciprocity.** A turn begun with the microphone is answered aloud; a
typed turn is answered in text. Plus a global mute.

This matches how people already expect assistants to behave and requires no
settings trip: the spoken mode is chosen by the act of using the microphone.

**The launch greeting is silent**, deliberately. An app that talks at you
unprompted on every launch is the fastest way to get the assistant switched off
for good. Speaking should be something the user initiated.

Speech output is free and available: measured at 219 voices in a real cocoa
WKWebView, no permission required. Speech *input* is permission-blocked in the
packaged app today; its fix is specced separately in
`2026-08-05-model-refresh-and-voice-permission-design.md`. Reciprocity degrades
correctly if that fix fails — voice output simply never triggers on its own,
leaving no dead control and nothing to explain.

## Verification

* **The eviction bug cannot return.** A test asserts the dock stays mounted
  across a `ui_open_panel` navigation to a full-screen tab. This is the
  regression that defines the feature, and it fails today.
* **Deixis resolves.** With `ui_context` naming a selected component, a
  scripted turn referring to "this" reaches the right component. Asserted
  against the component *name*, not merely that a tool was called.
* **Cache integrity.** A test asserts `ui_context` appears on the user turn and
  **not** in the system block. Without it, the tenfold input-cost regression is
  invisible until the invoice.
* **Graceful degradation.** A request with `ui_context` absent completes
  normally — the smoke harnesses send none.
* **Launch with no key** renders the local greeting and produces **no** error
  banner. This is the state the primary user is in today.
* **Reciprocity.** `input_mode: "voice"` triggers synthesis; `"text"` does not.
* **Enrichment rule.** The model turn is asserted not to restate the local
  summary's facts.
* Frontend `tsc -b` exits 0 and the full vitest suite passes — the union change
  is a compile-time breaking change by design, and every `setSlidePanel('chat')`
  site must be found by the compiler rather than by grep.

## Out of scope

Proactive triggers beyond launch — solve completion, navigation commentary,
validation warnings. All were considered and deferred; the `ui_context` channel
is their shared prerequisite, so each becomes small once this lands.

Charts rendered inside the conversation, and results interpretation as a
feature. That is a separate project with its own rendering and its own
judgment about what "interpret" means.

Wake-word activation. Tool-set reduction for local models.

## Dependency note

This and `2026-08-05-llm-provider-seam-design.md` both touch turn assembly in
`chat_service.py` — the seam restructures where the request is built, this adds
`ui_context` to it. The agreed (b) → (c) order avoids the collision. If (c) must
start first, they conflict in exactly one place, and that should be known in
advance rather than discovered mid-implementation.
