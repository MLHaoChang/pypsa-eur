# Unsaved solver results must be visible to the destructive-action guards

**Date:** 2026-08-27
**Status:** design approved, plan pending
**Branch context:** `feature/local-app-impl` @ `6e43188c`

## Problem

Three guards protect destructive actions, all added or corrected on 2026-08-27:

| Guard | Site | Commit |
|---|---|---|
| Import replaces the network | `pages/ImportExport.tsx` | `86d0fe00` |
| Palette snapshot restore | `components/CommandPalette.tsx` | `f0c993c7` |
| Sidebar destructive re-load | `layout/Sidebar.tsx` | `330ed9ce` |

All three ask the same question — "is there unsaved work?" — and all three answer it
the same way: `(await networkApi.undoInfo()).depth > 0`.

**Undo depth is a proxy, not the fact.** The undo stack is cleared on save
(`routers/projects.py:1021`, `:1241`, `:1402`, `:2278`), so a non-zero depth does imply
unsaved mutations. But the converse fails: work that never enters the stack is invisible
to every consumer of `depth`. Solver results are exactly that work. They are written
straight into the in-memory network by `services/solver_service.py`
(`network.optimize(...)`) and never pushed to the undo stack.

So a user who solves a network and then imports a file, restores a snapshot, or re-loads
the project is **not prompted at all**, and the solve is gone.

The gap is already documented at the site that causes it — `main.py:110-113` explains
that `/api/simulation/` was left out of `_UNDO_PREFIXES` "because the undo stack does not
capture it". That reasoning is correct for undo and wrong for dirt, and the bug is that
one list was used to answer both questions.

**Why this matters more than the three individual guards did:** it is a single blind
input behind three guards that now *look* complete. Each was verified against its own
prompt behaviour and each passed. A shared wrong input is invisible to per-guard review,
which is the same shape as the sibling-path and duplicated-predicate failures this branch
has already hit twice.

## Non-goals

- Making solves undoable. Undo entries are netcdf blobs against a 500 MB cap
  (`undo_service.MAX_BYTES`); a solve would evict genuine edit history, and "undo the
  solve" is not a behaviour users expect. Rejected during brainstorming.
- Changing what `depth` means. StatusBar's "3 unsaved edits" is a question about *edits*
  and `depth` remains its correct answer.
- Any change to the fail-closed unknown handling shipped today. That stays as-is.

## Design

### 1. A dirty flag, per project context

New `services/dirty_state.py`, mirroring `undo_service`'s established shape: module-level
functions operating on the ACTIVE project's state via a `_active()` helper, with the state
itself living on `ProjectContext` (as `ctx.dirty`, beside the existing `ctx.undo`).

Per-context, not global — two open projects must not share dirt. This is not a new pattern;
it is the one `_UndoState` already uses, for the same reason.

API: `mark_dirty()`, `clear()`, `is_dirty() -> bool`.

### 2. Two prefix lists, deliberately

```python
_UNDO_PREFIXES  = ("/api/network/", "/api/io/")            # what undo CAN restore
_DIRTY_PREFIXES = _UNDO_PREFIXES + ("/api/simulation/",)   # what makes memory differ from disk
```

Both live in `main.py` beside the existing constants, with the distinction stated in
words next to them: *"undo can restore this" and "this differs from disk" are different
questions; do not collapse these lists.* Collapsing them is precisely the present bug.

### 3. What sets it

`undo_snapshot_middleware` (`main.py:564`) already runs before every mutating request and
is the single chokepoint through which `_push_undo_snapshot` is reached. It calls
`dirty_state.mark_dirty()` for a **successful** non-GET request whose path matches
`_DIRTY_PREFIXES`, under the same `_UNDO_EXCLUDE` exemptions.

Deliberately at the middleware, not at each route: one site to maintain, and it is the
site that already answers "did this request mutate the network".

### 4. What clears it

**The rule, which matters more than the list: clear iff the operation leaves memory and
disk equal.** Implement against the rule and check each site against it; do not clear
"wherever undo clears" by reflex. The two happen to coincide today, and that coincidence
is not a reason.

There are six real `undo_service.clear()` call statements (grep also matches four prose
mentions in comments — those are not sites):

