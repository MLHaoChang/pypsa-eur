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
