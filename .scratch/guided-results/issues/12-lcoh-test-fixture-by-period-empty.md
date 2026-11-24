<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 12 The single-period LCOH test fixture sends `by_period: []`

Status: needs-triage
Source: #1939 review (GK nit)

## What

The single-period LCOH test fixture should send `by_period: []`, the shape the backend returns for a flat run, so the test covers the real payload.

## Where

The single-period lcoh test fixture added or touched in #1939. The backend shape is `pypsa-gui/backend/services/results/lcoh.py` (`per_period_rows` starts as `[]`, line 277, and is returned as `by_period`, line 337).
