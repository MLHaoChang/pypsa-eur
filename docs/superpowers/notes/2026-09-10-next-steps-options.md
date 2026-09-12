# Solution FMEA — what is done, and the options for what comes next

**Written for a decision, not as a plan.** Each option below states what it
is, why it might matter, what it costs, and what "done" would look like, so
one can be picked without reading anything else first. Nothing here is
started. Options are grouped; the groups are independent of one another.

## Where things stand

The solution-FMEA work is on `master` as `75e3a1a` (PR #5, squash-merged).
CI was green on the merged head: GUI backend, both integration jobs, unit
tests and both code-scanning checks. The one job that did not report is
`Run validation`, which sits on a self-hosted runner and has never picked up
work in this programme.

Two follow-up commits sit on `claude/solution-fmea-integration-0mx5lc`,
restarted from master after the merge, and are **not** in a pull request:

| Commit | What |
|---|---|
| `ad8673c` | Renaming any non-bus component through the editor was a 500 — PyPSA's rename helper derives its cross-reference column from the renamed class and indexes every component's table with it, which holds only for buses. Fixed, with the known-defect test replaced by two real ones. |
| `ac8da66` | Five small findings closed: a test assertion that could not fail, a zero-capacity asset offered in the capacity-credit picker, the undo rule that shipped without a test, a stale comment, one spelling written out in three modules. |

Verification on those two: 1023 backend tests, 458 frontend tests, a clean
typecheck, and an undefined-name count identical to master's.

## Group A — the two commits on the branch

Pick one.

| # | Option | Cost | Done when |
|---|---|---|---|
| **A1** | **Open a pull request** for them and let CI and a reviewer look. | Minutes of mine; a review of yours. | PR open, CI green. |
| **A2** | **Merge them** the way PR #5 went in, squashed, without a separate review. | Minutes. | On master. |
| **A3** | **Leave them on the branch** and decide later. They are pushed and lose nothing by waiting. | None. | — |

A1 is the conservative choice and the one I would take: the rename fix
changes a shared code path that every component edit runs through, and it has
not yet been seen by CI at all.

## Group B — what is still open in the feature

| # | Option | Why it might matter | Cost | Done when |
|---|---|---|---|---|
| **B1** | **The redundant solve.** A planning-loop run that hits the fleet ceiling spends one full solve plus one sampling run re-evaluating a plan it already has. Bounded by the controller's duplicate-plan check, so it costs time, never correctness. | A long study gets shorter. Nobody is misled today. | Small, but inside the shared controller both loops use — the part of this system with the most subtle invariants. | The clamped iterate reuses the plan instead of re-solving, with a test that counts solves. |
| **B2** | **The four findings I deliberately left.** A defensive fallback that is unreachable through the only path that reaches it; a chip and a verdict that state two different true things about the same ceiling; a mixture state an all-NaN column costs a zero-capacity unit. | Each is cosmetic or unreachable. Recorded in the review note with reasons. | Small each. The mixture one is inside the convolution and carries real risk for a state. | Closed, or explicitly declined in the note. |
| **B3** | **Nothing.** Treat the feature as finished and let real use surface the next thing. | The review passes have hit diminishing returns: the last one found nine items, of which five were worth doing. | None. | — |

## Group C — verification that has never happened

These are gaps in evidence, not known defects. C1 is the one I would not
leave open indefinitely.

| # | Option | The gap | Cost | Done when |
|---|---|---|---|---|
| **C1** | **Run the live journey on the PyPSA the project pins.** Every end-to-end pass — the IEEE 39-bus journey, the smoke suites — ran on this container's PyPSA 1.3.0. CI runs the unit suite on the pinned 1.1.2, but no *live* journey ever has. The version gap is not hypothetical: it hid six broken tests and one real schema gap until CI ran. | A solve-path difference between the two versions would be invisible today. | Half a day: a pixi environment, then the journey driver, which is committed under `smoke/ieee39` and takes an argument for the network. | The 46-step journey completes on 1.1.2 with the same findings set. |
| **C2** | **Get the self-hosted runner working.** `Run validation` has never completed. Nothing in this programme has been validated on the project's own validation job. | Not mine to fix — it is infrastructure — but it means one whole class of check is unproven. | Unknown; outside the repository. | The job completes on any head. |
| **C3** | **Add QA drivers for the adequacy routes.** Master added a `gui-qa-drivers` CI step for the standalone `qa_*.py` scripts, because pytest never collects them and five of nineteen had been broken for months. None of them covers adequacy, the FMEA worksheet or the planning loops. | The feature's live coverage is the 32 smoke suites, which CI does not run. The drivers are what CI *does* run. | A day for meaningful coverage. | A driver exercising the journey, wired into that step. |

## Group D — the feature's own deferred scope

Both are recorded deferrals from the design, not oversights.

| # | Option | What it is | Cost |
|---|---|---|---|
| **D1** | **Class-C climate years.** The stress module ships the machinery and accepts `kind="profiles"` entries, and the sweep reports them `profiles_not_supported_yet`. Real climate years were a data-procurement follow-up that this environment could not satisfy. | Correlated weather-and-demand extremes are what actually dominate adequacy tails; the parametric scenarios that run today are a stand-in. | Depends entirely on sourcing the data. The code path is already shaped for it. |
| **D2** | **PRAS or Antares handoff.** The design names it optional, and the screening chip already tells the user when the classical number is the misleading one — which is exactly when an external engine is worth reaching for. | An independent engine to check the sequential Monte Carlo against. | Large. A new integration, not a change. |

## Group E — housekeeping

| # | Option | Note |
|---|---|---|
| **E1** | **Rebase the other open pull requests.** #8, #9 and #3 all sit on the master from before this merge. #8 touches no file the earlier decomposition moved, so it should integrate cleanly; #9 and #3 I have not checked. | None of them is my work. |
| **E2** | **Answer the release-notes question.** The PR template asks for an entry in `doc/release_notes.md`; that file tracks the modelling workflow, and this is entirely inside the bundled GUI. The box was left unticked deliberately. | One line, once someone decides whether GUI features belong there. |

## What has been done since this was written

The recommendation below was taken, and one option beyond it.

| # | What happened |
|---|---|
| **A1** | PR #12 opened for the two follow-up commits, CI green, squash-merged. The branch was restarted from the new master. |
| **C1** | The IEEE 39-bus journey re-run against the pinned PyPSA 1.1.2 in a clean environment: 46 steps across both networks, every figure identical to the 1.3.0 runs. The only differences were three findings becoming *visible* on the older metadata, which is what the exercise was for. Recorded in `2026-09-09-ieee39-e2e-review.md` §11. |
| **C3** | `pypsa-gui/backend/tests/qa_adequacy_journey.py` — a QA driver covering the adequacy journey, discovered automatically by `tests/run_qa_drivers.py` and therefore run by the `gui-qa-drivers` CI step. 91 checks over preflight, the margin- and cap-constrained solve, `/results/reserve_margin`, `/results/adequacy`, `/results/copt`, `/results/mc` with ELCC, the stress registry, the class-B/C sweep, `/results/fmea_modes`, the worksheet sidecar, the bundle, and the margin loop with its 409 mesh. Passes on both PyPSA 1.3.0 and the pinned 1.1.2. |
| **B1** | Closed. The clamped planning-loop iterate is no longer re-solved: the controller takes an optional `solve_at.solved_value(x)` — the same duck-typed shape as its existing plan-hash probe — and the margin route offers it, so a refinement midpoint that stands for an already-solved margin stops the search before the solve instead of after it. The verdict, the certified margin and the restored config are unchanged; the run is one solve plus one MC shorter (3 -> 2 on the QA journey). |
| **C3+** | A second QA driver, `pypsa-gui/backend/tests/qa_adequacy_studies.py`, covering what the journey driver does not reach: the ε-cap coupling loop (the margin loop's sibling on the same controller), the frontier sweep and its monotonicity, and every study's empty-state and abort contract — 204 before a run, 404 to an abort of a study that never started, an abort mid-flight that keeps the points it had and still runs the closing restore, and the same abort answering 200 twice more. 72 checks, ~24 s, discovered by the same runner. |

Everything else in the tables above stands as written; of Group B, B1 is
now closed and B2 is still open.

## If you want one recommendation

**A1, then C1.** Put the two follow-up commits through CI, because the rename
fix touches the path every component edit takes. Then close the version gap,
because it is the only place left where something could be wrong and nothing
we run would show it.
