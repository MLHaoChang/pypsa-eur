<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Whole-application assistant coverage contract

Status: required implementation/release contract, not implemented coverage.
Load flow is one illustrative journey. It does not bound the assistant's scope.
The assistant covers every authorized, project-eligible existing tool and every
inventory-confirmed application interaction through shared services.

## Source inventory and known gaps

[capability-inventory.json](capability-inventory.json) records the current source
snapshot: **220 tool declarations, 14 result tabs and 17 SlidePanel IDs**. Each
tool records toolset membership, route mapping, input keys and schema digest.
Behavioral fixture mapping is explicitly pending. These counts do not establish
that every UI control is exposed or that every tool works through live voice.

The source snapshot identifies a real navigation gap: the actual Results view
has `adequacy` and `fmea` tabs, but `RESULTS_TAB_ENUM` does not offer those IDs.
Audit the rest of the panel aliases, subviews, filters and editable controls too.
AR is a requested label without an exact match in this inventory; map it to the
actual module/UI vocabulary during the audit instead of inventing its expansion.
Every mapped AR functionality belongs in the same coverage gate.

Maintain a generated capability matrix keyed by domain, tool/schema version,
operation, project eligibility, UI target, parameter/input type, job adapter,
evidence section, provenance and behavioral fixture. Each UI control without an
existing tool is a declared gap: add a shared typed tool over its service or
record an explicit supported limitation. Do not claim whole-application coverage
while an eligible required interaction is silently omitted. Do not use arbitrary
browser clicks or model-generated code to conceal missing service/tool coverage.

Official OpenAI tool offerings are currently bounded to 128 declarations at a
time, while the source catalogue has 220. Use `find_capabilities`/toolsets and
bounded schema selection; test that every eligible tool can be discovered and
offered on a subsequent request through natural conversation. Static schema
parity or `toolset='all'` alone does not prove reachability. No voice-specific
reduced catalogue, authorization bypass or provider-specific tool implementation.

## Required families and representative real journeys

| Family | Required interactions | Representative journey and evidence |
| --- | --- | --- |
| Projects/scenarios/templates | Find/open/create/save/rename/branch, authorized rebind, compare, snapshots | Create a typed project, edit, save, branch, compare and reopen; exact IDs/ACL and no repeated creates |
| Network/topology/asset parameters | Components, carriers, constraints, connection/asset detail, scalar and bulk edits | Inspect an asset, preview a typed edit, apply through existing gate, validate and show the affected UI |
| Time series/horizon/multi-period | Upload/inspect/map, profiles, snapshots, investment periods/vintages and overrides | Import a series, set horizon/period settings, solve, inspect the same period/window and export |
| Solver/simulation | Solver config, validation, LOPF/SCLOPF/AC, locks, queue/start/status/cancel/logs | Start a slow job, answer another read while it runs, receive completion and update the correct result view |
| All results | Overview, capacity expansion, dispatch, load flow, prices, economics, emissions, curtailment, lost load, adequacy, storage cycling, FMEA, investment, asset detail | Enumerate/acknowledge every eligible tab; real domain fixtures check values, source, windows, missing/stale evidence and detailed follow-ups |
| Reliability/health/adequacy | Health/asset assumptions, screening, frontier, Monte Carlo, margin/coupling/planning studies and FMEA | Edit a supported assumption, run a small study, monitor, interpret fidelity/limits and compare a bounded refinement |
| GridSpine/campus electrical | Study creation/config, dispatch source, staged runs, rankings, capacity/connection assessments and handoff | Create/configure a small study, run stages, discuss one assessment, refine, rerun and export verified evidence |
| Economics/investment/Library | Participants, tariffs, contracts, billing/value flows, site connections and Library inputs | Change a typed commercial/engineering input, recalculate, explain software-computed values and show matching views |
| Experiments/sensitivity | Numeric/bool/enum/nested config/file-ID variations, baseline isolation, provenance and stop conditions | Preview a multi-case plan, approve, run, monitor partial outcomes, compare compatible metrics and select/export evidence |
| Import/export/delivery | Existing file formats, inspection, mapping, validation, tables/charts/bundles/reports and downloads | Import supported data, expose validation, generate results/chart/report and verify the delivered file content |
| Reports/templates/edit round trip | Templates, sections, rendering, exports and supported revisions | Generate a report, change a supported section, rebuild, verify provenance and delivery without duplicate outputs |
| Audit/undo/checkpoints | Audit trail, scoped undo/restore, dirty state and versioned previews | Preview/edit, inspect audit, undo or checkpoint restore with ordinary safety gates and correct UI refresh |
| Harness/workflows/providers | Capability discovery, toolsets, skills, workflows, durable tasks and supported profile switching | Prolonged mixed-domain conversation, guarded provider switch, task resume and no replay of earlier actions |
| UI/preferences/monitoring | Every eligible panel/subview/filter/selection/editor; independent progress/results updates | Navigate while monitoring, invalidate/re-fetch changed evidence and acknowledge exact project/run/asset/generation |

At least one real journey per family, plus exhaustive deterministic
catalogue/schema/route/discovery/navigation contracts. Use isolated fixtures for
destructive operations. Domain validity and tool authorization remain enforced;
“all” does not grant a user capabilities forbidden by account/project ACL.

