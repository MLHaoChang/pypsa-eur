<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Enqueuing a solve fingerprints the whole network twice

Status: done (#1949)
Gated: GK B (plan first)
Type: task
Found by: SH-16 landing (#1594): `test_a_run_on_the_demo_data_reaches_the_hand_off` hit the harness per-tool deadline (`tool_timeout` at `solve.1`, `solve_queue_enqueue`) twice on CI.

## Problem

`solve_queue_enqueue` on a full-year project spends almost all its time fingerprinting the network. A cProfile of the enqueue on `eh_concentrator_staged`'s first stage (8760 h), run locally, shows:

- the enqueue takes 9.1 s;
- 8.4 s of that is `solve_capture.capture` → `current` → `run_envelope.identity`;
- `identity()` computes `provenance_graph.runs.input_fingerprint` twice: `_fingerprint` (exact=False, 1.7 s) and `_exact` (exact=True, 6.7 s);
- each pass encodes the whole network through `workflow_tools._network_input_fingerprint` → `_exact_frame_json`, about 1.6 M per-cell `scalar()` calls (4.6 s tottime in `scalar` alone).

On a busy host this goes past the harness `PER_TOOL_TIMEOUT_SECONDS` (30 s). Users with full-year projects pay the same seconds on every enqueue. #1594 raised the deadline in that one test only (GK A (32), option A). This ticket is the real cost.

## Ideas

- Encode the network once and derive both fingerprints from it.
- Vectorise `_exact_frame_json`'s per-cell `scalar()`.

## Conditions (GK B 67)

- Both `input_fingerprint` results (exact and non-exact) and `_network_input_fingerprint` stay byte-identical to master. Check the project templates and a full-year network, plus a frame with inexact floats (e.g. 0.187/0.4), NaN, ±inf, -0.0, mixed dtypes and object columns. Compare with exact `==` (the bit-identity rule).
- The investment-case digest and every stored fingerprint stay byte-identical, so no existing record turns stale.
- The PR carries a timing figure, before and after, on the 8760 h case.
- The plan goes to GK B first.

## Comments

- 2026-11-23 status sweep: landed in #1882 (be3dcbcd): exact fingerprint encodes plain numeric/date columns in bulk. Remains: encode once and derive both fingerprints, and the before/after timing figure.
- 2026-11-24 SH 225 builder: #1949: both forms from one walk (`one_input_walk`, identity and the solve stamp), empty series written from an empty copy, exact index text reused by exact bytes. identity() on the 8760 h stage 1.23 s → 0.39 s; every digest byte-identical to master 5e8cbcdc.
