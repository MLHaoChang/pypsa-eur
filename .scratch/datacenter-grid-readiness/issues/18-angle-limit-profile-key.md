<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Planning criteria: an angle-limit key in the grid-code profile

Status: ready-for-agent
Type: task
Blocked by: none (follows 13, merged in #708)
Gated: **yes** (gridspine)
Model tier: sonnet
Size: S
Owner decision: SAGC-1

**Confidentiality.** Cite only the public Saudi Arabian Grid Code (SAGC, May 2026) and generic engineering. Never name a client, its network, its MW figures or any scope-of-work text, here, in code, tests, fixtures or PR bodies.

## Why

The angle check takes its limit only as an `AngleLimit` argument to `screen_planning` (`gridspine/static/planning_criteria.py`). A profile in `gridspine/templates/grid_codes.py` cannot carry it, so a study set up from a profile reads the angle verdict as `unknown`.

## What to build

1. An optional profile key for the angle limit (degrees, with `source` code/assumed/extracted and the SAGC clause when sourced from the code), validated like the other single limits.
2. Build an `AngleLimit` from it when no explicit argument is given (`basis` "sagc" with the clause as `reference`, or "assumed"); an explicit argument wins.
3. Pairs stay an argument: the profile sets the figure only, so the default stays every in-service branch.

## Acceptance

- Profile validation refuses a non-finite or non-positive figure and a code-sourced figure without a clause.
- A test where the profile limit fails a row, and one where an explicit `AngleLimit` overrides it.
- No key and no argument still gives `unknown`, never `pass`.

## Triage (gatekeeper, 2026-10-26)

**Status `ready-for-agent`, gated, size S, sonnet.** Shares the `profile=None` keyword on
`screen_planning` with 17 (see 17's triage: same implementer, or after 17).

Rulings:

- **Key:** `angle_limit_deg`, a single limit `{value, clause, source[, page, quote]}` in
  `_SINGLE_LIMITS` and `PROFILE_KEYS`. `_check_limit` already refuses a missing clause or source
  for every limit, so add only the value check (finite, > 0, not bool).
- **Basis mapping:** source `code` → `AngleLimit(basis="sagc", reference=clause)`, and `assumed` →
  `basis="assumed"`. An unconfirmed `extracted` figure is **not** an SAGC basis: build it as
  `basis="assumed"` with a reference naming the clause, the page and "extracted, unconfirmed".
  The verdict row says so, and `confirm_limit` turns it into `code`.
- **Precedence:** an explicit `angle_limit` argument wins over the profile. The report's
  `angle_limit` entry says which was used. `pairs` stays argument-only (default: every in-service
  branch).
- **Tests:** the issue's three, plus the extracted → assumed mapping.
