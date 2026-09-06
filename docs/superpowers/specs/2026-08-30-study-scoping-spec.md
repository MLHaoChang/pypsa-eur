# Study-record scoping (Phase 10) — v1, BINDING

Workers implement THIS document. Where it disagrees with the plan, this wins
and the master is told. Amendments are recorded at the bottom, never silently.

## 0. The defect, established from the code and then reproduced

Every adequacy study publishes a record into the active project's
`solver_state` under its own key, and `GET /results/{study}` serves it back.
**Nothing clears those records — ever.** Verified, not remembered:

* `services/study_state.STUDY_KEYS` is referenced in exactly ONE place, the
  409 mutual-exclusion mesh. Never to reset.
* `PyPSAService.reset_network()` — which by its own docstring "runs at the
  START of every load/import/restore, and on explicit New/reset" — carries
  **the same `solver_state` dict object** forward onto a brand-new network.
* `load_project` re-hydrates `solver_config` and `RESULT_STATE_KEYS`
  (`services/project_context.py`). The five study keys are in NEITHER list —
  and `_hydrate_context_from_disk` even carries a comment about resetting keys
  first "so a project without a pkl can't inherit ghost state", for the other
  set of keys.

Reproduced over the real HTTP stack in
`tests/test_adequacy_study_scoping.py`, both RED before the fix:

```
project B is being served project A's MC study:
  {'status': 'done', 'result': {'marker': 'PROJECT_A'}, …}
a study of the discarded network survived the reset:
  {'status': 'done', 'result': {'marker': 'OLD_NETWORK'}, …}
```

This is the unfixed half of QA round 7. There, a stored 168 h MC study
answered a horizon question for a live 48 h network and put **a 3.5× wrong
reliability standard** on the wire (`bd8e8e4`). That fix corrected ONE read
site to consult the live network. Every other consumer — the MC panel's own
LOLE/EUE/ELCC table, both loop panels, the frontier, the FMEA sweep — still
renders a record that may describe a network the user has since replaced, and
none of them can tell.

## 1. Scope — and what is deliberately NOT in it

**IN: a FINISHED study record must not outlive the network it measured.**

**OUT, and it is a real defect: a study RUNNING during a network swap.**
`solve_at` closes over the `pypsa.Network` object captured once before the
worker starts (`routers/results.py`, `n = PyPSAService.get_network()` then
`_solve_once(..., n, ...)`). A load therefore does not stop the loop — it
detaches it. The loop keeps solving the old object and keeps writing into
`solver_state`, which is carried forward, so **project B's Adequacy tab fills
in live with a study of project A's network**, and a `restore="final"` closing
write lands its certified value in project B's `solver_config`.

That needs a 409 at each of the eight route entrypoints that replace the
network (`routers/io.py` ×1, `routers/projects.py` ×4, `routers/network.py`
×2, `routers/snapshots.py` ×1), reusing `blocking_study_detail`. It is a
behaviour change on the core load path and gets its own phase and its own
review. **This phase must not half-do it**, which fixes the shape of §2.

## 2. The fix

`reset_network()` is the ONE choke point — every path that replaces the
foreground network goes through it, so a fix there cannot be bypassed by a
caller that forgets.

It clears the study keys it is SAFE to clear: a record that is not running.

```
for key in STUDY_KEYS:
    if not record_is_running(state.get(key)):
        state[key] = None
```

★ **A running record is left alone, and that is deliberate, not an
oversight.** Clearing it would set `study_running()` to False while the worker
thread is still alive and still mutating the network — breaking the 409 mesh
and permitting a SECOND study, which is the exact corruption the mesh exists
to prevent. Leaking a running record keeps the mutex honest and the panel
shows "running" rather than a fabricated result. §1's phase is what actually
fixes that case. A test pins this choice so a later "tidy-up" cannot quietly
turn it into an unconditional clear.

### 2.1 One definition of "running", not two

`study_state.study_running` tests `status == "running" and thread is not None
and thread.is_alive()` against the ACTIVE state. §2 needs the same predicate
against an arbitrary dict. It is factored to **`project_context.record_is_running(record)`**
and `study_state.study_running` calls it — study_state's own docstring already
makes the argument ("a guard that differs between callers is not a guard").

`STUDY_KEYS` moves to `services/project_context.py`, beside
`RESULT_STATE_KEYS`, which is the same kind of datum. `study_state` re-exports
it so its existing importers are untouched. This is also what keeps the import
graph acyclic: `study_state` imports `PyPSAService`, so `pypsa_service` cannot
import `study_state` — but it already imports `project_context`.

## 3. Acceptance

★ **A1** — a finished MC record from project A is NOT served after loading
project B (the §0 reproduction, over real HTTP).
★ **A2** — a finished record does not survive `reset_network()` onto a new
network.
★ **A3** — a RUNNING record DOES survive, and `study_running` still reports it
— the §2 choice, pinned against a future unconditional clear.
★ **A4** — every one of the five study keys is cleared, not just `mc`.
Parametrized over `STUDY_KEYS` itself, so a sixth study added later is covered
by construction rather than by someone remembering.
★ **A5** — the 409 mesh still refuses a second study and still names it (the
Phase-7 guarantee, unchanged by the predicate refactor).

## 4. Gates

Backend adequacy gate + `test_activate` / `test_adequacy_http` / the project
suites (the fix is in `reset_network`, which they all exercise) 0 failed;
frontend untouched. Every ★ red first, bitten, restores verified BY HASH.

## Amendments

### v1.1 — the root cause was one level below §2 (master, during implementation)

§2's clear at the choke point closes both reproduced leaks, and the fix was
green. Then the full backend tree came back **44 failures against a 43
baseline**, and the extra one was mine:
`test_project_state.py::test_state_dict_keys_match_solver_state_fields`,
failing only in the full run and passing in isolation — my new tests writing
study keys into `_state` and leaving them there.

Chasing it found the actual root cause, stated in that test's own comment,
written long before this phase:

> the live `_state` dict must carry EXACTLY the ProjectSolverState fields:
> **no orphan key (the per-project dispatcher wouldn't reset it)**

**The five study keys were never declared fields of `ProjectSolverState`.**
Every reset path in the codebase iterates a declared group — `LIFECYCLE_KEYS`,
`RESULT_STATE_KEYS` — so a key in neither group is a key nothing can ever
reset. That is not a missing line in `reset_network`; it is a key that was
never part of the state's shape. The dataclass even carries the precedent
twice: `last_failure` and `last_reserve_margin` are both annotated *"(not an
orphan key)"* for exactly this reason.

So the fix has two halves, and the second is the structural one:

1. **Declare `fmea_sweep`, `frontier`, `mc`, `coupling_loop`, `margin_loop` as
   `ProjectSolverState` fields** — in NO persistence group, deliberately: a
   study measures a network *in memory* and must never be restored from disk
   beside a network it may no longer describe. A fresh per-project context now
   starts with them clean by construction rather than by a caller remembering.
2. **§2's conditional clear in `reset_network`** — still required, because the
   carry-forward reuses the SAME dict object rather than building a fresh
   `ProjectSolverState`.

`STUDY_KEYS` becomes a fourth group in the partition invariant
(`test_every_field_is_classified_exactly_once`), which now also asserts the
study keys are in NEITHER persistence group — the property that keeps a study
off disk.

**A5 is stronger than specified as a result**: the pollution was the bug
reporting itself. A test that only passes in isolation was the signal that the
state shape, not the reset, was wrong.
