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

Built on `claude/as-09-spectrum-input`: `services/adequacy/spectrum.py` and `POST /api/network/spectrum/trace`. f_c = the lowest frequency above which at most the limit's share of in-band energy lies; a split storage joins the smoothing tier when its assumed latency is at most 1/(2π f_c). Skipped: no screen or chat tool yet (declared `unbuilt` in `services/not_via_chat.py`), and the route returns the trace's equivalent (A, T) but does not write it into a project's stored envelope; `replace_oscillation` does that for a caller such as the AS-06 loop runner.

## Comments
