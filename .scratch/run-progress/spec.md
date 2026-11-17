<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Run progress, ETA and finish notification

Status: ready-for-agent (GK B plan GO slice 1; UX plan GO with conditions below)
Type: spec
Owner decision: UX-NEXT-1 (2026-11-17), with HOLISTIC-1, EXPERT-GUIDED-1, RAW-NAMES-1, DENSITY-1

## Problem

A run is a black box once started. The header's Run button turns into an amber
"Abort" with a spinner and nothing else: no stage, no elapsed time, no estimate.
A campus study (3–7 min today, 55 min before the island speed-up,
`.scratch/campus-run-time/`) or a long optimisation gives the user no reason to
believe it is moving, no idea when to come back, and no signal when it ends
unless they are looking at the queue panel.

## What exists (reuse, do not rebuild)

| Piece | Where | Gives us |
| ----- | ----- | -------- |
| Queue jobs, polled 1.5 s while any job is active | `hooks/useSolveQueue.ts`, `GET /api/simulation/queue` | `kind`, `status`, `position`, `enqueued_at`, `started_at`, `finished_at`, `error` for every run of every project |
| Terminal transition detection | `hooks/useJobTerminalInvalidation.ts` (`terminalTransitions`), mounted once in `ProjectTabs` | the one place that sees "running → done/failed/aborted" |
| Persisted job history | `solve_jobs` table, `services/solve_job_store.py` | start/finish times of every past run, kept across restarts |
| Campus run stages | `RUN_STAGES`, `run_job_state` in `campus_electrical_service.py`; labels in `pages/campusRunProgress.ts` | stage + `done/total` for island checks and cuts |
| Solving project is read-only | `setSolvingReadOnly` in `layout/AppHeader.tsx`, `evaluateMutation` | edits blocked with a named reason |
| In-app toasts | `react-hot-toast`, `components/ToasterHost.tsx` | the notification surface |
| Assistant runs | `components/runMonitor/`, `services/run_monitor.py` | their own stage card in chat (not changed here) |
| Detached progress for chat/voice | live-voice issue 11 (`/api/chat/{id}/progress`) | the channel completion events reach chat on |

## Journey fit

Journey steps: **run → wait → read results** (HOLISTIC-REC-1), Guided and
Expert alike. It starts where the Run button (readiness gate) hands off: the
button's amber state gains the status line. It ends where results begin: the
finish toast's "Open results" lands on the same results headline the Guided
results-legibility package and the next-step flow's "Next" lead to, never on a
separate page. A failure hands back to readiness ("See why" opens the run's
error, with the field to fix where the readiness gate names one). Shared
reference: `.scratch/ux-walkthrough-2026-11/README.md` once the walkthrough
audit writes it.

## 1. Stages the user sees, per run type

One status line per run, same words in Guided and Expert (EXPERT-GUIDED-1).
No raw tool or kind names on screen (RAW-NAMES-1): never `gridspine`,
`profile_gen`, `island_checks`, `lopf`.

| Run type (`kind`) | Name on screen | Stages shown |
| ----------------- | -------------- | ------------ |
| `solve` | "Optimisation" (or the run label the button already shows) | Waiting in queue (#n) → Solving → Saving results |
| `campus` | "Campus study" | Waiting in queue → Preparing → Ranking hours → Island checks *12 of 35* → Cut checks → Sizing → Harmonic scan → Voltage stability → Dips and restart → Investment (labels from `campusRunProgress.ts`) |
| `gridspine` | "Grid study" | Waiting in queue → Running → Saving results (study stages later, slice 5) |
| `profile_gen` | "Generating profiles" | Waiting in queue → Generating |
| AC power flow (foreground) | "Power flow" | Running (phase text later, slice 5) |
| Guided goal study (`eh_study`, not a queue job) | "Reliability study" | Studying *step 12 of up to 30* (its `pipeline.solves_consumed` / `budget_solves`) |

Expert adds, on the same line: the step count for staged work (*12 of 35*) and a
"Log" link to the existing log stream. Guided shows stage + time only.

The bar is **indeterminate** unless an estimate exists (section 2). We never
draw a percentage we cannot back.

**One place.** The header's existing status pill (today "Idle" / "Running…" /
"Queued #n", and "Idle" while a Guided study runs: walkthrough J4/J5) becomes
the status line. It reflects every run of the current project, queue job or
study, so Guided and Expert show the same state. No second element.

