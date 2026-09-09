# Chat Turn-Loop Decomposition Implementation Plan

**Target:** `pypsa-gui/backend/services/chat_service.py` — specifically
`_run_turn_body` (566 lines) and `_dispatch_real_tool_call` (292 lines).

**Status:** specced 2026-09-09. Not started.

**Why this and not "the next three god files":** the backend god-file
decomposition (`docs/superpowers/plans/2026-09-04-backend-god-file-decomposition.md`)
took four files from 16,839 lines to 4,357 and landed as PR #6. Measuring what
is left changes the diagnosis. The three largest remaining backend files are
not the same problem as each other:

| file | lines | shape | verdict |
|---|---:|---|---|
| `services/chat_service.py` | 3,054 | 51 fns, but ONE is 566 lines and another 292 | **this plan** |
| `routers/projects.py` | 2,964 | 23 routes, `_save_context` is 454 lines | next, same shape |
| `services/chat_tools.py` | 3,044 | **139 fns averaging ~22 lines** | deliberately NOT a target |

`chat_tools.py` is a catalogue, not a god function. Splitting it into
`chat_tools/excel.py`, `chat_tools/network.py` and so on would move lines
between files without making anything easier to reason about — the same
judgement Phase 4/5 made about `routers/network.py`'s ~80 two-line CRUD routes.
Recorded here so nobody "finishes the job" by mechanical file-splitting.

A long FILE is inconvenient. A long FUNCTION is the thing that actually costs:
you cannot read it, cannot test it in parts, and every bug in the feature lands
inside it. `_run_turn_body` is the largest function in the backend.

## Global Constraints

Same contract as the god-file plan, with one change of instrument.

1. **Strictly behaviour-preserving.** Defects found go to
   `docs/superpowers/findings/`, not into these commits. (PR #6 held this line
   for 26 commits; the two CodeQL fixes it eventually carried were written and
   tested on their own branch first, then ported as a labelled commit.)
2. **The contract is the SSE frame sequence.** This is the one real difference
   from the router phases. Nothing moves between routers here, so there is no
   OpenAPI document to compare byte-for-byte. What `_run_turn_body` *is*, from
   every caller's point of view, is a generator of `(event_name, payload)`
   tuples — and the 521 already-collected chat tests assert against exactly
   that, via `events = list(chat_service.run_turn(session, "…", client=client))`.
   So: **record the tuple sequence for a set of scripted turns before each cut
   and require it unchanged after.** That is a stronger gate than "the tests
   pass", and it is available for free because the fake-client harness already
   exists.
3. **Tripwires before the cut**, red first. An origin map that flips from
   `None` to the new location, so the guard is proven to fail before it passes.
4. **AST-driven extraction, never line-range moves.** Cost this twice in the
   earlier phases (`_safe_log`, `_CLS_TO_ATTR`).
5. **Mutation-test every guard.** Earned its keep three times in the earlier
   work — including catching a guard of mine that passed vacuously because it
   matched a comment, and one that walked `_effective_candidates` (empty until
   the property builds it) and so inspected zero routes.
6. **The gate is "the failing set is unchanged"**, not "green".

## Why these cuts are NOT the router phases' kind of move

Worth stating before any code moves, because it changes what "verified" can
mean here.

Phases 1–5 were re-export moves: lift a function, import it back under the old
name, prove the object is identical. `_run_turn_body` has no such seam. It is
one control-flow braid over a shared mutable frame, and **every candidate cut
crosses a generator boundary** — a `yield` inside an extracted helper only
reaches the client if the caller does `yield from`. Extracted pieces also
mutate frame state the loop reads afterwards (`tool_call_count`, `final_message`,
`attempt`, `model_fallback_used`, `switched_mid_turn`).

So these are real refactors, not moves, and "the object is identical" is not
available as a proof. The SSE recording is what replaces it. Each phase states
what it does with the mutable state rather than pretending there is none.

## The measured structure

`_run_turn_body`, lines 1851–2416, by top-level statement:

```
1859-1868   docstring
1880-1891   turn-context pinning + the _project_switched closure
1895-1901   gate 1 — session output-token budget      -> yield session_done; return
1909-1919   gate 2 — per-project/per-day token spend  -> yield session_done; return
1921-1933   client build (+ error frames)
1935-1940   yield session_init
1946-1950   history seeding
1967-2050   ATTACHMENTS — multimodal + tool-accessible annotation   (83 lines)
2056-2069   cache anchor, tools payload, system prompt
2071-2416   while True:                                             (346 lines)
              2072-2074   abort check
              2083-2095   system/tools cache blocks
              2111-2211   while attempt < max_attempts:             (101 lines)
                            SDK call, streaming, error mapping, model fallback
              2213-2228   usage accounting
              2232-2285   no-tool-use path -> persist turn, yield turn_done, return
              2294-2325   parallel-destructive offender check
              2338-2399   for each tool_use:                        (62 lines)
                            project-switch guard, per-turn call cap, rebinding tools
              2400-2416   replay results, switched_mid_turn handling
```

`_dispatch_real_tool_call`, lines 2419–2710: 21 top-level statements, largest
65 lines. **A sequential lifecycle, not a dispatch table** — resolve tool,
safety tier, confirmation gate, execute (inline for solver tools, else through
`_TOOL_EXECUTOR` with a copied `contextvars` context), apply the char budget,
emit. Three stages, and therefore decomposable. (I first wrote it up as "a flat
60-way dispatch, excluded for the same reason as chat_tools.py" and checked
before committing that to the plan; it is not.)

