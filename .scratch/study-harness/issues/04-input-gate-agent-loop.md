<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — Input gate loop L1: headless agent turns and the change ledger

Status: ready-for-agent
Type: task
Blocked by: 02, 03
Gated: yes (GK B)
Model tier: opus
Owner decisions: SH-1, SH-ENVELOPE-1, AI-558, TOOLSEL-1
Design: [design.md](../design.md) §6.1, §6.3, §6.4, §6.5

## What to build

1. Agent turns: `loop.run_turn` on the runner session, headless, with the stage instruction as per-turn content (`harness/workflows/profiles/<profile>/<stage>.md`, package files only) and a per-stage tool allowlist.
2. Stage 2 agent fill and stage 3 loop L1: trigger = failed `block` input rules; ≤3 rounds (profile may lower); `no_progress` when the failing set and the network fingerprint repeat; exhaustion → `needs_user`.
3. What the agent may change: `tunable` attrs of the failed rules and named data gaps inside the envelope; never deletes, objective, horizon, gate rules, grid-code limits.
4. Change ledger rows (design §6.4) from verified edit receipts only; a write without a receipt fails the round.
5. Budget accounting in `run.budget.used` (rounds, tokens from budget.py) by harness code.

## Acceptance criteria

- [ ] eh_site fixture with a missing load time series: L1 attaches it within one round, the ledger has one row with its receipt, the gate passes.
- [ ] A seeded defect the agent cannot fix within its tunables ends `no_progress` or after 3 rounds, run `needs_user`, ledger intact.
- [ ] An attempted edit outside the tunables or the envelope is carded (not run) and counted as a refusal.
- [ ] The headless turn offers only the allowlisted tools (assert the offered tool list).
- [ ] Round and token use in `run.budget.used` match budget.py, not the model's text.
- [ ] `attach_tariff`, `set_weather_choice` and series attaches are admitted only when listed in the envelope with a target; unlisted, each gets a card (both cases tested).

## Likely files

`pypsa-gui/backend/harness/runner.py`, `pypsa-gui/backend/harness/loop.py` (headless entry only), `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/harness/workflows/profiles/eh_site/` (new), `pypsa-gui/backend/tests/test_harness_run_loops.py` (new)

## Comments

## Folded from #970

Owner decision HARNESS-970-1 (3 Nov): #970's reliability items are folded into the study-harness plan. This issue's agent loop is the first consumer of three of them; each is its own issue, so nothing here changes the build in progress.

- **HH-02, requirements on assistant edits** → issue 23: the agent's edits are checked against the user's confirmed requirements at dispatch, and a violating edit is carded and counted as a refusal. Build after this issue.
- **HH-03, a durable reservation before each model send** → issue 24: the agent turn's model call is the first send it covers. It starts after this issue and after #1059 merges; this issue keeps counting rounds and tokens from `harness/budget.py` as written above.
- **HH-01A, atomic envelope edit** → issue 22: the envelope's unattended edits (including this loop's tunables) compare and write inside one project lock. Blocked by LC-40b, not by this issue.
- **Dropped:** HH-04 (typed domain references; `services/reference_resolution.py` already covers it) and HH-09B (delegation to worker agents; SH-FOLD-1 and AI-567 keep one agent loop).
