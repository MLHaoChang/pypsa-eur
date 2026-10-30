<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-09 — Spectrum input (later)

Status: done
Type: task
Blocked by: 03
Model: Opus
Reviewer: GK A

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

Accept an uploaded power trace; compute the PSD and band energy, derive f_c and test an in-band energy limit (for example 20 %). Replaces the single (A, T) pair per phase where a measured trace exists. Later issue; do not start before AS-03 is merged.

## Acceptance

- [x] Tests first: a synthetic 0.5 Hz sine of 10 MW amplitude shows its peak at 0.5 Hz with band energy matching the analytic value.
- [x] f_c from the trace feeds the AS-03 tier split.
- [x] Bad uploads (empty, non-monotonic time, wrong units) are rejected with a clear error.
- [x] No client trace enters the repo (SAGC-1).

## PRs

Built on `claude/as-09-spectrum-input`: `services/adequacy/spectrum.py`, `POST /api/spectrum/trace` (analysis and dry-run tier split, changes nothing) and `POST /api/network/spectrum/tier_split` (writes the `smoothing_tier` flags from f_c under the lock gate and auto-undo). Skipped: no screen or chat tool yet (declared `unbuilt` in `services/not_via_chat.py`); the derived (A, T) is not written into a project's stored envelope; `replace_oscillation` does that for a caller such as the AS-06 loop runner.

## Comments

GK A (8) plan verdict, GO with conditions (2026-10-30):

1. Latency from `smoothing.response_latency` through a technology→control default (assumed), unknown control taken as grid following; override validated (finite > 0, known split store).
2. Upload byte cap through `read_capped`, plus a sample-count cap; oversize upload tested.
3. The write goes through the /api/network lock gate and ACL; route-policy tests, route parity and assistant inventory checked at the trial merge.
4. Before and after flag per store, latency, 1/(2π f_c) and reason; a dry run returns the same table; a user-set 1 that turns 0 is reported; non-split stores untouched.
5. f_c = the lowest bin where the cumulative in-band share reaches 1 − limit; a two-tone test shows f_c is not the peak.
6. Validation all 422 `bad_trace`: limit, band, Nyquist clip noted, too short / too few bins, unit missing or disagreeing, time numeric seconds only (ISO refused), binary or non-UTF-8 or ragged CSV refused.
7. Equivalent (A, T) labelled "assumed: single-sine equivalent of a multi-tone band"; `replace_oscillation` returns a copy. Builder note: T is the energy-weighted mean period, not 1/f_peak, so a broadband trace's slow content stays in the energy floor (review finding); equal for one tone.
8. No tool-error-kinds fragment until a chat tool exists; existing 422 body shape. Builder note: `tests/test_tool_error_kind_manifest.py` derives emitted kinds statically from routers too and fails on an unclassified `bad_trace`, so the PR keeps an `inline` fragment.
9. scipy.signal: `tests/test_packaging_requirements.py` at the trial merge.
10. SAGC-1: synthetic traces only, assumed labels with the public arXiv citation, release-note fragment.
