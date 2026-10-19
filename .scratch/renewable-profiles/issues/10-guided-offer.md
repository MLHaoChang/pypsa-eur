<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-10 — Guided mode offers default generation

Status: needs-triage
Type: task
Baseline: master `5d5bf26b83aa18c9a2a0a3418d3beeecccc8d086`
Blocked by: RP-02, RP-08, RP-Q2
Requirements: V2, V7 in [the spec](../spec.md)

In Guided mode, when the readiness feed shows wind/solar generators with `profile: none` or `network_native`, the assistant offers one line: "N renewable generators have no weather profile, generate defaults?". The offer comes at the natural pause after a generator is added or a project is opened, and once per project session.

Acceptance: Tier A, a fixture with an unprofiled wind generator: after `open_project` the offer appears once; declining suppresses it for the session.