| Site | Direction | Clear? |
|---|---|---|
| `routers/projects.py:1402` `save_project` | memory → disk | yes |
| `routers/projects.py:2278` `load_project` | disk → memory | yes |
| `routers/projects.py:1021` `import_bundle` | writes members to disk (`atomic_write_bytes`), then loads | yes — verified |
| `routers/snapshots.py:497` `restore_snapshot` | copies the snapshot's files over the project's, then reloads | yes — verified |
| `routers/projects.py:1241` `create_from_template` | new project materialised then loaded | yes — confirm during implementation |
| `routers/network.py:2062` `reset_network` | fresh in-memory network | **confirm** — if the reset network is not persisted, memory differs from disk and this must NOT clear |

The two marked "verified" were checked because the naive reading is wrong: a snapshot
restore *sounds* like it leaves memory ahead of disk, and it does not, because it rewrites
the project's files first. The last two are marked confirm rather than guessed.

Clearing is added adjacent to each site, not folded into `undo_service.clear()` itself:
the two concepts are not synonyms, and a future caller may legitimately want one without
the other.

**Not cleared by undoing to depth 0.** If a user solves and then undoes their edits, the
results still differ from disk. This is the case that proves `depth` and `unsaved` are
different signals rather than one signal with two spellings.

### 4a. The error asymmetry, which should shape the tests

A missed **set** under-prompts: destructive work proceeds silently. That is the bug being
fixed, and it is unsafe.

A missed **clear** over-prompts: the user is asked about work that is already saved.
Annoying, and safe.

The two are not equally bad, so they do not deserve equal test effort. Weight coverage
toward the set path — that is what the route-coverage test below exists for — and treat
the clear sites as ordinary unit tests.

### 5. The interface

`GET /api/network/undo/info` gains one field:

```json
{ "depth": 3, "memory_bytes": 12345, "max_bytes": 524288000, "unsaved": true }
```

Additive, so existing consumers are unaffected. Mirrored in `frontend/src/api/network.ts`.

### 6. Consumers

The three guards switch from `depth > 0` to `unsaved`. Their fail-closed unknown handling
is untouched: a failed probe still prompts. StatusBar keeps reading `depth` for its count
and gains `unsaved` for the dot, so a solved-but-unsaved project no longer shows green.

## The fail-open problem, and the test that answers it

A dirty flag that misses a mutating route is a guard that silently stops prompting — a
strictly worse failure than the one being fixed, because the guards would still appear to
work. `_DIRTY_PREFIXES` is a hand-maintained allowlist, and every hand-maintained
allowlist in this codebase has eventually missed a member.

**Route-coverage test.** Walk the FastAPI app's own route table and assert that every
non-GET route is either matched by `_DIRTY_PREFIXES` or named in an explicit
`_DIRTY_EXEMPT` set with a stated reason. The exemption set is the opt-in, and adding to
it is a decision someone has to write down.

Precedent exists: `tests/fixtures/route_inventory_phase0.txt` driven by
`tests/test_chat_tools_endpoint_map.py` already pins the route surface this way.

This test is the reason the design is worth its ceremony. It fails when someone adds a
mutating route without considering dirt — which is the only mechanism that keeps this
correct after everyone here has forgotten the reasoning.

## Testing

RED before GREEN on each:

- **Backend, the defect itself:** solve, then assert `undo_info()["unsaved"]` is true while
  `depth` is 0. Fails today — there is no field.
- **Backend, the sibling:** save clears it; undo-to-depth-0 does NOT.
- **Backend, coverage:** the route-coverage test flags `/api/simulation/` against today's
  `_UNDO_PREFIXES`, proving the test can see the live bug before the fix.
- **Frontend, per guard:** with `unsaved: true, depth: 0`, each of the three guards prompts.
  Sibling assertion per guard: with `unsaved: false`, none of them prompt — the guards must
  not become unconditional.
- **Frontend, unknown:** unchanged fail-closed behaviour still holds.

Canonical gates: `pixi run gui-tests` (backend), `npx vitest run` + `npx tsc --noEmit`
(frontend), each with a before/after source digest to prove the tree was stable across the
run.

## Accepted consequence

`unsaved` is true after any solve until the project is saved, so solve-then-import now
always prompts. This is more prompting than today. It is correct — the results are real
work — and it is the entire point of the change, but it is a deliberate UX cost and should
be recorded as one rather than discovered later.

## Open

- Whether the prompt copy should distinguish "unsaved edits" from "unsaved results".
  One flag cannot say which; `depth > 0` alongside `unsaved` can. Deferred to the plan —
  it is a copy decision, not a structural one.