## Phases, ordered by risk

Deliberately ascending. The first two are cheap and prove the harness; the
later ones are where the value is.

### Phase A — `_build_user_content()` (83 lines, 1967–2050)

The attachment block: split `attachment_file_ids` into multimodal blocks
(images/PDF, prepended so the model sees references before the question) and
tool-accessible files (xlsx/docx/csv/txt, annotated into the text rather than
sent as content, because the multimodal API 415s on them).

**Correction, made on reading it rather than on planning it:** this section
first said the block "contains no `yield` and touches no loop state". Half
wrong. It has no loop state, but its `except HTTPException` branch yields
`error` + `session_done` and RETURNS — it can abort the whole turn. So it is
not the pure seam advertised.

That changes the shape of the cut, not its order. Three ways to extract a
fragment that both computes a value and can abort:

* a generator the caller drives with `yield from`, returning a sentinel through
  `StopIteration` — hides the abort in a mechanism most readers have to look up;
* raising an internal exception carrying the frames — control flow by exception
  for an expected case;
* **returning `(user_content, abort_frames)`**, where `abort_frames` is `None`
  on success and otherwise the frames the caller yields before returning.

The third. The helper stays an ordinary function (so it is callable directly
from a test, which is the point of extracting it), and the abort stays visible
at the call site instead of being buried in the helper's control flow.

**Why still first:** no loop state, no mutation of the turn's frame, and
`tests/test_chat_multimodal.py` (675 lines) already covers the behaviour.

**Do not restructure the text block.** The prefix+message concatenation is
pinned by substring assertions in two existing multimodal tests, and the
untrusted-delimiter placement is a security boundary: the trusted instruction
line sits OUTSIDE the delimiters, the per-file lines echoing user-controlled
filenames sit INSIDE. Moving a line across that boundary is a behaviour change
wearing a refactor's clothes.

**Guard:** the extracted function called directly for each attachment category,
asserting block order (references first, text last), the tool-accessible
annotation text, the delimiter placement, and the abort tuple on a bad
attachment; plus the SSE recording unchanged.

### Phase B — `_turn_budget_block()` (~25 lines, 1895–1919)

The two budget gates become one function returning the `session_done` payload
or `None`; the caller yields it. Mechanical, and it makes both caps testable
without constructing a whole turn.

**State:** none mutated. Reads `session.usage_acc`, the module-level
`PYPSA_GUI_CHAT_DAILY_TOKEN_CAP` (monkeypatched by tests, so it must stay a
call-time module-attribute read, not a captured default) and `_today_token_spend`.

**Guard:** both caps, each asserting the exact payload dict — `reason`, `kind`,
`limit`, and `spent` for the daily one. Mutation: swap `>=` for `>` and the
boundary case must fail.

### Phase C — `_dispatch_tool_uses()` (~83 lines, 2334–2416)

The per-tool loop: parallel-destructive offender check, the mid-turn
project-switch guard, the per-turn tool-call cap, the rebinding-tool
allowance.

**State:** this one is the reason Phase C is not Phase A. It mutates
`tool_call_count`, `switched_mid_turn` and `tool_results_for_next_turn`, and it
yields. Extraction means `yield from` plus returning the mutated counters
explicitly — a small dataclass, not a tuple, so a later field addition cannot
silently reorder at a call site.

**Guard:** the cap, the switch guard, and the rebinding allowance each as a
scripted turn whose frame sequence is recorded.

### Phase D — `_stream_one_attempt()` (~101 lines, 2111–2211)

The retry loop: SDK call, streaming, exception mapping, model fallback.

**Highest risk and last.** It yields SSE frames *and* mutates `pending_blocks`,
`final_message`, `attempt`, `model_fallback_used` and `emitted_this_attempt`,
and the interleaving of "what was already emitted this attempt" with "retry
from scratch" is the subtle part: emitting a partial answer and then retrying
would duplicate text in the client. `emitted_this_attempt` exists to prevent
exactly that, so the guard has to pin it.

**Guard:** a fake client that fails the first attempt after partial emission
and succeeds on the second, asserting no duplicated text frames — written and
red before the cut.

### Phase E — `_dispatch_real_tool_call` staging (292 lines)

Split the lifecycle at its two natural joins: the confirmation gate, and
result shaping (char budget + truncation + `tool_result` block). Execution
stays put — the `contextvars.copy_context()` around `_TOOL_EXECUTOR.submit` is
load-bearing (a worker thread does not inherit contextvars, and without the
copy every project tool 401s), and that is precisely the kind of detail a
careless split drops.

**Guard:** `tests/test_chat_tools_dispatch.py` (588 lines) already covers much
of it; add one that asserts the acting-user contextvar survives into the worker,
because that is the failure this phase could plausibly introduce and it would
show up as a permissions bug, not a refactor bug.

## Out of scope, deliberately

* **`chat_tools.py`** — reasoning above. A catalogue.
* **`ChatSession` (238 lines)** — a class with a lock and a deque; it is
  cohesive and its size is data, not control flow.
* **Any behaviour change**, including the ones that will be tempting on sight.
* **`routers/projects.py::_save_context` (454 lines)** — the same shape as this
  work and the obvious next target, but a separate plan.

## Verification per phase

1. Record the SSE frame sequence for the scripted turns, before the cut.
2. Write the phase's tripwire; watch it fail.
3. Cut, AST-driven.
4. Recording identical; the 521 chat tests' failing set unchanged; full backend
   suite's failing set unchanged; the 19 QA drivers pass.
5. Mutation-test each new guard.
