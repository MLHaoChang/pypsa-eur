<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Planning criteria: post-contingency voltage band from the grid-code profile

Status: needs-triage
Type: task
Blocked by: none (follows 13, merged in #708)
Gated: **yes** (gridspine)
Model tier: opus
Size: S
Owner decision: SAGC-1

**Confidentiality.** Cite only the public Saudi Arabian Grid Code (SAGC, May 2026) and generic engineering. Never name a client, its network, its MW figures or any scope-of-work text, here, in code, tests, fixtures or PR bodies.

## Why

Stage 2 judges bus voltages against a fixed band, `V_MIN_PU`/`V_MAX_PU` = 0.9–1.1 pu, taken from `gridspine/static/contingency.py` and marked as assumed in `PLANNING_LEDGER`. The grid-code profile (`gridspine/templates/grid_codes.py`, `voltage_bands`) already carries per-voltage-level bands with their source.

## What to build

1. Stage 2 takes its post-contingency band per bus from the selected profile's `voltage_bands` (by the bus's kV), falling back to the assumed band, and says which applied.
2. If the profile needs a separate post-contingency (emergency) band, add it as an optional band key, cited by SAGC clause or marked assumed.
3. Update the ledger line.

## Acceptance

- A test where a profile band narrower than 0.9–1.1 pu fails a bus that passes today.
- No profile gives today's band, labelled assumed.