## Parameters and experiment contract

Extract editable parameter schemas from actual backend models/services and GUI
bindings. Cover scalars, booleans, enums, lists, nested structures, engineering
units, component IDs, time-series/file references and topology where supported.
Model output names typed changes; the server validates and computes them.
Provide changed-path previews, defaults, range/unit checks, side effects, dirty
state and affected results. Revalidate the preview fingerprint at commit.

Generalize experiments with typed domain adapters over the existing tools and
queues, not arbitrary code execution. An experiment records baseline/version,
cases/typed changes, solver/study settings, seeds, objective metrics, authorized
run policy, time/cost/resource bounds, job/run IDs and stop conditions. Domain
schemas govern supported parameters; the current numeric network sensitivity
tool remains a useful implementation, not the only experiment type.

Preflight on isolated contexts; preserve the baseline and handle all/partial
failure explicitly. Check compatibility before comparisons. Persist every case's
inputs and outcome; no synthetic success when an artifact or result is missing.
Persist a dependency graph of ordinary task steps. Existing `start_task` stores
plans but runs no hidden actions; any new execution coordinator advances steps
through normal harness admission/confirmation/locks/budgets. On interruption or
unknown completion, reconcile before retry; unapproved steps wait for approval.

## Nonblocking work, monitoring and UI updates

Define a neutral job adapter contract: actor/project/task/job identity, kind,
status, phase/progress, sequence cursor, timestamps, input/result revision,
warnings, terminal outcome and cancellation acknowledgment. Cover queue solves,
foreground simulation/AC-PF, GridSpine, FMEA/adequacy/Monte Carlo/frontier and any
other asynchronous domain worker discovered by the inventory.

Current `run_simulation` and `run_ac_pf_stage` start workers but their harness
solver-log bridge holds the turn until completion. Fix or adapt dispatch to
return a stable job/task handle, detach progress observation and release the
turn lease. Returning “started” does not mark scientific results completed.
Use existing queued execution where semantically equivalent; AC-PF and other
workers need explicit adapters rather than assuming one solve queue covers all.

The user can ask a read or navigate while a job runs. Existing project locks
still refuse conflicting mutation. Explicit foreground/background tasks have
separate lifecycles; Stop speaking, Stop monitoring, Cancel task and Cancel job
are distinct actions. Read-only monitoring is default; further mutations follow
normal gates. Avoid holding a model turn open just to wait for hours.

Publish versioned progress/completion/evidence events with an authenticated
cursor and snapshot recovery. On reconnect, recover the job snapshot and events
after the last cursor; deduplicate transitions. Job completion invalidates only
the matching project/run/result-query revisions and updates open relevant views.
Explicit user navigation wins over old notification navigation. An update cannot
seize the planner turn, replay a mutation or move a newer view unexpectedly.
No repeated model calls for polling. Domain events may share existing neutral
UI/status infrastructure; extend closed SSE contracts deliberately with tests if
new frames are necessary, or use a separately documented event envelope.

## Prolonged conversation and deployment contract

Persist a conversation/thread record separately from the transient ChatSession
and audio connection: actor/org, current binding generation, discussed project/
run/asset/section/window references, user goals/corrections, task/experiment/job
IDs, checkpoint/evidence revisions, model profile lineage and cumulative budgets.
Keep exact numbers, units and constraints in structured state; summaries alone
are not the authoritative record. Compact bounded model context and archive
conversation checkpoints with access-controlled retention.

The in-process session registry, locks, abort events and confirmation tokens
cannot be shared merely by replaying history. Initial supported deployment has
ONE authoritative application worker; the hosted media worker submits/subscribes
through an authenticated gateway pinned to that owner. Route stream/confirm/
abort/history/task controls consistently. Refuse wrong-owner/failover access.
Managed media does not automatically deploy the agent worker: provision and test
its hosting, network, CPU/memory and concurrency; no local LLM/GPU is required.

A restart/resume creates a fresh transient session from an authorized durable
checkpoint, reconciles running/uncertain operations and invalidates pending
approval tokens. Do not clear unresolved work as completed or replay it to
reconstruct history. Parent conversation budgets remain persisted across child
sessions, restarts and compaction; existing per-session caps continue to apply.
Guard episode rotation/resume instead of disabling caps. Multi-worker deployment
requires a separate distributed owner/lease/cancellation/confirmation design and
failure tests before enablement.

The durable thread survives ordinary navigation, authorized project rebinding
and supported model switches. Current-view context and discussed-result references
are distinct. Rebind refreshes scoped media/bridge credentials and generation;
old-project callbacks and approvals are rejected. Model switching follows existing
history-portability guards; create a compatible child session when required,
preserving durable goals/tasks/evidence and never blindly copying provider tool
messages. Revalidate referenced project access before restoring its evidence.

## Release interpretation

This is a whole-application assistant with incremental feature-flag delivery.
The feature may expose tested subsets during development, labelled accordingly;
the full-coverage claim requires mapped eligible interactions and their tests.
Load flow, sensitivity and GridSpine are examples among the matrix above.
Hosted profile/device/latency gates remain pending; an architectural review pass
does not certify production behavior. Acknowledgment latency and grounded-answer
latency are measured separately, including turn detection, tools and TTS.
