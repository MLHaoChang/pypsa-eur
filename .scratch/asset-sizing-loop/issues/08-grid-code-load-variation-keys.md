<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-08 — Grid-code keys for load variation

Status: done
Type: task
Blocked by: none
Model: Sonnet
Reviewer: GK A

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

Add keys to `grid_codes.yaml` for load variation: ramp MW/min, ΔP per window (for example 10 MW per 5 s), and an LCL ride-through band, plus Stage 1 d_lim. All marked "assumed" with public sources only (SAGC-1). Read the IEC TR 61000-3-7 text (not the EPRI reproduction) before setting defaults. Intent D grid-code check: a profile key says whether transfer to BESS counts as ride-through (AS-NOUPS-1).

## Acceptance

- [x] Tests first: schema accepts and rejects the new keys; a profile missing them falls back to a marked default.
- [x] Every new value carries "assumed" and a public source or "generic engineering".
- [x] The ride-through-transfer key drives the AS-01 warning for intent D.
- [x] Existing profiles load unchanged.

## PRs

#963. `design_intent.grid_code_warnings` (AS-01) delegates to `ride_through_transfer_warning`. IEC TR 61000-3-7 text was paywalled: the Stage 1 value is as read by GK A, not verified by the author.

## Comments
