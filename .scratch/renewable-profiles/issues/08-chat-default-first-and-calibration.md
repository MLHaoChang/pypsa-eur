<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-08 — chat: default-first workflow, shaping and compare tools

Status: ready-for-agent; audit 2026-11-19: readiness, shaping and compare tools and the default-first workflow text are on master; left (unverified): OJ-47 to OJ-54 Tier A journeys, inventory check
Type: task
Baseline: master `5d5bf26b83aa18c9a2a0a3418d3beeecccc8d086`
Blocked by: RP-02, RP-06, RP-07; sequencing with live-copilot LC-03/04/05
Requirements: R7 in [the spec](../spec.md)

- Rewrite `harness/workflows/renewable-profile.md` default-first. With ready generators and a known weather choice it generates at once. It asks only for the weather source and year when unknown, and for coordinates when missing. The parameters step hands off with `ui_open_panel('timeseries')`. A new `calibrate` step maps deviation words to RP-06 ops, runs `compare_profiles`, shows before/after and asks before apply.
- Catalogue entries: `get_profile_readiness`, `shape_generated_profile`, `compare_profiles`; batch arguments on `apply_generated_profile` (RP-09). Append-only edits to `catalogue.py` and `chat_tools.py`, announced to the live-copilot build session first.
- Tier A journeys OJ-47–OJ-54 (`.scratch/live-copilot/owner-journeys.md`) scripted on the stub wire.

Acceptance: OJ-47–OJ-54 pass in Tier A. The inventory check passes. No profile number in the words lacks a tool fact.

## Comments

- 2026-11-23 status sweep: landed in no merged PR: per the issue audit of 2026-11-19 the readiness, shaping and compare tools and default-first workflow text are on master. Remains: OJ-47..OJ-54 Tier A journeys and the inventory check.
