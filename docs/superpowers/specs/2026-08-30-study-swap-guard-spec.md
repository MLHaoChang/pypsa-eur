# Refusing a network swap during a study (Phase 11) — v1, BINDING

Workers implement THIS document. Amendments are recorded at the bottom, never
silently. This is §1 of the Phase-10 spec, deferred there on purpose because
it is a behaviour change on the core load path.

## 0. The defect

`solve_at` closes over the `pypsa.Network` object captured ONCE before the
worker starts (`routers/results.py`: `n = PyPSAService.get_network()`, then
`_solve_once(..., n, ...)` on every iterate). A network swap therefore does
not stop a running study — **it detaches it.** The loop keeps solving the
object it captured, and keeps publishing into `solver_state`, which
`reset_network` carries forward to the new project.

Three consequences, in increasing severity:

1. Project B's Adequacy tab fills in **live** with a study of project A's
   network — worse than a stale record, because it looks fresh.
2. The study's own answer is silently wrong to its user: it is still measuring
   a network nobody is looking at any more.
3. A `restore="final"` closing re-solve **writes project A's certified
   `ens_cap_permyriad` / `reserve_margin` into project B's solver config**,
   and re-solves project B's network at it.

Phase 10 fixed the finished-record half and pinned the deliberate decision not
to clear a LIVE record (clearing it breaks the 409 mutex). This is the half it
deferred.

## 1. The guard goes at the choke point, NOT at seven call sites

Seven user-facing routes replace the foreground network, every one of them via
`PyPSAService.reset_network()`:

| route | file |
|---|---|
| `import_bundle` | `routers/projects.py` |
| `create_from_template` | `routers/projects.py` |
| `load_project` | `routers/projects.py` |
| `reset_network` ("New") | `routers/network.py` |
| `undo_last` | `routers/network.py` |
| `restore_snapshot` | `routers/snapshots.py` |
| `_reset_with_ts_clear` (import) | `routers/io.py` |

★ **A guard repeated at seven call sites is a guard the eighth route forgets.**
`reset_network` refuses BY DEFAULT and takes an explicit
`allow_during_study=True` opt-out, so a route added later is protected by
construction. This is the same reasoning Phase 10 used to put the *clear*
there.

It raises `fastapi.HTTPException(409, …)` directly. That is not a new pattern
here — `project_registry` (10 sites), `project_acl`, `upload_service`,
`upload_guard`, `storage_paths`, `chat_service` and `chat_tools` already do
it — so FastAPI's built-in handler returns the 409 with no exception handler
to register and no route left to remember.

The check runs BEFORE any mutation, so a refusal leaves the swap not-started
rather than half-done.

## 2. The refusal must not tell the user to do something impossible

★ **Only two of the five studies can be aborted.** `coupling_loop` and
`margin_loop` have `/abort` routes and a `stop_event`; `mc`, `frontier` and
`fmea_sweep` have neither.

So the existing Phase-7 sentence — *"Wait for it to finish, or abort it."* —
is **already wrong for three of the five studies**, and this phase would
propagate that falsehood to seven more routes. Fixed here rather than copied:

* abortable → name the abort as an option;
* not abortable → say so, and say waiting is the only option. Never invent a
  control that does not exist.

`ABORTABLE_STUDIES` is pinned by a test that asserts it matches the set of
studies which actually have an `/abort` route, so the copy cannot drift from
the routes.

## 3. Where the shared facts live

Phase 10 moved `STUDY_KEYS` and `record_is_running` to
`services/project_context.py` to keep the import graph acyclic
(`study_state` imports `PyPSAService`, so `pypsa_service` cannot import
`study_state`). `reset_network` now needs the *sentence* too, so
**`STUDY_LABELS` moves there as well**, with `study_state` re-exporting it
exactly as it re-exports `STUDY_KEYS`.

`project_context` gains three pure functions over an arbitrary state dict —
no `PyPSAService`, no routes:

