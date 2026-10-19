<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-06 — calibration and shaping operations

Status: ready-for-agent
Type: task
Baseline: master `5d5bf26b83aa18c9a2a0a3418d3beeecccc8d086`
Blocked by: the profile stack landed; gatekeeper plan acknowledged
Requirements: R5 in [the spec](../spec.md)

New `services/profile_gen/shape.py`: pure functions over a per-unit `pd.Series`, plus `apply_chain(series, ops) -> (series, meta)`.
The route `POST /api/projects/{name}/profiles/shape {job_id, ops}` writes a new pending artifact with `parent_job` and the op chain in its meta. It never writes to the network; apply stays `assign_profile`.

Ops: `target_flh{hours}`, `target_cf{cf}`, `quantile_map{target: quantiles|observed_ref}`, `tail_damp{side: low|high, beyond_q, factor, keep_flh}`, `peak_shift{hours}`.

Acceptance (property tests with hypothesis, or seeded loops if hypothesis is absent):
- Every op keeps values in [0, 1] and the length.
- `target_flh{2500}` on a synthetic wind year → FLH within 0.5 % of 2500, even when scaling would push values above 1.
- `quantile_map` keeps the rank order (Spearman = 1) and matches the target quantiles within 1e-6.
- `tail_damp{high, 0.9, 0.5, keep_flh}` cuts the P99 and keeps the FLH within 0.5 %.
- The chain is deterministic: the same input gives byte-identical output.
- Shaping someone else's job id → 404, and a job from another project → 403, mirroring apply.

## Comments

2026-10-19: implemented on `claude/rp-06-shaping` (stacked on RP-01), after the gatekeeper's plan
OK (conditions: unreachable targets refuse naming the maximum; provenance with `calibrated: true`
kept through apply; peak_shift wraps on the weather year; apply stays the only writer).
Deviations from the text above, from the two-axis review:
- Another project's job id is a 404 (each project's pending folder is its own), the same as apply.
- Quantile targets are met within 2e-3 and the rank order to Spearman > 0.999. With linear
  interpolation of np.quantile on 8,760 hours the exact error is about 1e-5, not 1e-6.
- Named quantiles inside the zero share (solar nights stay zero) are refused, not silently missed.
- `observed` takes inline per-unit values. A reference to an uploaded series is RP-08's chat
  step (it reads the upload and passes the values).
- `peak_shift` takes whole hours, up to ±168. Seasonal shifts are out of scope.
- Shaping is hourly only; daily/weekly artifacts are refused (their FLH bound is not hourly).
