<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Planning criteria: post-contingency voltage band from the grid-code profile

Status: ready-for-agent
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

## Triage (gatekeeper, 2026-10-26)

**Status `ready-for-agent`, gated, size S, opus.** `screen_planning` already judges through a
per-bus band dict (`bands_of`, today every bus at `(V_MIN_PU, V_MAX_PU)`), and
`grid_codes.band_for(profile, kv)` already finds a profile's band by nominal voltage. Nothing
outside the tests calls `screen_planning`, so the profile arrives as a new keyword.

Rulings:

- **Interface:** `screen_planning(..., profile=None)`, a validated profile dict (`load_grid_code`
  output). 17 and 18 share this keyword: give both to one implementer as one PR with two commits,
  or land 17 first and have 18 build on it. Never two different keywords.
- **Band per bus:** `bands_of` takes each judged bus's `vn_kv` through `band_for`. A bus whose kV
  no profile band covers keeps the assumed 0.9–1.1 pu band. Each row says which applied (profile
  band with clause and source, or `assumed`), and the report gives counts of both.
- **Which band:** stage 2 judges the post-contingency state after remedial action against the
  continuous `v_min`/`v_max`. The time-limited `v_max_30min` stays a reported requirement, as it
  is today. Item 2 (a separate emergency band key) is **dropped** from this issue. If a public
  SAGC clause states a distinct post-contingency band, that is a new issue with the clause cited.
- **Ledger:** the band line names the profile, or says "assumed" when none is given.
- **Tests:** the issue's two, plus a mixed-kV net where one level is covered and one is not.