```python
def running_study_key(state) -> str | None
def study_swap_refusal(state, action: str) -> str | None   # the 409 detail
ABORTABLE_STUDIES = ("coupling_loop", "margin_loop")
```

`action` is what the user was trying to do ("load a project", "undo",
"restore a snapshot"), so the sentence names it. A refusal that does not say
what it refused is a worse error than the bug.

## 4. Acceptance

★ **A1** — each of the seven routes returns **409** while a study is live, and
the network is NOT swapped (assert the live network is still the old one).
Parametrized over the routes, not written seven times.
★ **A2** — the same routes still succeed when no study is running (the guard
must not become a permanent block).
★ **A3** — the refusal names the study AND, for `mc`/`frontier`/`fmea_sweep`,
does **not** tell the user to abort; for the two loops it does.
★ **A4** — `ABORTABLE_STUDIES` matches the studies that actually expose an
`/abort` route.
★ **A5** — `allow_during_study=True` still swaps, so an internal caller that
genuinely must proceed can, and Phase 10's "a live record is not cleared"
pin still holds on that path.
★ **A6** — a *finished* study does not block anything (Phase 10's clear still
runs, and the swap proceeds).

## 5. Gates

Full backend tree against the proven 43-failure master baseline; adequacy
gate; frontend `tsc -b` + vitest. Live: a new S20 driving the refusal over
HTTP. Every ★ red first, bitten, restores verified BY HASH.

## 6. Open question for the master, answered before implementation

**Does the frontend render a 409 from `GET /api/projects/{name}` usefully, or
as a crash?** A correct backend refusal that the UI shows as a blank screen is
not a finished fix. Checked during implementation; if the handling is poor,
either it is fixed here or the gap is stated in the PR body — not left to be
discovered by a user.

## Amendments

### v1.1 — implementation findings (master)

1. **`action` is a DEFAULTED parameter, not a ContextVar.** The first sketch
   read the action from a `ContextVar` each route would set — which puts the
   burden back on the seven call sites the design exists to avoid. It is now
   `action: str = "replace the network"`, so the guard works with **zero**
   call-site changes and a route that never describes itself still produces a
   true sentence. Sharpening the wording per route is a copy improvement,
   never a correctness dependency.

2. **§6 answered: the refusal reaches the user as a toast, verbatim.** The
   axios interceptor (`src/api/client.ts`) pops `toast.error(formatApiDetail(
   detail))` for any non-quiet error, and a project-load 409 is neither in
   `QUIET_MUTATION_URLS` nor carrying the `solver_in_flight` quiet code. So
   the backend sentence IS the user-facing copy — which is precisely why §2's
   phantom-abort defect was worth fixing rather than propagating. No frontend
   change is needed; the sentence is long for a toast, and that is the
   accepted trade for saying what is running and what to do about it.

3. **Phase 10's "a live record is not cleared" pin had to be re-stated.** It
   called `reset_network()` directly with a live study, which this phase makes
   a 409. The user-facing path is now unreachable, so the pin moved to the
   `allow_during_study=True` opt-out, where the no-clear rule still has to
   hold. The property is unchanged; only the door it is tested through moved.

**Bite-quality note, recorded because the discipline caught the test and not
the code:** bite C (`record_is_running` by status string alone) **did not
bite** on first run. `test_a_FINISHED_study_blocks_nothing` named that exact
variant in its docstring, but its fixture record said `status: "done"` — so a
status-only check returned False there too. The test was weaker than it
claimed. The hazard it was supposed to cover is specific and severe: a worker
that dies without writing its terminal status leaves `status == "running"`
behind forever, and a status-string liveness test would then refuse every
network-replacing route for the rest of the session — a permanent outage
caused by the guard meant to protect the user. `test_a_CRASHED_worker_does_
not_wedge_every_route_forever` now constructs a genuinely dead thread, and
bite C fails against it.
