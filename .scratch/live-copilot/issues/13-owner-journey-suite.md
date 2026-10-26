<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-13 — owner-journey acceptance suite (Tier A in CI, Tier B capped)

Status: ready-for-agent. Owner decision Q2: Tier B is a manual-trigger run with a
Progress: oj_grid fixture, backend OJ modules, local browser runner, CI browser job `GUI owner journeys (browser)` (OJ-01, OJ-05) and Tier B `run_owner_journeys.py` (manual workflow, OJ-01 live) exist; PR open. Done once merged.
hard cost cap, reported per journey, never a merge gate. The Tier A harness
lands with the first layer that closes a journey (OJ-09 in LC-01 or OJ-01 in LC-03).
Type: task
Baseline: master `d006f4beaa302b97432c65757762ae180222731a`
Dependencies: none for the harness; each journey lands with its slice

## Problem

CI runs the 133-case pilot gate, 30 QA drivers and 4 backend shards, all with a
scripted model (`pixi.toml:275`, `:283`; `.github/workflows/test.yaml:130-215`).
No test plays an owner journey end to end. The real-browser runners
(`frontend/scripts/smoke-chat-composer.mjs`, `chat-narrow-cases.mjs`) run
locally only. No live-model evaluation runs anywhere.

## Scope

1. **Fixture** `oj-grid` and its hand-calculated oracle, as in
   [owner journeys](../owner-journeys.md).
2. **Tier A backend**: one pytest module per journey family under
   `pypsa-gui/backend/tests/`, using the loopback wire
   (`smoke/stub_openai_endpoint.py`) through the real provider path. Runs in
   the existing shards.
3. **Tier A browser**: one Playwright runner reusing
   `frontend/scripts/composer-fixture.py`, added as a CI job with a
   10-minute budget and Chromium from the runner image. Starts with OJ-01.
4. **Tier B**: `tools/run_owner_journeys.py --live`, manual
   `workflow_dispatch` only, a hard cost cap, results as an artifact with
   per-journey pass counts and denominators. Never blocks a merge.
5. Each slice adds its journeys; a slice is done only when its journeys pass.

## Done when

The harness exists with OJ-01 green in Tier A (backend and browser) in CI.
