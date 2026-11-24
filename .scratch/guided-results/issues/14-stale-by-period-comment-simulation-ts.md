<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 14 Stale comment on `LcohPeriodEntry` in `simulation.ts`

Status: needs-triage
Source: #1939 review (GK nit)

## What

The comment above `LcohPeriodEntry` says flat runs return `by_period: undefined`. The backend returns `by_period: []` for a flat run (`pypsa-gui/backend/services/results/lcoh.py`, `per_period_rows`), so the comment is stale. Fix the comment, and check that the `by_period?:` type nearby agrees with the empty list.

## Where

`pypsa-gui/frontend/src/api/simulation.ts:211-212` (verified present on master 7677384a2).