## 2. ETA

**Source.** Past finished runs in `solve_jobs`: duration = `finished_at − started_at`
of `completed` jobs with the same project, the same `kind` and the same solver
mode (from `solver_config`). The estimate is the median of the last 5. Queue
waiting time is not part of it; a queued job shows its position instead.

**Shown when known (≥ 2 matching past runs):**
`Solving · 2:14 · about 4 min left` — rounded: under 1 min → "under a minute
left"; under 10 min → whole minutes; above → nearest 5 min. Tooltip: "Based on
your last N runs of this project."

**Running past the estimate:** `Solving · 9:40 · longer than usual (usually ~6 min)`.
Never a negative or "0 min left".

**Shown when unknown (0–1 past runs):** `Solving · 2:14` plus, in muted text,
`first runs — no estimate yet`. Elapsed always counts.

**Not used:** runs of other projects (model size differs too much), failed or
aborted runs, runs whose `started_at` is missing. Changing the solver mode
starts a fresh history.

**Goal study:** no estimate. Its step budget is an upper bound (it can stop
early), it has no stored history, and it runs 12–33 s on the shipped sites
(walkthrough). It shows the step count and elapsed time only.

**Campus per-stage estimate** (slice 5): store stage durations on the job, then
estimate the remaining stages instead of the whole run.

## 3. Notification on finish or fail

Fires once per job, only for jobs the signed-in user started
(`enqueued_by_user_id`), from the existing `terminalTransitions` hook.

| Outcome | In-app toast (always) | Browser notification (only when the tab is hidden and permission granted) |
| ------- | --------------------- | ------------------------------------------------------------------------ |
| completed | "Campus study finished — *Project X* · 6 min" + **Open results** | same title; click focuses the tab and opens results |
| failed | "Optimisation failed — *Project X*" + **See why** (opens the queue row with the error) | same; stays until dismissed |
| aborted by the user | none (they did it) | none |
| interrupted (backend restarted) | "Run stopped when the app restarted — *Project X*" + **Run again** | none |

- **Permission** is asked only from a user click: a one-time "Notify me when it
  finishes" link on the running status line. Never on page load. Once granted
  or denied, the link goes away; a setting under user preferences can switch
  browser notifications off again.
- **One tab notifies.** Tabs claim a job id through `localStorage`
  (`run-notified:<job id>`) so two open tabs do not double-notify.
- **Missed while away.** On load, jobs of mine that finished since the tab
  last saw the queue (at most 24 h) get one summary toast:
  "2 runs finished while you were away" + **Show**.
- Toasts on the current project's run also refresh its results (already done by
  the invalidation hook); the toast for another project switches to it on click.

## 4. What stays usable during a run

- The whole app: navigation, other projects (edit, save, run — they queue),
  results of other projects, Guided chapters, chat.
