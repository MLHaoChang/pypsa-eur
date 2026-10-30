<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-09 — Spectrum input (later)

Status: ready-for-agent
Type: task
Blocked by: 03
Model: Opus
Reviewer: GK A

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

Accept an uploaded power trace; compute the PSD and band energy, derive f_c and test an in-band energy limit (for example 20 %). Replaces the single (A, T) pair per phase where a measured trace exists. Later issue; do not start before AS-03 is merged.

## Acceptance

- [ ] Tests first: a synthetic 0.5 Hz sine of 10 MW amplitude shows its peak at 0.5 Hz with band energy matching the analytic value.
- [ ] f_c from the trace feeds the AS-03 tier split.
- [ ] Bad uploads (empty, non-monotonic time, wrong units) are rejected with a clear error.
- [ ] No client trace enters the repo (SAGC-1).

## PRs

## Comments
