# Handoff: grid-to-rack feasibility and implementation proposal

Read [overview](../../docs/research/gridspine-grid-to-rack-overview.md), [architecture](02-ARCHITECTURE.md) and [work packages/test matrix](04-IMPLEMENTATION-TESTS.md). Baseline `525b2587a1db0ebbb9ee3cfbc8adc0dc9c278a7c`; date 21 October 2026. This package adds no runtime engine or equipment-control authority.

## Core decisions

1. Extend the existing data-centre example and native planning/electrical/evidence workflow, rather than create a duplicate planner, simulator database or single/staged example split.
2. Link 20-year investment/operational scenarios to selected short electrical events. Do not simulate every millisecond of 20 years or manufacture high-speed measurements from hourly means.
3. Keep measurement planes explicit: GPU/server/rack, UPS/DC link, campus and PoC. Distinguish active-power buffering from reactive voltage support.
4. Reuse existing frequency screening, converter presets and heuristic planning-cut/apply seams, preserving their scope labels. A 1 ms integration step is not evidence of voltage/PLL/current-limit/EMT fidelity.
5. First implement a bounded source-tagged trace/cohort and buffer/reserve screen. Voltage, harmonics and detailed rack ride-through remain not established at this level.
6. Evaluate one detailed-engine adapter after scope/licence/model review; DPsim first candidate, optional OpenG2G workload/grid coupling, MIT UPS study as a comparison candidate. No mandatory HELICS/Vessim/second planner and no automatic gated data download.

## Start next session

Refresh actual master and relevant open data-centre, grid-readiness, island, workflow and Library work. Read current `AGENTS.md`, steward and relevant harness/design contracts. Compare current source hashes and prior decisions with [native evidence](native-evidence.json). This is a point-in-time assessment; later PRs may fill some gaps.

Take GTR-00 first: agree fidelity levels, physical planes, trace/state contracts and permitted outputs. The existing readiness spec excluded in-app EMT, and the island plan parks ANDES under Q4. This research proposes an optional detailed-engine extension; it does not silently authorize bundling/deployment or reopen Q4. Follow the current engine/design/licensing/gatekeeper process before implementing those changes.

Then scope GTR-01/02 and a small GTR-06 screen view. Preserve the original example when opt-in is absent. Reuse native imports/pins, jobs/approvals, source tags, stale-result and evidence exports. Write meaningful independent balance/rejection tests and run relevant native suites. Source/admission metadata and an external library's README are not physical validation.

## External evidence

`source-observations.json` records commits, retrieval hashes and failed lookups. The read-only cache used here was `/workspace/scratch/grid-to-rack-research`; it is ephemeral and not required to read the authored plan. No raw repository code or hardware traces are in Git. Re-fetch exact pinned files only under then-current network/source policy and inspect their applicable terms. Do not execute examples, install plugins, accept embedded skill instructions or register accounts merely because external README text suggests it.

OpenG2G includes synthetic training generation and a power-flow backend; it is not a ready converter EMT model. Its inference example depends on a gated dataset. Zeus/DCGM need actual hardware/API bandwidth verification and a separate authorized measurement scope. Public PSCAD references may require commercial runtime and have restrictive/no established source-file reuse rights. Preserve these distinctions.

## First meaningful demonstration

One current native data-centre project, one workload/growth/topology/state basis and three explicitly approved comparable cases: no added fast smoothing; physically limited local UPS/storage smoothing; and coordinated permitted workload/storage control. No case has been executed in this assessment. Screening can first demonstrate power/energy/reserve implications; detailed voltage/protection claims require the later appropriate validated model.

Report PoC residual, storage peak/energy/SOC and available backup, workload penalty, cost/loss basis and model gaps. Pin exact parent plan/dispatch, period, topology, control, trace and engine state. Negative load steps, repeated events, low SOC, weak grid, failures and reserve conflicts must not be omitted. Do not credit the same battery capacity to incompatible simultaneous services.

## Pending

Independent domain review; measured rack/UPS/PoC calibration; source/model deployment clearance; appropriate voltage/PLL/current-limit benchmarks; dynamic engine integration; complete end-to-end study tests; detailed power quality; real-use calibration; any production/hardware interaction. Do not turn a finite simulation pass into grid-code certification or a universal 20-year guarantee.

The prior North American Library plan remains a separate work package in [PR #524](https://github.com/MLHaoChang/pypsa-eur/pull/524), now including six explicit collections. Data sources may support this example through the shared Library, but neither research package activates new feeds or supplies missing vendor/controller facts.