- The running project: read-only, as today (owner decision RUN-READONLY-1,
  2026-11-17), with one visible line at the top of the tab ("Being solved:
  edits open again when the run ends"); blocked controls refer to it (UX 4).
  Its previous results stay readable, shown as "Out of date" with a visible
  "Re-running" tag (ResultState words, UX 5).
- A **Runs indicator** in the header (count of my active runs across projects,
  amber while any run is active) opens the queue panel. It replaces the need to
  keep the queue panel open to see progress.
- Nothing modal. Abort stays on the Run button.

Owner decision RUN-READONLY-1: read-only, explained. Drafting edits during a
run is not in this package.

The package also owns the lasting header "Open results" link after a solve
(audit #25, handed over from the next-step flow).

## 5. Chat and voice

- No model turn for progress or completion (live-voice issue 11 rule).
- When a run ends, the chat panel appends one neutral system line
  ("Campus study finished · 6 min · Open results"), using the same terminal
  event as the toast; no new SSE frame names (issue 11 owns the observer stream).
- An active voice session says the same line once, only when it is not speaking
  or listening; otherwise it queues until it is idle.
- The assistant can read progress with the existing status/`wait_for_job`
  tools; slice 6 adds the ETA to what those tools return, in plain words.

## 6. Slices

Each is one PR, ≤ 1,500 lines, in this order. Files in open PRs wait.

| # | Slice | Files | Waits for |
| - | ----- | ----- | --------- |
| 1 | ETA service + route: `services/run_estimate.py` (median of past runs from `solve_job_store`), `GET /api/simulation/queue/{job_id}/estimate` | `services/run_estimate.py`, `routers/solve_queue.py`, `solve_job_store.py` (read helper), tests | none |
| 2 | Run status line + Runs indicator in the header: stage, elapsed, ETA, unknown state, Guided/Expert | `layout/AppHeader.tsx`, new `components/runStatus/`, `hooks/useRunEstimate.ts` | slice 1 |
| 3 | Finish/fail notification: toast, browser notification opt-in, one-tab claim, missed-while-away | `hooks/useJobTerminalInvalidation.ts` (or a sibling hook), new `hooks/useRunNotifications.ts`, user preference | slice 2 |
| 4 | Read-only wording + "being re-run" mark on the running project's results | `utils/` mutation verdict wording, results header | none |
| 5 | Per-stage progress: campus stage durations and ETA, solve phases, grid-study stages, power-flow phase | `services/solve_queue.py`, `campus_electrical_service.py`, `api/solveQueue.ts` | #1561, #1578 merged |
| 6 | Chat line + voice line on finish; ETA in the assistant's status tools | `routers/chat.py`, `ChatPanel.tsx`, harness status tools | #1550, #1556 merged; live-voice issue 11 owner |

## Done

Spec agreed with GK B and UX; slices 1–6 merged. For each run type: the user
sees a named stage and elapsed time from start to end; an estimate after two
runs of the same project; one notification on finish or fail in the tab or the
OS; and can work elsewhere in the app throughout.

## UX plan GO conditions (UX reviewer, 2026-11-17)

- (a) Header is fine with a stated truncation order and real shots at 1280 and
  1024 px. Drop order: ETA, then step count and Log link, then stage name;
  elapsed and the project/run name stay. Cut text still exists in the Runs
  panel. If it does not fit at 1024 px, a strip under the header instead, not
  both. Elapsed timer not announced; only a stage change goes to a
  `role="status"` region.
- 1 Guided: the Runs indicator opens a popover/in-place drawer listing the runs
  (the Solve queue is Expert-only); "See why" shows the error there in plain
  words. Open results: `openFlowTargetInPlace` (Guided) / `openFlowTarget`
  (Expert), always through `withReturn`. A finished run never jumps by itself.
- 2 Runs indicator not colour alone: visible count + "N runs active", with a key.
- 3 Colons, not em dashes: "Campus study finished: Project X · 6 min",
  "Optimisation failed: Project X", "first runs: no estimate yet",
  "usually about 6 min" (not "~6").
- 4 Read-only reason: one line at the top of the tab, not on every control.
- 5 "Out of date" + visible "Re-running" tag with a key; reports and exports
  made during a re-run carry the state in their caption (#1421).
- 6 Browser notification opt-in line says "Shows the project name in the
  notification"; the link stays until a choice; after a denial a muted
  "Blocked in this browser" under user preferences, named in both modes.
- 7 Finish toast auto-dismisses; failed and interrupted stay; all
  `role="status"`; "Run again: Campus study" with a double-click guard.
- 8 Indeterminate bar and animation honour `prefers-reduced-motion`;
  skeleton/busy rules apply while the line loads.
- 9 Chat/voice line uses the toast's words; slice 6 ETA in the same words.
- 10 Shots per UI slice, Guided and Expert: queued with position, running
  with ETA, unknown ETA, over-run, Runs indicator open, each toast, away
  summary, read-only line, narrow widths; hidden-tab notification text in the PR.

## Coordination

- UI-MODERN-1 04 ("solver-stage progress") overlaps slice 2/5: whoever holds it
  reuses this status line rather than adding a second one.
- Guided results legibility (session_01G7qCuinh1KruE32usEJvz7) owns
  `ResultsCard.tsx` and the chapter tiles' running lines; "Open results" lands
  on its section-10 headline.
- Live-voice issue 11 owns detached progress for chat/voice; slice 6 plugs into it.
- RunMonitor (assistant runs) keeps its own card; its solves show up in the
  Runs indicator like any other job.
